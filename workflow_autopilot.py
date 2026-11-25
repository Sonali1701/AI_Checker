"""Autopilot: every test of a student sheet checked from start to finish without a person.

Operator's request (26 Oct 2026, build 139): two sheets instead of a person at the computer.
The student sheet (the portal export: one row per copy) and an accounts sheet with one
admin + evaluator pair per row and its portal password. The autopilot reads both once:

* groups the copies by test, in sheet order (the sheet's priority), and works out each
  test's inputs from its columns: the exam type ("exam": SCHOOL PREPARATION...), the class
  and board ("ctexname": "Class 12th - Science - CBSE Board"), the subject (the brackets
  of the test name: "(Biology)", "(SST by Aditya Sir)" -> SST) and the total marks;
* gives each pair whole tests: a pair fetches its next test's question paper and Model
  Answer from the portal through its evaluator window, checks the paper's marks (build
  137), and starts an ordinary run with the pair's admin and evaluator; when that run ends
  the pair takes the next test, until none is left;
* saves each pair's password (encrypted) so its windows sign themselves in (build 138);
* skips a test it cannot do (an exam type it does not know) with the reason, and goes on;
  a test whose marks do not add up waits for the marks, and one the portal has no Model
  Answer for waits for a Model Answer given on the page (19 Nov 2026);
* keeps its state in workflow_data/autopilot.json, so after the app restarts it continues
  at the owner's next sign-in, as interrupted runs do.

Copies already in a saved run are left to that run. Every copy is tried; the portal
decides (the assignment step skips copies already evaluated).
"""
from __future__ import annotations

import csv
from workflow_sheet_status import retryable_saved_row
import hashlib
import io
import json
import re
import threading
import time
import uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

EMAIL = re.compile(r"[^\s@]+@[^\s@]+\.[^\s@]+")
PLAN_TTL_SECONDS = 60 * 60
RUN_POLL_SECONDS = 10
SIGN_IN_POLL_SECONDS = 15
RESUME_WITHIN_SECONDS = 24 * 3600
PREPARE_ATTEMPTS = 3          # a test whose paper fetch or run start keeps failing is skipped after this many
RETRY_SECONDS = 60
MARKS_SERVICE_WAIT_SECONDS = 30 * 60   # how long a test waits for the AI service to read its marks
REVIEW_POLL_SECONDS = 5       # how often the review thread looks for a test whose uploads have landed
# Bracketed parts of a test name that are not its subject: "(Infinity Pro)", a year...
NOT_SUBJECT = re.compile(r"infinity|\bpro\b|\bbatch\b|\bseries\b|\bphase\b|\bpart\b|\b20\d\d\b|\bset\b|\bshift\b|\bhindi medium\b|\benglish medium\b",
                         re.IGNORECASE)


# ----------------------------------------------------------------------------- inputs

def subject_from_test_name(name):
    """The subject a portal test name carries in brackets: "Board Subjective Test - 10
    (Biology) (Infinity Pro)" -> "Biology"; "(SST by Aditya Sir)" -> "SST"; the whole name
    when no bracket names a subject."""
    for part in re.findall(r"\(([^()]*)\)", str(name or "")):
        part = re.sub(r"\s+by\s+.*$", "", part.strip(), flags=re.IGNORECASE).strip(" -")
        if part and not NOT_SUBJECT.search(part) and not re.fullmatch(r"[\d\s.-]+", part):
            return part
    return " ".join(str(name or "").split())


def category_for(exam, board="", series="", program=""):
    """The checker's exam category for a test, from the sheet's exam type and board."""
    text = " ".join(str(value or "") for value in (exam, board, series, program)).lower()
    if "vidyapeeth" in text:
        return "vidyapeeth"
    if "commerce" in text:
        return "commerce"
    if re.search(r"\b(ca|cma|cs)\b|chartered|cost accountant|company secretar", text):
        return "ca"
    if re.search(r"school|board|cbse|icse|\bclass\s*\d|foundation", text):
        return "school"
    return ""


def class_for(rows):
    """The class and board text the checker reads ("Class 12th - Science - CBSE Board")."""
    for key in ("board_or_exam", "test_series_name"):
        values = [str(row.get(key) or "").strip() for row in rows if str(row.get(key) or "").strip()]
        if values:
            return Counter(values).most_common(1)[0][0]
    return ""


def total_for(rows):
    values = []
    for row in rows:
        try:
            value = float(str(row.get("total_marks") or "").strip())
        except ValueError:
            continue
        if value > 0:
            values.append(value)
    if not values:
        return ""
    value = Counter(values).most_common(1)[0][0]
    return str(int(value)) if value == int(value) else str(value)


def test_key(row):
    return "|".join(str(row.get(key) or "") for key in ("program_name", "test_series_name", "test_id", "test_name", "sheet_tab"))


def document_links(fetched):
    """Where the portal keeps a test's paper and Model Answer, shown with a marks problem. The
    student sheet has no paper link: the paper comes from the portal by the sheet's test id."""
    links = fetched.get("links") or {}
    return {"paper_link": links.get("question_paper") or "", "key_links": list(links.get("answer_key") or [])}


def pdf_page_count(data):
    """The pages of a PDF a person chose, or 0 when it is not one we can read (19 Nov 2026)."""
    try:
        import fitz
        with fitz.open(stream=data, filetype="pdf") as document:
            return 0 if document.needs_pass or not document.is_pdf else document.page_count
    except Exception:  # noqa: BLE001 - anything that is not a readable PDF
        return 0


def marks_read(check):
    """What the check read of the paper, to fill the marks form with (operator, 31 Oct 2026)."""
    return {"marks_items": check.get("items") or [], "marks_choices": check.get("operator_choices") or check.get("choice_groups") or []}


def tab_names(text):
    """The student tabs to work through, in order: "27 sept, 27 sept 5k-6k" (operator, 31 Oct 2026).
    The operator names them: a tab left out may be another computer's, and two computers on
    one tab would check the same copies twice."""
    names = []
    for name in str(text or "").replace("\n", ",").split(","):
        if name.strip() and name.strip() not in names:
            names.append(name.strip())
    return names


def rows_of_tabs(tabs):
    """[(tab, rows)] as one list in that order, each row marked with its tab. All are read at
    Check sheets, as the Sheets connection may have ended by the time a later tab is reached.
    A copy listed in two tabs is kept in the first, so it is never checked twice."""
    combined, seen = [], set()
    for tab, rows in tabs:
        for row in rows:
            identity = str(row.get("submission_id") or "").casefold()
            if identity not in seen:
                seen.add(identity)
                combined.append({**row, "sheet_tab": tab})
    return combined


# --------------------------------------------------------------------------- accounts

def account_pairs(values):
    """The accounts tab's rows as admin + evaluator pairs: columns named like admin,
    evaluator and password (in any order, any case). Raises ValueError naming what to fix."""
    rows = [[str(cell or "").strip() for cell in row] for row in values if any(str(cell or "").strip() for cell in row)]
    if not rows:
        raise ValueError("The accounts tab is empty")
    header = [cell.lower() for cell in rows[0]]
    def column(*words):
        return next((index for index, cell in enumerate(header) if any(word in cell for word in words)), None)
    admin, evaluator, password = column("admin"), column("evaluator", "checker"), column("password", "pass")
    # The reviewer approves this pair's copies at the end (operator, 12 Nov 2026). The column is
    # optional: without it a run behaves exactly as before and nothing is reviewed.
    reviewer = column("reviewer", "approver")
    if admin is None or evaluator is None:
        raise ValueError("The accounts tab needs an admin column and an evaluator column (and a password column)")
    pairs, seen, problems = [], set(), []
    for number, row in enumerate(rows[1:], 2):
        cell = lambda index: row[index] if index is not None and index < len(row) else ""
        pair = {"admin": cell(admin), "evaluator": cell(evaluator), "password": cell(password),
                "reviewer": cell(reviewer)}
        if not pair["admin"] and not pair["evaluator"]:
            continue
        for role in ("admin", "evaluator"):
            if not EMAIL.fullmatch(pair[role]):
                problems.append(f"row {number}: the {role} ID '{pair[role]}' is not an email")
        if pair["reviewer"] and not EMAIL.fullmatch(pair["reviewer"]):
            problems.append(f"row {number}: the reviewer ID '{pair['reviewer']}' is not an email")
        identity = (pair["admin"].lower(), pair["evaluator"].lower())
        if identity in seen:
            continue
        seen.add(identity)
        pairs.append(pair)
    admins = {pair["admin"].lower() for pair in pairs}
    both = sorted(admins & {pair["evaluator"].lower() for pair in pairs})
    if both:
        problems.append("an ID cannot be an admin in one pair and an evaluator in another: " + ", ".join(both))
    if problems:
        raise ValueError("Fix the accounts tab: " + "; ".join(problems))
    if not pairs:
        raise ValueError("The accounts tab has no admin + evaluator rows")
    return pairs


def tab_values(cache, owner, import_id, worksheet, access_token, google):
    """Every cell of one small tab (the accounts), as rows of text. `google` gives the
    workflow's Google helpers (descriptor reads need the Sheets token)."""
    with cache.lock:
        entry = cache.entries.get(import_id)
        if not entry or entry["owner"] != owner:
            raise LookupError("Sheet import not found or expired. Check the sheets again.")
        if worksheet not in entry["tabs"]:
            raise ValueError(f"The tab '{worksheet}' is not in the accounts sheet. Tabs: {', '.join(entry['tabs'])}")
        entry = dict(entry)
    if "descriptor" in entry:
        if not access_token:
            raise ValueError("Connect Google Sheets again before reading the accounts tab")
        descriptor = entry["descriptor"]
        tab = next(tab for tab in descriptor["tabs"] if tab["name"] == worksheet)
        quoted = "'" + worksheet.replace("'", "''") + "'"
        last = google["column_name"](max(0, min(int(tab.get("column_count") or 26), 26) - 1))
        data = google["api_json"](descriptor["document_id"], access_token,
                                  {"fields": "valueRanges(values)", "valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS",
                                   "ranges": [f"{quoted}!A1:{last}{min(int(tab.get('row_count') or 1000), 1000)}"]},
                                  google["max_bytes"], "/values:batchGet")
        ranges = data.get("valueRanges") or [{}]
        return ranges[0].get("values") or []
    data, filename = entry["data"], entry["filename"]
    snapshot = google["snapshot"](data, filename)
    if snapshot is not None:
        tab = next((tab for tab in snapshot if tab["name"] == worksheet), None)
        return list(tab["rows"]) if tab else []
    if filename.lower().endswith(".xlsx"):
        import openpyxl
        workbook = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        try:
            return [list(row) for row in workbook[worksheet].iter_rows(values_only=True)]
        finally:
            workbook.close()
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


# ------------------------------------------------------------------------------- plan

def build_plan(rows, pairs, saved_submissions=frozenset()):
    """Tests in sheet order with their inputs, and the pairs that will check them."""
    from integrated_workflow import nothing_left
    tests, order = {}, []
    for row in rows:
        key = test_key(row)
        if key not in tests:
            tests[key] = []
            order.append(key)
        tests[key].append(row)
    planned = []
    for key in order:
        test_rows = tests[key]
        first = test_rows[0]
        fresh = [row for row in test_rows if row["submission_id"].casefold() not in saved_submissions and not nothing_left(row)]
        test = {"key": key, "program_name": first["program_name"], "test_series_name": first["test_series_name"],
                "test_id": first["test_id"], "test_name": first["test_name"],
                "category": category_for(first.get("exam_kind"), first.get("board_or_exam"), first["test_series_name"], first["program_name"]),
                "student_class": class_for(test_rows), "subject": subject_from_test_name(first["test_name"]),
                "total_marks": total_for(test_rows), "copies": len(fresh), "already_in_runs": len(test_rows) - len(fresh),
                "tab": first.get("sheet_tab") or "",
                "status": "waiting", "detail": "", "run_id": "", "pair": None}
        test["problem"] = input_problem(test)
        if not fresh:
            test["status"], test["detail"] = "skipped", "Every copy of this test is completed or already in a saved run"
        planned.append(test)
    return planned, {test["key"]: [row for row in tests[test["key"]] if row["submission_id"].casefold() not in saved_submissions and not nothing_left(row)]
                     for test in planned}


def input_problem(test):
    if not test["category"]:
        return "the exam type is not one the checker knows (school, Vidyapeeth, commerce, CA)"
    if not test["total_marks"]:
        return "the sheet gives no total marks for this test"
    if not test["subject"] or not test["student_class"]:
        return "the subject or class could not be worked out"
    return ""


# ---------------------------------------------------------------------------- the pilot

class Autopilot:
    """Runs one autopilot per app: pairs take whole tests in sheet order until none is left."""

    def __init__(self, store, root):
        self.store, self.root = store, Path(root)
        self.lock = threading.RLock()
        self.plans = {}            # plan_id -> {"owner", "tests", "rows", "pairs", "expires"}
        self.state, self.rows = None, {}
        self.stop_event = threading.Event()
        self.threads = []
        self.resumed = set()
        self.clock = time.time
        self.run_poll = RUN_POLL_SECONDS
        self.sign_in_poll = SIGN_IN_POLL_SECONDS
        self.retry_seconds = RETRY_SECONDS
        self.review_poll = REVIEW_POLL_SECONDS
        self.marks_wait = MARKS_SERVICE_WAIT_SECONDS
        self.expired = None        # an app sign-in the server refused: wait for the next one

    # --------------------------------------------------------------------- files
    @property
    def state_path(self):
        return self.root / "autopilot.json"

    @property
    def rows_path(self):
        return self.root / "autopilot_rows.json"

    @property
    def keys_path(self):
        """Model Answers given by hand, kept beside the state so they survive a restart."""
        return self.root / "autopilot_keys"

    def save(self):
        with self.lock:                       # one writer at a time: pairs save from their own threads
            if self.state is None:
                return
            self.state["updated_at"] = datetime.now().isoformat(timespec="seconds")
            data = json.dumps(self.state, ensure_ascii=False, indent=1)
            self.root.mkdir(parents=True, exist_ok=True)
            temp = self.state_path.with_suffix(".tmp")
            for attempt in range(6):
                try:
                    temp.write_text(data, encoding="utf-8")
                    temp.replace(self.state_path)
                    return
                except PermissionError:
                    # A reader or virus scanner can hold the file for a moment, as with run.json.
                    # Left to rise this ended the pair's thread, and the pair stopped taking tests.
                    if attempt == 5:
                        raise
                    time.sleep(.02 * (2 ** attempt))

    def load(self):
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            rows = json.loads(self.rows_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, {}
        return state, rows

    # ---------------------------------------------------------------------- plan
    def saved_submissions(self):
        found = set()
        for path in self.store.root.glob("*/run.json"):
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
                idle = self.store.left_alone(run)
                found.update(row["submission_id"].casefold() for row in run["rows"]
                             if row.get("stage") != "superseded" and not retryable_saved_row(run, row, idle=idle))
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return found

    def plan(self, owner, rows, pairs, source_sheet=None, sources=None):
        """`sources`: {tab: its sheet}, so each test's statuses are saved in the tab it came from."""
        tests, rows_by_test = build_plan(rows, pairs, self.saved_submissions())
        for test in tests:
            test["source_sheet"] = (sources or {}).get(test["tab"]) or source_sheet
        plan_id = uuid.uuid4().hex
        with self.lock:
            now = self.clock()
            self.plans = {key: plan for key, plan in self.plans.items() if plan["expires"] > now}
            self.plans[plan_id] = {"owner": owner, "tests": tests, "rows": rows_by_test, "pairs": pairs,
                                   "source_sheet": source_sheet, "expires": now + PLAN_TTL_SECONDS}
        return {"plan_id": plan_id, **self.public_plan(tests, pairs)}

    @staticmethod
    def public_plan(tests, pairs):
        # The reviewer gets a window of its own like any other account, so it is counted here too
        # (25 Nov 2026): with a reviewer column the plan was understating the windows by a third,
        # and the operator reads that number to judge whether this computer has the memory.
        accounts = ({pair["admin"].lower() for pair in pairs} | {pair["evaluator"].lower() for pair in pairs}
                    | {pair["reviewer"].lower() for pair in pairs if pair.get("reviewer")})
        return {"tests": [{key: test[key] for key in ("key", "test_name", "program_name", "test_series_name", "category", "student_class",
                                                       "subject", "total_marks", "copies", "already_in_runs", "problem", "status", "detail", "tab")}
                          for test in tests],
                "pairs": [{"admin": pair["admin"], "evaluator": pair["evaluator"], "reviewer": pair.get("reviewer", ""),
                           "has_password": bool(pair.get("password"))} for pair in pairs],
                "windows": len(accounts), "copies": sum(test["copies"] for test in tests)}

    # --------------------------------------------------------------------- start
    def start(self, owner, plan_id, overrides=None, direct=False, copies_at_once=None):
        from integrated_workflow import MAX_GRADING_SLOTS
        if copies_at_once not in (None, ""):
            try:
                self.store.grading_slots.limit = max(1, min(MAX_GRADING_SLOTS, int(copies_at_once)))
            except (TypeError, ValueError):
                raise ValueError("Copies checked at the same time must be a number") from None
        with self.lock:
            if self.running():
                raise ValueError("The autopilot is already running. Stop it before starting another.")
            plan = self.plans.pop(plan_id, None)
            if plan is None or plan["owner"] != owner:
                raise LookupError("This plan has expired. Check the sheets again.")
        tests = plan["tests"]
        for test in tests:
            change = (overrides or {}).get(test["key"]) or {}
            for field in ("subject", "student_class", "total_marks", "category"):
                if field in change and str(change[field]).strip():
                    test[field] = str(change[field]).strip()
            test["problem"] = input_problem(test)
            if test["problem"] and test["status"] == "waiting":
                test["status"], test["detail"] = "skipped", f"Not started: {test['problem']}. Correct it on the plan, or run this test by hand."
        for pair in plan["pairs"]:
            if pair.get("password"):
                for account in (pair["admin"], pair["evaluator"], pair.get("reviewer", "")):
                    if account:
                        self.store.portal_logins.remember(account, pair["password"])
        state = {"id": uuid.uuid4().hex, "owner": owner, "status": "running", "created_at": datetime.now().isoformat(timespec="seconds"),
                 "pairs": [{"admin": pair["admin"], "evaluator": pair["evaluator"], "reviewer": pair.get("reviewer", ""),
                            "test": "", "state": "starting"} for pair in plan["pairs"]],
                 "tests": tests, "error": "", "source_sheet": plan.get("source_sheet"), "portal_direct": bool(direct)}
        self.root.mkdir(parents=True, exist_ok=True)
        self.rows_path.write_text(json.dumps(plan["rows"], ensure_ascii=False), encoding="utf-8")
        with self.lock:
            self.state, self.rows = state, plan["rows"]
        self.save()
        self.launch()
        return self.status(owner)

    def launch(self):
        self.stop_event = threading.Event()
        self.threads = [threading.Thread(target=self.pilot, args=(index,), name=f"autopilot-pair-{index}", daemon=True)
                        for index in range(len(self.state["pairs"]))]
        for thread in self.threads:
            thread.start()
        # Not one of self.threads: watch() joins those and then sweeps, and this one runs until the
        # autopilot ends, so joining it would wait for ever.
        threading.Thread(target=self.approve_finished, name="autopilot-review", daemon=True).start()
        threading.Thread(target=self.watch, name="autopilot-watch", daemon=True).start()

    def running(self):
        return any(thread.is_alive() for thread in self.threads)

    def resume(self, owner):
        """After the app restarted: an autopilot that was running continues (within a day)."""
        with self.lock:
            if owner in self.resumed or self.running():
                return
            self.resumed.add(owner)
        state, rows = self.load()
        if not state or state.get("owner") != owner or state.get("status") != "running":
            return
        try:
            if time.time() - self.state_path.stat().st_mtime > RESUME_WITHIN_SECONDS:
                return
        except OSError:
            return
        for test in state["tests"]:
            if test["status"] == "preparing":
                test["status"], test["detail"] = "waiting", ""
        with self.lock:
            self.state, self.rows = state, rows
        self.save()
        self.launch()

    def stop(self, owner):
        with self.lock:
            if self.state is None or self.state.get("owner") != owner:
                raise LookupError("No autopilot is running")
            self.stop_event.set()
            self.state["status"] = "stopped"
            running = {test["run_id"] for test in self.state["tests"] if test["status"] in ("running", "uploading") and test["run_id"]}
        # A Start replaces the list of tests, so the runs of earlier Autopilots are not in it (2 Nov 2026).
        for run_id in running | set(self.store.autopilot_runs()):
            self.store.stop_run(run_id)
        self.save()
        return self.status(owner)

    def status(self, owner):
        with self.lock:
            state = self.state
            if state is None:
                state, _ = self.load()
            if not state or state.get("owner") != owner:
                return {"status": "none"}
            counts = Counter(test["status"] for test in state["tests"])
            result = {"status": state["status"], "created_at": state["created_at"], "error": state.get("error", ""),
                      "pairs": [dict(pair) for pair in state["pairs"]],
                      "tests": [{key: test.get(key) for key in ("key", "test_name", "subject", "student_class", "category", "total_marks", "copies",
                                                                "status", "detail", "run_id", "pair", "tab", "paper_link", "key_links", "key_name", "marks_items", "marks_choices")}
                                for test in state["tests"]],
                      "counts": dict(counts), "review": dict(state.get("review") or {})}
        for test in result["tests"]:
            if test["status"] in ("running", "uploading") and test["run_id"]:
                test["progress"] = self.progress(test["run_id"])
        result["copy_totals"] = self.copy_totals(result["tests"])
        return result

    def paper_links(self, owner):
        """The links of marks problems saved before links were kept (operator, 31 Oct 2026). Read
        through the pair's own portal session: a second sign-in would end the one a run is using."""
        from integrated_workflow import DeskHandle
        with self.lock:
            state, rows = (self.state, self.rows) if self.state is not None else self.load()
            if not state or state.get("owner") != owner:
                raise LookupError("No autopilot has run yet")
            self.state, self.rows = state, rows
            tests = [test for test in state["tests"] if test["status"] == "marks_problem" and not test.get("paper_link")]
        found = {}
        for test in tests:
            if test["test_id"] not in found:
                pair = state["pairs"][test["pair"]]
                portal = DeskHandle(self.store.desks, threading.Event(), pair["evaluator"], admin=pair["admin"], direct=bool(state.get("portal_direct")))
                found[test["test_id"]] = document_links(portal.exam_documents({
                    "test_id": test["test_id"], "test_name": test["test_name"], "program_name": test["program_name"],
                    "test_series_name": test["test_series_name"], "evaluator_email": pair["evaluator"]}))
            self.test_state(test, **found[test["test_id"]])
        return self.status(owner)

    def retry_test(self, owner, key, total_marks=None, marks=None, entered_by=""):
        """A marks problem goes back in the queue, with the marks a person entered, or to have
        its marks read again (operator, 31 Oct 2026). Every tab's entry of that test goes with
        it: one paper, one correction. A finished autopilot's pairs start again for it."""
        from integrated_workflow import normalize_operator_marks
        marks = normalize_operator_marks(marks)
        if marks:
            try:
                total = float(total_marks)
            except (TypeError, ValueError):
                raise ValueError("Enter the official total for this test") from None
            if not total > 0:
                raise ValueError("Enter the official total for this test")
        with self.lock:
            state, rows = (self.state, self.rows) if self.state is not None else self.load()
            if not state or state.get("owner") != owner:
                raise LookupError("No autopilot has run yet")
            chosen = next((test for test in state["tests"] if test["key"] == key), None)
            if chosen is None:
                raise LookupError("This test is not in the autopilot")
            if chosen["status"] != "marks_problem":
                raise ValueError("This test no longer has a marks problem")
            if state["status"] == "stopped":
                raise ValueError("This autopilot was stopped. Check the sheets again and start it, then enter the marks there.")
            tests = [test for test in state["tests"] if test["test_id"] == chosen["test_id"] and test["status"] == "marks_problem"]
            for test in tests:
                test["status"], test["detail"] = "waiting", "Will run with the marks you entered" if marks else "Will read the paper's marks again"
                test.pop("operator_marks", None)
                test.pop("marks_by", None)
                if marks:
                    test["operator_marks"], test["marks_by"], test["total_marks"] = marks, entered_by, format(total, "g")
            self.state, self.rows = state, rows
            state["status"] = "running"
            restart = not self.running()
        self.save()
        if restart:
            self.launch()
        return self.status(owner)

    def given_answer_key(self, test):
        """The Model Answer a person gave for this test, or None when there is none to read."""
        path = test.get("operator_key")
        if not path:
            return None
        try:
            data = Path(path).read_bytes()
        except OSError:
            return None        # moved, or the data folder was cleaned: it is asked for again
        return data or None

    def save_answer_key(self, test_id, data):
        """One file per test and content, so two tabs of a test share it and a restart finds it."""
        folder = self.keys_path
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^A-Za-z0-9._-]", "_", str(test_id or "test"))[:60]
        path = folder / f"{stem}-{hashlib.sha1(data).hexdigest()[:8]}.pdf"
        path.write_bytes(data)
        return path

    def supply_answer_key(self, owner, key, data, filename="", entered_by=""):
        """The Model Answer for a test the portal has none for (operator, 19 Nov 2026). Every tab's
        entry of that test takes it: one paper, one Model Answer. A finished autopilot's pairs start
        again for it, as entered marks make them."""
        from integrated_workflow import MAX_BYTES
        data = bytes(data or b"")
        if not data:
            raise ValueError("Choose the Model Answer PDF for this test")
        if len(data) > MAX_BYTES:
            raise ValueError("The Model Answer is larger than 50 MB")
        pages = pdf_page_count(data)
        if not pages:
            raise ValueError("The Model Answer must be a readable PDF")
        with self.lock:
            state, rows = (self.state, self.rows) if self.state is not None else self.load()
            if not state or state.get("owner") != owner:
                raise LookupError("No autopilot has run yet")
            chosen = next((test for test in state["tests"] if test["key"] == key), None)
            if chosen is None:
                raise LookupError("This test is not in the autopilot")
            if chosen["status"] != "key_missing":
                raise ValueError("This test is not waiting for a Model Answer")
            if state["status"] == "stopped":
                raise ValueError("This autopilot was stopped. Check the sheets again and start it, then give the Model Answer there.")
            path = self.save_answer_key(chosen["test_id"], data)
            for test in state["tests"]:
                if test["test_id"] == chosen["test_id"] and test["status"] == "key_missing":
                    test["status"] = "waiting"
                    test["detail"] = f"Will run with the Model Answer you gave ({pages} pages)"
                    test["operator_key"], test["key_name"], test["key_by"] = str(path), str(filename or "")[:120], entered_by
            self.state, self.rows = state, rows
            state["status"] = "running"
            restart = not self.running()
        self.save()
        if restart:
            self.launch()
        return self.status(owner)

    def copy_totals(self, tests):
        """This autopilot's copies by outcome, for the page."""
        stages = Counter()
        for test in tests:
            if test.get("run_id"):
                try:
                    stages.update(row.get("stage") for row in self.store.read(test["run_id"]).get("rows") or [])
                except Exception:  # noqa: BLE001 - not saved yet
                    continue
        return {"copies": sum(int(test.get("copies") or 0) for test in tests), "uploaded": stages["uploaded"],
                "already_evaluated": stages["already_evaluated"], "rejected": stages["rejection_submitted"],
                "rejected_waiting": stages["rejected"], "checked_waiting": stages["checked"], "skipped": stages["skipped"]}

    def progress(self, run_id):
        """A running test's copies so far, for the page (build 140)."""
        try:
            run = self.store.read(run_id)
        except Exception:  # noqa: BLE001 - not saved yet
            return ""
        _, counts = self.store.run_summary(run)
        counts = counts.split(". Evaluator:")[0].rstrip(".")
        operations = [str(row.get("operation") or "") for row in run.get("rows") or [] if row.get("operation")]
        signing = sum(1 for operation in operations if operation.startswith("waiting for a portal login"))
        if operations:
            counts += f"; {len(operations)} in progress now"
        if signing:
            counts += f" ({signing} waiting for a portal sign-in)"
        return counts

    # ---------------------------------------------------------------- the pairs
    def authorization(self):
        authorization = self.store.latest_authorization.get(self.state["owner"])
        return None if authorization and authorization == self.expired else authorization

    def pair_state(self, index, **fields):
        with self.lock:
            self.state["pairs"][index].update(fields)
        self.save()

    def test_state(self, test, **fields):
        with self.lock:
            test.update(fields)
        self.save()

    def claim(self, index):
        """This pair's test: one it was already on (being prepared, or running after a restart),
        else the next waiting one in sheet order."""
        with self.lock:
            for test in self.state["tests"]:
                if test["status"] in ("preparing", "running") and test.get("pair") == index:
                    return test
            for test in self.state["tests"]:
                if test["status"] == "waiting":
                    test["status"], test["pair"], test["detail"] = "preparing", index, ""
                    return test
        return None

    def pilot(self, index):
        pair = self.state["pairs"][index]
        while not self.stop_event.is_set():
            self.settle_uploads()
            test = self.claim(index)
            if test is None:
                self.pair_state(index, test="", state="finished: no test left")
                return
            self.pair_state(index, test=test["test_name"], state="working")
            try:
                if test["status"] == "preparing":
                    self.prepare(index, pair, test)
                if test["status"] == "running":
                    self.follow(test)
            except Exception as exc:  # noqa: BLE001 - one test never stops the autopilot
                if self.stop_event.is_set():
                    return
                from integrated_workflow import PortalWait, internet_is_down
                reason = str(getattr(exc, "detail", None) or exc)
                if getattr(exc, "status_code", 0) in (401, 403):
                    self.expired = self.store.latest_authorization.get(self.state["owner"])
                    self.test_state(test, detail="Waiting for you to sign in to the app again")
                    continue                       # wait_for_sign_in() waits for the next sign-in
                if isinstance(exc, PortalWait):
                    self.test_state(test, detail=f"Waiting for the portal: {reason}")
                    self.stop_event.wait(self.retry_seconds)
                    continue                       # costs the test nothing, as for a run's copies
                if test["status"] == "preparing" and internet_is_down():
                    self.test_state(test, detail="Waiting for the internet")
                    self.stop_event.wait(self.retry_seconds)
                    continue
                attempts = int(test.get("attempts") or 0) + 1
                if attempts < PREPARE_ATTEMPTS and test["status"] == "preparing":
                    self.test_state(test, attempts=attempts, detail=f"Try {attempts} of {PREPARE_ATTEMPTS} failed ({reason}); trying again")
                    self.stop_event.wait(self.retry_seconds)
                    continue
                self.test_state(test, status="skipped", attempts=attempts, detail=f"Skipped: {reason}")

    def prepare(self, index, pair, test):
        """Fetch the test's paper and Model Answer, check the marks, start the run."""
        from integrated_workflow import MAX_COPIES_IN_FLIGHT, DeskHandle, prepare_exam_documents, validate_options
        store = self.store
        authorization = self.wait_for_sign_in()
        if authorization is None:
            return
        self.test_state(test, detail=f"Fetching the question paper and Model Answer through {pair['evaluator']}'s window")
        direct = bool(self.state.get("portal_direct"))
        share = -(-store.grading_slots.limit // max(1, len(self.state["pairs"])))   # each pair's share, rounded up
        portal = DeskHandle(store.desks, self.stop_event, pair["evaluator"], admin=pair["admin"], direct=direct)
        fetched = portal.exam_documents({"test_id": test["test_id"], "test_name": test["test_name"], "program_name": test["program_name"],
                                         "test_series_name": test["test_series_name"], "evaluator_email": pair["evaluator"]},
                                        progress=lambda text: self.test_state(test, detail=str(text)[:200]))
        documents = prepare_exam_documents(fetched)
        if "question_paper" not in documents:
            raise ValueError("the portal has no question paper for this test")
        # 19 Nov 2026: the portal has no Model Answer for some tests. Rather than skip such a
        # test, the autopilot waits for a person to give one on the page, as it waits for marks.
        given = self.given_answer_key(test)
        if given is None and "answer_key" not in documents:
            lost = bool(test.get("operator_key"))
            self.test_state(test, status="key_missing", operator_key="", key_name="", **document_links(fetched),
                            detail="Not started: the Model Answer given earlier cannot be read any more. Give it again below."
                            if lost else "Not started: the portal has no Model Answer for this test. Give the Model Answer below and it runs.")
            return
        options = validate_options({
            "admin_email": pair["admin"], "evaluator_email": pair["evaluator"],
            "deadline": (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
            "category": test["category"], "student_class": test["student_class"], "subject": test["subject"],
            "total_marks": test["total_marks"], "alert_email": store.owner_emails.get(self.state["owner"], ""),
            "submit_rejections": True, "review_min_confidence": 0, "portal_direct": direct, "operator_marks": test.get("operator_marks"),
            "copies_in_flight": max(1, min(MAX_COPIES_IN_FLIGHT, share + 2))})   # no slot idle while a pair changes test
        if options.get("operator_marks"):
            options["operator_marks"]["by"] = test.get("marks_by") or ""
        paper = documents["question_paper"]["data"]
        # A Model Answer given by hand comes first, exactly as an uploaded file does on the page.
        key = given if given is not None else documents["answer_key"]["data"]
        if store.paper_checker is not None:
            self.test_state(test, detail="Checking the question paper's marks")
            check = store.paper_checker(paper, options, authorization)
            # 30 Oct 2026: the proxy's storage filled up for some minutes and six tests lost
            # their chance. That is no fault of the paper, and nothing can be checked meanwhile.
            deadline = time.monotonic() + self.marks_wait
            while check.get("retry") and time.monotonic() < deadline and not self.stop_event.is_set():
                self.test_state(test, detail=f"Waiting for the AI service to read the marks. {check.get('message') or ''}"[:200])
                self.stop_event.wait(self.retry_seconds)
                check = store.paper_checker(paper, options, authorization)
            if self.stop_event.is_set():
                return
            if not check.get("ok"):
                self.test_state(test, status="marks_problem", **document_links(fetched), **marks_read(check),
                                detail=f"Not started: the paper's marks need fixing. {check.get('message') or ''} Start this test by hand with the marks entered.")
                return
            if check.get("note"):
                self.test_state(test, marks_note=check["note"])
        saved = self.saved_submissions()
        rows = [row for row in self.rows.get(test["key"], []) if row["submission_id"].casefold() not in saved]
        if not rows:
            self.test_state(test, status="skipped", detail="Every copy of this test is already in a saved run")
            return
        run = store.create(rows, options, self.state["owner"], paper, key, test.get("source_sheet") or self.state.get("source_sheet"))
        run["autopilot"] = self.state["id"]
        store.save(run)
        # Recorded before the start: a stop or restart in between leaves a run to continue, never a second one.
        self.test_state(test, status="running", run_id=run["id"], copies=len(rows),
                        detail=f"Running with admin {pair['admin']} and evaluator {pair['evaluator']}")
        if not self.stop_event.is_set():
            store.start(run["id"], authorization)

    def follow(self, test):
        """Wait until every copy of the test is checked, or its run has ended (it continues by itself
        after a restart). Once every copy is checked the pair takes its next test while this run's
        uploads go on in the background (operator's request, 29 Oct 2026, build 146): the portal
        being slow or saying "Unable to fetch task" never holds up the checking."""
        store = self.store
        while not self.stop_event.is_set():
            if test["run_id"] not in store.active:
                run = store.read(test["run_id"])
                if run.get("status") in ("running", "ready"):   # not yet started, or interrupted by a restart
                    authorization = self.wait_for_sign_in()
                    if authorization is None:
                        return
                    try:
                        store.start(test["run_id"], authorization)
                    except ValueError:
                        pass          # started meanwhile by the restart's own continuation, or its accounts are busy
                    self.stop_event.wait(self.run_poll)
                    continue
                self.finish(test, run)
                return
            if store.read(test["run_id"]).get("checking_done"):
                self.test_state(test, status="uploading",
                                detail="Every copy is checked; the uploads go on while this pair takes its next test")
                return
            self.stop_event.wait(self.run_poll)

    def finish(self, test, run):
        _, counts = self.store.run_summary(run)
        status = "done" if run.get("status") in ("complete", "needs_attention") else "stopped" if run.get("status") == "stopped" else "failed"
        self.test_state(test, status=status, detail=f"{run.get('status', '').replace('_', ' ')}: {counts}"
                        + (f" {run['error']}" if run.get("error") else "")
                        + (f" {test['marks_note']}" if test.get("marks_note") else ""))

    def settle_uploads(self):
        """Tests whose copies are all checked while their uploads go on: finished when their run has
        ended, started again with the latest sign-in after the app restarted. True while any is left."""
        store = self.store
        with self.lock:
            uploading = [test for test in self.state["tests"] if test["status"] == "uploading"]
        left = False
        for test in uploading:
            if test["run_id"] in store.active:
                left = True
                continue
            try:
                run = store.read(test["run_id"])
            except Exception:  # noqa: BLE001 - a run that cannot be read is left as it is
                continue
            if run.get("status") in ("running", "ready"):
                left = True
                authorization = self.authorization()
                if authorization:
                    try:
                        store.start(test["run_id"], authorization)
                    except ValueError:
                        pass
                continue
            self.finish(test, run)
        return left

    def wait_for_sign_in(self):
        """The owner's app sign-in; waits (with one alert) while there is none. None when stopped."""
        alerted = False
        while not self.stop_event.is_set():
            authorization = self.authorization()
            if authorization:
                return authorization
            if not alerted:
                alerted = True
                self.store.alerts.raise_alert(f"autopilot:{self.state['id']}:signin", "Autopilot waiting: sign in again",
                                              "Open the workflow page and sign in; the autopilot continues by itself.",
                                              to=self.recipients(), once_within=30 * 60)
            self.stop_event.wait(self.sign_in_poll)
        return None

    def uploaded_students(self, run_id):
        """The student ids of the copies this run put on the portal, and no others: another
        evaluator's work in the same program is left alone."""
        try:
            rows = self.store.read(run_id).get("rows", [])
        except Exception:  # noqa: BLE001 - a run saved by an older build, or removed
            return set()
        students = {str(row.get("student_id") or "").strip() for row in rows if row.get("stage") == "uploaded"}
        students.discard("")
        return students

    def uploaded_by_program(self):
        """Per pair: {program name: the student ids this run uploaded}. A test the per-test pass
        has already taken is left out, so no program is ever reviewed twice."""
        with self.lock:
            tests = [dict(test) for test in (self.state or {}).get("tests", [])]
        wanted = {}
        for test in tests:
            if not test.get("run_id") or not test.get("program_name") or test.get("reviewed"):
                continue
            students = self.uploaded_students(test["run_id"])
            if students:
                wanted.setdefault(test.get("pair", 0), {}).setdefault(test["program_name"], set()).update(students)
        return wanted

    def review_record(self):
        """The running tally the page shows, and the lock that keeps it to one review at a time.
        Seeded from the saved state, so after a restart this adds to what the earlier process
        approved instead of counting from zero again."""
        with self.lock:
            if not hasattr(self, "review_counts"):
                saved = (self.state or {}).get("review") or {}
                self.review_counts = Counter(saved.get("counts") or {})
                self.review_lines = list(saved.get("lines") or [])
                self.review_lock = threading.Lock()
            return self.review_counts, self.review_lines, self.review_lock

    def reviewer_for(self, pairs, test):
        """The reviewer account of the pair that took this test, or "" when it has none."""
        index = int(test.get("pair") or 0)
        return (pairs[index].get("reviewer") or "").strip() if 0 <= index < len(pairs) else ""

    def approve_finished(self):
        """Approve each test's copies as soon as THAT test's uploads have landed on the portal
        (operator, 25 Nov 2026). Before this the whole autopilot had to finish first, so a copy
        uploaded in the first hour waited for every other test to end before anyone approved it.

        On its own thread, because a pair must never wait for the portal (build 146), and one
        review at a time, so a reviewer's window is never asked for two programs at once.
        """
        while not self.stop_event.is_set():
            with self.lock:
                over = self.state is None or self.state["status"] in ("finished", "stopped")
            if over:
                return
            try:
                if self.review_round():
                    continue                 # straight back for the next finished test
            except Exception:  # noqa: BLE001 - this thread outlives any one failure
                pass
            self.stop_event.wait(self.review_poll)

    def review_round(self):
        """The next test that is done, has copies on the portal and has a reviewer. True when one
        was taken, so the caller comes straight back for the next."""
        with self.lock:
            if self.state is None or self.state["status"] != "running":
                return False
            pairs = [dict(pair) for pair in self.state["pairs"]]
            if not any(pair.get("reviewer") for pair in pairs):
                return False             # no reviewer column: nothing is ever reviewed
            test = next((test for test in self.state["tests"]
                         if test.get("status") == "done" and not test.get("reviewed")
                         and test.get("run_id") and test.get("program_name")
                         and self.reviewer_for(pairs, test)), None)
            if test is None:
                return False
            reviewer, program = self.reviewer_for(pairs, test), test["program_name"]
            label = test.get("test_name") or program
            # Taken before the portal is touched, so one test is never reviewed twice, and a
            # review that fails is reported rather than tried again against a changed list.
            test["reviewed"] = True
        self.save()
        students = self.uploaded_students(test["run_id"])
        if students:
            self.approve(reviewer, program, students, label)
        return True

    def approve(self, reviewer, program, students, label):
        """One program's copies, through that reviewer's own Chrome window."""
        counts, lines, gate = self.review_record()
        with gate:
            self.review_progress(counts, lines)
            try:
                outcome = self.store.desks.evaluator(reviewer).call(
                    self.stop_event, "review", program, students, lines.append)
                counts.update(outcome.get("counts") or {})
            except Exception as exc:  # noqa: BLE001 - one program never stops the others
                counts["failed"] += len(students)
                lines.append(f"{label}: review could not run ({exc})")
            self.review_progress(counts, lines)

    def review_uploaded(self):
        """The last sweep, once every test is done (operator, 12 Nov 2026; per test from 25 Nov).

        Each pair's reviewer account approves the copies that pair uploaded, program by program.
        approve_finished() has normally taken each test the moment its uploads landed, so what is
        left here is what that pass could not take: a test that was stopped or failed, one
        finished by an earlier run of the app, and the pairs that have no reviewer at all. Pairs
        without a reviewer column review nothing, which is how a run behaved before.
        """
        with self.lock:
            if self.state is None or self.state["status"] != "running":
                return
            pairs = [dict(pair) for pair in self.state["pairs"]]
            if not any(pair.get("reviewer") for pair in pairs):
                return
        counts, lines, gate = self.review_record()
        with gate:
            self.review_progress(counts, lines)
            wanted = self.uploaded_by_program()
            with self.lock:
                # This is the last pass, so everything it is about to take is marked before it
                # starts: the per-test thread can still be awake, and no copy is approved twice.
                for test in (self.state or {}).get("tests", []):
                    if test.get("run_id") and test.get("program_name"):
                        test["reviewed"] = True
            for index, pair in enumerate(pairs):
                reviewer = (pair.get("reviewer") or "").strip()
                for program, students in sorted((wanted.get(index) or {}).items()):
                    if self.stop_event.is_set():
                        break
                    if not reviewer:
                        counts["no reviewer"] += len(students)
                        lines.append(f"{program}: no reviewer for this pair, {len(students)} copies left pending")
                        continue
                    try:
                        outcome = self.store.desks.evaluator(reviewer).call(
                            self.stop_event, "review", program, students, lines.append)
                        counts.update(outcome.get("counts") or {})
                    except Exception as exc:  # noqa: BLE001 - one program never stops the others
                        counts["failed"] += len(students)
                        lines.append(f"{program}: review could not run ({exc})")
                    self.review_progress(counts, lines)
            with self.lock:
                if self.state is not None:
                    self.state["review"] = {"status": "finished", "counts": dict(counts), "lines": lines[-200:]}
            self.save()

    def review_progress(self, counts, lines):
        with self.lock:
            if self.state is not None:
                self.state["review"] = {"status": "running", "counts": dict(counts), "lines": lines[-200:]}
        self.save()

    def recipients(self):
        email = self.store.owner_emails.get(self.state["owner"])
        return [email] if email else []

    def watch(self):
        """When every pair has finished and the last uploads have ended: the autopilot's own end,
        with one alert."""
        for thread in list(self.threads):
            thread.join()
        while not self.stop_event.is_set() and self.settle_uploads():
            self.stop_event.wait(self.run_poll)
        self.review_uploaded()
        with self.lock:
            # A marks problem sent back meanwhile started the pairs again, and a newer watch.
            if self.state is None or self.state["status"] != "running" or self.running():
                return
            self.state["status"] = "finished"
            counts = Counter(test["status"] for test in self.state["tests"])
        self.save()
        body = ", ".join(f"{count} {name.replace('_', ' ')}" for name, count in sorted(counts.items()))
        try:
            self.store.alerts.raise_alert(f"autopilot:{self.state['id']}:end", "Autopilot finished",
                                          f"Every test has been taken: {body}.", to=self.recipients())
        except Exception:  # noqa: BLE001
            pass

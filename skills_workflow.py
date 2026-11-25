"""PW Skills copies: fetched from the admin portal, checked here, marks and feedback posted back.

The SMEs' recording of 4 Nov 2026 is the specification: pick a batch, Fetch assignments,
open a learner's row, download the file, type marks and feedback, Evaluate. The learner
then sees the marks, the feedback and a pass or fail the backend works out itself (60%).
"We don't need the checked file": only marks and feedback go to the portal.

Evaluate is final for the learner (their page says "no more submissions can be made"), so:
  * a run prepares marks and feedback and waits for a person unless it was started with
    automatic posting;
  * right before posting, the submission is read again; a file the learner has changed
    is checked again, and one a mentor has taken is left alone;
  * a post is written down as "posting" before it is sent, and a post whose answer was
    lost is settled by reading the portal, never by sending it twice.
"""
from __future__ import annotations

import base64
import concurrent.futures
import hashlib
import html
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

import categories
import integrated_workflow as workflow
import skills_portal as portal

CATEGORY = "innovation"
QUEUED, CHECKING, READY, POSTING, POSTED, HELD, TAKEN = (
    "queued", "checking", "ready", "posting", "posted", "held", "already_evaluated")
TO_CHECK = (QUEUED, CHECKING)
CHECK_ATTEMPTS = 2
SAVE_ATTEMPTS = 8
MOST_COPIES_AT_ONCE = 10
LOST_HEADING = "Where marks were lost:"
# The portal refuses a longer comment, counted as sent (its editor's HTML): "comment must not
# exceed 500 characters", first live run, 12 Nov 2026 (1,265 and 590 characters refused).
COMMENT_LIMIT = 500
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def scaled_marks(report, out_of):
    """The portal's marks for this report. A paper can print its own total (CSS Basics: 200)
    where the portal scores out of 100, so the paper's marks are scaled to the portal's."""
    total, maximum = float(report["total_score"]), float(report["max_total"])
    if maximum <= 0:
        raise ValueError("The checked copy has no total")
    return total if abs(maximum - float(out_of)) < 0.01 else round(total * float(out_of) / maximum, 2)


def _names(qids):
    return qids[0] if len(qids) == 1 else ", ".join(qids[:-1]) + " and " + qids[-1]


def _within(text, limit):
    """The text cut at a word so that, sent, it stays within the limit, ending a sentence."""
    words = text.split()
    while words and posted_length(" ".join(words).rstrip(",.") + ".") > limit:
        words.pop()
    return (" ".join(words).rstrip(",.") + ".") if words else ""


def feedback_text(report, out_of, limit=COMMENT_LIMIT):
    """What the learner reads as the Mentor Remark, within what the portal takes. The checked
    PDF does not reach them, so the questions that lost most are written out with their
    reason, the other questions that lost marks are listed with their marks in one line,
    and the overall remark opens it with as many of its sentences as there is room for."""
    sentences = [s for s in re.split(r"(?<=[.!?])\s+", workflow.portal_feedback(report).strip()) if s]
    lost = []      # in question order: (marks lost, order, qid, "5/6.5", reason)
    for order, question in enumerate(report.get("questions") or []):
        score, maximum = float(question["score"]), float(question["max_score"])
        if score < maximum - 1e-6:
            remark = " ".join(str(question.get("remark") or "").split())
            lost.append((maximum - score, order, str(question["qid"]), f"{score:g}/{maximum:g}", remark))
    note = ""
    if abs(float(report["max_total"]) - float(out_of)) >= 0.01:
        note = (f"Question marks are out of the paper's total of {float(report['max_total']):g}. "
                f"Your score here is out of {float(out_of):g}.")

    def text(opening, shown, with_marks=True):
        lines = [" ".join(opening)] + (["", note] if note else [])
        if lost:
            lines += ["", LOST_HEADING] + [shown[order] for order in sorted(shown)]
            rest = [f"{qid} ({marks})" if with_marks else qid
                    for _lost, order, qid, marks, _remark in lost if order not in shown]
            if rest:
                lines.append(f"Marks were also lost in {_names(rest)}." if shown else f"{_names(rest)}.")
        return "\n".join(lines)

    def fits(candidate):
        return posted_length(candidate) <= limit

    def best(opening, shown):
        with_marks = text(opening, shown)
        return with_marks if fits(with_marks) else text(opening, shown, with_marks=False)

    opening, shown = sentences[:1], {}
    for _lost, order, qid, marks, remark in sorted(lost, key=lambda item: (-item[0], item[1])):
        if not remark:
            continue                  # nothing to say about it beyond its marks
        first = re.split(r"(?<=[.!?])\s+", remark)[0]
        line = next((candidate for candidate in dict.fromkeys(f"{qid}: {marks}. {part}" for part in (remark, first))
                     if fits(best(opening, {**shown, order: candidate}))), None)
        if line is None:
            break
        shown[order] = line
    for sentence in sentences[1:]:
        if not fits(best(opening + [sentence], shown)):
            break
        opening = opening + [sentence]
    result = best(opening, shown)
    if not fits(result):
        result = best([_within(" ".join(opening), limit - posted_length(best([""], shown)))], shown)
    # Never reached with a usual report: a single paragraph cut to the limit if it is.
    return result if fits(result) else _within(" ".join(result.split()), limit)


def posted_length(text):
    """How many characters the portal counts for this feedback."""
    return len(comment_html(text))


def comment_html(text):
    """The feedback as the portal's editor stores it: paragraphs of escaped text."""
    parts = []
    for line in str(text or "").splitlines():
        line = line.strip()
        if line:
            body = html.escape(line, quote=False)
            parts.append(f"<p><strong>{body}</strong></p>" if line == LOST_HEADING else f"<p>{body}</p>")
    return "".join(parts)


BATCH_PAGE = 100
MOST_BATCHES = 500
MATCH_ORDER = ("exact", "starts", "contains", "other")


def _plain(text):
    return " ".join(str(text or "").casefold().split())


def batch_match(course, query):
    """How well a batch answers what was typed: "exact" when the batch code, the batch
    name, or "name | code" is what was typed (case and spacing aside), then "starts",
    "contains" (every word typed appears), else "other" (the portal matched it on
    something not shown)."""
    wanted = _plain(query)
    title, batch = _plain(course.get("title")), _plain(course.get("batch"))
    if not wanted:
        return "other"
    parts = [_plain(part) for part in str(query).split("|")]
    if wanted in (batch, title, f"{title} | {batch}") or (len(parts) == 2 and parts == [title, batch]):
        return "exact"
    if batch.startswith(wanted) or title.startswith(wanted):
        return "starts"
    words = f"{title} {batch}"
    if all(word in words for word in wanted.replace("|", " ").split()):
        return "contains"
    return "other"


def find_batches(client, query, most=MOST_BATCHES):
    """Every batch the portal finds for what was typed (paged, up to `most`), exact
    matches first. With many similar batches the one wanted was buried in a list of
    fifty in the portal's own order. "Name | code", as batches are written in sheets,
    is searched by its code, then by its name."""
    query = " ".join(str(query or "").split())
    if not query:
        return {"courses": [dict(course, match="") for course in client.courses("", limit=50)], "more": False, "exact": 0}
    terms = [part.strip() for part in query.split("|") if part.strip()][::-1] if "|" in query else [query]
    found, more = {}, False
    for term in terms:
        skip = 0
        while len(found) < most:
            page = client.courses(term, limit=BATCH_PAGE, skip=skip)
            new = [course for course in page if course["id"] not in found]
            for course in new:
                found[course["id"]] = course
            if len(page) < BATCH_PAGE or not new:
                break
            skip += BATCH_PAGE
        else:
            more = True
        if any(batch_match(course, query) == "exact" for course in found.values()):
            break
    ranked = sorted(({**course, "match": batch_match(course, query)} for course in found.values()),
                    key=lambda c: (MATCH_ORDER.index(c["match"]), _plain(c["title"]), _plain(c["batch"])))
    return {"courses": ranked[:most], "more": more or len(found) > most,
            "exact": sum(1 for course in ranked if course["match"] == "exact")}


def is_the_solution(submission, folder):
    """Whether the learner uploaded the assignment's own solution file: the same file, the
    same text, or a cover with the same links (a solution PDF whose workbook is behind
    "Click to open the Excel file"). Marked as it stood, it would score the solution."""
    import fitz
    from linked_work import links_in_pdf
    key = Path(folder) / "answer_key.pdf"
    if not key.exists():
        return False
    mine, theirs = Path(submission).read_bytes(), key.read_bytes()
    if mine == theirs:
        return True

    def text(data):
        try:
            with fitz.open(stream=data, filetype="pdf") as document:
                return " ".join(" ".join(page.get_text() for page in document).split()).casefold()
        except Exception:  # noqa: BLE001 - not a PDF: compared by bytes above
            return ""
    links = set(links_in_pdf(mine))
    if links and links == set(links_in_pdf(theirs)):
        return True
    own = text(mine)
    return len(own) >= 20 and own == text(theirs)


def drop_paper_as_key(folder):
    """A solution file that is the question paper again holds no answers: removed, so
    the checker writes its own key. Lessons a run prepared before build 168 kept it."""
    key, paper = Path(folder) / "answer_key.pdf", Path(folder) / "question_paper.pdf"
    if key.exists() and paper.exists() and key.stat().st_size == paper.stat().st_size \
            and key.read_bytes() == paper.read_bytes():
        key.unlink()
        return True
    return False


def paper_total(paper_bytes):
    """The total a paper prints for itself, or None when it does not print one."""
    import fitz
    from grader import paper_total_from_text
    try:
        with fitz.open(stream=paper_bytes, filetype="pdf") as document:
            return paper_total_from_text(" ".join(page.get_text() for page in document))
    except Exception:  # noqa: BLE001 - a paper with no readable text simply prints no total
        return None


def owner_for(token):
    payload = token.split(".")[1]
    identity = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))["sub"]
    return hashlib.sha256(identity.encode()).hexdigest()


class SkillsStore:
    """Runs on disk under root, one folder each; no token is ever written there."""

    def __init__(self, root, grade, *, sign_in=None, fetch=workflow.download_exam_file, paper_checker=None, pause=.5):
        self.root, self.grade, self.fetch = Path(root), grade, fetch
        self.root.mkdir(parents=True, exist_ok=True)
        self.sign_in = sign_in or portal.AdminSignIn(self.root / "admin_profile")
        self.paper_checker, self.pause = paper_checker, pause
        self.lock = threading.RLock()
        self.post_lock = threading.Lock()   # the portal gets one write at a time
        self.active, self.latest_authorization = {}, {}
        self.looked_for_saved_sign_in = False

    # Storage. Copies are changed from several threads, always under the lock.
    def folder(self, run_id):
        if not SAFE_ID.match(run_id):
            raise ValueError("Unknown run")
        return self.root / "runs" / run_id

    def read(self, run_id):
        with self.lock:
            return json.loads((self.folder(run_id) / "run.json").read_text(encoding="utf-8"))

    def save(self, run):
        with self.lock:
            run["updated_at"] = time.time()
            target = self.folder(run["id"]) / "run.json"
            temporary = target.with_suffix(".tmp")
            temporary.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
            for attempt in range(SAVE_ATTEMPTS):
                try:
                    os.replace(temporary, target)
                    break
                except PermissionError:   # Windows, while an indexer or antivirus has the file open
                    if attempt == SAVE_ATTEMPTS - 1:
                        raise
                    time.sleep(.1 * (attempt + 1))

    def change(self, run, copy, **fields):
        with self.lock:
            if "stage" in fields and fields["stage"] != copy.get("stage"):
                fields["stage_at"] = time.time()    # the page shows how long a copy has been in it
            copy.update(fields)
            self.save(run)

    def runs_of(self, owner):
        folder, found = self.root / "runs", []
        # Under the lock: Windows refuses to replace a file another thread is reading,
        # and that refusal stopped a run in the page test of 9 Nov 2026.
        with self.lock:
            for path in folder.glob("*/run.json") if folder.exists() else ():
                try:
                    run = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if run.get("owner") == owner:
                    found.append(run)
        return sorted(found, key=lambda run: run.get("created_at", 0), reverse=True)

    def create(self, owner, course, options):
        run_id = uuid.uuid4().hex
        self.folder(run_id).mkdir(parents=True)
        run = {"id": run_id, "owner": owner, "course": course, "options": options, "status": "created", "error": "",
               "lessons": {}, "copies": [], "created_at": time.time(), "updated_at": time.time(), "skipped_elsewhere": 0}
        self.save(run)
        return run

    @staticmethod
    def summary(run):
        counts = {}
        for copy in run["copies"]:
            counts[copy["stage"]] = counts.get(copy["stage"], 0) + 1
        return {key: run[key] for key in ("id", "course", "options", "status", "error", "created_at", "updated_at",
                                          "skipped_elsewhere")} | {"counts": counts, "copies": len(run["copies"])}

    def is_active(self, run_id):
        with self.lock:
            handle = self.active.get(run_id)
            return bool(handle and handle["thread"].is_alive())

    def in_other_runs(self, run):
        """Copies another run of this batch is still working on: checked once, not again."""
        taken = set()
        for other in self.runs_of(run["owner"]):
            if other["id"] != run["id"] and other["course"]["id"] == run["course"]["id"]:
                taken |= {copy["id"] for copy in other["copies"] if copy["stage"] in (*TO_CHECK, READY, POSTING)}
        return taken

    # Running.
    def start(self, run_id, authorization):
        with self.lock:
            if self.is_active(run_id):
                raise ValueError("This run is already going")
            run = self.read(run_id)
            run["status"], run["error"] = "running", ""
            self.save(run)
            stop = threading.Event()
            thread = threading.Thread(target=self.drive, args=(run, authorization, stop), daemon=True, name=f"skills-{run_id[:8]}")
            self.active[run_id] = {"thread": thread, "stop": stop}
            thread.start()

    def stop(self, run_id):
        with self.lock:
            handle = self.active.get(run_id)
        if handle:
            handle["stop"].set()

    def authorization(self, run, fallback):
        return self.latest_authorization.get(run["owner"]) or fallback

    def finish(self, run, status, error=""):
        with self.lock:
            run["status"], run["error"] = status, error
            self.save(run)
        self.write_report(run)

    def write_report(self, run):
        """report.xlsx next to the run, kept up to date each time it stops (skills_report.py).
        A report that cannot be written never fails the run."""
        import skills_report
        try:
            data = skills_report.xlsx(skills_report.build(run, self.folder(run["id"])))
            (self.folder(run["id"]) / "report.xlsx").write_bytes(data)
        except Exception as exc:  # noqa: BLE001 - e.g. the file is open in Excel
            print(f"[skills] report for run {run['id']} not written: {exc}", flush=True)

    def drive(self, run, authorization, stop):
        client = None
        try:
            client = self.sign_in.client()
            self.settle_posts(run, client)
            self.collect(run, client)
            self.prepare_lessons(run, client, authorization, stop)
            self.check_all(run, client, authorization, stop)
            status = "stopped" if stop.is_set() else "done"
            self.finish(run, status)
        except portal.SkillsSignInNeeded as exc:
            if client is not None:
                self.sign_in.ended(client.token)
            self.finish(run, "needs_sign_in", str(exc))
        except Exception as exc:  # noqa: BLE001 - the page shows it; Resume starts again from what is saved
            self.finish(run, "failed", " ".join(str(exc).split())[:400])

    def settle_posts(self, run, client):
        """A post that was on its way when the app stopped: the portal says what happened."""
        for copy in run["copies"]:
            if copy["stage"] == POSTING:
                self.settle(run, copy, client)
            elif copy["stage"] == CHECKING:
                self.change(run, copy, stage=QUEUED, operation="")

    def settle(self, run, copy, client):
        now = client.current(copy["course_id"], copy["lesson_id"], copy["email"])
        if now and now["id"] == copy["id"] and now["status"] == portal.EVALUATED:
            self.change(run, copy, stage=POSTED, posted_marks=now.get("received"), note="", operation="")
        else:
            self.change(run, copy, stage=READY, operation="",
                        note="The last post did not reach the portal. It has not been evaluated; post it again.")

    def collect(self, run, client):
        options = run["options"]
        rows = client.submissions(run["course"]["id"], titles=options.get("titles") or (), most=0,
                                  learners=options.get("learners") or ())
        known = {copy["id"] for copy in run["copies"]}
        elsewhere = self.in_other_runs(run)
        added = skipped = 0
        room = int(options.get("most_copies") or 0) - len(run["copies"]) if options.get("most_copies") else None
        with self.lock:
            # Oldest first: the list is newest first and the learners who waited longest go first.
            for row in reversed(rows):
                if row["id"] in known:
                    continue
                if row["id"] in elsewhere:
                    skipped += 1
                    continue
                if room is not None and added >= room:
                    break
                run["copies"].append({**row, "stage": QUEUED, "attempts": 0, "operation": "", "note": "",
                                      "marks": None, "feedback": "", "reasons": [], "stage_at": time.time()})
                added += 1
            run["skipped_elsewhere"] = skipped
            self.save(run)

    def prepare_lessons(self, run, client, authorization, stop):
        needed = {copy["lesson_id"]: copy for copy in run["copies"] if copy["stage"] in TO_CHECK}
        for lesson_id, copy in needed.items():
            if stop.is_set():
                return
            if run["lessons"].get(lesson_id, {}).get("ready"):
                continue
            lesson = self.prepare_lesson(run, client, lesson_id, copy, authorization)
            with self.lock:
                run["lessons"][lesson_id] = lesson
                self.save(run)

    def prepare_lesson(self, run, client, lesson_id, copy, authorization):
        folder = self.folder(run["id"]) / "lessons" / lesson_id
        folder.mkdir(parents=True, exist_ok=True)
        details = client.lesson(copy["course_id"], lesson_id)
        title = details["title"] or copy["assignment"]
        lesson = {"title": title, "ready": False, "problem": "", "has_solution": False,
                  "out_of": float(copy.get("out_of") or details.get("points") or 0)}
        if not lesson["out_of"]:
            return {**lesson, "problem": "The portal gives no maximum marks for this assignment."}
        if not details["paper"]:
            return {**lesson, "problem": "This assignment has no question paper file on the portal."}
        try:
            paper = workflow.readable_copy(self.fetch(details["paper"]))
        except (workflow.UnreadableCopy, ValueError, requests.RequestException) as exc:
            return {**lesson, "problem": f"The question paper could not be opened: {exc}"}
        (folder / "question_paper.pdf").write_bytes(paper)
        key = folder / "answer_key.pdf"
        key.unlink(missing_ok=True)
        if details["solution"] and details["solution"] != details["paper"]:
            try:
                solution = workflow.readable_copy(self.fetch(details["solution"]))
                # Two Data Analytics assignments (13 Nov 2026) carry the question paper
                # itself as their solution file, byte for byte. It holds no answers, so
                # the checker writes its own key, as for an assignment with no solution.
                if solution != paper:
                    key.write_bytes(solution)
                    lesson["has_solution"] = True
            except (workflow.UnreadableCopy, ValueError, requests.RequestException):
                pass     # the checker writes its own key, as for an assignment with no solution
        printed = paper_total(paper)
        lesson["total"] = float(printed) if printed else lesson["out_of"]
        if self.paper_checker is not None:
            verdict = self.paper_checker(paper, self.grading_options(run, lesson), self.authorization(run, authorization))
            if not verdict.get("ok"):
                return {**lesson, "problem": f"The paper's marks could not be read: {verdict.get('message') or 'no reason given'}"}
        return {**lesson, "ready": True}

    @staticmethod
    def grading_options(run, lesson, copies_to_check=1):
        return {"category": CATEGORY, "student_class": "", "subject": lesson["title"] or run["course"]["title"],
                "total_marks": f"{lesson['total']:g}", "operator_marks": None, "copies_to_check": copies_to_check}

    def check_all(self, run, client, authorization, stop):
        waiting = [copy for copy in run["copies"] if copy["stage"] in TO_CHECK]
        workers = max(1, min(int(run["options"].get("copies_at_once") or 3), MOST_COPIES_AT_ONCE))
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="skills-copy") as pool:
            futures = [pool.submit(self.check_one, run, copy, authorization, stop) for copy in waiting]
            for future in concurrent.futures.as_completed(futures):
                future.result()
        if run["options"].get("auto_post") and not stop.is_set():
            self.post_ready(run, client, [copy["id"] for copy in run["copies"] if copy["stage"] == READY], stop)

    def check_one(self, run, copy, authorization, stop):
        if stop.is_set():
            return
        lesson = run["lessons"].get(copy["lesson_id"]) or {}
        if not lesson.get("ready"):
            self.change(run, copy, stage=HELD, note=lesson.get("problem") or "The assignment could not be prepared.")
            return
        left = 1 + sum(1 for other in run["copies"] if other is not copy and other["lesson_id"] == copy["lesson_id"]
                       and other["stage"] in TO_CHECK)
        try:
            self.change(run, copy, stage=CHECKING, operation="downloading the copy", stage_at=time.time())
            source = self.download_copy(run, copy)
            pages = workflow.too_many_pages(source)
            if pages:
                raise workflow.UnreadableCopy(f"The file has {pages} pages, more than an answer sheet could run to.")
            self.change(run, copy, operation="checking")
            folder = self.folder(run["id"]) / "lessons" / copy["lesson_id"]
            drop_paper_as_key(folder)
            if is_the_solution(source, folder):
                raise workflow.UnreadableCopy(
                    "The learner uploaded the assignment's own solution file, not their work. "
                    "No marks were given. Ask the learner for their own submission.")
            outcome = self.grade(folder, source, self.grading_options(run, lesson, left), self.authorization(run, authorization),
                                 stop=stop, progress=lambda stage: self.change(run, copy, operation=str(stage)))
        except workflow.UnreadableCopy as exc:
            self.change(run, copy, stage=HELD, note=str(exc), operation="")
            return
        except Exception as exc:  # noqa: BLE001 - one copy's failure does not stop the others
            self.retry_or_hold(run, copy, " ".join(str(exc).split())[:300])
            return
        if outcome is None:      # stopped while waiting for a turn
            self.change(run, copy, stage=QUEUED, operation="")
            return
        self.apply_outcome(run, copy, lesson, outcome)

    def retry_or_hold(self, run, copy, reason):
        attempts = int(copy.get("attempts") or 0) + 1
        if attempts < CHECK_ATTEMPTS:
            self.change(run, copy, stage=QUEUED, attempts=attempts, operation="", note=f"Will be tried again: {reason}")
        else:
            self.change(run, copy, stage=HELD, attempts=attempts, operation="", note=f"The check did not finish: {reason}")

    def download_copy(self, run, copy):
        target = self.folder(run["id"]) / "copies" / copy["id"]
        target.mkdir(parents=True, exist_ok=True)
        if portal.is_learner_link(copy["file"]):
            # Read as a link-only submission: linked_work opens the learner's GitHub or Drive.
            source = target / "submission.url"
            source.write_text(f"[InternetShortcut]\nURL={copy['file']}\n", encoding="utf-8")
            return source
        if not copy["file"]:
            raise workflow.UnreadableCopy("The portal has no file for this submission.")
        source = target / "submission.pdf"
        source.write_bytes(workflow.readable_copy(self.fetch(portal.file_url(copy["file"]))))
        return source

    def apply_outcome(self, run, copy, lesson, outcome):
        target = self.folder(run["id"]) / "copies" / copy["id"]
        report = outcome.get("report")
        # Every School of Innovation copy carries the category's own "goes to faculty review".
        # A person is shown only what is in doubt about this copy.
        general = categories.get(CATEGORY).review.review_reason
        reasons = [str(reason) for reason in (outcome.get("review_reasons") or []) if str(reason).strip() and str(reason) != general]
        error = str(outcome.get("error") or "").strip()
        if outcome.get("rejected"):
            self.change(run, copy, stage=HELD, operation="", reasons=reasons,
                        note=f"Not posted: {error or 'this does not look like an answer to this assignment.'}")
            return
        if not report and outcome.get("failure_code") == "NOT_VERIFIED_ANSWER_SHEET":
            # About the submission itself (a cover whose work is elsewhere, a file too long to
            # be an answer): checking it again cannot change that, so a person has it now.
            self.change(run, copy, stage=HELD, operation="", reasons=reasons,
                        note=f"Not posted: {error or 'the submission could not be checked as it is.'}")
            return
        if outcome.get("failed") or not report:
            self.retry_or_hold(run, copy, error or "the checker returned no marks")
            return
        if outcome.get("pdf"):
            (target / "checked.pdf").write_bytes(outcome["pdf"])
        (target / "report.json").write_text(json.dumps(report), encoding="utf-8")
        out_of = lesson["out_of"]
        marks = scaled_marks(report, out_of)
        fields = {"marks": marks, "out_of": out_of, "paper_marks": float(report["total_score"]),
                  "paper_total": float(report["max_total"]), "feedback": feedback_text(report, out_of),
                  "reasons": reasons, "has_checked_pdf": bool(outcome.get("pdf")), "operation": "", "note": "",
                  "edited": False}
        if marks <= 0:
            # The portal's own form refuses 0 marks unless the copy is left "under evaluation".
            self.change(run, copy, stage=HELD, **{**fields, "note": "0 marks: the portal's form does not accept 0. A person should decide."})
            return
        self.change(run, copy, stage=READY, **fields)

    # Posting.
    def post_ready(self, run, client, ids, stop=None):
        wanted = set(ids)
        for copy in run["copies"]:
            if copy["id"] in wanted and copy["stage"] == READY and not (stop is not None and stop.is_set()):
                self.post_one(run, copy, client)
                time.sleep(self.pause)

    def post_one(self, run, copy, client):
        """Read it again, then Evaluate once. Returns the stage the copy ended in."""
        with self.post_lock:
            try:
                now = client.current(copy["course_id"], copy["lesson_id"], copy["email"])
            except portal.SkillsSignInNeeded:
                raise
            except portal.SkillsPortalError as exc:
                self.change(run, copy, note=f"Not posted, the portal could not be read: {exc}")
                return copy["stage"]
            if now is None:
                self.change(run, copy, stage=HELD, note="Not posted: the submission is no longer on the portal.")
                return HELD
            if now["status"] == portal.EVALUATED:
                self.change(run, copy, stage=TAKEN, posted_marks=now.get("received"), note="Already evaluated on the portal by someone else.")
                return TAKEN
            if now["status"] == portal.UNDER_EVALUATION:
                self.change(run, copy, stage=HELD, note="Not posted: a mentor has marked this copy under evaluation.")
                return HELD
            if now["id"] != copy["id"] or now["file"] != copy["file"]:
                # A learner may replace the file until it is evaluated: these marks are for the old one.
                self.change(run, copy, **{**now, "out_of": copy["out_of"]}, stage=QUEUED, marks=None, feedback="", attempts=0,
                            note="The learner changed the file after it was checked. Resume the run to check the new one.")
                return QUEUED
            marks, out_of = float(copy["marks"]), float(copy["out_of"])
            if not 0 < marks <= out_of:
                self.change(run, copy, stage=HELD, note=f"Not posted: {marks:g} is outside the portal's range of more than 0 up to {out_of:g}.")
                return HELD
            if posted_length(copy["feedback"]) > COMMENT_LIMIT:
                saved = self.folder(run["id"]) / "copies" / copy["id"] / "report.json"
                if copy.get("edited") or not saved.exists():
                    self.change(run, copy, note=f"Not posted: the feedback is {posted_length(copy['feedback'])} characters and "
                                                f"the portal takes at most {COMMENT_LIMIT}. Shorten it.")
                    return copy["stage"]
                # Checked before the limit was known (build 166): written again from the saved report.
                self.change(run, copy, feedback=feedback_text(json.loads(saved.read_text(encoding="utf-8")), out_of))
            self.change(run, copy, stage=POSTING, operation="posting")
            try:
                client.grade(copy["id"], copy["course_id"], copy["lesson_id"], marks, out_of, comment_html(copy["feedback"]))
            except portal.SkillsPortalBusy:
                self.settle(run, copy, client)     # the answer was lost: never sent twice
                return copy["stage"]
            except portal.SkillsSignInNeeded:
                self.change(run, copy, stage=READY, operation="", note="The admin sign-in ended before this was posted.")
                raise
            except portal.SkillsPortalError as exc:
                self.change(run, copy, stage=READY, operation="", note=f"The portal refused this post: {exc}")
                return READY
            self.change(run, copy, stage=POSTED, posted_marks=marks, operation="", note="")
            return POSTED

    def start_posting(self, run_id, ids):
        with self.lock:
            if self.is_active(run_id):
                raise ValueError("Wait until this run has finished checking, or stop it first")
            run = self.read(run_id)
            client = self.sign_in.client()
            stop = threading.Event()
            run["status"], run["error"] = "posting", ""
            self.save(run)

            def post():
                try:
                    self.post_ready(run, client, ids, stop)
                    self.finish(run, "stopped" if stop.is_set() else "done")
                except portal.SkillsSignInNeeded as exc:
                    self.sign_in.ended(client.token)
                    self.finish(run, "needs_sign_in", str(exc))
                except Exception as exc:  # noqa: BLE001 - shown on the page
                    self.finish(run, "failed", " ".join(str(exc).split())[:400])
            thread = threading.Thread(target=post, daemon=True, name=f"skills-post-{run_id[:8]}")
            self.active[run_id] = {"thread": thread, "stop": stop}
            thread.start()

    def edit(self, run_id, copy_id, marks, feedback):
        """A person's change to a checked copy before it is posted."""
        with self.lock:
            if self.is_active(run_id):
                raise ValueError("Wait until this run has stopped")
            run, copy = self.find(run_id, copy_id)
            if copy["stage"] not in (READY, HELD) or copy.get("marks") is None:
                raise ValueError("Only a checked copy that is not posted can be changed")
            value = float(marks)
            if not 0 < value <= float(copy["out_of"]):
                raise ValueError(f"Marks must be more than 0 and at most {float(copy['out_of']):g}")
            if not str(feedback or "").strip():
                raise ValueError("Write some feedback for the learner")
            length = posted_length(str(feedback))
            if length > COMMENT_LIMIT:
                raise ValueError(f"The feedback is {length} characters as the portal counts them. It takes at most {COMMENT_LIMIT}")
            self.change(run, copy, marks=value, feedback=str(feedback).strip(), stage=READY, edited=True,
                        note="Changed by a person.")
            return copy

    def check_again(self, run_id, copy_id):
        with self.lock:
            if self.is_active(run_id):
                raise ValueError("Wait until this run has stopped")
            run, copy = self.find(run_id, copy_id)
            if copy["stage"] in (POSTED, TAKEN, POSTING):
                raise ValueError("This copy has already been evaluated on the portal")
            self.change(run, copy, stage=QUEUED, attempts=0, marks=None, feedback="", reasons=[], note="")
            return copy

    def find(self, run_id, copy_id):
        run = self.read(run_id)
        for copy in run["copies"]:
            if copy["id"] == copy_id:
                return run, copy
        raise KeyError(copy_id)


def install(app, require_allowed, grade, root, paper_checker=None):
    store = SkillsStore(root, grade, paper_checker=paper_checker)
    router = APIRouter(prefix="/api/skills")

    async def access(request, authorization):
        if not request.client or request.client.host not in ("127.0.0.1", "::1", "localhost", "testclient"):
            raise HTTPException(403, "The PW Skills workflow runs only in the local desktop app")
        token = await run_in_threadpool(require_allowed, authorization)
        owner = owner_for(token)
        store.latest_authorization[owner] = authorization
        return owner

    def owned(run_id, owner):
        try:
            run = store.read(run_id)
        except (FileNotFoundError, ValueError):
            raise HTTPException(404, "Run not found")
        if run["owner"] != owner:
            raise HTTPException(404, "Run not found")
        return run

    def public(run):
        result = {key: value for key, value in run.items() if key != "owner"}
        result["comment_limit"] = COMMENT_LIMIT
        if result["status"] in ("running", "posting") and not store.is_active(run["id"]):
            result["status"] = "interrupted"
        return result

    def portal_call(function, *args, **kwargs):
        try:
            return function(*args, **kwargs)
        except portal.SkillsSignInNeeded as exc:
            raise HTTPException(409, str(exc))
        except portal.SkillsPortalError as exc:
            raise HTTPException(502, str(exc))

    @router.get("/sign-in")
    async def sign_in_status(request: Request, authorization: str | None = Header(None)):
        await access(request, authorization)
        if not store.looked_for_saved_sign_in:
            store.looked_for_saved_sign_in = True
            store.sign_in.start(headless=True)
        return store.sign_in.status()

    @router.post("/sign-in")
    async def sign_in_open(request: Request, authorization: str | None = Header(None)):
        await access(request, authorization)
        store.sign_in.start(headless=False)
        return store.sign_in.status()

    @router.get("/courses")
    async def courses(request: Request, q: str = "", authorization: str | None = Header(None)):
        await access(request, authorization)
        client = portal_call(store.sign_in.client)
        return await run_in_threadpool(portal_call, find_batches, client, q)

    @router.get("/courses/{course_id}/assignments")
    async def assignments(course_id: str, request: Request, authorization: str | None = Header(None)):
        await access(request, authorization)
        if not SAFE_ID.match(course_id):
            raise HTTPException(400, "Unknown batch")
        client = portal_call(store.sign_in.client)
        return {"titles": await run_in_threadpool(portal_call, client.assignment_titles, course_id)}

    @router.get("/runs")
    async def runs(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        return {"runs": [SkillsStore.summary(public({**run})) for run in await run_in_threadpool(store.runs_of, owner)]}

    @router.post("/runs")
    async def create_run(request: Request, authorization: str | None = Header(None)):
        owner = await access(request, authorization)
        payload = await request.json()
        course = payload.get("course") if isinstance(payload, dict) else None
        if not isinstance(course, dict) or not SAFE_ID.match(str(course.get("id") or "")):
            raise HTTPException(400, "Choose a batch first")
        try:
            learners = [part.strip() for part in re.split(r"[,;\n]", str(payload.get("learners") or "")) if part.strip()]
            if any("@" not in learner and len(portal.phone_digits(learner)) < 10 for learner in learners):
                raise ValueError("A learner's phone number needs 10 digits")
            options = {"titles": [str(title) for title in payload.get("titles") or []], "learners": learners,
                       "most_copies": max(0, int(payload.get("most_copies") or 0)),
                       "copies_at_once": max(1, min(int(payload.get("copies_at_once") or 3), MOST_COPIES_AT_ONCE)),
                       "auto_post": payload.get("auto_post") is True}
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc) if "phone" in str(exc) else "Check the numbers on the form")
        portal_call(store.sign_in.client)
        run = store.create(owner, {"id": str(course["id"]), "title": str(course.get("title") or "")}, options)
        store.start(run["id"], authorization)
        return public(store.read(run["id"]))

    @router.get("/runs/{run_id}")
    async def run_detail(run_id: str, request: Request, authorization: str | None = Header(None)):
        return public(owned(run_id, await access(request, authorization)))

    @router.post("/runs/{run_id}/resume")
    async def resume(run_id: str, request: Request, authorization: str | None = Header(None)):
        owned(run_id, await access(request, authorization))
        portal_call(store.sign_in.client)
        try:
            store.start(run_id, authorization)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return public(store.read(run_id))

    @router.post("/runs/{run_id}/stop")
    async def stop(run_id: str, request: Request, authorization: str | None = Header(None)):
        owned(run_id, await access(request, authorization))
        store.stop(run_id)
        return public(store.read(run_id))

    @router.post("/runs/{run_id}/post")
    async def post(run_id: str, request: Request, authorization: str | None = Header(None)):
        run = owned(run_id, await access(request, authorization))
        payload = await request.json()
        ready = {copy["id"] for copy in run["copies"] if copy["stage"] == READY}
        ids = [str(value) for value in (payload.get("ids") if isinstance(payload, dict) else None) or [] if str(value) in ready]
        if not ids:
            raise HTTPException(400, "No checked copy was chosen")
        try:
            portal_call(store.start_posting, run_id, ids)
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return public(store.read(run_id))

    @router.patch("/runs/{run_id}/copies/{copy_id}")
    async def edit(run_id: str, copy_id: str, request: Request, authorization: str | None = Header(None)):
        owned(run_id, await access(request, authorization))
        payload = await request.json()
        try:
            store.edit(run_id, copy_id, payload.get("marks"), payload.get("feedback"))
        except KeyError:
            raise HTTPException(404, "Copy not found")
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))
        return public(store.read(run_id))

    @router.post("/runs/{run_id}/copies/{copy_id}/check-again")
    async def check_again(run_id: str, copy_id: str, request: Request, authorization: str | None = Header(None)):
        owned(run_id, await access(request, authorization))
        try:
            store.check_again(run_id, copy_id)
        except KeyError:
            raise HTTPException(404, "Copy not found")
        except ValueError as exc:
            raise HTTPException(409, str(exc))
        return public(store.read(run_id))

    @router.get("/runs/{run_id}/report")
    async def run_report(run_id: str, request: Request, authorization: str | None = Header(None)):
        import skills_report
        run = owned(run_id, await access(request, authorization))
        built = await run_in_threadpool(skills_report.build, run, store.folder(run_id))
        return skills_report.summary(built)

    @router.get("/runs/{run_id}/report.xlsx")
    async def run_report_file(run_id: str, request: Request, authorization: str | None = Header(None)):
        import skills_report
        run = owned(run_id, await access(request, authorization))
        built = await run_in_threadpool(skills_report.build, run, store.folder(run_id))
        data = await run_in_threadpool(skills_report.xlsx, built)
        name = " ".join(re.sub(r"[^A-Za-z0-9 _.-]+", " ", run["course"].get("title", "")).split())[:60] or "run"
        stamp = time.strftime("%Y-%m-%d", time.localtime(run.get("created_at") or time.time()))
        return Response(data, headers={"Cache-Control": "no-store",
                                       "Content-Disposition": f'attachment; filename="PW Skills report {name} {stamp}.xlsx"'},
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    @router.get("/runs/{run_id}/copies/{copy_id}/checked.pdf")
    async def checked_pdf(run_id: str, copy_id: str, request: Request, authorization: str | None = Header(None)):
        owned(run_id, await access(request, authorization))
        if not SAFE_ID.match(copy_id):
            raise HTTPException(404, "Copy not found")
        path = store.folder(run_id) / "copies" / copy_id / "checked.pdf"
        if not path.exists():
            raise HTTPException(404, "No checked copy")
        return Response(path.read_bytes(), media_type="application/pdf", headers={"Cache-Control": "no-store"})

    app.include_router(router)
    return store

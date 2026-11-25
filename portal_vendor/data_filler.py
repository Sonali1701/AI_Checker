"""
# Vendored from Data_Filler v0.9.10 ("Paper Uploader"), file data_filler.py.
# Integrated-workflow guards added on top of the supplied source (keep them when
# a newer Data_Filler is vendored):
#   whole_label()            - whole-cell identity for test / category names
#   row_named()              - exact unique row only (waits for the list); never a similarly named test
#   verify_student_row()     - a stale search must not select another student: the ID in the
#                              row text/markup, or (live portal, which shows names not IDs) the
#                              search box still holding exactly this ID plus the sheet's name
#   StudentRecord.student_name - the sheet's name for that check
#   allot_student()          - calls verify_student_row before ticking the row; before_submit hook
#   find_evaluate_row()      - whole-label cell filters + verify_student_row
#   fill_evaluation_modal()/evaluate_student() - before_submit hook right before the Submit click
#   find_chrome()            - falls back to Microsoft Edge (part of Windows) when Chrome is absent

Evaluation Data Filler
For every student row in a CSV / Excel file chosen on this computer:
  1. Allotment  - assigns the student's test to an evaluator (with a deadline).
  2. Evaluation - uploads the checked paper, fills marks / ratings / comment and submits.

Setup:
    pip install -r requirements.txt
    (Google Chrome must be installed; no separate browser download is needed.)

No Google sign-in is needed: the data file and the checked PDFs are read from this computer.
The window app makes the data file itself from the checked PDFs (see build_marks.build_sheet).
The only login is the evaluation site itself, done by hand in the Chrome window.

First run: keep CONFIRM_FIRST_N >= 1 so you can check the screen before anything is submitted.

Sheet columns (one row per student per test):
    Program | Test_Series | Test_Name | Student_ID | Evaluator | Deadline |
    Q1 ... Qn | Total_Marks | Introduction_Competence | Content_Coverage | Conclusion_Competence |
    Presentation_Structure | Language_Proficiency | Comment
  - Q columns: fill only the questions on that paper (e.g. Q8, Q9, Q10); leave the rest empty.
  - Checked papers: PDFs in PAPERS_FOLDER, named with the Student ID (e.g. evaluated_Rahul_STU1001.pdf).
  - Evaluator: the evaluator's email, as shown in the site's Evaluator list.
  - Deadline: optional (e.g. 30/09/2026 18:00). Empty = DEFAULT_DEADLINE_DAYS from now.
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from playwright.sync_api import (
    Browser,
    BrowserContext,
    Error as PWError,
    Locator,
    Page,
    TimeoutError as PWTimeout,
    sync_playwright,
)

# ───────────────────────────── Configuration ─────────────────────────────

BASE_URL = "https://special-ops-content-frontend.penpencil.co/"
LOGGED_IN_URL_PART = "/tev/"        # every page after login has this in its address
# The site keeps its login token in localStorage and shows the dashboard whenever it is there, even after
# the server has cancelled it (e.g. the same account logged in on another browser). This harmless read-only
# request tells us whether the server still accepts the token.
SESSION_CHECK_URL = "https://special-ops-content-backend.penpencil.co/workflows/organizations?page=1&limit=none"
LOGIN_STORAGE_KEYS = ("access_token", "is_authenticated", "user-details", "has_name")

DATA_FILE = Path("students.xlsx")           # .csv or .xlsx; the first sheet of an Excel file is used
PROGRESS_LOG = Path("progress.csv")         # finished steps are skipped on the next run
REPORT_LOG = Path("report.csv")             # one line per student with the result of the last run
PAPERS_FOLDER: Path | None = None           # folder of checked PDFs; each file name contains the Student ID
USER_DATA_DIR = Path("./browser_profile")   # holds your site login; don't share this folder
ADMIN_USER_DATA_DIR = Path("./browser_profile_admin")   # the admin login used for assigning copies, kept apart

SCREENSHOT_DIR = Path("./screenshots")     # a picture of the screen is saved whenever a student fails

RUN_ALLOTMENT = False               # switched off until evaluation is proven on the real site
RUN_EVALUATION = True
CONFIRM_FIRST_N = 1                 # pause before Submit for the first N students of each step (0 = never)
VALIDATE_TOTAL = True               # reject rows where the question marks don't add up to Total_Marks
DEFAULT_DEADLINE_DAYS = 1
MAX_ALLOT_ROUNDS = 200              # safety stop for "assign this page, then the next" (one round = one Submit)

CHROME_PATHS = [
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google/Chrome/Application/chrome.exe",
]
EDGE_PATHS = [                      # integrated-workflow fallback: Edge ships with Windows and speaks the same protocol
    Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Microsoft/Edge/Application/msedge.exe",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/Edge/Application/msedge.exe",
]
CDP_PORT = 9222                     # local-only port used to connect to the Chrome window
LOGIN_MODE = "auto"                 # "auto": wait for dashboard URL | "enter": wait for Enter keypress

LOGIN_TIMEOUT_MS = 5 * 60 * 1000
DEFAULT_TIMEOUT_MS = 15_000
UPLOAD_TIMEOUT_MS = 60_000
FILE_CHOOSER_TIMEOUT_MS = 3_000
LIST_SETTLE_TIMEOUT_MS = 4_000
TOAST_CLEAR_TIMEOUT_MS = 10_000
CHROME_START_TIMEOUT_S = 30
HEADLESS = False                    # integrated workflow: True starts the window hidden (no window on screen)
HEADLESS_WINDOW_SIZE = "1920,1080"  # the page size a hidden window lays the portal out at
CHROME_CLOSE_TIMEOUT_S = 10
SLOW_MO_MS = 50

# Selectors taken from the screen recording; confirm with `playwright codegen` if a step fails.
SEL = {
    "search":           'input[placeholder*="Search" i]:visible',
    "data_rows":        "tbody tr",
    "loading":          '.MuiCircularProgress-root, [class*="spinner" i], [class*="loader" i]',
    "row_checkbox":     'input[type="checkbox"]',
    # Allotment → Students page
    "deadline":         'input[type="datetime-local"]',
    "allot_success":    "text=/allotted successfully/i",
    "select_all":       'thead input[type="checkbox"]',
    "next_page":        '[aria-label*="next" i], button:has-text("Next"), a:has-text("Next"), li.next > a',
    # Evaluation → Evaluate page
    "upload_success":   "text=/file uploaded/i",
    "file_input":       'input[type="file"]',
    "modal":            '[role="dialog"], .MuiDialog-paper, .modal-content, .ant-modal-content',
    "modal_title":      "Uploaded Pdf",
    "total_marks":      'input[placeholder*="Total marks" i]',
    "comment":          'textarea',
    "modal_close":      '[aria-label*="close" i], button:has-text("×"), button:has-text("✕")',
    "eval_success":     "text=/evaluat\\w*.{0,20}success|success\\w*.{0,20}evaluat/i",
}

SITE_LOGIN_EXPIRED = "Unable to fetch task"   # toast the site shows when the saved login was replaced

RATING_COLUMNS = {                  # sheet column → label in the "Rate Student" dropdown
    "Introduction_Competence": "Introduction Competence",
    "Content_Coverage":        "Content Coverage",
    "Conclusion_Competence":   "Conclusion Competence",
    "Presentation_Structure":  "Presentation & Structure",
    "Language_Proficiency":    "Language Proficiency",
}
REQUIRED_COLUMNS = ["Program", "Test_Name", "Student_ID", "Total_Marks"]
ALLOTMENT_COLUMNS = ["Test_Series", "Evaluator"]    # needed only when RUN_ALLOTMENT is on
Q_COL_RE = re.compile(r"^Q\.?(\d+)$", re.IGNORECASE)

ALLOTTED, EVALUATED = "allotted", "evaluated"

log = logging.getLogger("data_filler")


class AbortRun(Exception):
    """Stops the whole run (e.g. the browser window was closed)."""


class SkippedByUser(Exception):
    """User chose not to submit this student during manual review."""


# ───────────────────────────── Hooks ─────────────────────────────
# The window app (app.py) replaces these; the defaults use the terminal.

_RUNTIME = threading.local()


def runtime_setting(name: str):
    """This portal thread's override, or the standalone tool's normal setting."""
    settings = getattr(_RUNTIME, "settings", {})
    return settings[name] if name in settings else globals()[name]


@contextmanager
def runtime_context(**settings):
    """Scope an account's browser configuration and hooks to its desk thread.

    The integrated workflow runs several accounts at once, while the original
    tool configures module globals. Keep those defaults for standalone callers;
    nested scopes inherit the current account and restore it even after failure.
    """
    previous = getattr(_RUNTIME, "settings", {})
    _RUNTIME.settings = {**previous, **settings}
    try:
        yield
    finally:
        _RUNTIME.settings = previous


def ask_continue(message: str) -> None:
    """Block until the user has done something by hand. Raise AbortRun to stop instead."""
    callback = getattr(_RUNTIME, "settings", {}).get("ask_continue")
    if callback is not None:
        return callback(message)
    input(f">>> {message} Press Enter to continue...")


def ask_yes_no(message: str) -> bool:
    return input(f">>> {message} [Enter = yes, 'n' = no]: ").strip().lower() != "n"


def should_stop() -> bool:
    callback = getattr(_RUNTIME, "settings", {}).get("should_stop")
    return bool(callback()) if callback is not None else False


def on_progress(step: str, done: int, total: int) -> None:
    pass


def on_status(message: str) -> None:
    """One-line 'what is happening now' text, e.g. while waiting for the user to sign in."""


# ───────────────────────────── Sheet data ─────────────────────────────

@dataclass
class StudentRecord:
    program: str
    test_series: str
    test_name: str
    student_id: str
    evaluator: str
    deadline: str                                           # 'YYYY-MM-DDTHH:MM' for datetime-local
    total_marks: str
    comment: str
    marks: dict[int, str] = field(default_factory=dict)     # question number → mark
    ratings: dict[str, str] = field(default_factory=dict)   # dropdown label → score
    paper_path: Path | None = None                          # checked PDF found in PAPERS_FOLDER
    student_name: str = ""                                  # integrated workflow: the sheet's name, for identity checks

    @property
    def key(self) -> tuple[str, str, str]:
        # test_series (the batch) is part of the key: two batches can have a test with the same name
        return self.test_series, self.test_name, self.student_id


def normalize(value) -> str:
    """Strip whitespace and turn float artefacts like '12345.0' into '12345'."""
    text = str(value).strip()
    return re.sub(r"^(-?\d+)\.0+$", r"\1", text)


def read_data_file(path: Path) -> pd.DataFrame:
    """Read the students file (CSV or Excel) with every cell as text."""
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    kind = path.suffix.lower()
    if kind in (".xlsx", ".xlsm"):
        try:
            df = pd.read_excel(path, sheet_name=0, dtype=str, engine="openpyxl")
        except PermissionError:
            raise PermissionError(f"Can't read {path.name}. Close it in Excel and try again.") from None
        return df.fillna("")
    if kind == ".csv":
        for encoding in ("utf-8-sig", "cp1252"):   # Excel's "CSV" (not "CSV UTF-8") uses the Windows encoding
            try:
                return pd.read_csv(path, dtype=str, keep_default_na=False, encoding=encoding)
            except UnicodeDecodeError:
                continue
    if kind == ".xls":
        raise ValueError("Old .xls files are not supported. In Excel use File > Save As > Excel Workbook (.xlsx).")
    raise ValueError(f"Choose a .csv or .xlsx file (not {path.suffix or 'a file without extension'}).")


def load_data(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip().replace(" ", "_") for c in df.columns]

    required = REQUIRED_COLUMNS + (ALLOTMENT_COLUMNS if RUN_ALLOTMENT else [])
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in the first row of the file: {', '.join(missing)}")
    if not question_columns(df):
        raise ValueError("The file needs question columns named Q1, Q2, ...")

    df = df.map(normalize)
    return df[df["Student_ID"] != ""]


def question_columns(df: pd.DataFrame) -> dict[int, str]:
    return {int(m.group(1)): c for c in df.columns if (m := Q_COL_RE.match(c))}


def parse_deadline(raw: str) -> str:
    when = pd.to_datetime(raw, dayfirst=True) if raw else datetime.now() + timedelta(days=DEFAULT_DEADLINE_DAYS)
    return when.strftime("%Y-%m-%dT%H:%M")


def build_record(row: pd.Series, q_cols: dict[int, str]) -> StudentRecord:
    return StudentRecord(
        program=row["Program"],
        test_series=row.get("Test_Series", ""),
        test_name=row["Test_Name"],
        student_id=row["Student_ID"],
        evaluator=row.get("Evaluator", ""),
        deadline=parse_deadline(row.get("Deadline", "")),
        total_marks=row["Total_Marks"],
        comment=row.get("Comment", ""),
        marks={q: row[c] for q, c in sorted(q_cols.items()) if row[c] != ""},
        ratings={label: row[col] for col, label in RATING_COLUMNS.items() if row.get(col, "") != ""},
    )


def validate_record(rec: StudentRecord) -> None:
    """Catch bad sheet data before the browser touches anything."""
    needed = ["program", "test_name"] + (["test_series", "evaluator"] if RUN_ALLOTMENT else [])
    for name in needed:
        if not getattr(rec, name):
            raise ValueError(f"{name} is empty")
    if not rec.marks:
        raise ValueError("No question marks given")
    try:
        marks = [float(m) for m in rec.marks.values()]
        total = float(rec.total_marks)
        [float(r) for r in rec.ratings.values()]
    except ValueError as exc:
        raise ValueError(f"Non-numeric mark or rating: {exc}") from exc
    if VALIDATE_TOTAL and abs(sum(marks) - total) > 1e-6:
        raise ValueError(f"Question marks sum to {sum(marks):g} but Total_Marks is {total:g}")


# ───────────────────────────── Progress log ─────────────────────────────

PROGRESS_COLUMNS = ["Test_Series", "Test_Name", "Student_ID", "Stage", "At"]


def load_progress() -> dict[tuple[str, str, str], set[str]]:
    progress: dict[tuple[str, str, str], set[str]] = {}
    if PROGRESS_LOG.exists():
        done = pd.read_csv(PROGRESS_LOG, dtype=str, keep_default_na=False)
        if list(done.columns) != PROGRESS_COLUMNS:  # file from an older version: add the missing columns
            done = done.reindex(columns=PROGRESS_COLUMNS, fill_value="")
            done.to_csv(PROGRESS_LOG, index=False)
        for series, test, sid, stage in zip(done["Test_Series"], done["Test_Name"], done["Student_ID"],
                                            done["Stage"]):
            progress.setdefault((series, test, sid), set()).add(stage)
    return progress


def mark_stage(rec: StudentRecord, stage: str, progress: dict[tuple[str, str, str], set[str]]) -> None:
    """Append immediately so progress survives a crash mid-run."""
    progress.setdefault(rec.key, set()).add(stage)
    row = pd.DataFrame([{"Test_Series": rec.test_series, "Test_Name": rec.test_name,
                         "Student_ID": rec.student_id, "Stage": stage,
                         "At": datetime.now().isoformat(timespec="seconds")}], columns=PROGRESS_COLUMNS)
    row.to_csv(PROGRESS_LOG, mode="a", header=not PROGRESS_LOG.exists(), index=False)


# ───────────────────────────── Checked papers ─────────────────────────────

def list_pdfs(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


def id_in_name(student_id: str, file_stem: str) -> bool:
    """
    True if the file name ends with the ID, as in 'evaluated_<name>_<ID>'. Only the part after the last '_'
    counts, so digits in the name never match, and 'STU100' never matches 'STU1001'.
    A copy marker like ' (1)' is ignored, so duplicate copies are still noticed.
    """
    last_part = re.sub(r"\s*\(\d+\)$", "", file_stem).rsplit("_", 1)[-1].strip()
    return last_part.lower() == student_id.strip().lower()


def resolve_paper(rec: StudentRecord, pdfs: list[Path], repeated_ids: set[str]) -> None:
    """Find this student's checked PDF in the chosen folder."""
    matches = [p for p in pdfs if id_in_name(rec.student_id, p.stem)]
    if not matches:
        raise ValueError("No PDF with this Student ID in the folder")
    if len(matches) > 1:
        raise ValueError(f"{len(matches)} PDFs in the folder have this Student ID: "
                         + ", ".join(p.name for p in matches))
    if rec.student_id in repeated_ids:
        raise ValueError("This Student ID is in the file for more than one test, so the folder PDF can't be "
                         "matched to the right test. Use a separate file and folder for each test.")
    rec.paper_path = matches[0]


# ───────────────────────────── Chrome ─────────────────────────────

def find_chrome() -> Path:
    """Google Chrome if installed, otherwise Microsoft Edge (verified over CDP: same flags, same protocol)."""
    for path in CHROME_PATHS + EDGE_PATHS:
        if path.is_file():
            return path
    raise FileNotFoundError("Neither Google Chrome nor Microsoft Edge is installed. "
                            "Please install Chrome from google.com/chrome.")


def hidden_window_args(browser: Path) -> list[str]:
    """Chrome's headless mode with the page size and browser name of a normal window: a hidden
    window would otherwise call itself "HeadlessChrome" to the site (integrated workflow)."""
    args = ["--headless=new", f"--window-size={HEADLESS_WINDOW_SIZE}"]
    try:
        versions = [p.name for p in browser.parent.iterdir() if re.fullmatch(r"\d+(\.\d+){3}", p.name)]
    except OSError:
        versions = []
    if versions:
        major = max(int(v.split(".")[0]) for v in versions)
        agent = (f"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                 f"Chrome/{major}.0.0.0 Safari/537.36")
        if browser.name.lower() == "msedge.exe":
            agent += f" Edg/{major}.0.0.0"
        args.append(f"--user-agent={agent}")
    return args


def chrome_is_listening() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{runtime_setting('CDP_PORT')}/json/version", timeout=1):
            return True
    except OSError:
        return False


def start_chrome() -> subprocess.Popen | None:
    """
    Start Chrome as a normal program (not an automation-launched browser) so Google allows signing in,
    then let Playwright attach to it. Returns None if our Chrome window is already open from before.
    """
    if chrome_is_listening():
        log.info("Using the Chrome window that is already open.")
        return None

    profile = runtime_setting("USER_DATA_DIR")
    profile.mkdir(parents=True, exist_ok=True)
    browser = find_chrome()
    proc = subprocess.Popen([
        str(browser),
        f"--remote-debugging-port={runtime_setting('CDP_PORT')}",
        f"--user-data-dir={profile.resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
        *(hidden_window_args(browser) if runtime_setting("HEADLESS") else ["--start-maximized"]),
        "about:blank",
    ])
    for _ in range(CHROME_START_TIMEOUT_S * 2):
        if chrome_is_listening():
            return proc
        try:
            proc.wait(timeout=0.5)  # returns early only if Chrome exited
            raise RuntimeError("Chrome closed right after starting. Close any other window using "
                               f"'{profile}' and try again.")
        except subprocess.TimeoutExpired:
            pass
    proc.terminate()
    raise RuntimeError("Chrome did not start in time.")


def close_chrome(browser: Browser, chrome: subprocess.Popen, page: Page) -> None:
    """Ask Chrome to quit cleanly (keeps the logins saved). Must never stop the run from finishing."""
    if chrome.poll() is not None or page.is_closed() or not browser.is_connected():
        return  # the user closed the window: Chrome is gone or shutting down by itself
    # If Chrome dies before replying, the request below would wait forever; ending the process unblocks it.
    watchdog = threading.Timer(CHROME_CLOSE_TIMEOUT_S, chrome.terminate)
    watchdog.start()
    try:
        browser.new_browser_cdp_session().send("Browser.close")
    except PWError:
        pass
    finally:
        watchdog.cancel()


# ───────────────────────────── Page helpers ─────────────────────────────

def exact(text: str) -> re.Pattern:
    return re.compile(rf"^\s*{re.escape(text)}\s*$", re.IGNORECASE)


def loose(text: str) -> re.Pattern:
    """Contains `text`, ignoring case and spaces/hyphens between words ("Test - 05" matches "Test 05")."""
    words = [w for w in re.split(r"[\s\-]+", text.strip()) if w]
    return re.compile(r"[\s\-]*".join(map(re.escape, words)), re.IGNORECASE)


def whole_label(text: str) -> re.Pattern:
    """Whole cell identity, allowing only spacing/hyphen variations."""
    return re.compile(r"^\s*" + loose(text).pattern + r"\s*$", re.IGNORECASE)


def fill_field(locator: Locator, value: str) -> None:
    """Fill then blur, so forms that validate/recalculate on blur see the value."""
    locator.fill(value)
    locator.blur()


def click_if_visible(scope: Page | Locator, selector: str, timeout: int = 3_000) -> bool:
    loc = scope.locator(selector).first
    try:
        loc.wait_for(state="visible", timeout=timeout)
    except PWTimeout:
        return False
    loc.click(timeout=timeout)
    return True


def wait_list_settled(page: Page) -> None:
    """Wait for the table to finish reloading after a search / navigation."""
    try:
        page.locator(SEL["loading"]).first.wait_for(state="hidden", timeout=LIST_SETTLE_TIMEOUT_MS)
        page.wait_for_load_state("networkidle", timeout=LIST_SETTLE_TIMEOUT_MS)
    except PWTimeout:
        pass  # some pages keep polling; the row checks after this still guard correctness


_ROWS_TEXT_JS = "sel => [...document.querySelectorAll(sel)].map(r => r.innerText).join('\\n')"

# True once the table text differs from `before` and has not changed for 500 ms.
_ROWS_CHANGED_AND_STABLE_JS = """([sel, before]) => {
    const text = [...document.querySelectorAll(sel)].map(r => r.innerText).join('\\n');
    if (window.__rowsText !== text) { window.__rowsText = text; window.__rowsChangedAt = Date.now(); }
    return text !== before && Date.now() - window.__rowsChangedAt > 500;
}"""


def search_box(page: Page) -> Locator:
    """The Search box that is on screen. The Evaluate page also has one in the Filters side panel,
    which is parked off-screen but still counts as 'visible'; typing there does nothing."""
    boxes = page.locator(SEL["search"])
    boxes.first.wait_for(state="visible")
    width = page.evaluate("() => innerWidth")
    for i in range(boxes.count()):
        bb = boxes.nth(i).bounding_box()
        if bb and 0 <= bb["x"] < width:
            return boxes.nth(i)
    return boxes.first


def search(page: Page, value: str) -> None:
    """Type into the list's search box and wait until the table shows the NEW results."""
    box = search_box(page)
    before = page.evaluate(_ROWS_TEXT_JS, SEL["data_rows"])
    box.fill(value)
    box.press("Enter")
    try:
        page.wait_for_function(_ROWS_CHANGED_AND_STABLE_JS, arg=[SEL["data_rows"], before],
                               timeout=LIST_SETTLE_TIMEOUT_MS, polling=100)
    except PWTimeout:
        pass  # table unchanged: the new results are identical to the old ones
    wait_list_settled(page)


def row_with_cell(page: Page, text: str) -> Locator:
    """The table row that has a cell whose whole text equals `text`."""
    return page.locator(SEL["data_rows"]).filter(has=page.locator("td", has_text=exact(text))).first


def row_named(page: Page, text: str) -> Locator:
    """Resolve a unique complete name; never open a similarly named test.
    Waits for the list to show it: the site renders search results a moment later."""
    rows = page.locator(SEL["data_rows"]).filter(has=page.locator("td", has_text=whole_label(text)))
    give_up_at = time.monotonic() + DEFAULT_TIMEOUT_MS / 1000
    while True:
        count = rows.count()
        if count == 1:
            return rows.first
        if count > 1:
            raise LookupError(f"Expected one exact row for '{text}', found {count}")
        if time.monotonic() >= give_up_at:
            raise LookupError(f"Expected one exact row for '{text}', found 0")
        page.wait_for_timeout(250)


def open_row(row: Locator, name: str = "") -> None:
    """Click the arrow / ⋮ button in the row's last cell. `name` is what we searched for, for the error."""
    try:
        row.wait_for(state="visible")
    except PWTimeout:
        if not name:
            raise
        raise LookupError(f"'{name}' was not found in the list on the site (check the spelling in the sheet)") from None
    cell = row.locator("td").last
    # Programs list: <i class="fa-arrow-right cursor-pointer">; Evaluate list: <button>⋮</button>
    target = cell.locator("button, a, [role='button'], .cursor-pointer, i, svg").first
    (target if target.count() else cell).click()


def wait_heading(page: Page, text: str) -> None:
    page.get_by_text(text, exact=True).first.wait_for(state="visible")


def confirm_submit(what: str) -> None:
    if not ask_yes_no(f"Please check the Chrome window ({what}).\n\nSubmit now?\n(No = skip this student)"):
        raise SkippedByUser("Skipped during manual review")


def close_extra_pages(context: BrowserContext, main_page: Page) -> None:
    for pg in context.pages:
        if pg is not main_page and not pg.is_closed():
            pg.close()


def reset_page(page: Page, context: BrowserContext) -> None:
    """Return to a clean state after each student; never raises."""
    try:
        close_extra_pages(context, page)
        page.bring_to_front()
        page.keyboard.press("Escape")  # dismiss any open menu / pop-up
        modal = page.locator(SEL["modal"]).filter(has_text=SEL["modal_title"]).last
        if modal.is_visible():  # marks pop-up left open by a failure or a skipped student
            click_if_visible(modal, SEL["modal_close"], timeout=1_000)
        if page.locator(SEL["search"]).first.is_visible():
            search_box(page).fill("")
    except PWError as exc:
        log.warning("Could not reset the page: %s", exc)


# ───────────────────────────── Login & navigation ─────────────────────────────

def select_test_evaluation(page: Page) -> bool:
    """Pass the portal's optional collection chooser, selecting only Test Evaluation.

    The chooser can appear both for saved sessions and after a fresh login. Keep
    this in the shared login check so every admin/evaluator login path handles it,
    including a hidden window waiting for someone to sign in.
    """
    portal = urllib.parse.urlsplit(BASE_URL)

    def on_collection_chooser():
        current = urllib.parse.urlsplit(page.url)
        return ((current.scheme, current.netloc) == (portal.scheme, portal.netloc)
                and current.path.rstrip("/") == "/collections")

    if not on_collection_chooser():
        return False
    choices = page.get_by_text("Test Evaluation", exact=True)
    give_up_at = time.monotonic() + DEFAULT_TIMEOUT_MS / 1000
    while True:
        if should_stop():
            raise AbortRun("Stopped by you")
        if not on_collection_chooser():
            # An expired session may redirect to Login while the chooser loads;
            # a restored session may go straight to its dashboard instead.
            return False
        visible = [choices.nth(index) for index in range(choices.count())
                   if choices.nth(index).is_visible()]
        if len(visible) > 1:
            raise LookupError("The portal shows more than one Test Evaluation option")
        if visible:
            visible[0].click()
            # A click alone is not a successful login: require the evaluation
            # route and its own menu before continuing with any student.
            page.wait_for_url(re.compile(r"^" + re.escape(BASE_URL) + r"tev/"),
                              timeout=DEFAULT_TIMEOUT_MS)
            page.get_by_text("Task Management", exact=True).first.wait_for(
                state="visible", timeout=DEFAULT_TIMEOUT_MS)
            log.info("Selected Test Evaluation on the portal's collection chooser.")
            return True
        if time.monotonic() >= give_up_at:
            raise LookupError("The portal's collection chooser has no visible Test Evaluation option")
        page.wait_for_timeout(250)


def site_logged_in(page: Page) -> bool:
    select_test_evaluation(page)
    return LOGGED_IN_URL_PART in page.url and page.get_by_text("Task Management", exact=True).first.is_visible()


def server_accepts_login(page: Page) -> bool:
    """False only when the server clearly rejects the saved token; network trouble counts as accepted."""
    token = page.evaluate("() => localStorage.getItem('access_token')")
    if not token:
        return True  # nothing to check (the site would show its login page by itself)
    try:
        resp = page.request.get(SESSION_CHECK_URL, headers={"authorization": token}, timeout=DEFAULT_TIMEOUT_MS)
    except PWError as exc:
        log.warning("Could not check the saved login (%s); continuing.", first_line(exc))
        return True
    if resp.ok:
        return True
    log.info("The server rejected the saved login (HTTP %s).", resp.status)
    return False


def forget_site_login(page: Page) -> None:
    """Remove the cancelled token so the site shows its login page again."""
    page.evaluate("keys => keys.forEach(k => localStorage.removeItem(k))", list(LOGIN_STORAGE_KEYS))
    page.goto(BASE_URL, wait_until="domcontentloaded")


def wait_for_manual_login(page: Page) -> None:
    page.goto(BASE_URL, wait_until="domcontentloaded")
    select_test_evaluation(page)
    try:
        page.get_by_text("Task Management", exact=True).first.wait_for(state="visible", timeout=5_000)
    except PWTimeout:
        pass  # login page
    if site_logged_in(page) and not server_accepts_login(page):
        log.warning("Your saved login has expired (the same account was probably used in another browser). "
                    "Please log in again in the Chrome window.")
        forget_site_login(page)

    if LOGIN_MODE == "enter":
        ask_continue("Log in to the evaluation site in the Chrome window.")
        return

    if not site_logged_in(page):
        on_status("Waiting for you to log in to the evaluation site in the Chrome window...")
        log.info("Please log in to the evaluation site in the Chrome window (waiting up to %d min)...",
                 LOGIN_TIMEOUT_MS // 60_000)
    give_up_at = time.monotonic() + LOGIN_TIMEOUT_MS / 1000
    while not site_logged_in(page):
        if should_stop():
            raise AbortRun("Stopped by you")
        if time.monotonic() > give_up_at:
            ask_continue("Site login was not detected.\n\n"
                         "Finish logging in to the evaluation site in the Chrome window.")
            return
        page.wait_for_timeout(1_000)  # polling for a manual action; nothing to wait on in the page
    log.info("Logged in to the evaluation site.")


def open_program(page: Page, section: str, program: str, relogin_allowed: bool = True) -> None:
    """Task Management → Allotment/Evaluation → open the program from the Programs list."""
    select_test_evaluation(page)
    section_link = page.get_by_text(section, exact=True).first
    if not section_link.is_visible():  # clicking Task Management again would collapse the open menu
        page.get_by_text("Task Management", exact=True).first.click()
    section_link.click()
    wait_heading(page, "Programs")
    search(page, program)
    # Old toasts can remain visible after a successful retry. A visible exact
    # program row is usable; never force another login because of an old toast.
    if page.get_by_text(SITE_LOGIN_EXPIRED, exact=False).first.is_visible() and not row_with_cell(page, program).is_visible():
        if not relogin_allowed or server_accepts_login(page):
            raise AbortRun("The site says 'Unable to fetch task'. The site may be having problems; try again later. "
                           "If it keeps happening, log out in the Chrome window (top right) and log in again.")
        log.warning("The site cancelled this login during the run (the same account was probably used in "
                    "another browser). Please log in again in the Chrome window.")
        forget_site_login(page)
        wait_for_manual_login(page)
        open_program(page, section, program, relogin_allowed=False)
        return
    open_row(row_with_cell(page, program), program)


def open_test_students(page: Page, program: str, test_series: str, test_name: str) -> None:
    """… → Test Series tab → series → test → Students page."""
    open_program(page, "Allotment", program)
    page.get_by_text("Test Series", exact=True).first.click()
    search(page, test_series)
    open_row(row_named(page, test_series), test_series)
    wait_heading(page, "Test")
    search(page, test_name)
    open_row(row_named(page, test_name), test_name)
    wait_heading(page, "Students")
    log.info("Opened allotment for %s", test_name)


def open_allotment_test(page: Page, rec: StudentRecord) -> None:
    open_test_students(page, rec.program, rec.test_series, rec.test_name)


def open_evaluate(page: Page, program: str) -> None:
    open_program(page, "Evaluation", program)
    wait_heading(page, "Evaluate")
    page.get_by_text("Pending", exact=True).first.click()
    wait_list_settled(page)
    log.info("Opened evaluation list for %s", program)


# ───────────────────────────── Step 1: Allotment ─────────────────────────────

EVALUATOR_CONTROL_SEL = 'select, [role="combobox"], button[aria-haspopup="listbox"]'


def _email_option_matches(text: str, email: str) -> bool:
    return bool(re.search(r"(?<![\w.+@-])" + re.escape(email.strip()) + r"(?![\w.+@-])",
                          text, re.IGNORECASE))


def _evaluator_controls(page: Page) -> list[dict]:
    controls = page.locator(EVALUATOR_CONTROL_SEL).evaluate_all(r"""elements => elements.map((el, index) => {
        let label = Array.from(el.labels || [], n => n.innerText).join(' ') + ' ' +
            (el.getAttribute('aria-label') || '') + ' ' +
            (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id =>
                document.getElementById(id)?.innerText || '').join(' ');
        for (let parent = el.parentElement, depth = 0; parent && depth < 3; parent = parent.parentElement, depth++) {
            if (parent.querySelectorAll('select, [role="combobox"], button[aria-haspopup="listbox"]').length === 1 &&
                /\bevaluator\b/i.test(parent.innerText)) { label += ' Evaluator'; break; }
        }
        return {index, tag: el.tagName.toLowerCase(), label, visible: !!el.getClientRects().length &&
                getComputedStyle(el).visibility !== 'hidden', disabled: !!el.disabled || el.getAttribute('aria-disabled') === 'true',
                options: Array.from(el.options || [], (o, index) => ({index, text: o.textContent.trim(),
                    value: o.value, disabled: o.disabled}))};
    })""")
    candidates = []
    for control in controls:
        if not control["visible"]:
            continue
        texts = {o["text"].strip().casefold() for o in control["options"]}
        # Never mistake the Submitted/In Evaluation status filter for an evaluator dropdown.
        if "submitted" in texts or "in evaluation" in texts or "inevaluation" in texts:
            continue
        candidates.append(control)
    labelled = [c for c in candidates if re.search(r"\bevaluator\b", c["label"], re.IGNORECASE)]
    return labelled or candidates


def _real_evaluator_options(options: list[dict]) -> list[dict]:
    return [o for o in options if not o["disabled"] and o["text"].strip()
            and not re.fullmatch(r"(?:select|choose)(?: an?)?(?: evaluator)?(?:\.\.\.)?|loading(?:\.\.\.)?",
                                 o["text"].strip(), re.IGNORECASE)]


class EvaluatorListUnavailable(RuntimeError):
    """The evaluator options did not load; callers may recover before submitting."""


def select_evaluator(page: Page, email: str) -> str:
    """Resolve an email only from the evaluator control; open lazy-loaded lists and retry once."""
    email = email.strip()
    if not email:
        raise ValueError("Enter an evaluator email before assigning copies.")
    names_seen, control_seen = 0, False
    for attempt in range(2):
        give_up_at = time.monotonic() + DEFAULT_TIMEOUT_MS / 1000
        opened = False
        while True:
            if should_stop():
                raise AbortRun("Stopped by you")
            candidates = _evaluator_controls(page)
            if len(candidates) > 1:
                raise RuntimeError("More than one possible Evaluator dropdown was found; stopped without choosing an account.")
            if candidates:
                control_seen = True
                metadata = candidates[0]
                control = page.locator(EVALUATOR_CONTROL_SEL).nth(metadata["index"])
                options = _real_evaluator_options(metadata["options"])
                if metadata["tag"] == "select":
                    matches = [o for o in options if _email_option_matches(o["text"], email)
                               or _email_option_matches(o["value"], email)]
                    if len(matches) > 1:
                        raise RuntimeError(f"More than one evaluator option matches '{email}'; stopped without selecting one.")
                    if matches and not metadata["disabled"]:
                        option = matches[0]
                        control.select_option(index=option["index"])
                        if control.evaluate("el => el.selectedIndex") != option["index"]:
                            raise RuntimeError("The website did not retain the selected evaluator; no copies were submitted.")
                        return option["text"].strip()
                if not metadata["disabled"] and not opened:
                    control.click()
                    opened = True
                    if metadata["tag"] == "select":
                        control.press("Escape")  # Close the native popup; options may load after opening it.
                    elif metadata["tag"] == "input":
                        control.fill(email)  # Searchable ARIA comboboxes load options after typing.
                if metadata["tag"] != "select" and opened:
                    popup = page.get_by_role("option")
                    options = _real_evaluator_options(popup.evaluate_all("""elements => elements.map((el, index) => ({
                        index, text: el.innerText.trim(), value: el.getAttribute('data-value') || '',
                        disabled: el.getAttribute('aria-disabled') === 'true' || !el.getClientRects().length
                    }))"""))
                    matches = [o for o in options if _email_option_matches(o["text"], email)
                               or _email_option_matches(o["value"], email)]
                    if len(matches) > 1:
                        raise RuntimeError(f"More than one evaluator option matches '{email}'; stopped without selecting one.")
                    if matches:
                        option = matches[0]
                        popup.nth(option["index"]).click()
                        return option["text"].strip()
                names_seen = max(names_seen, len(options))
            if time.monotonic() >= give_up_at:
                break
            page.wait_for_timeout(500)
        if attempt == 0:
            log.warning("Evaluator options are not ready or the email was not found; reopening the dropdown once.")
            if control_seen and opened:
                control.press("Escape")
    if not control_seen:
        raise EvaluatorListUnavailable("The Evaluator dropdown could not be detected after selecting students.")
    if not names_seen:
        raise EvaluatorListUnavailable("The Evaluator dropdown stayed empty after opening it and retrying.")
    raise LookupError(f"Evaluator '{email}' was not found among {names_seen} readable evaluator options for this test. "
                      "Check the exact email in this test's dropdown; no copies were submitted.")


def verify_student_row(row: Locator, student_id: str, *, page: Page | None = None, student_name: str = "") -> str:
    """Integrated-workflow guard: a stale search must not select another student.

    Evidence accepted, in this order:
      1. the student ID in the row text, in a data attribute, or anywhere in the
         row's markup (links, values, ids);
      2. otherwise - the live portal shows names, not IDs - the search box still
         holds exactly this ID (so the row is that search's single result) and,
         when the sheet names the student, every word of that name is in the row.
    Returns a short description of the evidence; raises LookupError otherwise.
    """
    identifier = re.compile(r"(?<![A-Za-z0-9_-])" + re.escape(student_id) + r"(?![A-Za-z0-9_-])")
    if identifier.search(row.inner_text()):
        return "student ID in the row"
    for attribute in ("data-student-id", "data-studentid"):
        if row.get_attribute(attribute) == student_id:
            return f"{attribute} on the row"
        if row.locator(f"[{attribute}]").count():
            elements = row.locator(f"[{attribute}]")
            if any(elements.nth(i).get_attribute(attribute) == student_id for i in range(elements.count())):
                return f"{attribute} in the row"
    try:
        markup = row.evaluate("element => element.outerHTML") or ""
    except Exception:  # noqa: BLE001 - a row double without markup
        markup = ""
    if identifier.search(markup):
        return "student ID in the row markup"
    if page is not None:
        typed = ""
        try:
            if page.locator(SEL["search"]).count():
                typed = search_box(page).input_value().strip()
        except Exception:  # noqa: BLE001 - no usable search box: no evidence
            typed = ""
        if typed.casefold() == student_id.casefold():
            words = [w for w in re.split(r"\W+", student_name.casefold()) if w]
            row_text = " ".join(row.inner_text().casefold().split())
            if not words:
                return "single result of the student ID search (the sheet gives no name to compare)"
            if all(re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", row_text) for w in words):
                return "single result of the student ID search, name as in the sheet"
            raise LookupError(f"The row found for student ID {student_id} does not show the sheet's name '{student_name}'; identity cannot be verified")
    raise LookupError(f"Portal row does not expose student ID {student_id}; search identity cannot be verified")


def allot_student(page: Page, rec: StudentRecord, confirm: bool, before_submit=lambda: None) -> bool:
    """Returns True if allotted now, False if the site shows it was already allotted.
    `before_submit` runs right before the Submit click (integrated workflow: persist intent)."""
    search(page, rec.student_id)
    rows = page.locator(SEL["data_rows"]).filter(has=page.locator(SEL["row_checkbox"]))
    count = rows.count()
    if count != 1:
        raise LookupError(f"Student ID search returned {count} rows (expected 1)")
    row = rows.first
    log.info("Identity of %s: %s", rec.student_id,
             verify_student_row(row, rec.student_id, page=page, student_name=rec.student_name))

    status = row.locator("td").last.inner_text().strip()
    if status.lower() != "submitted":
        log.info("%s is already '%s' - no allotment needed", rec.student_id, status)
        return False

    row.locator(SEL["row_checkbox"]).first.check()
    deadline = page.locator(SEL["deadline"]).first
    deadline.wait_for(state="visible")  # Evaluator + Deadline appear only after a student is ticked
    select_evaluator(page, rec.evaluator)
    deadline.fill(rec.deadline)

    if confirm:
        confirm_submit(f"allotment of {rec.student_id} to {rec.evaluator}")
    before_submit()
    page.get_by_role("button", name="Submit").first.click()
    page.locator(SEL["allot_success"]).first.wait_for(state="visible")
    return True


# ───────────────────────────── Assign copies in bundles ─────────────────────────────
# Admin login. Submitted copies are assigned; In Evaluation copies can be reassigned. In Review and Finished
# copies are left untouched.

UNASSIGNED = "Submitted"
REASSIGNABLE = "In Evaluation"


def choose_status(page: Page, status: str) -> None:
    """Set the status filter above the Students list, unless it already shows `status`."""
    selects = page.locator("select")
    for i in range(selects.count()):
        if status in [o.strip() for o in selects.nth(i).locator("option").all_inner_texts()]:
            if selects.nth(i).evaluate("s => s.options[s.selectedIndex].text.trim()") != status:
                before = page.evaluate(_ROWS_TEXT_JS, SEL["data_rows"])
                selects.nth(i).select_option(label=status)
                wait_rows_changed(page, before, LIST_SETTLE_TIMEOUT_MS)
                wait_list_settled(page)
            return
    raise LookupError(f"The status filter with '{status}' is not on this page")


def wait_rows_changed(page: Page, before: str, timeout: int) -> bool:
    try:
        page.wait_for_function(_ROWS_CHANGED_AND_STABLE_JS, arg=[SEL["data_rows"], before],
                               timeout=timeout, polling=100)
        return True
    except PWTimeout:
        return False


NEXT_PAGE_TEXT = re.compile(r"^\s*[>›»]\s*$")     # the site's page bar:  <  01  02  03 ... 08  >

_IS_DISABLED_JS = """e => {
    for (let n = e, i = 0; n && i < 3; n = n.parentElement, i++) {
        const style = getComputedStyle(n);
        if (n.disabled || n.getAttribute('aria-disabled') === 'true' || /disabled/i.test(n.className + '')
            || style.pointerEvents === 'none' || style.cursor === 'not-allowed') return true;
    }
    return false;
}"""


def go_next_page(page: Page) -> bool:
    """Click the '>' of the page bar under the list. False if there is none or it is the last page."""
    nxt = page.get_by_text(NEXT_PAGE_TEXT).last
    if not nxt.count():
        nxt = page.locator(SEL["next_page"]).last
    if not nxt.count() or not nxt.is_visible() or nxt.evaluate(_IS_DISABLED_JS):
        return False
    before = page.evaluate(_ROWS_TEXT_JS, SEL["data_rows"])
    nxt.click()
    changed = wait_rows_changed(page, before, LIST_SETTLE_TIMEOUT_MS)
    wait_list_settled(page)
    return changed


TOAST_SEL = '.Toastify__toast, [role="alert"], [class*="toast" i], [class*="snackbar" i]'
SAME_LIST_RETRIES = 3               # the site can take a few seconds to move assigned copies out of "Submitted"
SUBMIT_REACT_TIMEOUT_MS = 45_000    # a bundle of 50 copies can take the site a while
BUNDLE_RESUBMITS = 2                # times one bundle is submitted again when the site did not take it


def submit_bundle(page: Page, deadline_box: Locator) -> bool:
    """Click Submit and wait for the site to take it. The site does not reload the list: it only unticks the
    copies and hides the Evaluator / Deadline boxes (and shows a message). False if it did not react."""
    before = page.evaluate(_ROWS_TEXT_JS, SEL["data_rows"])
    refused: list[str] = []         # what the server answered, for log.txt

    def note(resp) -> None:
        if resp.request.method != "GET" and resp.status >= 400:
            refused.append(f"HTTP {resp.status} {resp.request.method} {resp.url}")

    page.on("response", note)
    try:
        page.get_by_role("button", name="Submit").first.click()
        give_up_at = time.monotonic() + SUBMIT_REACT_TIMEOUT_MS / 1000
        message = ""
        reacted = False
        while not reacted and time.monotonic() < give_up_at:
            toast = page.locator(TOAST_SEL).first
            if toast.count() and toast.is_visible():
                message = " ".join(toast.inner_text().split())
            reacted = (not deadline_box.is_visible() or page.evaluate(_ROWS_TEXT_JS, SEL["data_rows"]) != before
                       or page.locator(SEL["allot_success"]).first.is_visible())
            if not reacted:
                page.wait_for_timeout(250)  # waiting for the site's answer to Submit
    finally:
        page.remove_listener("response", note)
    if message:
        log.info("The site says: %s", message)
    for line in refused:
        log.warning("The site refused a request: %s", line)
    return reacted and not refused


def refresh_students(page: Page, reopen) -> None:
    """Load the Students list again so it shows what is still unassigned."""
    page.reload(wait_until="domcontentloaded")
    try:
        page.get_by_text("Students", exact=True).first.wait_for(state="visible", timeout=LIST_SETTLE_TIMEOUT_MS)
    except PWTimeout:
        reopen()    # the reload went back to the dashboard
    wait_list_settled(page)


def allot_submitted(page: Page, test_name: str, evaluator: str, deadline: str, results: list[dict], reopen) -> None:
    """
    Assign every 'Submitted' copy of the open test to `evaluator`, one page (bundle) at a time.
    `reopen()` opens this test's Students page again from the menu.
    """
    bundles = 0
    sent: list[str] = []            # names in the bundle just submitted, until the list shows they are gone
    same_list = resubmits = 0

    def confirm_sent() -> None:
        for name in sent:
            add_result(results, name, test_name, "Allotment", DONE, f"Assigned to {evaluator}")
        if sent:
            log.info("OK   bundle %d: %d copies assigned to %s", bundles, len(sent), evaluator)
            on_status(f"Assigned {len(results)} copies so far...")
        sent.clear()

    for _ in range(MAX_ALLOT_ROUNDS * (SAME_LIST_RETRIES + 2)):
        if should_stop():
            confirm_sent()
            raise AbortRun("Stopped by you")
        choose_status(page, UNASSIGNED)
        rows = _allotment_rows(page, UNASSIGNED)
        count = rows.count()
        if count == 0:
            confirm_sent()
            if go_next_page(page):
                continue
            return
        names = [" ".join(t.split()) for t in rows.locator("td:nth-child(2)").all_inner_texts()]
        if sent and names == sent:  # the copies just submitted are still listed as Submitted
            same_list += 1
            if same_list <= SAME_LIST_RETRIES:
                page.wait_for_timeout(3_000)
                refresh_students(page, reopen)
                continue
            if resubmits >= BUNDLE_RESUBMITS:
                raise RuntimeError(f"Submit was clicked {resubmits + 1} times for the same {len(sent)} copies, but the "
                                   "site still lists them as Submitted. Try again later; the copies assigned so far "
                                   "are in the report.")
            resubmits += 1
            bundles -= 1
            sent.clear()
            log.warning("The site did not take the last bundle; submitting it again (try %d).", resubmits + 1)
        else:
            resubmits = 0
        same_list = 0
        confirm_sent()

        all_records = _allotment_snapshot(page)
        targets = [r for r in all_records if _status_pattern(UNASSIGNED).fullmatch(r["status"])]
        if [r["name"] for r in targets] != names:
            raise RuntimeError("The Submitted students changed while preparing the bundle; no copies in this bundle were submitted.")
        _select_reassignment_rows(page, all_records, targets, status=UNASSIGNED)
        deadline_box = page.locator(SEL["deadline"]).first
        deadline_box.wait_for(state="visible")  # Evaluator + Deadline appear only after a student is ticked
        _select_evaluator_with_refresh(page, evaluator, UNASSIGNED, targets, reopen)
        deadline_box.fill(deadline)

        if bundles < runtime_setting("CONFIRM_FIRST_N") and not ask_yes_no(
                f"Please check the Chrome window: {count} copies of '{test_name}' are ticked, to go to "
                f"{evaluator}.\n\nSubmit now?\n(No = stop without assigning these)"):
            raise AbortRun("You chose not to assign")
        if not submit_bundle(page, deadline_box):
            log.warning("The site did not react to Submit; checking the list to see if the copies were assigned.")
        bundles += 1
        sent.extend(names)
        refresh_students(page, reopen)
    raise RuntimeError("Stopped after too many rounds; there are still Submitted copies left.")


def _status_pattern(status: str) -> re.Pattern:
    return re.compile(
        r"^\s*" + r"\s*".join(re.escape(word) for word in status.split()) + r"\s*$",
        re.IGNORECASE,
    )


def _allotment_rows(page: Page, status: str):
    return (page.locator(SEL["data_rows"]).filter(has=page.locator(SEL["row_checkbox"]))
            .filter(has=page.locator("td:last-child", has_text=_status_pattern(status))))


def _allotment_identity(row: Locator) -> tuple[str, str, str]:
    cells = row.locator("td")
    if cells.count() < 8:
        raise ValueError("The Students table row has an unexpected layout; no copies were reassigned.")
    return tuple(" ".join(cells.nth(index).inner_text().split()).casefold() for index in (1, 2, 7))


def _evaluator_matches(row: Locator, selected_option: str) -> bool:
    return _evaluator_text_matches(row.locator("td").nth(3).inner_text(), selected_option)


def _evaluator_text_matches(displayed: str, selected_option: str) -> bool:
    displayed = " ".join(displayed.split()).casefold()
    expected = selected_option.split("(", 1)[0].strip().casefold()
    return bool(expected and displayed.startswith(expected))


def _allotment_snapshot(page: Page) -> list[dict]:
    """Read one whole table in a single browser round trip; never mutate it in JavaScript."""
    raw = page.locator(SEL["data_rows"]).evaluate_all("""rows => rows.map((row, index) => {
        const box = row.querySelector('input[type="checkbox"]');
        return {index, cells: Array.from(row.querySelectorAll('td'), c => c.innerText),
                selectable: !!box, checked: !!box && box.checked};
    })""")
    records = []
    for row in raw:
        if not row["selectable"]:
            continue
        cells = [" ".join(cell.split()) for cell in row["cells"]]
        if len(cells) < 9:
            raise ValueError("The Students table row has an unexpected layout; no copies were reassigned.")
        records.append({"index": row["index"], "identity": tuple(cells[i].casefold() for i in (1, 2, 7)),
                        "name": cells[1], "evaluator": cells[3], "status": cells[-1],
                        "checked": row["checked"]})
    return records


def _evaluation_snapshot(page: Page) -> list[dict]:
    return [r for r in _allotment_snapshot(page) if _status_pattern(REASSIGNABLE).fullmatch(r["status"])]


def _select_reassignment_rows(page: Page, records: list[dict], targets: list[dict],
                              status: str = REASSIGNABLE) -> None:
    wanted = {r["identity"] for r in targets}
    if not targets or len(wanted) != len(targets):
        raise RuntimeError("The intended student bundle is empty or has duplicate identities; stopped before submitting.")
    rows = page.locator(SEL["data_rows"])
    # Clear any stale selections, including rows outside the assignable status.
    for record in records:
        if record["checked"] and record["identity"] not in wanted:
            rows.nth(record["index"]).locator(SEL["row_checkbox"]).first.uncheck()
    select_all = page.locator(SEL["select_all"]).first
    if len(targets) == len(records) == rows.count() and select_all.count():
        select_all.check()
    else:
        for record in targets:
            if should_stop():
                raise AbortRun("Stopped by you")
            if not record["checked"]:
                rows.nth(record["index"]).locator(SEL["row_checkbox"]).first.check()
    _verify_selected_bundle(page, targets, status)


def _verify_selected_bundle(page: Page, targets: list[dict], status: str) -> None:
    wanted = {r["identity"] for r in targets}
    selected = [r for r in _allotment_snapshot(page) if r["checked"]]
    if (len(selected) != len(targets) or {r["identity"] for r in selected} != wanted
            or any(not _status_pattern(status).fullmatch(r["status"]) for r in selected)):
        raise RuntimeError("The selected Students rows changed; stopped before submitting this bundle.")


def _restore_evaluator_bundle(page: Page, targets: list[dict], status: str, reopen) -> None:
    """Reload and locate the original bundle by identity, never by old row index or page number."""
    if should_stop():
        raise AbortRun("Stopped by you")
    wanted = {r["identity"] for r in targets}
    if not targets or len(wanted) != len(targets):
        raise RuntimeError("Cannot restore a bundle with empty or duplicate student identities.")
    refresh_students(page, reopen)
    choose_status(page, status)
    _first_allotment_page(page)
    seen_pages = set()
    while True:
        if should_stop():
            raise AbortRun("Stopped by you")
        records = _allotment_snapshot(page)
        signature = tuple(r["identity"] for r in records)
        if signature and signature in seen_pages:
            raise RuntimeError("The Students list repeated a page during recovery; this bundle was not submitted.")
        seen_pages.add(signature)
        matches = [r for r in records if r["identity"] in wanted]
        if len({r["identity"] for r in matches}) != len(matches):
            raise RuntimeError("Student identities became ambiguous after refresh; this bundle was not submitted.")
        if len(matches) == len(targets):
            if any(not _status_pattern(status).fullmatch(r["status"]) for r in matches):
                raise RuntimeError("Student statuses changed after refresh; this bundle was not submitted.")
            _select_reassignment_rows(page, records, matches, status=status)
            return
        if not go_next_page(page):
            raise RuntimeError("Could not restore the exact student bundle after refresh (students moved, disappeared, "
                               "or changed status); this bundle was not submitted.")


def _select_evaluator_with_refresh(page: Page, email: str, status: str, targets: list[dict], reopen) -> str:
    """Recover an unloaded evaluator list with at most two full reloads, before any Submit."""
    for attempt in range(3):
        if should_stop():
            raise AbortRun("Stopped by you")
        try:
            selected = select_evaluator(page, email)
        except EvaluatorListUnavailable as exc:
            if attempt == 2:
                raise RuntimeError("The evaluator list did not load after 2 automatic page refreshes. "
                                   "The current bundle was not submitted; earlier confirmed assignments are in the report.") from exc
            log.warning("Evaluator list unavailable. Automatic page refresh %d/2; restoring %d selected students (%s).",
                        attempt + 1, len(targets), status)
            on_status(f"Recovering evaluator list: page refresh {attempt + 1}/2...")
            _restore_evaluator_bundle(page, targets, status, reopen)
        else:
            _verify_selected_bundle(page, targets, status)
            if attempt:
                log.info("Evaluator list recovered after %d page refresh(es); exact student selection verified.", attempt)
            return selected
    raise AssertionError("Unreachable evaluator recovery state")


def _first_allotment_page(page: Page) -> None:
    """Reset pagination explicitly; reload/filter changes need not preserve the page."""
    if should_stop():
        raise AbortRun("Stopped by you")
    # The site's faded '<' on page 01 is a no-op but does not reliably expose
    # disabled/aria-disabled. Selecting 01 is idempotent, including on page 01.
    first = page.get_by_text(re.compile(r"^\s*0?1\s*$")).last
    if first.count() and first.is_visible():
        if not first.evaluate(_IS_DISABLED_JS):
            before = page.evaluate(_ROWS_TEXT_JS, SEL["data_rows"])
            first.click()
            wait_rows_changed(page, before, LIST_SETTLE_TIMEOUT_MS)
        wait_list_settled(page)
        return
    # Fall back to the previous arrow if the first numbered link is not shown.
    for _ in range(MAX_ALLOT_ROUNDS):
        if should_stop():
            raise AbortRun("Stopped by you")
        previous = page.get_by_text(re.compile(r"^\s*[<‹«]\s*$")).last
        if not previous.count() or not previous.is_visible() or previous.evaluate(_IS_DISABLED_JS):
            return
        before = page.evaluate(_ROWS_TEXT_JS, SEL["data_rows"])
        previous.click()
        if not wait_rows_changed(page, before, LIST_SETTLE_TIMEOUT_MS):
            raise RuntimeError("Could not return to the first Students page to verify assignments.")
        wait_list_settled(page)
    raise RuntimeError("Stopped after too many Students pages while resetting pagination.")


def _scan_reassigned_students(page: Page, identities: dict, selected_option: str) -> dict:
    """Verify all submitted bundles in one fresh, forward pass, including moved students."""
    choose_status(page, REASSIGNABLE)
    _first_allotment_page(page)
    found, seen_pages = {}, set()
    page_number = 0
    while True:
        if should_stop():
            raise AbortRun("Stopped by you")
        records = _evaluation_snapshot(page)
        signature = tuple(r["identity"] for r in records)
        if signature and signature in seen_pages:
            raise RuntimeError("The Students pagination repeated a page during verification.")
        seen_pages.add(signature)
        page_number += 1
        on_status(f"Verifying assignments: page {page_number}, {sum(found.values())}/{len(identities)} confirmed")
        for record in records:
            identity = record["identity"]
            if identity not in identities:
                continue
            if identity in found:
                raise RuntimeError(f"Could not uniquely verify the reassignment for {identities[identity]}.")
            found[identity] = _evaluator_text_matches(record["evaluator"], selected_option)
        if not go_next_page(page):
            log.info("Verification: %d pages checked; %d/%d assignments confirmed",
                     page_number, sum(found.values()), len(identities))
            return found


def _verify_reassignment_queue(page: Page, pending: dict, selected_option: str, evaluator: str,
                               reopen) -> None:
    """Update queued report entries only after reading the evaluator back from the site."""
    for attempt in range(3):
        refresh_students(page, reopen)
        names = {identity: result["Student_ID"] for identity, result in pending.items()}
        found = _scan_reassigned_students(page, names, selected_option)
        for identity, matched in found.items():
            if matched:
                result = pending.pop(identity)
                result["Result"] = DONE
                result["Details"] = f"Reassigned to {evaluator}; verified on the Students list"
                log.info("OK   reassigned %s to %s", result["Student_ID"], evaluator)
        if not pending:
            return
        if attempt < 2:
            page.wait_for_timeout(1_000)
    identity, result = next(iter(pending.items()))
    detail = ("The student was not found across the In Evaluation pages."
              if identity not in found else "The student still shows a different evaluator.")
    raise RuntimeError(
        f"The site did not confirm that {result['Student_ID']} was assigned to {evaluator}. "
        f"{detail} Check the Students page before retrying."
    )


def allot_in_evaluation(page: Page, test_name: str, evaluator: str, deadline: str, results: list[dict],
                        reopen) -> None:
    """One assignment pass, then one verification pass; no full-list scan per bundle."""
    bundles, selected_option = 0, None
    recorded, pending = set(), {}
    while True:
        choose_status(page, REASSIGNABLE)
        _first_allotment_page(page)
        seen_pages, seen_students = set(), set()
        page_number, verify_early = 0, False
        while True:
            if should_stop():
                raise AbortRun("Stopped by you")
            all_records = _allotment_snapshot(page)
            records = [r for r in all_records if _status_pattern(REASSIGNABLE).fullmatch(r["status"])]
            signature = tuple(r["identity"] for r in records)
            if signature and signature in seen_pages:
                raise RuntimeError("The Students pagination repeated a page while assigning copies.")
            seen_pages.add(signature)
            page_number += 1
            if records and selected_option is None:
                # Reveal and resolve the evaluator once, not once per student or page.
                box = page.locator(SEL["data_rows"]).nth(records[0]["index"]).locator(SEL["row_checkbox"]).first
                box.check()
                selected_option = _select_evaluator_with_refresh(page, evaluator, REASSIGNABLE, records[:1], reopen)
                # Recovery may have reordered the rows; clear the selection using fresh indices.
                for record in _allotment_snapshot(page):
                    if record["checked"]:
                        page.locator(SEL["data_rows"]).nth(record["index"]).locator(SEL["row_checkbox"]).first.uncheck()
                all_records = _allotment_snapshot(page)
                records = [r for r in all_records if _status_pattern(REASSIGNABLE).fullmatch(r["status"])]
            identities = [r["identity"] for r in records]
            if len(set(identities)) != len(identities) or seen_students.intersection(identities):
                raise RuntimeError("Students have duplicate identities; stopped before submitting this bundle.")
            seen_students.update(identities)
            targets = []
            for record in records:
                identity, name = record["identity"], record["name"]
                if _evaluator_text_matches(record["evaluator"], selected_option):
                    if identity not in recorded:
                        add_result(results, name, test_name, "Allotment", DONE_EARLIER,
                                   f"Already assigned to {evaluator}; left unchanged")
                        recorded.add(identity)
                else:
                    targets.append(record)
            on_status(f"Assigning page {page_number}: {len(targets)} to reassign, {len(records) - len(targets)} unchanged")
            log.info("Assignment page %d: %d to reassign, %d already assigned",
                     page_number, len(targets), len(records) - len(targets))
            if targets:
                _select_reassignment_rows(page, all_records, targets)
                deadline_box = page.locator(SEL["deadline"]).first
                deadline_box.wait_for(state="visible")
                # Selecting a new bundle may reset the dropdown; select the resolved option again.
                selected_option = _select_evaluator_with_refresh(page, evaluator, REASSIGNABLE, targets, reopen)
                deadline_box.fill(deadline)
                if bundles < runtime_setting("CONFIRM_FIRST_N") and not ask_yes_no(
                        f"Please check the Chrome window: {len(targets)} copies currently in 'In Evaluation' are "
                        f"selected for reassignment to {evaluator}.\n\nSubmit now?\n"
                        "(No = stop without reassigning these copies)"):
                    raise AbortRun("You chose not to reassign these copies")
                if should_stop():
                    raise AbortRun("Stopped by you")
                for record in targets:
                    add_result(results, record["name"], test_name, "Allotment", UNVERIFIED,
                               f"Submission attempted for {evaluator}; verification not completed")
                    pending[record["identity"]] = results[-1]
                    recorded.add(record["identity"])
                reacted = submit_bundle(page, deadline_box)
                bundles += 1
                log.info("Submitted bundle %d: %d copies; %d awaiting verification",
                         bundles, len(targets), len(pending))
                if not reacted:
                    # Do not continue submitting when the website stops acknowledging actions.
                    log.warning("Submit was not acknowledged; verifying queued assignments before continuing.")
                    verify_early = True
                    break
            if not go_next_page(page):
                break
        if pending:
            _verify_reassignment_queue(page, pending, selected_option, evaluator, reopen)
        if not verify_early:
            return
        # Only an unacknowledged Submit requires another assignment pass. Confirmed rows are skipped.


# ───────────────────────────── Step 2: Evaluation ─────────────────────────────

def find_evaluate_row(page: Page, rec: StudentRecord) -> Locator:
    search(page, rec.student_id)
    rows = page.locator(SEL["data_rows"]).filter(has=page.locator("td", has_text=whole_label(rec.test_name)))
    if rec.test_series:     # the Evaluate list shows it as "Test Category", e.g. "PARISHRAM ... - Batch Tests"
        rows = rows.filter(has=page.locator("td", has_text=whole_label(rec.test_series)))
    count = rows.count()
    if count == 0:
        raise LookupError("Not in your Pending list with this Test_Name"
                          + (" and Test_Series (Test Category)" if rec.test_series else "")
                          + " (is it allotted to the logged-in account?)")
    if count > 1:
        raise LookupError(f"{count} rows match this Student ID + Test_Name")
    log.info("Identity of %s: %s", rec.student_id,
             verify_student_row(rows.first, rec.student_id, page=page, student_name=rec.student_name))
    return rows.first


MENU_ITEMS = ("Download Submission", "Model Answer", "Upload")


def close_row_menu(page: Page) -> None:
    """Close an open ⋮ menu (clicking ⋮ while it is open would just close it)."""
    marker = page.get_by_text("Model Answer", exact=True).first
    if marker.is_visible():
        page.keyboard.press("Escape")
        if marker.is_visible():
            page.get_by_text("Evaluate", exact=True).first.click()  # click on empty page area
        marker.wait_for(state="hidden")


def click_menu_item(page: Page, row: Locator, item: str) -> None:
    close_row_menu(page)
    open_row(row)  # the ⋮ Actions button
    option = page.get_by_text(item, exact=True).first
    try:
        option.wait_for(state="visible", timeout=5_000)
    except PWTimeout:
        shown = [t for t in MENU_ITEMS + ("View Upload", "Download") if page.get_by_text(t, exact=True).first.is_visible()]
        raise LookupError(f"'{item}' is not in the ⋮ menu for this row (menu shows: {', '.join(shown) or 'nothing'})") from None
    option.click()


def upload_paper(page: Page, row: Locator, path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(f"Paper not found: {path}")
    file_arg = str(path.resolve())

    # A toast left over from the previous student would give a false "uploaded" signal.
    try:
        page.locator(SEL["upload_success"]).first.wait_for(state="hidden", timeout=TOAST_CLEAR_TIMEOUT_MS)
    except PWTimeout:
        log.warning("Previous upload message still visible; upload confirmation may be unreliable.")

    # 'Upload' either opens a native file chooser directly or reveals an <input type=file>.
    try:
        with page.expect_file_chooser(timeout=FILE_CHOOSER_TIMEOUT_MS) as fc_info:
            click_menu_item(page, row, "Upload")
        fc_info.value.set_files(file_arg)
    except PWTimeout:
        page.locator(SEL["file_input"]).first.set_input_files(file_arg)

    page.locator(SEL["upload_success"]).first.wait_for(state="visible", timeout=UPLOAD_TIMEOUT_MS)
    # The ⋮ menu stays open after the upload and the list reloads (View Upload appears only now).
    close_row_menu(page)
    wait_list_settled(page)


def fill_dropdown_inputs(modal: Locator, opener: str, values: dict[str, str]) -> None:
    """Open a dropdown like 'Select Marks' and type each value into the input next to its label."""
    modal.get_by_text(opener, exact=True).first.click()
    for label, value in values.items():
        # Exact label match (so "Q.1" never picks "Q.10"); the box may sit inside the label's element
        # or right after it, and the union returns whichever comes first in the page.
        # Whole text, not text(): the site renders "Q.1" as two text nodes ("Q." + "1").
        # The not(...) keeps only the innermost element with that text (the label, not its wrapper).
        lbl = f'.//*[normalize-space()="{label}" and not(*[normalize-space()="{label}"])]'
        box = modal.locator(f"xpath=({lbl}/descendant::input | {lbl}/following::input)[1]")
        if not box.count():
            raise LookupError(f"'{label}' is not on this evaluation form")
        fill_field(box.first, value)
    modal.get_by_text(SEL["modal_title"], exact=True).first.click()  # close the dropdown


def fill_evaluation_modal(page: Page, rec: StudentRecord, confirm: bool, before_submit=lambda: None) -> None:
    modal = page.locator(SEL["modal"]).filter(has_text=SEL["modal_title"]).last
    try:
        modal.wait_for(state="visible")
    except PWTimeout:
        modal = page.locator("body")  # pop-up has no standard wrapper; search the whole page instead

    fill_dropdown_inputs(modal, "Select Marks", {f"Q.{q}": m for q, m in rec.marks.items()})
    fill_field(modal.locator(SEL["total_marks"]).first, rec.total_marks)
    if rec.ratings:
        fill_dropdown_inputs(modal, "Rate Student", rec.ratings)
    if rec.comment:
        fill_field(modal.locator(SEL["comment"]).first, rec.comment)

    if confirm:
        confirm_submit(f"marks for {rec.student_id}")
    before_submit()
    modal.get_by_role("button", name="Submit").first.click()
    page.locator(SEL["eval_success"]).first.wait_for(state="visible")

    # The marks are saved at this point, so tidying up must never turn this student into a failure.
    try:
        if not click_if_visible(modal, SEL["modal_close"], timeout=2_000):
            page.keyboard.press("Escape")
    except PWError:
        pass  # the pop-up closed by itself


def evaluate_student(page: Page, context: BrowserContext, rec: StudentRecord, confirm: bool,
                     before_submit=lambda: None) -> None:
    stage = "find student"
    try:
        row = find_evaluate_row(page, rec)

        stage = "upload"
        upload_paper(page, row, rec.paper_path)     # the user's own file: only read, never changed

        stage = "open View Upload"          # paper IS uploaded from here on
        click_menu_item(page, row, "View Upload")

        stage = "fill marks"
        fill_evaluation_modal(page, rec, confirm, before_submit)
    except SkippedByUser:
        raise
    except Exception as exc:
        raise RuntimeError(f"[{stage}] {exc}") from exc


# ───────────────────────────── Runner ─────────────────────────────

DONE, DONE_EARLIER, FAILED, SKIPPED, INVALID = "Done", "Done earlier", "Failed", "Skipped", "Invalid data"
UNVERIFIED = "Unverified"


def first_line(exc: Exception) -> str:
    return (str(exc) or type(exc).__name__).splitlines()[0]


def add_result(results: list[dict], student_id: str, test_name: str, step: str, result: str,
               details: str = "", screenshot: str = "") -> None:
    results.append({"Student_ID": student_id, "Test_Name": test_name, "Step": step, "Result": result,
                    "Details": details, "Screenshot": screenshot})


def save_screenshot(page: Page, student_id: str) -> str:
    """Picture of the screen at the moment of failure, for troubleshooting; never raises."""
    try:
        screenshot_dir = runtime_setting("SCREENSHOT_DIR")
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        safe_id = re.sub(r"[^\w.-]", "_", student_id)
        path = screenshot_dir / f"{safe_id}_{datetime.now():%Y%m%d_%H%M%S}.png"
        page.screenshot(path=str(path))
        return str(path)
    except Exception:  # noqa: BLE001
        return ""


def prepare_records(df: pd.DataFrame, results: list[dict]) -> list[StudentRecord]:
    """Parse + validate every row up front and drop duplicate rows."""
    q_cols = question_columns(df)
    seen: set[tuple[str, str, str]] = set()
    records: list[StudentRecord] = []
    test_cols = [c for c in ("Test_Series", "Test_Name") if c in df.columns]
    pdfs = list_pdfs(PAPERS_FOLDER) if PAPERS_FOLDER else []
    tests_per_id = df.drop_duplicates(test_cols + ["Student_ID"])["Student_ID"].value_counts()
    repeated_ids = set(tests_per_id[tests_per_id > 1].index)
    if PAPERS_FOLDER:
        log.info("Found %d PDFs in %s", len(pdfs), PAPERS_FOLDER)

    for _, row in df.iterrows():
        key = (row["Test_Name"], row["Student_ID"])
        full_key = (row.get("Test_Series", ""), *key)
        if full_key in seen:
            log.info("Skipping duplicate row for %s / %s", key[1], key[0])
            continue
        seen.add(full_key)
        try:
            rec = build_record(row, q_cols)
            validate_record(rec)
            resolve_paper(rec, pdfs, repeated_ids)
            records.append(rec)
        except Exception as exc:  # noqa: BLE001
            log.error("FAIL Sheet row for %s / %s: %s", key[1], key[0], first_line(exc))
            add_result(results, key[1], key[0], "Sheet", INVALID, str(exc))

    log.info("Sheet has %d valid rows, %d invalid", len(records), sum(r["Step"] == "Sheet" for r in results))
    return records


def group_by(records: list[StudentRecord], key) -> dict:
    groups: dict = {}
    for rec in records:
        groups.setdefault(key(rec), []).append(rec)
    return groups


def run_step(page: Page, context: BrowserContext, step: str, groups: dict, open_group, do_student,
             progress: dict, results: list[dict]) -> None:
    """Shared loop for both steps: open each group's page once, then process its students."""
    total = sum(len(recs) for recs in groups.values())
    attempted = 0
    on_progress(step, 0, total)

    for group_key, recs in groups.items():
        if should_stop():
            raise AbortRun("Stopped by you")
        try:
            open_group(page, recs[0])
        except (PWError, LookupError) as exc:
            if page.is_closed():
                raise AbortRun("The Chrome window was closed") from exc
            shot = save_screenshot(page, "open_page")
            log.error("FAIL %s: could not open '%s': %s", step, group_key, first_line(exc))
            log.debug("Details: %s", exc)
            for rec in recs:
                add_result(results, rec.student_id, rec.test_name, step, FAILED,
                           f"Could not open the page for '{group_key}': {first_line(exc)}", shot)
            attempted += len(recs)
            on_progress(step, attempted, total)
            continue

        for rec in recs:
            if page.is_closed():
                raise AbortRun("The Chrome window was closed")
            if should_stop():
                raise AbortRun("Stopped by you")
            attempted += 1
            try:
                do_student(page, context, rec, attempted <= runtime_setting("CONFIRM_FIRST_N"))
                mark_stage(rec, ALLOTTED if step == "Allotment" else EVALUATED, progress)
                add_result(results, rec.student_id, rec.test_name, step, DONE)
                log.info("OK   %s / %s", rec.student_id, rec.test_name)
            except SkippedByUser:
                add_result(results, rec.student_id, rec.test_name, step, SKIPPED, "Skipped by you at the check step")
                log.warning("SKIP %s / %s", rec.student_id, rec.test_name)
            except AbortRun:
                raise
            except Exception as exc:  # noqa: BLE001 - keep the loop alive on any per-student error
                shot = "" if page.is_closed() else save_screenshot(page, rec.student_id)
                add_result(results, rec.student_id, rec.test_name, step, FAILED, str(exc), shot)
                log.error("FAIL %s / %s: %s", rec.student_id, rec.test_name, first_line(exc))
                log.debug("Details: %s", exc)
            finally:
                if not page.is_closed():
                    reset_page(page, context)
                on_progress(step, attempted, total)


def write_report(results: list[dict]) -> Path | None:
    if not results:
        return None
    frame = pd.DataFrame(results)
    try:
        frame.to_csv(REPORT_LOG, index=False, encoding="utf-8-sig")  # BOM so Excel shows Hindi text correctly
        return REPORT_LOG
    except PermissionError:  # the previous report is still open in Excel
        alt = REPORT_LOG.with_name(f"report_{datetime.now():%Y%m%d_%H%M%S}.csv")
        frame.to_csv(alt, index=False, encoding="utf-8-sig")
        return alt


def summarize(results: list[dict]) -> dict:
    counts = {name: sum(r["Result"] == name for r in results)
              for name in (DONE, DONE_EARLIER, FAILED, SKIPPED, INVALID, UNVERIFIED)}
    return {"total": len(results), "counts": counts, "report": None, "stopped": ""}


def run(data_file: str | Path | None = None, papers_folder: str | Path | None = None,
        left_out: list[tuple[str, str]] = ()) -> dict:
    """
    Run the whole job and return a summary: {'total', 'counts', 'report', 'stopped'}.
    `left_out` lists PDFs that got no row in the marks sheet, as (student id, why); they go in the report.
    """
    global DATA_FILE, PAPERS_FOLDER
    if data_file:
        DATA_FILE = Path(data_file)
    if papers_folder is not None:
        PAPERS_FOLDER = Path(papers_folder) if str(papers_folder).strip() else None
    if PAPERS_FOLDER and not PAPERS_FOLDER.is_dir():
        raise ValueError(f"Folder not found: {PAPERS_FOLDER}")

    # Check the whole file before Chrome opens, so data mistakes show up straight away.
    results: list[dict] = []
    for student_id, why in left_out:
        log.error("FAIL Checked PDF for %s: %s", student_id, why)
        add_result(results, student_id, "", "Checked PDF", INVALID, why)
    records = prepare_records(load_data(read_data_file(DATA_FILE)), results)
    log.info("Read %s", DATA_FILE.name)
    progress = load_progress()
    stopped = ""
    if not records:
        summary = summarize(results)
        summary["report"] = write_report(results)
        log.info("Nothing to do on the site: no valid rows in the file.")
        return summary
    chrome = start_chrome()

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{runtime_setting('CDP_PORT')}", slow_mo=SLOW_MO_MS)
        context = browser.contexts[0]
        context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            wait_for_manual_login(page)

            if RUN_ALLOTMENT:
                to_allot = [r for r in records if ALLOTTED not in progress.get(r.key, set())]
                log.info("%d to allot, %d already allotted", len(to_allot), len(records) - len(to_allot))
                groups = group_by(to_allot, lambda r: (r.program, r.test_series, r.test_name))

                def do_allot(pg: Page, _ctx: BrowserContext, rec: StudentRecord, confirm: bool) -> None:
                    if not pg.get_by_text("Students", exact=True).first.is_visible():
                        open_allotment_test(pg, rec)  # recover after a failure / session expiry
                    allot_student(pg, rec, confirm)

                run_step(page, context, "Allotment", groups, open_allotment_test, do_allot, progress, results)

            if RUN_EVALUATION:
                # With allotment switched off, assume the sheet's students are already allotted.
                ready = [r for r in records if ALLOTTED in progress.get(r.key, set()) or not RUN_ALLOTMENT]
                to_evaluate = [r for r in ready if EVALUATED not in progress.get(r.key, set())]
                for rec in ready:
                    if rec not in to_evaluate:
                        add_result(results, rec.student_id, rec.test_name, "Evaluation", DONE_EARLIER)
                log.info("%d to evaluate, %d already done in an earlier run",
                         len(to_evaluate), len(ready) - len(to_evaluate))
                groups = group_by(to_evaluate, lambda r: r.program)

                def do_evaluate(pg: Page, ctx: BrowserContext, rec: StudentRecord, confirm: bool) -> None:
                    if not pg.get_by_text("Evaluate", exact=True).first.is_visible():
                        open_evaluate(pg, rec.program)
                    evaluate_student(pg, ctx, rec, confirm)

                run_step(page, context, "Evaluation", groups, lambda pg, rec: open_evaluate(pg, rec.program),
                         do_evaluate, progress, results)
        except (KeyboardInterrupt, AbortRun) as exc:
            stopped = first_line(exc) if str(exc) else "Stopped by you"
        except PWError:
            if not page.is_closed():
                raise
            stopped = "The Chrome window was closed"
        finally:
            summary = summarize(results)
            summary["stopped"] = stopped
            summary["report"] = write_report(results)
            if stopped:
                log.warning("Run stopped: %s", stopped)
            log.info("Finished: %s", ", ".join(f"{n} {name.lower()}" for name, n in summary["counts"].items() if n)
                     or "nothing to do")
            if chrome is not None:
                close_chrome(browser, chrome, page)
    return summary


def run_allotment(program: str, test_series: str, test_name: str, evaluator: str,
                  deadline_days: float = DEFAULT_DEADLINE_DAYS) -> dict:
    """Assign Submitted and reassign In Evaluation copies of one test to an evaluator."""
    deadline = (datetime.now() + timedelta(days=deadline_days)).strftime("%Y-%m-%dT%H:%M")
    results: list[dict] = []
    stopped = ""
    with runtime_context(USER_DATA_DIR=ADMIN_USER_DATA_DIR):
        chrome = start_chrome()

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{runtime_setting('CDP_PORT')}", slow_mo=SLOW_MO_MS)
        context = browser.contexts[0]
        context.set_default_timeout(DEFAULT_TIMEOUT_MS)
        page = context.pages[0] if context.pages else context.new_page()

        try:
            log.info("Assigning copies needs the ADMIN login (the account that has Allotment).")
            wait_for_manual_login(page)
            open_test_students(page, program, test_series, test_name)
            reopen = lambda: open_test_students(page, program, test_series, test_name)
            allot_submitted(page, test_name, evaluator, deadline, results, reopen)
            allot_in_evaluation(page, test_name, evaluator, deadline, results, reopen)
        except (KeyboardInterrupt, AbortRun) as exc:
            stopped = first_line(exc) if str(exc) else "Stopped by you"
        except PWError:
            if not page.is_closed():
                save_screenshot(page, "assign")
                raise
            stopped = "The Chrome window was closed"
        except Exception:
            save_screenshot(page, "assign")
            raise
        finally:
            summary = summarize(results)
            summary["stopped"] = stopped
            summary["report"] = write_report(results)
            if stopped:
                log.warning("Run stopped: %s", stopped)
            assigned = sum(result["Result"] == DONE for result in results)
            unchanged = sum(result["Result"] == DONE_EARLIER for result in results)
            unverified = sum(result["Result"] == UNVERIFIED for result in results)
            log.info("Finished: %d copies assigned/reassigned to %s; %d already assigned; %d unverified",
                     assigned, evaluator, unchanged, unverified)
            if chrome is not None:
                close_chrome(browser, chrome, page)
    return summary


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-7s | %(message)s")
    run()

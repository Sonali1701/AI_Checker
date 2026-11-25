"""
Find the question paper that belongs to a Model Answer.

On the site, many tests have almost the same name (Team A / Team B, two teachers...), so staff open the test's
"Model Answer" and then open the PDFs of the "Copy Checking Test paper" Google Sheet one by one until the
solutions look the same. This module does that comparison: it reads the words of the Model Answer PDF and of
every paper in the sheet (column "Copied PDF Link") and reports the row whose PDF contains the same solutions.

collect_site() does it for every test in the evaluator's Pending list and saves the question papers in a
folder. It asks the site's backend for the list and for each test's files (the same read-only requests the
site's own pages make), so nothing is clicked or changed on the site.

The sheet and its PDFs are shared with "Anyone with the link", so no Google sign-in is needed.
The text of each paper is kept in paper_cache/, so a paper is downloaded only once.
"""
from __future__ import annotations
# Vendored from Data_Filler v0.9.10; imports rewritten for the portal_vendor package.

import csv
import logging
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import unquote, urlsplit

import fitz
import openpyxl
from playwright.sync_api import Error as PWError, Page, Response, sync_playwright

from . import data_filler as engine

# ───────────────────────────── Configuration ─────────────────────────────

SHEET_URL = "https://docs.google.com/spreadsheets/d/1Ji7Ld7JOc0tdCbBDbdqalGq-YvQqYi8WvCPV3n31Wm0/edit"
SHEET_EXPORT_URL = "https://docs.google.com/spreadsheets/d/{id}/export?format=xlsx"
SHEET_COPY = Path("paper_sheet.xlsx")       # last downloaded copy of the Google Sheet
DRIVE_URLS = [                              # tried in order; both work without sign-in for link-shared files
    "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t",
    "https://drive.google.com/uc?export=download&id={id}",
]
CACHE_DIR = Path("./paper_cache")           # <Drive file id>.txt = the words of that paper
MATCH_REPORT = Path("match_report.csv")

LINK_COLUMN = "copied pdf link"             # the only column a tab must have (header names, lower case)
COLUMNS = {"batch": "batch name", "test_date": "test date", "cls": "class", "subject": "subject name",
           "name": "copied pdf name"}
DATE_FORMATS = ("%d-%b-%Y", "%d-%B-%Y", "%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y")
ALL = "All"

SHINGLE_WORDS = 4                   # papers are compared by their runs of 4 words
MIN_SHINGLES = 40                   # fewer than this = a scanned picture or an almost empty PDF
MATCH_SCORE = 0.80                  # share of the Model Answer's word runs found in the paper
POSSIBLE_SCORE = 0.50
SHOW_TOP = 5
DOWNLOAD_THREADS = 10
DOWNLOAD_TIMEOUT_S = 45
SAME_PDF_PAUSE_S = 30               # the site's viewer asks for one PDF in several pieces; handle it once
WATCH_POLL_MS = 500

# The site's backend (read-only requests, sent with the login of the Chrome window)
BACKEND = "https://special-ops-content-backend.penpencil.co/workflows/tev/"
TASKS_URL = BACKEND + "tests/tasks/evaluation?type=pending&programId={program}&search=&page={page}&limit={limit}"
TEST_URL = BACKEND + "v2/tests/{test}/details"
TASKS_PER_PAGE = 100
SAME_SCORE = 0.02                   # papers this close to the best one count as "the same paper on another row"
LIST_NAME = "Question papers list.csv"
NAME_MAX = 75                       # folder names are cut to this, to stay inside Windows' path limit
PATH_MAX = 235                      # whole path of a saved PDF; its file name is cut to fit (Windows allows 259)
FILE_NAME_MIN = 25

log = logging.getLogger("data_filler.match")


# ───────────────────────────── Hooks ─────────────────────────────
# The window app (app.py) replaces these.

def on_bar(done: int, total: int) -> None:
    pass


def on_match(result: dict) -> None:
    """Called after every Model Answer that was compared (the app keeps the best paper's link)."""


# ───────────────────────────── The sheet ─────────────────────────────

@dataclass
class Paper:
    tab: str
    row: int
    file_id: str
    link: str
    batch: str = ""
    test_date: str = ""
    cls: str = ""
    subject: str = ""
    name: str = ""
    date: datetime | None = None

    def label(self) -> str:
        return f"{self.tab} row {self.row} | {self.batch} | {self.test_date} | {self.name}"


@dataclass
class Options:
    sheet: str = SHEET_URL          # Google Sheet link, or an Excel file downloaded from it
    subject: str = ALL
    batch: str = ""                 # part of the batch name; empty = every batch
    days: int | None = None         # only tests of the last N days; None = any date


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT_S) as resp:
        return resp.read()


def sheet_file(source: str) -> Path:
    """The Excel file to read: a fresh download of the Google Sheet, or the file the user chose."""
    source = source.strip().strip('"')
    found = re.search(r"docs\.google\.com/spreadsheets/d/([\w-]+)", source)
    if not found:
        path = Path(source)
        if path.suffix.lower() not in (".xlsx", ".xlsm") or not path.is_file():
            raise ValueError("Give the link of the Google Sheet, or an Excel file (.xlsx) downloaded from it.")
        return path
    try:
        data = fetch(SHEET_EXPORT_URL.format(id=found.group(1)))
        if data[:2] != b"PK":   # Google answered with its sign-in page instead of the file
            raise ValueError("the sheet is not shared with 'Anyone with the link'")
        SHEET_COPY.write_bytes(data)
    except (OSError, ValueError) as exc:
        if not SHEET_COPY.exists():
            raise ValueError(f"Could not download the Google Sheet ({engine.first_line(exc)}). In Google Sheets "
                             "use File > Download > Microsoft Excel and choose that file here.") from exc
        log.warning("Could not download the Google Sheet (%s); using the copy from %s.", engine.first_line(exc),
                    datetime.fromtimestamp(SHEET_COPY.stat().st_mtime).strftime("%d-%b %H:%M"))
    return SHEET_COPY


def parse_date(value) -> datetime | None:
    if isinstance(value, datetime):
        return value
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            pass
    return None


def drive_id(link: str) -> str:
    found = re.search(r"/d/([\w-]{20,})|[?&]id=([\w-]{20,})", link)
    return (found.group(1) or found.group(2)) if found else ""


def read_sheet(path: Path) -> list[Paper]:
    """Every row with a Drive link in 'Copied PDF Link', from every visible tab that has that column."""
    papers: list[Paper] = []
    book = openpyxl.load_workbook(path)
    for ws in book.worksheets:
        if ws.sheet_state != "visible":
            continue
        headers = {engine.normalize(c.value).lower(): i for i, c in enumerate(ws[1]) if c.value}
        if LINK_COLUMN not in headers:
            continue
        for row in ws.iter_rows(min_row=2):
            cell = row[headers[LINK_COLUMN]]
            link = (cell.hyperlink.target if cell.hyperlink and cell.hyperlink.target else "") or str(cell.value or "")
            file_id = drive_id(link)
            if not file_id:
                continue
            raw = {key: row[headers[col]].value if col in headers else None for key, col in COLUMNS.items()}
            date = parse_date(raw["test_date"])
            text = {key: engine.normalize(value) for key, value in raw.items()}
            if date:
                text["test_date"] = date.strftime("%d-%b-%Y")
            papers.append(Paper(tab=ws.title.strip(), row=cell.row, file_id=file_id, link=link.strip(),
                                date=date, **text))
    if not papers:
        raise ValueError("No row with a 'Copied PDF Link' was found in the sheet.")
    return papers


def choose(papers: list[Paper], options: Options) -> list[Paper]:
    subject, batch = options.subject.strip().lower(), options.batch.strip().lower()
    oldest = datetime.now() - timedelta(days=options.days) if options.days else None
    chosen = [p for p in papers
              if (subject in ("", ALL.lower()) or p.subject.lower() == subject)
              and batch in p.batch.lower()
              and (oldest is None or p.date is None or p.date >= oldest)]
    return sorted(chosen, key=lambda p: p.date or datetime.min, reverse=True)


# ───────────────────────────── Reading PDFs ─────────────────────────────

def pdf_text(data: bytes) -> str:
    with fitz.open(stream=data, filetype="pdf") as doc:
        return "\n".join(page.get_text() for page in doc)


def shingles(text: str) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i:i + SHINGLE_WORDS]) for i in range(len(words) - SHINGLE_WORDS + 1)}


def drive_pdf(file_id: str) -> bytes:
    why = "no answer"
    for _ in range(2):              # Drive sometimes fails once and works on the next try
        answered = False
        for url in DRIVE_URLS:
            try:
                data = fetch(url.format(id=file_id))
            except OSError as exc:
                why = why if answered else engine.first_line(exc)
                continue
            if data[:5] == b"%PDF-":
                return data
            answered = True
            why = "Google did not give a PDF (the file is not a PDF, or not shared with 'Anyone with the link')"
        if answered:
            break                   # a clear "no" from Google; asking again will not change it
    raise ValueError(why)


def paper_text(paper: Paper) -> str:
    """The words of one paper, from paper_cache/ or downloaded from Drive (and then remembered)."""
    cached = CACHE_DIR / f"{paper.file_id}.txt"
    if cached.exists():
        return cached.read_text(encoding="utf-8")
    text = pdf_text(drive_pdf(paper.file_id))
    cached.write_text(text, encoding="utf-8")
    return text


def similarity(model: set[str], paper: set[str]) -> float:
    """1.0 = everything in the Model Answer is in the paper (or the other way round, when the site's file
    is the whole paper and the sheet has only a part of it)."""
    if not model or not paper:
        return 0.0
    common = len(model & paper)
    score = common / len(model)
    if len(paper) >= MIN_SHINGLES:
        score = max(score, common / len(paper))
    return score


# ───────────────────────────── Matching ─────────────────────────────

def verdict(score: float) -> str:
    return "MATCH" if score >= MATCH_SCORE else "Possible" if score >= POSSIBLE_SCORE else "Different"


def read_library(papers: list[Paper]) -> tuple[list[tuple[Paper, set[str]]], list[tuple[Paper, str]]]:
    """Read every paper (from paper_cache/ or Drive). Returns (paper, its word runs) for the readable ones,
    and the papers that could not be read with the reason."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    library: list[tuple[Paper, set[str]]] = []
    unread: list[tuple[Paper, str]] = []
    texts: dict[str, str | Exception] = {}     # the same Drive file can be on several rows
    ids = list(dict.fromkeys(p.file_id for p in papers))
    by_id = {p.file_id: p for p in papers}
    with ThreadPoolExecutor(DOWNLOAD_THREADS) as pool:
        jobs = {pool.submit(paper_text, by_id[file_id]): file_id for file_id in ids}
        for done, job in enumerate(as_completed(jobs), start=1):
            if engine.should_stop():
                pool.shutdown(cancel_futures=True)
                raise engine.AbortRun("Stopped by you")
            try:
                texts[jobs[job]] = job.result()
            except Exception as exc:  # noqa: BLE001 - one bad paper must not stop the search
                texts[jobs[job]] = exc
            on_bar(done, len(ids))
            engine.on_status(f"Reading the papers of the sheet: {done} of {len(ids)}")
    for paper in papers:
        text = texts[paper.file_id]
        if isinstance(text, Exception):
            unread.append((paper, engine.first_line(text)))
            continue
        words = shingles(text)
        if len(words) < MIN_SHINGLES:
            unread.append((paper, "no readable text (scanned picture)"))
            continue
        library.append((paper, words))
    return library, unread


def rank(model: set[str], library: list[tuple[Paper, set[str]]]) -> list[tuple[float, Paper]]:
    """(score, paper) for every readable paper, best first."""
    return sorted(((similarity(model, words), paper) for paper, words in library),
                  key=lambda item: item[0], reverse=True)


def find_matches(model_text: str, papers: list[Paper]) -> tuple[list[tuple[float, Paper]], list[tuple[Paper, str]]]:
    """Compare the Model Answer with every paper. Returns (score, paper) best first, and the papers that
    could not be read with the reason."""
    model = shingles(model_text)
    if len(model) < MIN_SHINGLES:
        raise ValueError("This Model Answer has no readable text (it is a scanned picture or almost empty), "
                         "so it cannot be compared automatically.")
    library, unread = read_library(papers)
    return rank(model, library), unread


def write_match_report(model_name: str, scored: list[tuple[float, Paper]], unread: list[tuple[Paper, str]]) -> Path:
    def line(result: str, score: str, p: Paper, note: str = "") -> list:
        return [model_name, result, score, p.tab, p.row, p.batch, p.test_date, p.cls, p.subject, p.name, p.link, note]

    rows = [line(verdict(score), f"{score:.0%}", p) for score, p in scored
            if score >= POSSIBLE_SCORE] or [line(verdict(score), f"{score:.0%}", p) for score, p in scored[:SHOW_TOP]]
    rows += [line("Could not read - check by hand", "", p, why) for p, why in unread]
    with MATCH_REPORT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["Model answer", "Result", "Same text", "Sheet tab", "Sheet row", "Batch", "Test date",
                         "Class", "Subject", "Copied PDF Name", "Copied PDF Link", "Note"])
        writer.writerows(rows)
    return MATCH_REPORT


def compare(model_name: str, model_text: str, papers: list[Paper]) -> dict:
    """Find the paper for one Model Answer, tell the result in the log and the report. Returns the result."""
    log.info("Comparing '%s' with %d papers of the sheet...", model_name, len(papers))
    scored, unread = find_matches(model_text, papers)
    matches = [(score, p) for score, p in scored if score >= MATCH_SCORE]
    possible = [(score, p) for score, p in scored if POSSIBLE_SCORE <= score < MATCH_SCORE]
    for score, p in matches:
        log.info("OK MATCH %.0f%%  %s", score * 100, p.label())
        log.info("      %s", p.link)
    if len({p.file_id for _, p in matches}) > 1:
        log.warning("More than one row has this same paper. Choose the row of your batch and date.")
    for score, p in possible[:SHOW_TOP]:
        log.warning("Possible %.0f%%  %s", score * 100, p.label())
    if not matches:
        log.warning("No paper in the sheet has the same solutions as this Model Answer%s.",
                    " (the closest ones are listed above)" if possible else "")
        if not possible:
            for score, p in scored[:3]:
                log.info("   closest: %.0f%%  %s", score * 100, p.label())
    if unread:
        log.warning("%d papers could not be read and were NOT compared (listed in the report).", len(unread))
    result = {"model": model_name, "matches": matches, "possible": possible, "checked": len(scored),
              "unread": len(unread), "report": write_match_report(model_name, scored, unread)}
    on_match(result)
    return result


def load_papers(options: Options) -> list[Paper]:
    engine.on_status("Reading the sheet...")
    all_papers = read_sheet(sheet_file(options.sheet))
    papers = choose(all_papers, options)
    log.info("The sheet has %d papers; %d fit your choice (subject: %s%s%s).", len(all_papers), len(papers),
             options.subject or ALL, f", batch has '{options.batch}'" if options.batch else "",
             f", last {options.days} days" if options.days else "")
    if not papers:
        raise ValueError("No paper in the sheet fits the subject / batch / dates you chose.")
    return papers


def run_file(model_pdf: str | Path, options: Options) -> dict:
    """Find the paper for a Model Answer PDF saved on this computer."""
    model_pdf = Path(model_pdf)
    model_text = pdf_text(model_pdf.read_bytes())
    return compare(model_pdf.name, model_text, load_papers(options))


# ───────────────────────────── From the site ─────────────────────────────

def is_pdf_response(resp: Response) -> bool:
    if not resp.url.startswith("http") or resp.status not in (200, 206):
        return False
    return "pdf" in resp.headers.get("content-type", "").lower() or urlsplit(resp.url).path.lower().endswith(".pdf")


def site_pdf(context, page, url: str) -> bytes:
    """Download a PDF the site has just shown: plain, then with the site's login, then with Chrome's cookies."""
    try:
        data = fetch(url)
        if data[:5] == b"%PDF-":
            return data
    except OSError:
        pass
    token = page.evaluate("() => localStorage.getItem('access_token')") or ""
    for headers in ({}, {"authorization": token}):
        resp = context.request.get(url, headers=headers, timeout=DOWNLOAD_TIMEOUT_S * 1000)
        if resp.ok and resp.body()[:5] == b"%PDF-":
            return resp.body()
    raise ValueError("the site did not give the file")


def watch_site(options: Options, admin: bool = False) -> dict:
    """Open the site and wait. Every time the user opens a 'Model Answer' there, find its paper in the sheet.
    Runs until Stop. Nothing is clicked or changed on the site."""
    papers = load_papers(options)
    last: dict = {}
    stopped = ""
    with site_session(admin) as (context, page):
        opened: list[str] = []      # PDFs the site has shown, filled by Chrome, handled in the loop below
        context.on("response", lambda resp: opened.append(resp.url) if is_pdf_response(resp) else None)
        handled: dict[str, float] = {}
        try:
            engine.wait_for_manual_login(page)
            log.info("Ready. In the Chrome window open a test and click 'Model Answer'. "
                     "The matching paper will be shown here. Click Stop when you are done.")
            while not engine.should_stop():
                engine.on_status("Waiting: open a test in the Chrome window and click 'Model Answer'...")
                page.wait_for_timeout(WATCH_POLL_MS)
                while opened:
                    url = opened.pop(0)
                    key = url.split("?")[0]
                    if time.monotonic() - handled.get(key, -SAME_PDF_PAUSE_S) < SAME_PDF_PAUSE_S:
                        continue
                    handled[key] = time.monotonic()
                    name = unquote(urlsplit(url).path.rsplit("/", 1)[-1]) or "Model Answer"
                    try:
                        text = pdf_text(site_pdf(context, page, url))
                        if len(shingles(text)) < MIN_SHINGLES:
                            log.info("A PDF without readable text was opened (%s) - a student's copy? Left out.", name)
                            continue
                        last = compare(name, text, papers)
                    except engine.AbortRun:
                        raise
                    except Exception as exc:  # noqa: BLE001 - keep watching
                        log.error("FAIL Could not compare '%s': %s", name, engine.first_line(exc), exc_info=True)
                    opened.clear()      # pieces of the same PDF that arrived while comparing
        except (KeyboardInterrupt, engine.AbortRun) as exc:
            stopped = engine.first_line(exc) if str(exc) else "Stopped by you"
        except PWError:
            if not page.is_closed():
                raise
            stopped = "The Chrome window was closed"
    last["stopped"] = stopped
    return last


@contextmanager
def site_session(admin: bool = False):
    """The app's Chrome window (evaluator or admin login) as (context, page); closed again afterwards."""
    profile = engine.USER_DATA_DIR
    if admin:
        engine.USER_DATA_DIR = engine.ADMIN_USER_DATA_DIR
    try:
        chrome = engine.start_chrome()
    finally:
        engine.USER_DATA_DIR = profile
    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{engine.CDP_PORT}")
        context = browser.contexts[0]
        context.set_default_timeout(engine.DEFAULT_TIMEOUT_MS)
        page = context.pages[0] if context.pages else context.new_page()
        try:
            yield context, page
        finally:
            if chrome is not None:
                engine.close_chrome(browser, chrome, page)


# ───────────────────────────── All pending tests → a folder of question papers ─────────────────────────────

def api_get(page: Page, url: str) -> dict:
    token = page.evaluate("() => localStorage.getItem('access_token')") or ""
    resp = page.request.get(url, headers={"authorization": token}, timeout=DOWNLOAD_TIMEOUT_S * 1000)
    if resp.status in (401, 403):
        raise engine.AbortRun("The site did not accept the login. Log out in the Chrome window (top right), "
                              "log in again and click Start again.")
    if not resp.ok:
        raise RuntimeError(f"The site answered with an error ({resp.status}) for {url.split('?')[0]}")
    return resp.json()


def pending_tests(page: Page, program_id: str) -> dict[str, dict]:
    """Every test that has copies in the Pending list: test id → name, category (batch) and number of copies."""
    tests: dict[str, dict] = {}
    seen = total = 0
    page_no = 1
    while True:
        if engine.should_stop():
            raise engine.AbortRun("Stopped by you")
        answer = api_get(page, TASKS_URL.format(program=program_id, page=page_no, limit=TASKS_PER_PAGE))
        tasks = answer.get("data") or []
        total = (answer.get("paginate") or {}).get("totalCount", total)
        for task in tasks:
            test_id = task.get("testId") or (task.get("testDetails") or {}).get("_id")
            if not test_id:
                continue
            test = tests.setdefault(test_id, {"id": test_id, "copies": 0,
                                              "name": engine.normalize((task.get("testDetails") or {}).get("name")),
                                              "category": engine.normalize((task.get("categoryDetails") or {}).get("name"))})
            test["copies"] += 1
        seen += len(tasks)
        engine.on_status(f"Reading your Pending list: {seen} of {total} copies, {len(tests)} tests so far")
        on_bar(seen, max(total, 1))
        if not tasks or seen >= total:
            return tests
        page_no += 1


def file_url(file) -> str:
    """The address of a file object of the site ({baseUrl, key}); '' when there is none."""
    if isinstance(file, dict) and file.get("key"):
        return (file.get("baseUrl") or "https://static.pw.live/") + file["key"]
    return ""


def site_files(details: dict) -> tuple[list[str], str]:
    """(addresses of the Model Answer PDFs, address of the question paper PDF) of one test."""
    solutions = [file_url(item.get("fileId")) for item in details.get("solutions") or [] if isinstance(item, dict)]
    return [url for url in solutions if url], file_url(details.get("questionFileId"))


def pdf_path(folder: Path, name: str) -> Path:
    """folder/name.pdf with the name cut so that the whole path stays short enough for Windows."""
    while name.lower().endswith(".pdf"):
        name = name[:-4]
    room = max(FILE_NAME_MIN, PATH_MAX - len(str(folder.resolve())) - 5)
    return folder / (safe_name(name, room) + ".pdf")


def safe_name(text: str, limit: int = NAME_MAX) -> str:
    """A text that is allowed as a Windows file / folder name."""
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', " ", text)
    return " ".join(cleaned.split())[:limit].rstrip(". ") or "no name"


def batch_fit(paper: Paper, test: dict) -> float:
    """How much of the sheet's batch name is in the site's batch + test name (to choose between two rows
    that hold the same paper)."""
    batch = set(re.findall(r"\w+", paper.batch.lower()))
    site = set(re.findall(r"\w+", f"{test['category']} {test['name']}".lower()))
    return len(batch & site) / len(batch) if batch else 0.0


def match_test(test: dict, details: dict, library: list[tuple[Paper, set[str]]]) -> dict:
    """Find the sheet's paper for one test of the site. Returns the result with the site's own files."""
    solution_urls, question_url = site_files(details)
    files = {"model answer": [fetch(url) for url in solution_urls],
             "question paper": [fetch(question_url)] if question_url else []}
    best: list[tuple[float, Paper]] = []
    used = ""
    for kind, pdfs in files.items():    # the Model Answer first; the question paper when that gives no match
        words = shingles("\n".join(pdf_text(data) for data in pdfs if data[:5] == b"%PDF-"))
        if len(words) < MIN_SHINGLES:
            continue
        scored = rank(words, library)
        if scored and (not best or scored[0][0] > best[0][0]):
            best, used = scored, kind
        if best and best[0][0] >= MATCH_SCORE:
            break
    result = {"test": test, "files": files, "used": used, "paper": None, "score": best[0][0] if best else 0.0,
              "same": [], "closest": best[:3]}
    if best and best[0][0] >= MATCH_SCORE:
        same = [p for score, p in best if score >= best[0][0] - SAME_SCORE]
        same.sort(key=lambda p: batch_fit(p, test), reverse=True)      # stable: equal fits keep the score order
        result["paper"], result["same"] = same[0], same[1:]
    return result


def save_test(result: dict, out_folder: Path) -> Path:
    """Put the question paper of one test in <folder>/<batch>/<test>/. Returns that folder.
    When the sheet has no paper for it, the site's own question paper and Model Answer are saved instead."""
    test, paper = result["test"], result["paper"]
    folder = out_folder / safe_name(test["category"] or "No batch") / safe_name(test["name"] or test["id"])
    folder.mkdir(parents=True, exist_ok=True)
    if paper:
        target = pdf_path(folder, paper.name or paper.file_id)
        if not target.exists():
            target.write_bytes(drive_pdf(paper.file_id))
        return folder
    for kind, pdfs in result["files"].items():
        for i, data in enumerate(pdfs, start=1):
            pdf_path(folder, f"FROM SITE - {kind}{'' if len(pdfs) == 1 else f' {i}'}").write_bytes(data)
    return folder


def write_list(rows: list[list], out_folder: Path) -> Path:
    header = ["Batch on the site", "Test on the site", "Pending copies", "Result", "Same text", "Sheet tab",
              "Sheet row", "Batch in the sheet", "Test date", "Copied PDF Name", "Copied PDF Link",
              "Same paper also on", "Folder", "Note"]
    target = out_folder / LIST_NAME
    try:
        handle = target.open("w", newline="", encoding="utf-8-sig")
    except PermissionError:     # the old list is open in Excel
        target = out_folder / f"Question papers list {datetime.now():%H-%M-%S}.csv"
        handle = target.open("w", newline="", encoding="utf-8-sig")
    with handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)
    return target


def collect_site(options: Options, out_folder: str | Path, program: str, only: str = "") -> dict:
    """For every test in the evaluator's Pending list: read its Model Answer from the site, find the paper in
    the sheet and save it in out_folder/<batch>/<test>/. `only` = part of a test or batch name, to do less.
    Returns {"counts", "stopped", "report", "folder"}."""
    out_folder = Path(out_folder)
    out_folder.mkdir(parents=True, exist_ok=True)
    papers = load_papers(options)
    rows: list[list] = []
    counts = {"Question paper found": 0, "Not in the sheet (the site's own files were saved)": 0, "Failed": 0}
    stopped = ""
    with site_session() as (context, page):
        try:
            engine.wait_for_manual_login(page)
            engine.open_evaluate(page, program)
            found = re.search(r"/evaluation/([0-9a-f]{24})", page.url)
            if not found:
                raise RuntimeError("Could not see which program is open on the Evaluate page.")
            tests = list(pending_tests(page, found.group(1)).values())
            wanted = only.strip().lower()
            if wanted:
                tests = [t for t in tests if wanted in f"{t['category']} {t['name']}".lower()]
            tests.sort(key=lambda t: (t["category"].lower(), t["name"].lower()))
            log.info("Your Pending list has %d tests%s.", len(tests), f" with '{only.strip()}'" if wanted else "")
            if not tests:
                raise ValueError("No test in your Pending list fits. Nothing to do.")

            log.info("Reading the %d papers of the sheet (slow only the first time)...", len(papers))
            library, unread = read_library(papers)
            if unread:
                log.warning("%d papers of the sheet are scanned pictures / unreadable and cannot be compared.",
                            len(unread))
            for done, test in enumerate(tests, start=1):
                if engine.should_stop():
                    raise engine.AbortRun("Stopped by you")
                title = f"{test['category']} | {test['name']}"
                engine.on_status(f"Finding question papers: {done} of {len(tests)}")
                on_bar(done - 1, len(tests))
                base = [test["category"], test["name"], test["copies"]]
                try:
                    result = match_test(test, api_get(page, TEST_URL.format(test=test["id"])).get("data") or {},
                                        library)
                    folder = save_test(result, out_folder)
                except engine.AbortRun:
                    raise
                except Exception as exc:  # noqa: BLE001 - one bad test must not stop the others
                    counts["Failed"] += 1
                    log.error("FAIL %s: %s", title, engine.first_line(exc), exc_info=True)
                    rows.append(base + ["Failed", "", "", "", "", "", "", "", "", "", engine.first_line(exc)])
                    continue
                paper = result["paper"]
                if paper:
                    counts["Question paper found"] += 1
                    also = "; ".join(f"{p.tab} row {p.row} ({p.batch})" for p in result["same"])
                    log.info("OK %s  →  %s row %d | %s | %s", title, paper.tab, paper.row, paper.batch, paper.name)
                    rows.append(base + ["Found", f"{result['score']:.0%}", paper.tab, paper.row, paper.batch,
                                        paper.test_date, paper.name, paper.link, also, str(folder),
                                        "The same paper is on more rows; the row with the closest batch name "
                                        "was taken" if also else ""])
                else:
                    counts["Not in the sheet (the site's own files were saved)"] += 1
                    closest = "; ".join(f"{score:.0%} {p.tab} row {p.row}" for score, p in result["closest"])
                    why = ("The site has no readable Model Answer or question paper for this test"
                           if not result["used"] else
                           "No readable paper of the sheet has the same text (its paper may be a scanned "
                           f"picture, or missing in the sheet). Closest: {closest}")
                    log.warning("NOT FOUND %s: %s", title, why)
                    rows.append(base + ["Not found - the site's own files were saved", f"{result['score']:.0%}",
                                        "", "", "", "", "", "", "", str(folder), why])
            on_bar(len(tests), len(tests))
        except (KeyboardInterrupt, engine.AbortRun) as exc:
            stopped = engine.first_line(exc) if str(exc) else "Stopped by you"
        except PWError:
            if not page.is_closed():
                raise
            stopped = "The Chrome window was closed"
        finally:
            report = write_list(rows, out_folder) if rows else None
    if stopped:
        log.warning("Stopped: %s", stopped)
    log.info("Finished: %s. Folder: %s", ", ".join(f"{name}: {n}" for name, n in counts.items()), out_folder)
    return {"counts": counts, "stopped": stopped, "report": report, "folder": out_folder}

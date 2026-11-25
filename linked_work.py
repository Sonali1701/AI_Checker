# -*- coding: utf-8 -*-
"""Learner work that is not a typed PDF (School of Innovation): links and other files.

PW Skills learners submit practical assignments as a GitHub or Google Drive link on a
one-page cover PDF, as a Jupyter notebook, an Excel workbook, a Power BI or Tableau
file, or a Google Sheet. MEASURED on the team's samples (21 Oct 2026): every link in
the link-only covers was publicly downloadable, and behind them were full answer PDFs,
notebooks with saved outputs, workbooks with one sheet per question, and a Power BI
model holding the DAX the assignment asked for.

prepare_submission() turns any such upload into ONE PDF: the learner's own cover pages
(if any), then each linked or uploaded file converted to pages the typed reader and the
grader can read (work_document.py). The marked copy is drawn on that PDF, so the
reviewer and the learner see exactly what was marked.

Only these hosts are ever contacted, over HTTPS, with size and file-count limits:
GitHub, Google Drive / Docs / Sheets / Colab, and OneDrive. Nothing is executed:
notebooks are read with the outputs the learner saved, workbooks with the formulas and
values the learner saved.

Imports nothing from pipeline, grader or server.
"""
from __future__ import annotations

import base64
import io
import json
import re
import zipfile
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

MAX_FILE_BYTES = 40 * 1024 * 1024
MAX_TOTAL_BYTES = 80 * 1024 * 1024
MAX_FILES = 12                 # files taken from one repository or folder
MAX_LINKS = 6                  # links opened from one submission
TIMEOUT = (10, 60)
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AI-Checker/1 (assignment files)"
# A cover page whose own text, links removed, is shorter than this holds the learner's
# details and links, not answers (typed_sheets.LINK_ONLY_MAX_CHARS).
COVER_MAX_CHARS = 600

_HOSTS = frozenset({
    "github.com", "raw.githubusercontent.com", "codeload.github.com", "gist.github.com",
    "gist.githubusercontent.com", "objects.githubusercontent.com",
    "drive.google.com", "docs.google.com", "drive.usercontent.google.com",
    "colab.research.google.com",
    "1drv.ms", "onedrive.live.com", "api.onedrive.com",
})
_HOST_SUFFIXES = (".googleusercontent.com", ".1drv.com", ".livefilestore.com",
                  ".sharepoint.com", ".onedrive.live.com")
# Where a sign-in wall sends a private link.
_SIGN_IN_HOSTS = ("accounts.google.com", "login.live.com", "login.microsoftonline.com")

# What is worth marking in a repository or a folder, best first.
NOTEBOOK = (".ipynb",)
DOCUMENTS = (".pdf", ".docx")
SHEETS = (".xlsx", ".xlsm")
BI = (".pbix", ".pbit", ".twb", ".twbx")
CODE = (".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".htm", ".css", ".scss", ".sql",
        ".java", ".c", ".cpp", ".cs", ".go", ".rb", ".php", ".r", ".sh", ".md", ".txt")
IMAGES = (".png", ".jpg", ".jpeg")
DATA = (".csv",)
# A learner's video or recording (the Digital Marketing one-minute project summary, a
# walkthrough of the work): the checker does not watch it, so it is listed for the
# reviewer and never downloaded. PW SKILLS TEAM (22 Oct 2026): DM assignments carry a
# video every time, and a 40 MB video was reported as "larger than the checker reads".
VIDEOS = (".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi", ".wmv", ".3gp", ".mpeg",
          ".mpg", ".mp3", ".m4a", ".wav", ".ogg")
_VIDEO_HOSTS = ("youtube.com", "youtu.be", "loom.com", "vimeo.com")
VIDEO_REASON = "a video"
# A picture this share of a page or larger (a screenshot, a chart) makes a page with
# little text a page of answers, not a cover (typed_sheets.FIGURE_MIN_SHARE).
PICTURE_MIN_SHARE = 0.03
_SKIP_PARTS = ("node_modules/", ".git/", "__pycache__/", "venv/", ".venv/", "dist/",
               "build/", ".next/", "vendor/", ".ipynb_checkpoints/", "site-packages/")
_SKIP_NAMES = ("package-lock.json", "yarn.lock", "license", "license.md", "license.txt")


class LinkError(Exception):
    """A link that could not be opened; the message says why, for the reviewer."""


class VideoLink(LinkError):
    """A link to a video or a recording: listed for the reviewer, never downloaded."""

    def __init__(self, name: str = ""):
        shown = f" ({name})" if name else ""
        super().__init__(f"{VIDEO_REASON}{shown}: the checker does not watch videos, so "
                         "the reviewer marks it")


@dataclass
class LinkedFile:
    name: str
    data: bytes
    url: str                    # the link as the learner gave it


@dataclass
class Prepared:
    data: bytes                 # the PDF to grade
    filename: str
    converted: bool = False     # the PDF was built here, not uploaded as is
    files: list = field(default_factory=list)       # LinkedFile, opened and converted
    problems: list = field(default_factory=list)    # (url, reason) not opened
    link_only: bool = False     # the uploaded PDF held only details and links
    cover_pages: int = 0        # pages of the learner's own PDF in front of the linked work

    @property
    def label_free_pages(self) -> frozenset:
        """Cover pages whose question numbers are not answer headings: a short cover
        (two pages or fewer) in front of linked work lists the questions it answers
        elsewhere ("Q2 ... Q14" on a workbook's cover, the question list on a Power BI
        cover)."""
        if self.files and 0 < self.cover_pages <= 2:
            return frozenset(range(1, self.cover_pages + 1))
        return frozenset()

    @property
    def videos(self) -> list:
        """The learner's video links, which the reviewer watches and marks."""
        return [url for url, reason in self.problems if reason.startswith(VIDEO_REASON)]


def host_allowed(host: str | None) -> bool:
    host = str(host or "").lower().rstrip(".")
    return host in _HOSTS or host.endswith(_HOST_SUFFIXES)


# PW's own file hosts: a link there is a file, whatever its path looks like.
_FILE_HOSTS = frozenset({"cdn.pwskills.com", "static.pw.live"})
# A link straight to a file the checker reads (".../Probability_Basics.xlsx") is opened on
# any public site, not only the share sites above. PW SKILLS (19 Nov 2026): copies whose PDF
# was a "Click to open the Excel file" cover were marked 0 "Not attempted" because their
# link went to a site off that list and was dropped without a word.
_FILE_LINK_ENDINGS = NOTEBOOK + DOCUMENTS + SHEETS + (".xls", ".csv", ".zip") + BI


def _public_host(host: str) -> bool:
    """Whether every address the host has is on the public internet. The checker runs on PW's
    network (VPN), so a learner's link is never let reach an internal machine."""
    import ipaddress
    import socket
    try:
        addresses = {info[4][0].split("%")[0] for info in socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)}
    except (OSError, UnicodeError):
        return False
    try:
        return bool(addresses) and all(ipaddress.ip_address(address).is_global for address in addresses)
    except ValueError:
        return False


def is_file_link(url: str) -> bool:
    """An https link to a file the checker reads, on a site off the share-site list."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower().rstrip(".")
    if parts.scheme != "https" or not host or host_allowed(host):
        return False
    return host in _FILE_HOSTS or unquote(parts.path).lower().endswith(_FILE_LINK_ENDINGS)


def is_video_link(url: str) -> bool:
    """A link to a video site or a video file, known from the link alone."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower().rstrip(".")
    return (any(host == h or host.endswith("." + h) for h in _VIDEO_HOSTS)
            or unquote(parts.path).lower().endswith(VIDEOS))


def _followed(url: str) -> bool:
    """A link the checker opens, or lists for the reviewer as a video."""
    return host_allowed(urlsplit(url).hostname) or is_video_link(url) or is_file_link(url)


# A short cover that sends the reader to work kept elsewhere. MEASURED 19 Nov 2026 (PW
# Skills): "Click to open the Excel file", "Click to open the editable Excel workbook".
_POINTS_ELSEWHERE = re.compile(
    r"\bclick\s+(?:here|to\s+open|to\s+view|to\s+download)\b(?:\s+(?:the|my|this))?(?:\s+[A-Za-z][\w-]*){0,3}"
    r"|\b(?:open|view|download)\s+(?:the|my)\s+(?:editable\s+)?(?:excel|google|workbook|sheet|file|notebook|colab|drive|project|repository|repo)\b"
    r"|\b(?:drive|workbook|sheet|file|notebook|colab|github|repository|project)\s+link\b"
    r"|\blink\s+(?:to|for)\s+(?:the|my)\s+(?:excel|workbook|sheet|file|notebook|project|work|answers?)\b",
    re.IGNORECASE)
NO_LINK_REASON = ("the PDF tells the reader to open the work elsewhere ({said}) but holds no link "
                  "the checker can follow; a link is lost when a document is printed to PDF instead "
                  "of saved or exported as PDF")


def unfollowed_reason(url: str) -> str:
    """Why the checker leaves a link in a learner's PDF unopened."""
    parts = urlsplit(str(url or ""))
    host = (parts.hostname or "").lower() or "an unknown site"
    if parts.scheme != "https":
        return f"the link is not a secure (https) web link ({parts.scheme or 'no scheme'}), so the checker does not open it"
    return (f"{host} is not a site the checker opens, and the link does not lead straight to a "
            "file (GitHub, Google Drive, Docs, Sheets, Colab, OneDrive and direct file links are opened)")


_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)


def _clean(url: str) -> str:
    return str(url or "").strip().rstrip(".,;:!)]}>'\"")


def links_in_pdf(data: bytes) -> list[str]:
    """http(s) links a PDF carries, as link annotations or written out, in page order."""
    import fitz
    found: list[str] = []
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                for link in page.get_links():
                    uri = _clean(link.get("uri") or "")
                    if uri.lower().startswith(("http://", "https://")) and uri not in found:
                        found.append(uri)
                text = page.get_text()
                # A URL the PDF wrapped over two lines is written out in pieces.
                for uri in _URL_RE.findall(text):
                    uri = _clean(uri)
                    if uri not in found and not any(other.startswith(uri) for other in found):
                        found.append(uri)
    except Exception:  # noqa: BLE001 - an unreadable PDF has no links to follow
        return []
    return found


def links_in_text(text: str) -> list[str]:
    found: list[str] = []
    for uri in _URL_RE.findall(str(text or "")):
        uri = _clean(uri)
        if uri not in found:
            found.append(uri)
    return found


def pdf_own_text(data: bytes) -> str:
    """The PDF's visible text with its URLs taken out."""
    import fitz
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            text = " ".join(page.get_text() for page in doc)
    except Exception:  # noqa: BLE001
        return ""
    return " ".join(_URL_RE.sub(" ", text).replace("​", " ").split())


# --- HTTP ------------------------------------------------------------------------------

def _has_pictures(data: bytes) -> bool:
    """Whether a PDF shows a picture of real size on any page (a screenshot, a chart):
    its pages are then answers even when they carry little text. MEASURED 22 Oct 2026:
    a Digital Marketing copy of seven screenshot pages with 589 characters of text was
    taken for a link-only cover and held."""
    import fitz
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page in doc:
                area = abs(page.rect) or 1.0
                for info in page.get_image_info():
                    shown = fitz.Rect(info.get("bbox") or (0, 0, 0, 0)) & page.rect
                    if abs(shown) / area >= PICTURE_MIN_SHARE:
                        return True
    except Exception:  # noqa: BLE001 - an unreadable PDF shows nothing
        return False
    return False


class _Budget:
    def __init__(self, total: int = MAX_TOTAL_BYTES):
        self.left = total
        # Videos found inside a folder: listed for the reviewer as (link, name).
        self.videos: list[tuple[str, str]] = []


def _get(url: str, budget: _Budget, *, accept_html: bool = False,
         any_public: bool = False) -> tuple[bytes, dict, str]:
    """(body, headers, final url) of an HTTPS GET on an allowed host, following
    redirects one hop at a time so every hop is checked against the allowed hosts.
    With any_public (a direct link to a file), any site on the public internet is
    allowed, checked again at every hop."""
    import requests
    current = url
    for _ in range(8):
        parts = urlsplit(current)
        host = (parts.hostname or "").lower()
        if host in _SIGN_IN_HOSTS or host.endswith(_SIGN_IN_HOSTS):
            raise LinkError("the link is not shared publicly (it asks to sign in)")
        if parts.scheme != "https":
            raise LinkError(f"the link leads to {host or 'an unknown site'} without https, "
                            "which the checker does not open")
        if not host_allowed(host):
            if not any_public:
                raise LinkError(f"the link leads to {host or 'an unknown site'}, which the "
                                "checker does not open")
            if not _public_host(host):
                raise LinkError(f"the link leads to {host or 'an unknown site'}, which is not "
                                "on the public internet")
        try:
            response = requests.get(current, stream=True, allow_redirects=False,
                                    timeout=TIMEOUT, headers={"User-Agent": USER_AGENT})
        except requests.RequestException as exc:
            raise LinkError(f"the site did not answer ({type(exc).__name__})") from None
        with response:
            if response.status_code in (301, 302, 303, 307, 308):
                current = urljoin(current, response.headers.get("Location", ""))
                continue
            if response.status_code == 404:
                raise LinkError("the link leads to a page that does not exist (404)")
            if response.status_code in (401, 403):
                raise LinkError("the link is not shared publicly (access denied)")
            if response.status_code == 429:
                raise LinkError("the site refused for now (too many requests); try again later")
            if response.status_code >= 400:
                raise LinkError(f"the site answered with an error ({response.status_code})")
            found = {k.lower(): v for k, v in response.headers.items()}
            name = _filename(found, current, "")
            if (str(found.get("content-type", "")).lower().startswith(("video/", "audio/"))
                    or name.lower().endswith(VIDEOS)):
                raise VideoLink(name)
            limit = min(MAX_FILE_BYTES, budget.left)
            body = bytearray()
            for chunk in response.iter_content(65536):
                body.extend(chunk)
                if len(body) > limit:
                    raise LinkError("the file is larger than the checker reads "
                                    f"({MAX_FILE_BYTES // (1024 * 1024)} MB)")
            budget.left -= len(body)
            headers = {k.lower(): v for k, v in response.headers.items()}
        data = bytes(body)
        if not accept_html and _is_html(data, headers):
            if _signs_in(data):
                raise LinkError("the link is not shared publicly (it asks to sign in)")
            raise LinkError("the link opened a web page, not a file")
        return data, headers, current
    raise LinkError("the link redirects too many times")


def _is_html(data: bytes, headers: dict) -> bool:
    head = data[:600].lstrip().lower()
    return (str(headers.get("content-type", "")).startswith("text/html")
            or head.startswith((b"<!doctype html", b"<html")))


def _signs_in(data: bytes) -> bool:
    text = data[:200000].decode("utf-8", "ignore").lower()
    return any(marker in text for marker in (
        "accounts.google.com/servicelogin", "you need access", "request access",
        "sign in - google accounts", "login.live.com", "you need permission"))


def _filename(headers: dict, url: str, fallback: str) -> str:
    disposition = headers.get("content-disposition", "")
    match = re.search(r"filename\*=UTF-8''([^;]+)", disposition, re.IGNORECASE)
    if match:
        return unquote(match.group(1)).strip().strip('"')
    match = re.search(r'filename="?([^";]+)"?', disposition, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    name = unquote(urlsplit(url).path.rstrip("/").split("/")[-1])
    return name if "." in name else fallback


_READ = NOTEBOOK + DOCUMENTS + SHEETS + BI + CODE + IMAGES + DATA


def _named(name: str, data: bytes) -> str:
    """The file's name with the extension its content shows, when the name has none
    the checker reads. MEASURED 22 Oct 2026: a Colab notebook downloads from Drive
    under its title alone ("Excel Basic"), and was listed as a file not read."""
    if name.lower().endswith(_READ):
        return name
    head = data[:2048].lstrip(b"\xef\xbb\xbf \t\r\n")
    extension = ""
    if head[:5] == b"%PDF-":
        extension = ".pdf"
    elif head[:1] == b"{" and b'"cells"' in data and b'"nbformat"' in data:
        extension = ".ipynb"
    elif head[:8] == b"\x89PNG\r\n\x1a\n":
        extension = ".png"
    elif head[:3] == b"\xff\xd8\xff":
        extension = ".jpg"
    elif data[:2] == b"PK":
        try:
            members = zipfile.ZipFile(io.BytesIO(data)).namelist()
        except zipfile.BadZipFile:
            members = []
        if "xl/workbook.xml" in members:
            extension = ".xlsx"
        elif "word/document.xml" in members:
            extension = ".docx"
        elif "DataModel" in members or any(m.startswith("Report/") for m in members):
            extension = ".pbix"
        elif "DataModelSchema" in members:
            extension = ".pbit"
        elif any(m.lower().endswith(".twb") for m in members):
            extension = ".twbx"
    return name + extension


# --- one link to its files ------------------------------------------------------------

def _drive_id(url: str) -> str:
    parts = urlsplit(url)
    match = re.search(r"/(?:file/d|d|folders)/([A-Za-z0-9_-]{10,})", parts.path)
    if match:
        return match.group(1)
    values = parse_qs(parts.query).get("id")
    return values[0] if values else ""


def _drive_file(file_id: str, url: str, budget: _Budget, name: str = "") -> LinkedFile:
    download = (f"https://drive.usercontent.google.com/download?id={file_id}"
                "&export=download&confirm=t")
    data, headers, final = _get(download, budget)
    return LinkedFile(_named(name or _filename(headers, final, f"drive_{file_id}"), data),
                      data, url)


def _drive_folder(folder_id: str, url: str, budget: _Budget, depth: int = 0) -> list[LinkedFile]:
    page, _, _ = _get(f"https://drive.google.com/embeddedfolderview?id={folder_id}", budget,
                      accept_html=True)
    text = page.decode("utf-8", "replace")
    if _signs_in(page):
        raise LinkError("the folder is not shared publicly (it asks to sign in)")
    entries = re.findall(
        r'href="(https://drive\.google\.com/(?:file/d|drive/folders)/[^"]+)"[^>]*>.*?'
        r'<div class="flip-entry-title">([^<]+)</div>', text, re.S)
    if not entries:
        raise LinkError("the folder is empty or not shared publicly")
    budget.videos += [(url, title.strip()) for _, title in entries
                      if title.strip().lower().endswith(VIDEOS)]
    picked = _pick([(title.strip(), href) for href, title in entries])
    files: list[LinkedFile] = []
    for title, href in picked:
        if "/drive/folders/" in href:
            if depth < 1:
                try:
                    files.extend(_drive_folder(_drive_id(href), url, budget, depth + 1))
                except LinkError:
                    pass
            continue
        files.append(_drive_file(_drive_id(href), url, budget, name=title.strip()))
        if len(files) >= MAX_FILES:
            break
    return files


def _google_doc(url: str, budget: _Budget) -> LinkedFile:
    parts = urlsplit(url)
    file_id = _drive_id(url)
    kind = parts.path.split("/")[1] if parts.path.count("/") > 1 else ""
    query = parse_qs(parts.query)
    if kind == "spreadsheets":
        if "rtpof" in query:            # an uploaded Excel file kept in Drive
            return _drive_file(file_id, url, budget)
        try:
            data, headers, final = _get(
                f"https://docs.google.com/spreadsheets/d/{file_id}/export?format=xlsx", budget)
            return LinkedFile(_filename(headers, final, f"sheet_{file_id}.xlsx"), data, url)
        except LinkError:
            return _drive_file(file_id, url, budget)
    if kind == "document":
        if "rtpof" in query:
            return _drive_file(file_id, url, budget)
        data, headers, final = _get(
            f"https://docs.google.com/document/d/{file_id}/export?format=pdf", budget)
        return LinkedFile(_filename(headers, final, f"document_{file_id}.pdf"), data, url)
    if kind == "presentation":
        data, headers, final = _get(
            f"https://docs.google.com/presentation/d/{file_id}/export/pdf", budget)
        return LinkedFile(_filename(headers, final, f"slides_{file_id}.pdf"), data, url)
    raise LinkError("this kind of Google link is not read by the checker")


def _github(url: str, budget: _Budget) -> list[LinkedFile]:
    parts = urlsplit(url)
    segments = [s for s in parts.path.split("/") if s]
    if parts.hostname == "raw.githubusercontent.com":
        data, headers, final = _get(url, budget)
        return [LinkedFile(_named(_filename(headers, final, "file"), data), data, url)]
    if parts.hostname in ("gist.github.com",):
        if len(segments) < 2:
            raise LinkError("the gist link is incomplete")
        data, _, _ = _get(f"https://gist.github.com/{segments[0]}/{segments[1]}/download", budget)
        return _from_zip(data, url, "")
    if len(segments) < 2:
        raise LinkError("the link is a GitHub profile, not a repository or a file")
    owner, repo = segments[0], segments[1].removesuffix(".git")
    if len(segments) >= 5 and segments[2] in ("blob", "raw"):
        path = "/".join(segments[3:])
        data, headers, final = _get(f"https://raw.githubusercontent.com/{owner}/{repo}/{path}", budget)
        return [LinkedFile(unquote(segments[-1]), data, url)]
    ref, prefix = "HEAD", ""
    if len(segments) >= 4 and segments[2] == "tree":
        ref, prefix = segments[3], "/".join(segments[4:])
    data, _, _ = _get(f"https://codeload.github.com/{owner}/{repo}/zip/{ref}", budget)
    return _from_zip(data, url, prefix)


def _onedrive(url: str, budget: _Budget) -> LinkedFile:
    host = (urlsplit(url).hostname or "").lower()
    if host.endswith(".sharepoint.com"):
        target = url + ("&" if "?" in url else "?") + "download=1"
    else:
        token = base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")
        target = f"https://api.onedrive.com/v1.0/shares/u!{token}/root/content"
    data, headers, final = _get(target, budget)
    return LinkedFile(_named(_filename(headers, final, "onedrive_file"), data), data, url)


def _colab(url: str, budget: _Budget) -> list[LinkedFile]:
    path = urlsplit(url).path
    if path.startswith("/github/"):
        return _github("https://github.com/" + path[len("/github/"):], budget)
    segments = [s for s in path.split("/") if s]
    file_id = segments[1] if len(segments) >= 2 and segments[0] == "drive" else _drive_id(url)
    if not file_id:
        raise LinkError("the Colab link does not name a notebook")
    return [_drive_file(file_id, url, budget)]


def open_link(url: str, budget: _Budget | None = None) -> list[LinkedFile]:
    """The files behind one link. Raises LinkError with a reason a reviewer can act on."""
    budget = budget or _Budget()
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    if is_video_link(url):
        raise VideoLink(unquote(parts.path.rstrip("/").split("/")[-1])
                        if parts.path.lower().endswith(VIDEOS) else "")
    if is_file_link(url):
        data, headers, final = _get(url, budget, any_public=True)
        name = _named(_filename(headers, final, "file"), data)
        if name.lower().endswith(".zip"):
            return _from_zip(data, url, name.rsplit(".", 1)[0])
        return [LinkedFile(name, data, url)]
    if not host_allowed(host):
        raise LinkError(f"{host or 'this site'} is not one the checker opens (GitHub, "
                        "Google Drive, Docs, Sheets, Colab and OneDrive are)")
    if host in ("github.com", "raw.githubusercontent.com", "gist.github.com"):
        return _github(url, budget)
    if host == "colab.research.google.com":
        return _colab(url, budget)
    if host == "docs.google.com":
        return [_google_doc(url, budget)]
    if host == "drive.google.com":
        if "/folders/" in parts.path:
            return _drive_folder(_drive_id(url), url, budget)
        file_id = _drive_id(url)
        if not file_id:
            raise LinkError("the Drive link does not name a file or a folder")
        return [_drive_file(file_id, url, budget)]
    if host in ("1drv.ms", "onedrive.live.com") or host.endswith(".sharepoint.com"):
        return [_onedrive(url, budget)]
    data, headers, final = _get(url, budget)
    return [LinkedFile(_named(_filename(headers, final, "file"), data), data, url)]


# --- choosing files from a repository, a folder or a zip --------------------------------

def _rank(name: str) -> int | None:
    lower = name.lower().replace("\\", "/")
    if any(part in lower for part in _SKIP_PARTS) or lower.rsplit("/", 1)[-1] in _SKIP_NAMES:
        return None
    if lower.endswith((".min.js", ".map", ".lock")):
        return None
    for rank, group in enumerate((NOTEBOOK, BI, SHEETS, DOCUMENTS, CODE, IMAGES, DATA)):
        if lower.endswith(group):
            return rank
    return None


def _pick(entries: list[tuple[str, object]]) -> list[tuple[str, object]]:
    """The entries worth marking, best first, at most MAX_FILES (images at most four,
    data files at most two). Folders (no extension) are kept for one level of descent."""
    ranked = []
    folders = []
    for name, value in entries:
        rank = _rank(name)
        if rank is None:
            if "." not in name.rsplit("/", 1)[-1]:
                folders.append((name, value))
            continue
        ranked.append((rank, name.lower(), name, value))
    ranked.sort()
    out, images, data = [], 0, 0
    for rank, _, name, value in ranked:
        if name.lower().endswith(IMAGES):
            images += 1
            if images > 4:
                continue
        if name.lower().endswith(DATA):
            data += 1
            if data > 2:
                continue
        out.append((name, value))
        if len(out) >= MAX_FILES:
            break
    return out + folders


def _from_zip(data: bytes, url: str, prefix: str) -> list[LinkedFile]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise LinkError("the download is not a readable archive") from None
    members = []
    for info in archive.infolist():
        if info.is_dir() or info.file_size > MAX_FILE_BYTES:
            continue
        # A repository archive puts everything under "<repo>-<ref>/".
        inner = info.filename.split("/", 1)[1] if "/" in info.filename else info.filename
        if prefix and not inner.startswith(prefix.rstrip("/") + "/") and inner != prefix:
            continue
        members.append((inner, info))
    picked = [(name, info) for name, info in _pick(members) if _rank(name) is not None]
    if not picked:
        raise LinkError("the repository has no files the checker reads (notebooks, "
                        "documents, workbooks, Power BI or Tableau files, code)")
    total = 0
    files = []
    for name, info in picked:
        total += info.file_size
        if total > MAX_TOTAL_BYTES:
            break
        files.append(LinkedFile(name, archive.read(info), url))
    return files


# --- the submission ---------------------------------------------------------------------

def _kind(filename: str, data: bytes) -> str:
    name = str(filename or "").lower()
    if data[:5] == b"%PDF-" or b"%PDF-" in data[:1024]:
        return "pdf"
    if name.endswith((".txt", ".url")):
        return "links"
    return "file"


def prepare_submission(data: bytes, filename: str) -> Prepared:
    """The upload as one PDF to grade, with any linked work opened and converted.

    - A typed PDF with no links comes back unchanged.
    - A PDF with GitHub / Drive / Docs / Sheets / Colab / OneDrive links keeps its pages
      and gains the linked work after them. Links that cannot be opened are listed on a
      page of their own with the reason (problems), so the reviewer can follow up.
    - A notebook, workbook, Power BI, Tableau, Word or code file uploaded directly, or a
      .txt / .url file holding links, becomes the converted PDF.
    """
    from work_document import build_document, readable_kind
    kind = _kind(filename, data)
    if kind == "pdf":
        links = links_in_pdf(data)
        urls = [u for u in links if _followed(u)]
        if not urls:
            # A short cover pointing to work kept elsewhere, with no link the checker can
            # open, goes to a person with the reason. Marked as it stood it scored 0
            # "Not attempted" on every question (PW Skills, 19 Nov 2026).
            own = pdf_own_text(data)
            if len(own) <= COVER_MAX_CHARS and not _has_pictures(data):
                said = _POINTS_ELSEWHERE.search(own)
                problems = [(u, unfollowed_reason(u)) for u in links]
                if not problems and said:
                    problems = [("", NO_LINK_REASON.format(said=f'"{said.group(0)}"'))]
                if problems:
                    return Prepared(data, filename, problems=problems, link_only=True,
                                    cover_pages=_page_count(data))
            return Prepared(data, filename)
        own = pdf_own_text(data)
        link_only = len(own) <= COVER_MAX_CHARS and not _has_pictures(data)
        files, problems = open_links(urls)
        if not files and not problems:
            return Prepared(data, filename)
        document = build_document(cover_pdf=data, files=files, problems=problems)
        return Prepared(document, _pdf_name(filename), converted=True, files=files,
                        problems=problems, link_only=link_only, cover_pages=_page_count(data))
    if kind == "links":
        text = data.decode("utf-8", "replace")
        urls = [u for u in links_in_text(text) if _followed(u)]
        files, problems = open_links(urls)
        document = build_document(cover_pdf=None, files=files, problems=problems)
        return Prepared(document, _pdf_name(filename), converted=True, files=files,
                        problems=problems, link_only=True)
    if not readable_kind(filename, data):
        return Prepared(data, filename)
    upload = LinkedFile(str(filename or "submission"), data, "")
    document = build_document(cover_pdf=None, files=[upload], problems=[])
    return Prepared(document, _pdf_name(filename), converted=True, files=[upload])


def _work_key(item: LinkedFile) -> tuple:
    """Two links to one piece of work give one key. A notebook is the same work when its
    cells say the same, whatever outputs and metadata each copy saved. MEASURED 22 Oct
    2026: a learner linked one notebook from Drive and from GitHub, and both were read."""
    if item.name.lower().endswith(NOTEBOOK):
        try:
            cells = json.loads(item.data).get("cells") or []
            sources = "\x00".join("".join(c.get("source") or "") for c in cells
                                  if isinstance(c, dict))
            return ("notebook", item.name.lower().rsplit("/", 1)[-1], hash(sources))
        except (ValueError, AttributeError, TypeError):
            pass
    return ("file", hash(item.data))


def open_links(urls: list[str]) -> tuple[list[LinkedFile], list[tuple[str, str]]]:
    budget = _Budget()
    files: list[LinkedFile] = []
    problems: list[tuple[str, str]] = []
    seen: dict[tuple, int] = {}
    for url in urls[:MAX_LINKS]:
        try:
            for item in open_link(url, budget):
                key = _work_key(item)
                if key in seen:           # the same file linked twice
                    # Of two copies of one notebook, keep the one with more saved output.
                    if len(item.data) > len(files[seen[key]].data):
                        files[seen[key]] = item
                    continue
                seen[key] = len(files)
                files.append(item)
        except LinkError as exc:
            problems.append((url, str(exc)))
        except Exception as exc:  # noqa: BLE001 - one bad link is never the run's failure
            problems.append((url, f"the link could not be read ({type(exc).__name__})"))
    for url in urls[MAX_LINKS:]:
        problems.append((url, f"not opened: the checker opens the first {MAX_LINKS} links"))
    for url, name in budget.videos:
        problems.append((url, str(VideoLink(name))))
    return files, problems


def _page_count(data: bytes) -> int:
    import fitz
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return int(doc.page_count)
    except Exception:  # noqa: BLE001
        return 0


def _pdf_name(filename: str) -> str:
    name = str(filename or "submission")
    return name if name.lower().endswith(".pdf") else name + ".pdf"


def review_notes(prepared: Prepared) -> list[str]:
    """What the reviewer must check by hand on a copy that was marked: the learner's
    videos, and links that could not be opened (the SME review flag the PW Skills team
    asked for, 22 Oct 2026)."""
    notes = [f"The learner's video was not watched by the checker ({url[:90]}). Watch it "
             "and mark the part of the assignment it answers." for url in prepared.videos]
    shut = [(url, reason) for url, reason in prepared.problems
            if not reason.startswith(VIDEO_REASON)]
    if shut:
        shown = "; ".join(f"{url[:90]} ({reason})" for url, reason in shut[:3])
        notes.append(f"A link in the submission could not be opened: {shown}. Check "
                     "whether it holds answers the marks do not cover.")
    return notes


def unopened_reason(prepared: Prepared) -> str:
    """Why a link-only submission whose links could not be opened goes to a reviewer,
    or "" when there is work to mark."""
    if not prepared.link_only or prepared.files or not prepared.problems:
        return ""
    if len(prepared.videos) == len(prepared.problems):
        return ("This submission holds the learner's details and a video "
                f"({prepared.videos[0][:90]}), not written answers. The checker does not "
                "watch videos, so a reviewer watches it and marks it. No marks were given.")
    if all(not url for url, _reason in prepared.problems):
        return (f"This submission is a cover page and its work is elsewhere: {prepared.problems[0][1]}. "
                "Ask the learner for the link or the file itself, or find the work and mark it by "
                "hand. No marks were given.")
    shown = "; ".join(f"{url[:90]} ({reason})" for url, reason in prepared.problems[:3])
    return ("This submission holds the learner's details and links, and the links could not "
            f"be opened: {shown}. Ask the learner to share the work with anyone who has the "
            "link, or open it with a PW account and mark it by hand. No marks were given.")

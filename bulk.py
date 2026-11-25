"""Folder mode: grade thousands of answer sheets from a folder on this computer.

An upload holds about 60 copies (the 300 MB request limit) and keeps every marked
PDF in memory, so 10,000 copies cannot go through the upload route. Here the copies
are read from disk one at a time, each marked copy is written to disk the moment it
is finished, and a progress log records it. Starting again with the same folders
skips every copy already finished, so a crash, a closed laptop or a stopped run
loses at most the copies that were being marked at that moment.

Several computers can share one batch: each takes "part k of n", a fixed split by
file path, and writes its own progress log, so they never mark the same copy and
never write the same file.

Output layout (subfolders of the input are kept):
    to_students/…/evaluated_<name>.pdf
    faculty_review/…/REVIEW_<name>.pdf  and  REVIEW_<name>_reasons.txt
    rejected/…/<name>_reason.txt         not a genuine copy for this paper
    failed/…/<name>_error.txt            could not be marked; retried on the next run
    _reports/…/<name>.json.gz            the graded report + OCR, for a free re-render
    results_part<k>of<n>.csv             one row per copy
    _progress/ledger_part<k>of<n>.jsonl  the resume log (one line per finished copy)
    _progress/split_plan.json            where each multi-student PDF was split

A PDF that holds several students' booklets (PW Vidyapeeth: a scanned stack, each
booklet opening with the printed cover) is planned as one copy per booklet, named
"<file> #01.pdf", "<file> #02.pdf" ... Each booklet's pages are cut from the file
when its turn comes, so the original is never changed and a re-run skips finished
booklets like any other copy.
"""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import os
import threading
import time
from pathlib import Path

EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}
# Seconds of wall time per copy with one worker (measured 66-220 s), and the
# typical cost per copy (measured on the Luna verifier, 2026-09). Estimates only.
SECONDS_PER_COPY = 140
COST_PER_COPY_INR = {"school": 11.0, "professional": 16.0}
# A run of copies failing one after another is an outage (proxy down, sign-in
# ended, disk full), not a run of bad copies. Stop rather than burn the batch.
MAX_CONSECUTIVE_FAILURES = 10


class BulkError(ValueError):
    """A folder setting the operator must fix before the run can start."""


def default_output_dir(input_dir: Path) -> Path:
    return input_dir.parent / f"{input_dir.name} - evaluated"


def resolve_folders(input_folder: str, output_folder: str = "") -> tuple[Path, Path]:
    raw = str(input_folder or "").strip().strip('"')
    if not raw:
        raise BulkError("Enter the folder that holds the answer sheets.")
    source = Path(raw).expanduser()
    if not source.is_dir():
        raise BulkError(f"Folder not found: {source}")
    out_raw = str(output_folder or "").strip().strip('"')
    target = Path(out_raw).expanduser() if out_raw else default_output_dir(source)
    source, target = source.resolve(), target.resolve()
    if target == source:
        raise BulkError("Save the evaluated copies to a different folder from the answer sheets.")
    return source, target


def list_sheets(source: Path, target: Path, extensions=None) -> list[str]:
    """Every answer sheet under `source`, as sorted forward-slash relative paths."""
    found: list[str] = []
    for root, dirs, files in os.walk(source):
        here = Path(root)
        # Never read our own output back in when it sits inside the input folder.
        dirs[:] = sorted(d for d in dirs if not d.startswith((".", "_"))
                         and (here / d).resolve() != target)
        for name in files:
            if name.startswith((".", "~$")):
                continue
            if Path(name).suffix.lower() in (extensions or EXTENSIONS):
                found.append((here / name).relative_to(source).as_posix())
    return sorted(found, key=str.lower)


def check_part(part: int, parts: int) -> tuple[int, int]:
    try:
        part, parts = int(part), int(parts)
    except (TypeError, ValueError) as exc:
        raise BulkError("Machine number and number of machines must be whole numbers.") from exc
    if parts < 1 or parts > 50 or not 1 <= part <= parts:
        raise BulkError("Machine number must be between 1 and the number of machines (at most 50).")
    return part, parts


def in_part(rel: str, part: int, parts: int) -> bool:
    """A fixed split by path, the same on every computer."""
    if parts <= 1:
        return True
    digest = hashlib.sha1(rel.lower().encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % parts == part - 1


def _ledger_dir(target: Path) -> Path:
    return target / "_progress"


def _ledger_entries(target: Path):
    """Every ledger line of every computer, oldest first within each ledger."""
    folder = _ledger_dir(target)
    if not folder.is_dir():
        return
    for ledger in sorted(folder.glob("ledger_*.jsonl")):
        try:
            with open(_os_path(ledger), encoding="utf-8") as handle:
                lines = handle.read().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                entry = json.loads(line)
            except ValueError:
                continue  # a line cut short by a crash
            if isinstance(entry, dict) and entry.get("source"):
                yield entry


def finished_copies(target: Path) -> dict[str, dict]:
    """Last entry per source across every computer's ledger. A copy that FAILED is
    not finished: it is tried again on the next run."""
    done: dict[str, dict] = {}
    for entry in _ledger_entries(target):
        done[entry["source"]] = entry
    return {src: e for src, e in done.items() if e.get("state") != "failed"}


def expand_sheets(source: Path, target: Path, rels: list[str], splitter) -> tuple[list[str], dict]:
    """Every PDF the splitter finds several booklets in, as one copy per booklet.

    splitter(pdf_bytes, rel) -> [segment with .first, .last, .problem] or [] for a
    single booklet. Plans are kept in _progress/split_plan.json by size and date, so
    a folder is scanned once. Returns (copies, {copy: {base, first, last, problem}})."""
    from vidyapeeth_intake import segment_name
    cache_path = _ledger_dir(target) / "split_plan.json"
    try:
        with open(_os_path(cache_path), encoding="utf-8") as handle:
            cache = json.load(handle)
    except (OSError, ValueError):
        cache = {}
    out: list[str] = []
    segments: dict[str, dict] = {}
    changed = False
    for rel in rels:
        if not rel.lower().endswith(".pdf"):
            out.append(rel)
            continue
        path = source / rel
        try:
            stat = os.stat(_os_path(path))
        except OSError:
            out.append(rel)
            continue
        key = f"{stat.st_size}:{int(stat.st_mtime)}"
        entry = cache.get(rel)
        if not isinstance(entry, dict) or entry.get("key") != key:
            with open(_os_path(path), "rb") as handle:
                found = splitter(handle.read(), rel)
            entry = {"key": key, "segments": [[s.first, s.last, s.problem] for s in found]}
            cache[rel] = entry
            changed = True
        if not entry["segments"]:
            out.append(rel)
            continue
        for number, (first, last, problem) in enumerate(entry["segments"], 1):
            name = segment_name(rel, number)
            out.append(name)
            segments[name] = {"base": rel, "first": first, "last": last, "problem": problem}
    if changed:
        _write_atomic(cache_path, json.dumps(cache, indent=1).encode("utf-8"))
    return sorted(out, key=str.lower), segments


def plan(source: Path, target: Path, part: int, parts: int, splitter=None,
         extensions=None) -> dict:
    """What a run would do, without doing it. `extensions`: the answer-sheet file
    types the category takes (School of Innovation also reads notebooks, workbooks,
    Power BI and Tableau files); PDF and photos when not given."""
    everything = list_sheets(source, target, extensions)
    segments: dict[str, dict] = {}
    if splitter is not None:
        everything, segments = expand_sheets(source, target, everything, splitter)
    mine = [rel for rel in everything if in_part(rel, part, parts)]
    done = finished_copies(target)
    todo = [rel for rel in mine if rel not in done]
    return {"source": str(source), "target": str(target), "found": len(everything),
            "this_part": len(mine), "already_done": len(mine) - len(todo), "todo": todo,
            "segments": segments}


def estimate(todo: int, workers: int, exam_type: str) -> dict:
    workers = max(1, workers)
    rate = COST_PER_COPY_INR.get(exam_type, COST_PER_COPY_INR["school"])
    return {"hours": round(todo * SECONDS_PER_COPY / workers / 3600, 1),
            "cost_inr": round(todo * rate)}


def _stem(rel: str) -> tuple[str, str]:
    path = Path(rel)
    parent = "" if str(path.parent) in ("", ".") else path.parent.as_posix()
    return parent, path.stem


def _os_path(path: Path) -> str:
    """The path as Windows can open it beyond 260 characters.

    MEASURED 2026-10-09 (CA folder run): the output
    "<folder> - evaluated/faculty_review/REVIEW_<uuid>.pdf.part" was 274 characters
    long, so every graded copy of six folders raised FileNotFoundError when it was
    filed. The copy was paid for and graded, then lost. Teachers keep batches in deep
    Downloads folders, so an absolute path past the limit gets the extended prefix.
    """
    text = str(path)
    if os.name != "nt" or text.startswith(_LONG_PREFIX):
        return text
    full = os.path.abspath(text)
    if len(full) < 240:
        return text
    if full.startswith("\\\\"):
        return _LONG_PREFIX + "UNC\\" + full[2:]
    return _LONG_PREFIX + full


_LONG_PREFIX = "\\\\?\\"


def _write_atomic(path: Path, data: bytes) -> None:
    os.makedirs(_os_path(path.parent), exist_ok=True)
    temp = path.with_name(path.name + ".part")
    with open(_os_path(temp), "wb") as handle:
        handle.write(data)
    os.replace(_os_path(temp), _os_path(path))


class FolderSink:
    """Files each finished copy on disk, then commits it to the progress log."""

    _CSV_FIELDS = ["source", "state", "score", "max", "percent", "cost_inr", "output",
                   "reasons", "finished_at"]

    def __init__(self, source: Path, target: Path, part: int, parts: int,
                 segments: dict | None = None):
        self.source, self.target = source, target
        self.tag = f"part{part}of{parts}"
        self._lock = threading.Lock()
        self._seen: dict[str, str] = {}
        # "<subfolder>/<file name>" -> the copy it was given to (copy_name), read
        # from the ledgers when first needed so a resumed run keeps every name.
        self._names: dict[str, str] | None = None
        # "<file> #NN.pdf" -> {base, first, last, problem} (expand_sheets).
        self.segments = dict(segments or {})
        self._base: tuple[str, bytes] | None = None
        target.mkdir(parents=True, exist_ok=True)
        _ledger_dir(target).mkdir(parents=True, exist_ok=True)
        self.ledger = _ledger_dir(target) / f"ledger_{self.tag}.jsonl"
        self.csv = target / f"results_{self.tag}.csv"

    def claim(self, rel: str, data: bytes) -> str:
        """The copy this file duplicates in this run, or "" (and it is now claimed)."""
        digest = hashlib.sha256(data).hexdigest()
        with self._lock:
            earlier = self._seen.setdefault(digest, rel)
        return "" if earlier == rel else earlier

    def load(self, rel: str) -> bytes:
        segment = self.segments.get(rel)
        if segment is not None:
            return self._load_segment(segment)
        path = (self.source / rel).resolve()
        if self.source not in path.parents:
            raise BulkError(f"{rel} is outside the answer-sheet folder.")
        with open(_os_path(path), "rb") as handle:
            return handle.read()

    def segment_problem(self, rel: str) -> str:
        """Why a person must look at this booklet before it is graded, or ""."""
        return str((self.segments.get(rel) or {}).get("problem") or "")

    def _load_segment(self, segment: dict) -> bytes:
        from vidyapeeth_intake import Segment, segment_pdf
        base = segment["base"]
        with self._lock:
            cached = self._base if self._base and self._base[0] == base else None
        if cached is None:
            path = (self.source / base).resolve()
            if self.source not in path.parents:
                raise BulkError(f"{base} is outside the answer-sheet folder.")
            with open(_os_path(path), "rb") as handle:
                cached = (base, handle.read())
            with self._lock:
                self._base = cached       # booklets of one file come one after another
        return segment_pdf(cached[1], Segment(int(segment["first"]), int(segment["last"])))

    def copy_name(self, rel: str, info: dict | None) -> str:
        """The copy's file name: the student's name and roll number when its cover
        was read (result_export.copy_stem), else the source file's name. A name
        another copy in the same subfolder already has gets _2, _3."""
        from result_export import copy_stem
        parent, stem = _stem(rel)
        wanted = copy_stem(info, stem)
        if wanted == stem:
            return stem
        with self._lock:
            if self._names is None:
                self._names = {}
                for entry in _ledger_entries(self.target):
                    if entry.get("stem"):
                        key = f"{_stem(entry['source'])[0]}/{entry['stem']}".casefold()
                        self._names.setdefault(key, entry["source"])
            candidate, n = wanted, 1
            while self._names.get(f"{parent}/{candidate}".casefold(), rel) != rel:
                n += 1
                candidate = f"{wanted}_{n}"
            self._names[f"{parent}/{candidate}".casefold()] = rel
        return candidate

    def file(self, rel: str, result: dict, pdf: bytes | None, saved: dict | None) -> str:
        """Write the copy's files, then its ledger line. Returns the output path."""
        parent, source_stem = _stem(rel)
        stem = self.copy_name(rel, result.get("student_info"))
        state = result.get("state") or ("released" if result.get("release") else "failed")
        reasons = [str(r) for r in (result.get("review_reasons") or [])]
        error = str(result.get("error") or "")

        def under(folder: str, name: str) -> Path:
            return self.target / folder / parent / name

        output: Path | None = None
        if result.get("release") is True and pdf:
            output = under("to_students", f"evaluated_{stem}.pdf")
            _write_atomic(output, pdf)
        elif state == "faculty_review":
            text = "\n".join(reasons) or error or "Automatic verification asked for a review."
            if pdf:
                output = under("faculty_review", f"REVIEW_{stem}.pdf")
                _write_atomic(output, pdf)
            note = under("faculty_review", f"REVIEW_{stem}_reasons.txt")
            _write_atomic(note, text.encode("utf-8"))
            output = output or note
        elif state == "rejected":
            output = under("rejected", f"{stem}_reason.txt")
            _write_atomic(output, (error or "Not a genuine answer sheet for this paper.").encode("utf-8"))
        else:
            state = "failed"
            output = under("failed", f"{stem}_error.txt")
            _write_atomic(output, (error or "Automatic grading failed.").encode("utf-8"))
        if saved:
            blob = gzip.compress(json.dumps(saved).encode("utf-8"))
            _write_atomic(under("_reports", f"{source_stem}.json.gz"), blob)

        cost = (result.get("cost") or {}).get("total_inr")
        row = {
            "source": rel, "state": state,
            "score": result.get("score", ""), "max": result.get("max", ""),
            "percent": result.get("percent", ""),
            "cost_inr": round(float(cost), 2) if isinstance(cost, (int, float)) else "",
            "output": output.relative_to(self.target).as_posix() if output else "",
            "reasons": " | ".join(reasons) or error,
            "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self._lock:
            new_csv = not os.path.exists(_os_path(self.csv))
            with open(_os_path(self.csv), "a", newline="", encoding="utf-8-sig") as handle:
                writer = csv.DictWriter(handle, fieldnames=self._CSV_FIELDS)
                if new_csv:
                    writer.writeheader()
                writer.writerow(row)
            # The ledger line is the commit: written last, flushed to disk.
            with open(_os_path(self.ledger), "a", encoding="utf-8") as handle:
                handle.write(json.dumps(dict(row, stem=stem)) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        return row["output"]

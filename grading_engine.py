# -*- coding: utf-8 -*-
"""Grade one copy, here or in worker processes, so a folder of 1,000 copies takes an
hour or two instead of a working day.

MEASURED 2026-10-05 on three folder runs of the 27 Sept batch (2.6 pages a copy):
92 to 119 copies an hour, so 1,000 copies took 8 to 11 hours. A copy spends about
140 seconds waiting on the model, which more copies at a time hides. It also needs
about 5 seconds of this computer's own work per page: page images, the evaluated
PDF, the release check, and on 93 percent of copies a second layout pass. In one
process all of that queues behind one render lock and one Python interpreter, so
past about 8 copies at a time more workers only made every copy wait longer.

The engine starts several worker processes. Each grades a few copies at a time, and
all of them take copies from one queue, so a long booklet never holds up the rest.
The main process keeps everything that must be shared: reading and claiming copies,
the job record, the output folder and its progress log. A worker receives a copy's
bytes and returns the grading result. Nothing in the grading path carries over
from one copy to the next, but two things belong to the process and are handed over
(_handover): the proxy pass from the operator's sign-in, and the key that signs the
marks graph proof.

MEASURED 2026-10-06, the first live run of build 82 (three NEEV 27 Sept folders): 466
copies went to faculty review with no PDF, "The question/maxima proof was not issued
for this exact paper, total, and graph". exam_package makes its signing key at import,
so every worker had its own key and refused the main process's proof before grading.
Nothing was charged. A worker now takes the main process's key, and checks the paper
before it says it is ready: one that judges it differently is not used at all.
"""
from __future__ import annotations

import pdf_thread_guard

pdf_thread_guard.install()   # a worker process starts here, before anything imports fitz

import math
import multiprocessing
import os
import pickle
import queue
import sys
import threading
import time
import traceback
import uuid
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout

import pw_access
from strict_grading import StrictGradeError

# A digital PDF yields roughly 1,500-3,000 characters of text per page; a scan yields
# almost none. Same threshold pipeline._prefer_text_over_images uses to decide a text
# layer really covers the pages.
DIGITAL_CHARS_PER_PAGE = 600

# Up to this many copies at a time everything stays in one process, as it always was.
SINGLE_PROCESS_UP_TO = 4
# Beyond it, one worker process for every few copies at a time. Five keeps each
# process's render lock busy about half the time on a typical copy.
COPIES_PER_PROCESS = 5
# MEASURED 2026-10-05 (36 saved copies, 5 at a time per worker): a worker peaked at
# 0.5 to 0.9 GB, and with 6 workers this 16 GB laptop fell to 0.5 GB free and ran a
# third slower than with memory to spare; 8 workers were slower than 6. So one
# worker for every 2.5 GB of the computer's memory, and never more than 6.
MAX_PROCESSES = 6
GB_PER_PROCESS = 2.5
# A worker is replaced by a fresh one after this many copies, so memory the PDF and
# image libraries keep after a large booklet cannot pile up over a 1,000 copy run.
COPIES_PER_WORKER = 40
# A worker that dies (out of memory, a crash in the PDF library) is replaced this many
# times in one run; after that the run carries on with the workers it has left.
MAX_RESTARTS = 4
# Seconds to wait for the workers to load the grading code before giving up on them
# and grading in this process instead.
READY_TIMEOUT = 120
# A copy normally takes 2 to 8 minutes. One that has not come back in an hour was
# lost with its worker and is failed, so the run can finish and retry it next time.
COPY_TIMEOUT = 3600

# The fields of grade_sheet's result the main process reads (server.grade_one). Only
# these cross back from a worker.
RESULT_KEYS = ("report", "cost", "pdf_bytes", "issues", "blocking", "release",
               "ocr_pages", "verification_complete", "verification_qids",
               "layout_repaired", "budget", "answer_map", "package_id")


def is_digital_pdf(data: bytes, filename: str) -> bool:
    """True when this file carries a real text layer, so it was generated, not photographed.

    A generated PDF is upright in its own coordinate space by construction — there is
    no camera angle to correct — so spending a model call to ask which way up it is
    buys nothing. Students photograph their sheets and genuinely need the probe; the
    question paper and the answer key are digital almost every time.
    """
    if not (filename or "").lower().endswith(".pdf"):
        return False
    try:
        import fitz
        from grader import pdf_text_layer
        with fitz.open(stream=data, filetype="pdf") as doc:
            pages = doc.page_count
        return pages > 0 and len(pdf_text_layer(data, filename).strip()) >= (
            DIGITAL_CHARS_PER_PAGE * pages)
    except Exception:
        return False


def make_orienter(cfg, usage_out: dict | None = None, is_digital=None):
    """A function that turns one uploaded file's pages the right way up."""
    from grader import ORIENTATION_MODEL, orientation_probe
    from orient import normalize_orientation

    digital = is_digital or is_digital_pdf

    def orient(data: bytes, filename: str) -> bytes:
        def probe(pages):
            return orientation_probe(
                pages,
                model=ORIENTATION_MODEL,
                google_token=cfg.google_token,
                task_id=cfg.task_id,
                filename=filename,
                usage_out=usage_out,
            )

        # MEASURED on the CA Inter Taxation set: the question paper is 14 pages and the
        # answer key 21, both digital (34,723 and 39,482 characters of text), and both
        # were rasterised at 150 dpi and sent to the orientation model on every single
        # copy — 35 page renders and 2 model calls per student, to straighten pages that
        # were never crooked and that the grader then reads as TEXT, not images.
        # normalize_orientation falls back to its free local heuristic when probe is
        # None, so a rotated digital page is still handled; it just costs nothing.
        # Scanned student pages need the four-way visual probe even in strict mode.
        # The free projection heuristic rotated two already-upright ruled notebook
        # pages (270 and 180 degrees) in a released copy. Digital paper/key PDFs stay
        # free because their text layer proves the page coordinate system is upright.
        use_probe = None if digital(data, filename) else probe
        try:
            # A photo of the OPEN notebook stays one page with one strip. Cutting it
            # at the fold went wrong whenever the fold was misjudged: Sweety's
            # Physics spread (2026-09-30) was cut at 34% of its width, through the
            # left page, and "According to gravitational force" was split across two
            # evaluated pages. The OCR reads such a page left page first instead
            # (mathpix.order_spread_lines), which is all the split was for.
            return normalize_orientation(data, filename, probe=use_probe)[0]
        except Exception:
            return data

    return orient


def grade_copy(batch: dict, name: str, sa_bytes: bytes, stage_callback=None, *,
               orienter=None, grade=None) -> dict:
    """Straighten one copy and grade it against the batch's paper and key.

    ``batch`` holds what is the same for every copy (server._run_job builds it).
    ``orienter`` and ``grade`` default to the real ones; the server passes its own
    names, so a test that fakes them fakes this path too.
    """
    if grade is None:
        from pipeline import grade_sheet as grade
    usage: dict = {}
    oriented = (orienter or make_orienter)(batch["cfg"], usage)(sa_bytes, name)
    return grade(
        qp_pngs=batch["qp_pngs"],
        sa_bytes=oriented,
        filename=name,
        ak_pngs=batch["ak_pngs"],
        ak_text=batch["ak_text"],
        marks_scheme=batch["marks_scheme"],
        cfg=batch["cfg"],
        student_class=batch["student_class"],
        subject=batch["subject"],
        use_mathpix=True,
        usd_to_inr=batch["rate"],
        total_marks=batch["total_marks"],
        qp_text=batch["qp_text"],
        ak_text_layer=batch["ak_text_layer"],
        stage_callback=stage_callback,
        prior_usage=usage,
    )


def total_memory_gb() -> float:
    """This computer's installed memory, or 0 when it cannot be read."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong),
                            ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong),
                            ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

            status = MemoryStatus()
            status.dwLength = ctypes.sizeof(MemoryStatus)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return status.ullTotalPhys / 2 ** 30
            return 0.0
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2 ** 30
    except Exception:  # noqa: BLE001 - unknown memory: plan on the cores alone
        return 0.0


_ACTIVE_LOCK = threading.Lock()
# Worker processes of every folder run open in this app right now.
_ACTIVE_PROCESSES = 0


def active_processes() -> int:
    with _ACTIVE_LOCK:
        return _ACTIVE_PROCESSES


def _count_processes(delta: int) -> None:
    global _ACTIVE_PROCESSES
    with _ACTIVE_LOCK:
        _ACTIVE_PROCESSES = max(0, _ACTIVE_PROCESSES + delta)


def plan_processes(workers: int, cpus: int | None = None,
                   memory_gb: float | None = None, busy: int | None = None) -> int:
    """Worker processes for ``workers`` copies at a time; 1 means grade in this process.

    The computer's budget is shared by every folder run open at once (``busy``, the
    processes they already hold). MEASURED 2026-10-06: three folders started together
    each took a full set, 15 processes on a 16 GB laptop, and the runs slowed while
    their connections dropped. AICHECKER_ENGINE_PROCESSES overrides the plan (1 turns
    the engine off).
    """
    workers = max(1, int(workers or 1))
    forced = str(os.environ.get("AICHECKER_ENGINE_PROCESSES") or "").strip()
    if forced:
        try:
            return max(1, min(int(forced), workers))
        except ValueError:
            pass
    if workers <= SINGLE_PROCESS_UP_TO:
        return 1
    cpus = cpus or os.cpu_count() or 2
    memory_gb = total_memory_gb() if memory_gb is None else memory_gb
    by_memory = int(memory_gb // GB_PER_PROCESS) if memory_gb else MAX_PROCESSES
    busy = active_processes() if busy is None else busy
    # Two cores stay free for the main process, which files every result, and for
    # whatever else the computer is doing.
    budget = min(cpus - 2, by_memory, MAX_PROCESSES) - max(0, busy)
    return max(1, min(math.ceil(workers / COPIES_PER_PROCESS), budget))


class EngineError(RuntimeError):
    """A copy failed inside a worker process. The message is the worker's own, so the
    results table reads exactly as it would have in one process."""


class EnginePool:
    """``processes`` worker processes, each grading ``threads`` copies at a time.

    grade() blocks the calling thread until its copy is graded, so the server keeps
    one thread per copy in flight, exactly as before, and only the work moves. This
    process hands each copy to one named worker with a free slot, so when a worker
    dies it is known exactly which copies went with it.
    """

    def __init__(self, batch: dict, processes: int, threads: int, *, grade=None,
                 log=None, copies_per_worker: int = COPIES_PER_WORKER):
        self._batch = batch
        self._threads = max(1, int(threads))
        self._copies_per_worker = max(1, int(copies_per_worker))
        # A module-level function, so a worker can import it; None is grade_copy.
        self._grade = grade
        self._log = log or (lambda line: print(line, flush=True))
        self._mp = multiprocessing.get_context("spawn")
        self._events = self._mp.Queue()
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        # task -> (future, stage callback, copy name, worker id)
        self._waiting: dict[str, tuple[Future, object, str, int]] = {}
        # worker id -> {"proc", "queue", "tasks"}
        self._workers: dict[int, dict] = {}
        # Workers that reached COPIES_PER_WORKER and were told to stop.
        self._retired: list[dict] = []
        self._ready: set[int] = set()
        # Workers that judged the paper differently from this process (_handover).
        self._refused: set[int] = set()
        self._next_id = 0
        self._restarts = 0
        self._closed = False
        self._handover = _handover(batch)
        self._counted = max(1, int(processes))
        _count_processes(self._counted)
        for _ in range(self._counted):
            self._spawn()
        self._reader = threading.Thread(target=self._read_events, name="engine-events",
                                        daemon=True)
        self._reader.start()
        self._watcher = threading.Thread(target=self._watch, name="engine-watch",
                                         daemon=True)
        self._watcher.start()

    # -- workers --------------------------------------------------------------
    def _spawn(self) -> None:
        worker_id = self._next_id
        self._next_id += 1
        tasks = self._mp.Queue()
        proc = self._mp.Process(
            target=_worker_main,
            args=(worker_id, self._threads, self._batch, self._handover, tasks,
                  self._events, self._grade),
            name=f"grading-engine-{worker_id}", daemon=True)
        proc.start()
        self._workers[worker_id] = {"proc": proc, "queue": tasks, "tasks": set(),
                                    "given": 0, "retiring": False}

    @property
    def processes(self) -> int:
        with self._lock:
            return len(self._workers)

    def wait_ready(self, timeout: float = READY_TIMEOUT) -> int:
        """Wait until every worker has loaded the grading code and agreed about the
        paper; returns how many did."""
        end = time.monotonic() + timeout
        with self._lock:
            while (not self._closed
                   and not set(self._workers) <= self._ready | self._refused):
                left = end - time.monotonic()
                if left <= 0:
                    break
                self._changed.wait(min(left, 1.0))
            return len(self._ready & set(self._workers))

    # -- grading --------------------------------------------------------------
    def grade(self, name: str, sa_bytes: bytes, stage_callback=None) -> dict:
        """Grade one copy in a worker; raises what the worker raised."""
        task = uuid.uuid4().hex
        future: Future = Future()
        with self._lock:
            waiting_since = time.monotonic()
            while True:
                if self._closed or not self._workers:
                    raise EngineError("The grading processes have stopped. Start the run "
                                      "again: finished copies are skipped.")
                # Only a worker that has loaded the grading code gets a copy, so one
                # that hangs while starting never sits on copies.
                free = [(len(w["tasks"]), wid) for wid, w in self._workers.items()
                        if wid in self._ready and not w["retiring"]
                        and len(w["tasks"]) < self._threads]
                if free:
                    break
                if (not self._ready & set(self._workers)
                        and time.monotonic() - waiting_since > READY_TIMEOUT):
                    raise EngineError("No grading process is ready. Start the run again: "
                                      "finished copies are skipped.")
                # Every slot is taken, which happens only while a dead worker is
                # being replaced; wait for one to come free.
                self._changed.wait(1.0)
            worker_id = min(free)[1]
            worker = self._workers[worker_id]
            worker["tasks"].add(task)
            worker["given"] += 1
            # Its last copy: it takes no more and is replaced once this one is back.
            worker["retiring"] = worker["given"] >= self._copies_per_worker
            self._waiting[task] = (future, stage_callback, name, worker_id)
        try:
            worker["queue"].put((task, name, sa_bytes))
            return future.result(timeout=COPY_TIMEOUT)
        except FutureTimeout:
            error = EngineError(
                f"No result for this copy after {COPY_TIMEOUT // 60} minutes. It will be "
                "tried again on the next run.")
            self._finish(task, error=error)
            raise error from None

    def _finish(self, task: str, result=None, error: BaseException | None = None) -> None:
        retire = None
        with self._lock:
            entry = self._waiting.pop(task, None)
            if entry is not None:
                worker = self._workers.get(entry[3])
                if worker is not None:
                    worker["tasks"].discard(task)
                    if worker["retiring"] and not worker["tasks"] and not self._closed:
                        retire = worker
                        # Replaced now, so its slots are back while it shuts down.
                        self._workers.pop(entry[3])
                        self._retired.append(worker)
                        self._spawn()
                self._changed.notify_all()
        if retire is not None:
            for _ in range(self._threads):
                try:
                    retire["queue"].put(None)
                except (OSError, ValueError):
                    break
        if entry is None or entry[0].done():
            return
        if error is not None:
            entry[0].set_exception(error)
        else:
            entry[0].set_result(result)

    def _read_events(self) -> None:
        while True:
            try:
                event = self._events.get()
            except (EOFError, OSError, ValueError):
                return
            if event is None:
                return
            kind = event[0]
            if kind == "log":
                self._log(event[1])
            elif kind == "ready":
                with self._lock:
                    self._ready.add(event[1])
                    self._changed.notify_all()
            elif kind == "refused":
                self._log(f"[engine] grading process {event[1]} not used: {event[2]}")
                with self._lock:
                    self._refused.add(event[1])
                    self._changed.notify_all()
            elif kind == "stage":
                with self._lock:
                    entry = self._waiting.get(event[1])
                callback = entry[1] if entry else None
                if callback is not None:
                    try:
                        callback(*event[2])
                    except Exception:  # noqa: BLE001 - progress display, never a result
                        pass
            elif kind == "done":
                try:
                    result = pickle.loads(event[2])
                except Exception as exc:  # noqa: BLE001 - a result we cannot read fails the copy
                    self._finish(event[1], error=EngineError(
                        f"The graded result could not be read: {exc}"))
                else:
                    self._finish(event[1], result=result)
            elif kind == "error":
                info = event[2]
                if not info.get("strict"):
                    with self._lock:
                        entry = self._waiting.get(event[1])
                    name = entry[2] if entry else event[1]
                    self._log(f"[engine] {name}: {info.get('kind')}: {info.get('message')}\n"
                              f"{info.get('trace') or ''}".rstrip())
                self._finish(event[1], error=_rebuild(info))

    def _watch(self) -> None:
        while True:
            time.sleep(1)
            with self._lock:
                if self._closed:
                    return
                dead = [(wid, w) for wid, w in self._workers.items()
                        if not w["proc"].is_alive()]
                for wid, _worker in dead:
                    self._workers.pop(wid)
            for wid, worker in dead:
                lost = list(worker["tasks"])
                for task in lost:
                    self._finish(task, error=EngineError(_LOST_MESSAGE))
                with self._lock:
                    refused = wid in self._refused
                if refused:
                    continue  # a fresh one would judge the paper the same way
                self._log(f"[engine] grading process {wid} stopped (exit code "
                          f"{worker['proc'].exitcode}) holding {len(lost)} copy(ies); "
                          "they will be tried again on the next run")
                with self._lock:
                    if not self._closed and self._restarts < MAX_RESTARTS:
                        self._restarts += 1
                        self._spawn()
                    self._changed.notify_all()

    def close(self, timeout: float = 30.0) -> None:
        """Let the workers finish what they hold, then stop them."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            workers = list(self._workers.values())
            self._changed.notify_all()
        _count_processes(-self._counted)
        for worker in workers:
            for _ in range(self._threads):
                try:
                    worker["queue"].put(None)
                except (OSError, ValueError):
                    break
        workers += self._retired
        end = time.monotonic() + timeout
        for worker in workers:
            worker["proc"].join(max(0.1, end - time.monotonic()))
        for worker in workers:
            if worker["proc"].is_alive():
                worker["proc"].terminate()
                worker["proc"].join(5)
        with self._lock:
            orphans = list(self._waiting)
        for task in orphans:
            self._finish(task, error=EngineError(_LOST_MESSAGE))
        try:
            self._events.put(None)
        except (OSError, ValueError):
            pass
        self._reader.join(5)


_LOST_MESSAGE = ("The grading process stopped while marking this copy. It will be tried "
                 "again on the next run.")


def _describe(exc: BaseException) -> dict:
    if isinstance(exc, StrictGradeError):
        return {"strict": True, "code": exc.code, "message": str(exc)}
    return {"strict": False, "kind": type(exc).__name__, "message": str(exc),
            "trace": traceback.format_exc()}


def _rebuild(info: dict) -> BaseException:
    if info.get("strict"):
        return StrictGradeError(info.get("code") or "", info.get("message") or "")
    return EngineError(info.get("message") or info.get("kind") or "grading failed")


class _EventStream:
    """A worker's stdout and stderr: whole lines go to the main process, which prints
    them into its own console and aichecker.log."""

    encoding = "utf-8"

    def __init__(self, events, prefix: str):
        self._events = events
        self._prefix = prefix
        self._local = threading.local()

    def write(self, text) -> int:
        text = str(text)
        buffered = getattr(self._local, "text", "") + text
        *lines, rest = buffered.split("\n")
        for line in lines:
            if line.strip():
                self._send(line)
        self._local.text = rest
        return len(text)

    def flush(self) -> None:
        rest = getattr(self._local, "text", "")
        if rest.strip():
            self._send(rest)
        self._local.text = ""

    def isatty(self) -> bool:
        return False

    def _send(self, line: str) -> None:
        try:
            self._events.put(("log", self._prefix + line))
        except Exception:  # noqa: BLE001 - logging must never fail a copy
            pass


def _package_check(batch: dict):
    """The batch package's blocking issue codes, or None when there is no package."""
    package = getattr(batch.get("cfg"), "exam_package", None)
    if package is None:
        return None
    try:
        return sorted(issue.code for issue in package.blockers())
    except Exception as exc:  # noqa: BLE001 - a check that cannot run is a disagreement
        return [f"check-failed: {type(exc).__name__}"]


def _handover(batch: dict) -> dict:
    """What a worker takes from this process so it judges every copy the same way."""
    import exam_package
    return {
        # The proxy pass minted from the operator's sign-in lasts seven days, while the
        # Google sign-in itself lasts an hour, so a worker started (or restarted) late
        # in a long run still works.
        "sessions": dict(pw_access._sessions),
        # The marks graph proof (exam_package.issue_question_graph_proof) is signed
        # with a key made at import; a worker's own key refuses this process's proof.
        "proof_key": exam_package._QUESTION_GRAPH_PROOF_KEY,
        # How this process judged the paper; a worker must judge it the same way.
        "package_check": _package_check(batch),
    }


def _worker_main(worker_id: int, threads: int, batch: dict, handover: dict, tasks,
                 events, grade) -> None:
    """A worker process: grade the copies handed to it until told to stop."""
    stream = _EventStream(events, f"[engine {worker_id}] ")
    sys.stdout = sys.stderr = stream
    pw_access._sessions.update(handover["sessions"])
    import exam_package
    exam_package._QUESTION_GRAPH_PROOF_KEY = handover["proof_key"]
    import pipeline  # noqa: F401 - load the grading code before saying ready
    seen = _package_check(batch)
    if seen != handover["package_check"]:
        events.put(("refused", worker_id,
                    f"it judged the paper {seen} where the main process judged it "
                    f"{handover['package_check']}"))
        stream.flush()
        return
    grade = grade or grade_copy
    parent = multiprocessing.parent_process()
    events.put(("ready", worker_id))

    def serve() -> None:
        while True:
            try:
                item = tasks.get(timeout=5)
            except queue.Empty:
                if parent is not None and not parent.is_alive():
                    return
                continue
            if item is None:
                return
            task, name, data = item

            def stage(*args, _task=task) -> None:
                events.put(("stage", _task, args))

            try:
                result = grade(batch, name, data, stage)
                # Packed here, not by the queue: the queue packs in a background thread
                # and drops what it cannot pack, which would leave the copy waiting for
                # an hour. A result that cannot cross fails the copy instead.
                message = ("done", task, pickle.dumps(
                    {key: result[key] for key in RESULT_KEYS if key in result},
                    pickle.HIGHEST_PROTOCOL))
            except BaseException as exc:  # noqa: BLE001 - every failure goes back
                message = ("error", task, _describe(exc))
            events.put(message)

    workers = [threading.Thread(target=serve, name=f"engine-{worker_id}-{n}", daemon=True)
               for n in range(max(1, threads))]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join()
    stream.flush()


# -- self test (tests and the release smoke check; no grading, no network) ----------
def selftest_grade(batch: dict, name: str, sa_bytes: bytes, stage_callback=None) -> dict:
    """A stand-in for grade_copy that exercises everything but the grading."""
    if stage_callback is not None:
        stage_callback("ocr", f"reading {name}")
    if name.startswith("strict"):
        raise StrictGradeError("NOT_AN_ANSWER_SHEET", f"{name} is typed pages")
    if name.startswith("boom"):
        raise ValueError(f"{name} could not be read")
    if name.startswith("crash"):
        os._exit(3)
    if name.startswith("slow"):
        time.sleep(float(name.split("-")[1]))
    if name.startswith("unpicklable"):
        return {"report": lambda: None}
    if name == "package-check":
        return {"report": {"blockers": _package_check(batch)}}
    print(f"graded {name}")
    return {"report": {"name": name, "batch": batch.get("label"), "pid": os.getpid()},
            "pdf_bytes": sa_bytes[::-1], "release": True,
            # Not a result field, and could not cross anyway: it must be left behind.
            "not_sent": lambda: None}


def _selftest_package():
    """A paper whose marks graph carries this process's signed proof, as a verified
    paper does in a real run."""
    from types import SimpleNamespace
    from exam_package import build_exam_package, issue_question_graph_proof
    from grader import MarksItem, MarksScheme
    scheme = MarksScheme(total=3, items=[MarksItem(qid="Q1", max=1, description="first"),
                                         MarksItem(qid="Q2", max=2, description="second")])

    def package(proof: str):
        return build_exam_package(
            exam_type="school", student_class="Class 9", subject="Physics", level="9",
            paper="", attempt="", applicable_law_date=None, total_marks=3,
            question_paper=b"paper", answer_key=b"key", key_source="upload",
            marks_scheme=scheme, scheme_source="ai_verified", question_graph_proof=proof)

    unsigned = package("")
    return SimpleNamespace(exam_package=package(issue_question_graph_proof(
        unsigned.question_paper_sha256, unsigned.total_marks, unsigned.questions,
        unsigned.choice_groups)))


def selftest(processes: int = 2, threads: int = 2) -> dict:
    """Start real worker processes, grade a few stand-in copies, stop them."""
    lines: list[str] = []
    pool = EnginePool({"label": "selftest", "cfg": _selftest_package()}, processes, threads,
                      grade=selftest_grade, log=lines.append)
    try:
        ready = pool.wait_ready(READY_TIMEOUT)
        stages: list[tuple] = []
        out = pool.grade("copy-1", b"abc", lambda *a: stages.append(a))
        try:
            pool.grade("strict-2", b"x")
            strict = None
        except StrictGradeError as exc:
            strict = exc.code
        # A verified paper must pass its own check inside a worker (build 82 failed
        # it in every worker: 466 copies to faculty review on 2026-10-06).
        package_ok = pool.grade("package-check", b"x")["report"]["blockers"] == []
        return {"ready": ready, "processes": processes, "package_ok": package_ok,
                "result_ok": out["pdf_bytes"] == b"cba" and out["report"]["batch"] == "selftest",
                "worker_pid_differs": out["report"]["pid"] != os.getpid(),
                "stage_forwarded": stages == [("ocr", "reading copy-1")],
                "strict_code": strict, "log_forwarded": any("graded copy-1" in l for l in lines)}
    finally:
        pool.close()

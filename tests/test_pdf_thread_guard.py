# -*- coding: utf-8 -*-
"""PyMuPDF under the workflow's threads (crash dumps of builds 161 and 162, 9 Nov 2026).

Each case runs in a process of its own: the guard must come before the first import of
fitz, and the failure it prevents ends the whole process.
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

WORK = r"""
import random, sys, threading, time
sys.path.insert(0, ROOT)
import pdf_thread_guard
assert pdf_thread_guard.install() is True
assert pdf_thread_guard.install() is True, "a second install changes nothing"
import fitz
from pymupdf import mupdf
assert mupdf.reinit_singlethreaded.__name__ == "<lambda>", "the single-thread setting must be skipped"
document = fitz.open()
for number in range(4):
    document.new_page().insert_text((40, 40), f"Answer {number} " * 12)
GOOD = document.tobytes()
EXPECTED = [page.get_text() for page in document]
STOP = time.monotonic() + 6
wrong = []

def work(seed):
    rng = random.Random(seed)
    while time.monotonic() < STOP:
        data = bytearray(GOOD)
        for _ in range(rng.randint(3, 40)):
            at = rng.randrange(len(data) - 20)
            data[at:at + rng.randint(1, 15)] = bytes(rng.randrange(256) for _ in range(rng.randint(1, 15)))
        try:
            with fitz.open(stream=bytes(data), filetype="pdf") as damaged:
                for page in damaged:
                    page.get_text(); page.get_pixmap(dpi=40); page.get_drawings()
        except Exception:
            pass
        with fitz.open(stream=GOOD, filetype="pdf") as intact:
            if [page.get_text() for page in intact] != EXPECTED:
                wrong.append(seed)

threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
[t.start() for t in threads]
[t.join() for t in threads]
assert not wrong, "a thread read wrong text"
print("survived")
"""


def run(code):
    prefix = f"ROOT = {ROOT!r}\n"
    return subprocess.run([sys.executable, "-c", prefix + code], capture_output=True, text=True, timeout=120, cwd=ROOT)


def test_threads_survive_damaged_pdfs_and_read_good_ones_correctly():
    result = run(WORK)
    assert result.returncode == 0 and "survived" in result.stdout, (hex(result.returncode & 0xFFFFFFFF), result.stderr[-800:])


def test_too_late_is_reported_not_raised():
    result = run("import sys\nsys.path.insert(0, ROOT)\nimport fitz, pdf_thread_guard\n"
                 "print(pdf_thread_guard.install(), pdf_thread_guard.installed())")
    assert result.returncode == 0 and "False False" in result.stdout, result.stderr[-500:]


def test_the_app_installs_it_before_any_pdf_import():
    code = "import sys\nsys.path.insert(0, ROOT)\nimport server\nprint(server.health()['pdf_threads_guarded'])"
    result = run(code)
    assert result.returncode == 0 and "True" in result.stdout, result.stderr[-800:]


def test_a_grading_worker_process_installs_it_too():
    result = run("import sys\nsys.path.insert(0, ROOT)\nimport grading_engine, pdf_thread_guard\nprint(pdf_thread_guard.installed())")
    assert result.returncode == 0 and "True" in result.stdout, result.stderr[-800:]

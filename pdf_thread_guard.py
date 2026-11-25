"""Make PyMuPDF safe for the workflow's many threads. Install before anything imports fitz.

On import PyMuPDF calls mupdf.reinit_singlethreaded(): one MuPDF context and one error
stack for the whole process, with no locks. The workflow checks several copies at once, so
a PDF error in one thread could jump to another thread's stack. Windows then ends the
process on the spot, with no Python error: the terminal closes and the page says "Failed
to fetch" (operator's report, 9 Nov 2026; five crash dumps from builds 161 and 162, all in
mupdfcpp64.dll at its longjmp, code 0xc0000409).

Skipping that one call leaves MuPDF's default: each thread has its own context and error
stack, and shared data is locked. PyMuPDF's own helper layer (_extra) keeps static state,
so calls into it take one lock.
"""
from __future__ import annotations

import functools
import importlib
import importlib.util
import sys
import threading
import types

_EXTRA_LOCK = threading.RLock()
_installed = False


def _locked(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        with _EXTRA_LOCK:
            return function(*args, **kwargs)
    return call


def _guard_extra(extra_module):
    """Every function of the helper layer under the lock. A SWIG destructor must stay a
    C function, and the class registration functions run only at import."""
    for name, value in list(vars(extra_module).items()):
        if callable(value) and isinstance(value, types.BuiltinFunctionType) and not (
                name.startswith(("delete_", "SWIG")) or name.endswith(("_swigregister", "_swiginit"))):
            setattr(extra_module, name, _locked(value))


def install():
    """Returns True when the guard is in place, False when PyMuPDF was imported first and
    its single-thread setting has already been applied (a test importing fitz, not the app:
    /api/health says which, and the release check refuses an EXE without the guard)."""
    global _installed
    if _installed:
        return True
    if "pymupdf" in sys.modules or "fitz" in sys.modules:
        return False
    spec = importlib.util.find_spec("pymupdf")
    placeholder = types.ModuleType("pymupdf")
    placeholder.__path__ = list(spec.submodule_search_locations)
    sys.modules["pymupdf"] = placeholder
    try:
        # Loaded first, under a placeholder package, so the real package's __init__
        # finds this module already imported, with the one call replaced.
        mupdf = importlib.import_module("pymupdf.mupdf")
    finally:
        del sys.modules["pymupdf"]
    mupdf.reinit_singlethreaded = lambda: None
    pymupdf = importlib.import_module("pymupdf")
    _guard_extra(pymupdf.extra._extra)
    _installed = True
    return True


def installed():
    return _installed

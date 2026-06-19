"""EXE entry point: start the local client server and open the browser to the UI.

Hardened for non-technical users on Windows:
  - Disables console "QuickEdit" mode, so clicking the window can't FREEZE the server
    (a frozen server is what shows up in the browser as "Failed to fetch").
  - Mirrors all output to aichecker.log next to the EXE, so if anything goes wrong we
    have the real traceback instead of a vanished console.
  - Picks a free port if the default is busy, and waits until the server actually answers
    before opening the browser (no opening a dead page).
"""
from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

from client_app import app

APP_DIR = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
           else os.path.dirname(os.path.abspath(__file__)))
LOG_PATH = os.path.join(APP_DIR, "aichecker.log")


class _Tee:
    """Write to the console (if any) AND the log file, so a windowed/headless run still
    records everything and clicking the console can't lose the logs. Behaves enough like a
    real stream that libraries which introspect it (e.g. uvicorn calling isatty() to decide
    on log colours) don't choke."""
    def __init__(self, *streams):
        self.streams = [s for s in streams if s is not None]

    def write(self, data):
        for s in self.streams:
            try:
                s.write(data)
                s.flush()
            except Exception:
                pass

    def flush(self):
        for s in self.streams:
            try:
                s.flush()
            except Exception:
                pass

    def isatty(self):
        return False              # it's a tee to a file → no terminal colour codes

    def __getattr__(self, name):
        # Delegate anything else (encoding, fileno, ...) to the first underlying stream.
        for s in self.__dict__.get("streams", []):
            if hasattr(s, name):
                return getattr(s, name)
        raise AttributeError(name)


def _setup_logging():
    try:
        f = open(LOG_PATH, "a", buffering=1, encoding="utf-8")
        sys.stdout = _Tee(sys.__stdout__, f)
        sys.stderr = _Tee(sys.__stderr__, f)
    except Exception:
        pass


def _disable_quickedit():
    """On Windows, QuickEdit mode pauses the process when the user clicks the console —
    which silently freezes the local server. Turn it off."""
    if os.name != "nt":
        return
    try:
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)                       # STD_INPUT_HANDLE
        mode = wintypes.DWORD()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            ENABLE_EXTENDED_FLAGS = 0x0080
            ENABLE_QUICK_EDIT = 0x0040
            new = (mode.value & ~ENABLE_QUICK_EDIT) | ENABLE_EXTENDED_FLAGS
            k.SetConsoleMode(h, new)
    except Exception:
        pass


def _pick_port() -> int:
    env = os.environ.get("AICHECKER_PORT")
    if env:
        return int(env)
    for p in (8800, 8801, 8802, 8803, 0):            # 0 → OS picks any free port
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", p))
            port = s.getsockname()[1]
            s.close()
            return port
        except OSError:
            continue
    return 8800


def _wait_then_open(url: str) -> None:
    import urllib.request
    for _ in range(120):                              # up to ~60s (onefile unpack is slow)
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    _setup_logging()
    _disable_quickedit()
    try:
        host = "127.0.0.1"
        port = _pick_port()
        url = f"http://{host}:{port}"
        print(f"AI Checker is running at {url}")
        print("Keep this window open while you work. Close it to quit.")
        print(f"(Logs are also saved to {LOG_PATH})")
        threading.Thread(target=_wait_then_open, args=(url,), daemon=True).start()
        uvicorn.run(app, host=host, port=port, log_level="info")
    except Exception:
        import traceback
        traceback.print_exc()
        print("\nAI Checker hit an error on startup (see above / aichecker.log).")
        try:
            input("Press Enter to close...")
        except Exception:
            time.sleep(20)


if __name__ == "__main__":
    main()

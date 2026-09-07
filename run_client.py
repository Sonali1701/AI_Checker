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
from server import API_PROTOCOL_VERSION, BUILD_ID

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


# 8765 is the door everyone knows; the rest are for when it is already taken.
_PORTS = (8765, 8766, 8767, 8768, 8769, 8770)


class NoAvailablePortError(RuntimeError):
    """Raised when every supported localhost port belongs to another process."""


def _port_free(port: int) -> bool:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def _pick_port() -> int:
    """First free port, so a second copy still starts instead of dying on the doorstep.

    This used to loop over the single-element tuple `(8765,)` and then `return 8765`
    anyway, so the module docstring's promise to "pick a free port if the default is
    busy" was never kept: with 8765 taken it handed uvicorn a port it could not bind.
    """
    env = os.environ.get("AICHECKER_PORT")
    if env:
        return int(env)
    for p in _PORTS:
        if _port_free(p):
            return p
    # Uvicorn accepts port 0, but it then chooses an ephemeral port internally.  The
    # launcher cannot know that number when it constructs the browser URL and health
    # probe, so advertising http://127.0.0.1:0 leaves a healthy server unreachable.
    # Keep the supported ports explicit (also important for Google OAuth origins) and
    # tell the user exactly what must be freed instead of opening a dead page.
    ports = ", ".join(str(port) for port in _PORTS)
    raise NoAvailablePortError(
        "AI Checker could not start because every supported local port is already "
        f"in use ({ports}). Close the other programs using these ports and try again."
    )


def _ports_to_check_for_running_copy() -> tuple[int, ...]:
    """Use only an explicit diagnostic/deployment port when one is configured."""
    env = os.environ.get("AICHECKER_PORT")
    return (int(env),) if env else _PORTS


def _already_running(port: int) -> bool:
    """True when the thing holding this port is another AI Checker, not a stranger."""
    import json as _json
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/health", timeout=1.5) as r:
            health = _json.load(r)
            return bool(
                health.get("ok")
                and health.get("app") == "AI Copy Checker"
                and health.get("build_id") == BUILD_ID
                and health.get("protocol_version") == API_PROTOCOL_VERSION
            )
    except Exception:
        return False


def _hold(message: str = "") -> None:
    """Keep the console up long enough to be read. A window that flashes and vanishes
    tells the user nothing at all, which is exactly how a busy port presented itself."""
    if message:
        print(message)
    try:
        input("Press Enter to close...")
    except Exception:
        time.sleep(20)


def _wait_then_open(url: str) -> None:
    import urllib.request
    for _ in range(120):                              # up to ~60s (onefile unpack is slow)
        try:
            urllib.request.urlopen(url + "/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    _open_browser(url)


def _open_browser(url: str) -> bool:
    """Open the UI unless an operator explicitly requested a headless launch."""
    disabled = str(os.environ.get("AICHECKER_NO_BROWSER") or "").strip().lower()
    if disabled in {"1", "true", "yes"}:
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:
        return False


def main() -> None:
    _setup_logging()
    _disable_quickedit()
    try:
        host = "127.0.0.1"
        # A copy already open is the ordinary case, not an error: the user clicked the
        # icon again, or closed the browser tab and wants it back. Take them to the one
        # that is running instead of starting a second server that cannot bind.
        for p in _ports_to_check_for_running_copy():
            if not _port_free(p) and _already_running(p):
                running = f"http://{host}:{p}"
                print(f"AI Checker is already running at {running}.")
                print("Opening it in your browser. To restart it, close the other")
                print("AI Checker window first, then open this again.")
                _open_browser(running)
                _hold()
                return
        port = _pick_port()
        url = f"http://{host}:{port}"
        print(f"AI Checker is running at {url}")
        print("Keep this window open while you work. Close it to quit.")
        print(f"(Logs are also saved to {LOG_PATH})")
        threading.Thread(target=_wait_then_open, args=(url,), daemon=True).start()
        started = time.time()
        uvicorn.run(app, host=host, port=port, log_level="info")
        # uvicorn does NOT raise when it cannot bind: it logs "[Errno 10048] ... only one
        # usage of each socket address", shuts down and returns normally. So the `except`
        # below never fired, main() simply returned, and the console closed too fast to
        # read — which is precisely how a busy port looked to the user. Anything under a
        # few seconds is a failure to start, not a session someone ended.
        if time.time() - started < 5:
            _hold(f"\nAI Checker could not start on port {port} (see above / "
                  f"{os.path.basename(LOG_PATH)}).")
    except NoAvailablePortError as exc:
        _hold(f"\n{exc}")
    except Exception:
        import traceback
        traceback.print_exc()
        _hold("\nAI Checker hit an error on startup (see above / aichecker.log).")


if __name__ == "__main__":
    main()

"""EXE entry point: start the local client server and open the browser to the UI.

This is the script PyInstaller freezes into the .exe. It binds to a local port only
(127.0.0.1), so the app is reachable just from the user's own machine.
"""
from __future__ import annotations

import os
import threading
import time
import webbrowser

import uvicorn

from client_app import app


def _open_browser(url: str) -> None:
    time.sleep(1.5)          # give uvicorn a moment to bind before opening the tab
    try:
        webbrowser.open(url)
    except Exception:
        pass


def main() -> None:
    host = "127.0.0.1"
    port = int(os.environ.get("AICHECKER_PORT", "8800"))
    url = f"http://{host}:{port}"
    print(f"AI Checker is running at {url}  (close this window to quit)")
    threading.Thread(target=_open_browser, args=(url,), daemon=True).start()
    # Pass the app OBJECT (not an import string) so it works inside a frozen EXE.
    uvicorn.run(app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()

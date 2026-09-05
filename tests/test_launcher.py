# -*- coding: utf-8 -*-
"""Double-clicking the EXE while a copy is already open.

REPORTED 2026-08-27: "when i click on the exe then terminal opens and closes in
seconds". aichecker.log had it three times over, once per attempt:

    ERROR: [Errno 10048] error while attempting to bind on address
           ('127.0.0.1', 8765): only one usage of each socket address ... is permitted

Two faults, both in run_client.py:

  * `_pick_port` looped over the ONE-ELEMENT tuple `(8765,)` and then returned 8765
    anyway, so the module docstring's promise to "pick a free port if the default is
    busy" was never kept — with the port taken it handed uvicorn a port it could not
    bind.
  * `uvicorn.run` does not RAISE on a bind failure. It logs the error, shuts down and
    returns normally, so the `except Exception` around it never fired: main() just
    returned and the console closed before anyone could read why.

The window vanishing is the part that matters. The user cannot report what they never
saw, and the actual cause was sitting in the log the whole time.
"""
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_client as rc

FAILS = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILS.append(name)


print("== the fallback ports are real ==")
check("more than one port is tried", len(rc._PORTS) > 1, rc._PORTS)
check("8765 is still the front door", rc._PORTS[0] == 8765, rc._PORTS)


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


# Everything below binds sockets, so it must NOT use the real 8765: this suite has to
# pass while the app is running, and it failed the moment someone opened the EXE. Swap
# in ports the OS just told us are free — the logic under test is identical.
_REAL_PORTS = rc._PORTS
rc._PORTS = tuple(dict.fromkeys(_free_port() for _ in range(6)))

print("\n== a busy port no longer stops the app starting ==")
squatter = socket.socket()
squatter.bind(("127.0.0.1", rc._PORTS[0]))
squatter.listen(1)
try:
    check("the front door reads as busy", not rc._port_free(rc._PORTS[0]))
    picked = rc._pick_port()
    check("so a different port is chosen", picked != rc._PORTS[0], f"picked {picked}")
    check("and it is one of ours", picked in rc._PORTS, f"picked {picked}")
    check("and it is actually free", rc._port_free(picked), f"picked {picked}")

    print("\n== a stranger on the port is not mistaken for us ==")
    check("a plain socket does not answer /api/health",
          not rc._already_running(rc._PORTS[0]),
          "otherwise the app would send the user to someone else's server")
finally:
    squatter.close()

check("the front door is taken again once released", rc._pick_port() == rc._PORTS[0])

print("\n== exhausting every supported port fails clearly, never with a :0 URL ==")
squatters = []
try:
    for port in rc._PORTS:
        listener = socket.socket()
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        squatters.append(listener)
    try:
        rc._pick_port()
    except rc.NoAvailablePortError as exc:
        message = str(exc)
        check("port exhaustion raises the launcher-specific error", True)
        check("the error explains that every supported port is occupied",
              "every supported local port" in message, message)
        check("the error names the supported ports",
              all(str(port) in message for port in rc._PORTS), message)
    else:
        check("port exhaustion raises the launcher-specific error", False,
              "_pick_port returned instead of failing")
finally:
    for listener in squatters:
        listener.close()

check("port selection works again after the occupied ports are released",
      rc._pick_port() == rc._PORTS[0])

print("\n== main presents the exhaustion error instead of opening a dead URL ==")
originals = (rc._setup_logging, rc._disable_quickedit, rc._port_free,
             rc._already_running, rc._hold)
held_messages = []
try:
    rc._setup_logging = lambda: None
    rc._disable_quickedit = lambda: None
    rc._port_free = lambda _port: False
    rc._already_running = lambda _port: False
    rc._hold = held_messages.append
    rc.main()
finally:
    (rc._setup_logging, rc._disable_quickedit, rc._port_free,
     rc._already_running, rc._hold) = originals

check("main holds one clear exhaustion message", len(held_messages) == 1,
      held_messages)
check("main never advertises localhost port zero",
      bool(held_messages) and ":0" not in held_messages[0], held_messages)
check("main tells the user how to recover",
      bool(held_messages) and "Close the other programs" in held_messages[0],
      held_messages)

print("\n== but a REAL running copy is recognised ==")


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps({
            "ok": True,
            "app": "AI Copy Checker",
            "build_id": rc.BUILD_ID,
            "protocol_version": rc.API_PROTOCOL_VERSION,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


srv = HTTPServer(("127.0.0.1", rc._PORTS[0]), Health)
t = threading.Thread(target=srv.serve_forever, daemon=True)
t.start()
try:
    check("an answering AI Checker is detected", rc._already_running(rc._PORTS[0]),
          "this is what sends the user to the copy they already have open")
    check("a port nobody is on is not", not rc._already_running(rc._PORTS[-1]))
finally:
    srv.shutdown()
    srv.server_close()

print("\n== the console is held open instead of vanishing ==")
import inspect

src = inspect.getsource(rc)
check("_hold exists", hasattr(rc, "_hold"))
check("startup errors hold the window", '_hold("\\nAI Checker hit an error' in src)
check("a failure to bind holds it too", "could not start on port" in src)
check("total port exhaustion is shown without a traceback",
      "except NoAvailablePortError as exc" in src and '_hold(f"\\n{exc}")' in src)
check("the already-running path holds it as well",
      src.count("_hold()") >= 1)
check("the silent-return trap is documented so it is not undone",
      "uvicorn does NOT raise when it cannot bind" in src)
check("a short run is treated as a failure, a long one as a real session",
      "time.time() - started < 5" in src)
check("the old bare input() call is gone",
      'input("Press Enter to close...")' in src and src.count('input("Press Enter') == 1,
      "it should live in _hold only")

print("\n== a packaged smoke test can run without opening a browser ==")
original_browser_open = rc.webbrowser.open
opened_urls = []
try:
    rc.webbrowser.open = lambda url: opened_urls.append(url) or True
    os.environ["AICHECKER_NO_BROWSER"] = "1"
    check("headless mode suppresses the browser",
          rc._open_browser("http://127.0.0.1:8765") is False)
    check("headless mode did not call the browser backend", not opened_urls,
          opened_urls)
    del os.environ["AICHECKER_NO_BROWSER"]
    check("normal double-click mode still opens the browser",
          rc._open_browser("http://127.0.0.1:8765") is True)
    check("normal mode opens the requested local URL",
          opened_urls == ["http://127.0.0.1:8765"], opened_urls)
finally:
    os.environ.pop("AICHECKER_NO_BROWSER", None)
    rc.webbrowser.open = original_browser_open

print("\n== env override still wins ==")
os.environ["AICHECKER_PORT"] = "9111"
try:
    check("AICHECKER_PORT is honoured", rc._pick_port() == 9111)
    check("an explicit port isolates diagnostic launches",
          rc._ports_to_check_for_running_copy() == (9111,),
          rc._ports_to_check_for_running_copy())
finally:
    del os.environ["AICHECKER_PORT"]
check("and removing it restores normal picking", rc._pick_port() in rc._PORTS)
check("normal double-click mode still discovers existing copies",
      rc._ports_to_check_for_running_copy() == rc._PORTS)

rc._PORTS = _REAL_PORTS
check("the real port list is restored", rc._PORTS[0] == 8765, rc._PORTS)

print()
if FAILS:
    print(f"FAILED: {len(FAILS)} -> {FAILS}")
    sys.exit(1)
print("ALL CHECKS PASSED")

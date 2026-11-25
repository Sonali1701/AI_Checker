"""Both packaged EXEs together: the checker running, the workflow EXE opened second.

    python tools/smoke_workflow_pair.py build101

Starts dist/AICheckerClient-<tag>.exe headless on a free port, then runs
dist/AICheckerWorkflow-<tag>.exe with the same port and checks that it reports the
running copy and points at /workflow.html instead of starting a second server.
No browser is opened and no grading or portal action happens.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path


def main():
    tag = sys.argv[1] if len(sys.argv) > 1 else ""
    root = Path(__file__).resolve().parents[1]
    suffix = f"-{tag}" if tag else ""
    client = root / f"dist/AICheckerClient{suffix}.exe"
    workflow = root / f"dist/AICheckerWorkflow{suffix}.exe"
    data = Path(tempfile.mkdtemp(prefix=f"pair-{tag or 'dist'}-", dir=root / "build"))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {**os.environ, "AICHECKER_NO_BROWSER": "1", "AICHECKER_PORT": str(port), "AICHECKER_DATA_DIR": str(data),
           "AI_CHECKER_WORKFLOW_DIR": str(data / "workflow_data")}
    flags = subprocess.CREATE_NO_WINDOW
    log = (data / "client.log").open("w", encoding="utf-8")
    first = subprocess.Popen([str(client)], cwd=data, env=env, stdout=log, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, creationflags=flags)
    ok = False
    try:
        end = time.monotonic() + 120
        while True:
            try:
                with opener.open(f"http://127.0.0.1:{port}/api/health", timeout=2) as response:
                    health = json.load(response)
                break
            except OSError:
                if first.poll() is not None:
                    raise SystemExit(f"client exited early; see {data / 'client.log'}")
                if time.monotonic() > end:
                    raise SystemExit("client health timeout")
                time.sleep(.5)
        print("client up on", port, health["build_id"], flush=True)
        started = time.monotonic()
        second = subprocess.run([str(workflow)], cwd=data, env=env, capture_output=True, text=True, timeout=120,
                                stdin=subprocess.DEVNULL, creationflags=flags)
        print(f"workflow exe: rc={second.returncode} after {time.monotonic() - started:.1f}s", flush=True)
        print("STDOUT:", repr(second.stdout[-600:]), flush=True)
        ok = f"already running at http://127.0.0.1:{port}/workflow.html" in second.stdout
    finally:
        subprocess.run(["taskkill", "/PID", str(first.pid), "/T", "/F"], capture_output=True, creationflags=flags)
        try:
            first.wait(timeout=15)
        except Exception:  # noqa: BLE001
            pass
        log.close()
    print(("PASS" if ok else "FAIL") + " - pair test " + (tag or "dist"), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()

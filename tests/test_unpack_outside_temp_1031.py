"""31 Oct 2026: a cleaner emptied the EXE's unpacked folder in Temp while the app ran. The certificate
file and fonts went, every later call failed and 111 tests were skipped. The same night a save of
autopilot.json refused for a moment ended a pair's thread. No network and no EXE here;
tools/smoke_release.py checks the built EXEs.
"""
from __future__ import annotations

import os
import pathlib
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import run_client
from test_workflow_autopilot import OWNER, pilot

OLD = time.time() - run_client.OLD_UNPACKED_COPY_SECONDS - 60


def unpacked(home, name, age=OLD):
    folder = home / name
    folder.mkdir(parents=True)
    (folder / "python313.dll").write_bytes(b"x")
    os.utime(folder, (age, age))
    return folder


def test_the_exe_does_not_unpack_into_temp():
    spec = (pathlib.Path(run_client.__file__).parent / "AICheckerClient.spec").read_text(encoding="utf-8")
    assert r"runtime_tmpdir=r'%LOCALAPPDATA%\AIChecker\runtime' if os.name == 'nt' else None," in spec


def test_a_killed_copy_is_removed_and_nothing_else_is(tmp_path):
    home = tmp_path / "runtime"
    killed = unpacked(home, "_MEI111")
    starting = unpacked(home, "_MEI333", time.time())
    this_copy = unpacked(home, "_MEI444")
    other = unpacked(home, "notes")
    run_client._clear_old_unpacked_copies(str(this_copy))
    assert not killed.exists()
    assert sorted(path.name for path in home.iterdir()) == ["_MEI333", "_MEI444", "notes"]
    assert (starting / "python313.dll").exists() and (other / "python313.dll").exists()


def test_a_running_copys_files_are_never_touched(tmp_path):
    home = tmp_path / "runtime"
    running = unpacked(home, "_MEI555")
    with (running / "python313.dll").open("rb"):      # as a loaded DLL holds it
        os.utime(running, (OLD, OLD))
        run_client._clear_old_unpacked_copies(str(unpacked(home, "_MEI666")))
        assert (running / "python313.dll").exists()
    assert sorted(path.name for path in home.iterdir()) == ["_MEI555", "_MEI666"]


def test_only_the_runtime_folder_is_ever_cleared(tmp_path):
    temp = tmp_path / "Temp"
    other_program = unpacked(temp, "_MEI777")
    run_client._clear_old_unpacked_copies(str(unpacked(temp, "_MEI888")))
    run_client._clear_old_unpacked_copies("")
    assert other_program.exists()


def test_a_save_refused_for_a_moment_is_tried_again(tmp_path, monkeypatch):
    _, ap = pilot(tmp_path)
    ap.state = {"owner": OWNER, "status": "running"}
    real, refused = pathlib.Path.replace, []

    def held_by_a_scanner(path, target):
        if len(refused) < 3:
            refused.append(path)
            raise PermissionError(13, "Access is denied")
        return real(path, target)

    monkeypatch.setattr(pathlib.Path, "replace", held_by_a_scanner)
    ap.save()
    assert len(refused) == 3 and ap.state_path.exists()

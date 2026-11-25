"""Live run of 2 Nov 2026: a Start on a new tab replaced the Autopilot's list of tests, but seven runs of
the earlier Autopilots went on. Stop could not reach them, a restart brought them back, and they held every
checking slot, so the new tab never had a check. No network.
"""
from __future__ import annotations

import json
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import integrated_workflow as workflow
import workflow_autopilot as autopilot

OWNER = "owner"


def store(root, in_charge="now"):
    made = workflow.WorkflowStore.__new__(workflow.WorkflowStore)
    made.root, made.lock, made.resumed_owners, made.active = root, threading.Lock(), set(), {}
    made.started, made.save = [], lambda run: None
    made.start = lambda run_id, authorization: made.started.append(run_id)
    made.read = lambda run_id: json.loads((root / run_id / "run.json").read_text(encoding="utf-8"))
    if in_charge:
        (root / "autopilot.json").write_text(json.dumps({"id": in_charge}), encoding="utf-8")
    return made


def saved(root, run_id, **fields):
    (root / run_id).mkdir()
    run = {"id": run_id, "owner": OWNER, "status": "running", "rows": [], **fields}
    (root / run_id / "run.json").write_text(json.dumps(run), encoding="utf-8")


def test_a_restart_continues_this_autopilots_runs_and_leaves_earlier_ones(tmp_path):
    made = store(tmp_path)
    saved(tmp_path, "current", autopilot="now")
    saved(tmp_path, "earlier", autopilot="before")
    saved(tmp_path, "by_hand")
    made.resume_interrupted(OWNER, "token")
    assert sorted(made.started) == ["by_hand", "current"]


def test_with_no_autopilot_saved_every_run_continues_as_before(tmp_path):
    made = store(tmp_path, in_charge="")
    saved(tmp_path, "one", autopilot="before")
    made.resume_interrupted(OWNER, "token")
    assert made.started == ["one"]


def test_a_left_out_run_keeps_its_status_so_a_start_can_pick_its_copies_up(tmp_path):
    made = store(tmp_path)
    saved(tmp_path, "earlier", autopilot="before")
    made.resume_interrupted(OWNER, "token")
    assert made.read("earlier")["status"] == "running" and made.left_alone(made.read("earlier"))


def test_the_runs_of_any_autopilot_are_found_and_others_are_not(tmp_path):
    made = store(tmp_path)
    saved(tmp_path, "current", autopilot="now")
    saved(tmp_path, "earlier", autopilot="before")
    saved(tmp_path, "by_hand")
    made.active = {"current": {}, "earlier": {}, "by_hand": {}, "a fetch": {}}
    assert sorted(made.autopilot_runs()) == ["current", "earlier"]


def test_stop_reaches_runs_of_earlier_autopilots(tmp_path):
    made = store(tmp_path)
    saved(tmp_path, "current", autopilot="now")
    saved(tmp_path, "earlier", autopilot="before")
    made.active = {"current": {}, "earlier": {}}
    stopped = []
    made.stop_run = stopped.append
    pilot = autopilot.Autopilot.__new__(autopilot.Autopilot)
    pilot.store, pilot.lock, pilot.stop_event = made, threading.RLock(), threading.Event()
    pilot.state = {"owner": OWNER, "status": "running", "tests": [{"status": "running", "run_id": "current"}]}
    pilot.save, pilot.status = (lambda: None), (lambda owner: {"status": pilot.state["status"]})
    assert pilot.stop(OWNER) == {"status": "stopped"}
    assert sorted(stopped) == ["current", "earlier"]

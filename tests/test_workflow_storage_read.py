"""A progress poll tolerates brief Windows locks without hiding corrupt state."""
import json
from pathlib import Path

import pytest

import integrated_workflow as workflow


def test_saved_state_read_recovers_from_transient_windows_lock(tmp_path, monkeypatch):
    store = workflow.WorkflowStore(tmp_path, lambda *args: {})
    run_id = "a" * 32
    run = {"id": run_id, "status": "complete", "rows": []}
    store.save(run)
    target = store.folder(run_id) / "run.json"
    original = Path.read_text
    attempts, delays = [], []

    def locked_read(path, *args, **kwargs):
        if path == target:
            attempts.append(path)
            if len(attempts) < 3:
                raise PermissionError("Temporary Windows sharing violation")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", locked_read)
    monkeypatch.setattr(workflow.time, "sleep", delays.append)
    assert store.read(run_id) == run
    assert len(attempts) == 3
    assert delays == [.02, .04]


def test_persistent_saved_state_lock_has_bounded_retries(tmp_path, monkeypatch):
    store = workflow.WorkflowStore(tmp_path, lambda *args: {})
    attempts, delays = [], []

    def locked_read(path, *args, **kwargs):
        attempts.append(path)
        raise PermissionError("Persistent permission denial")

    monkeypatch.setattr(Path, "read_text", locked_read)
    monkeypatch.setattr(workflow.time, "sleep", delays.append)
    with pytest.raises(PermissionError, match="Persistent permission denial"):
        store.read("a" * 32)
    assert len(attempts) == 6
    assert delays == [.02, .04, .08, .16, .32]


def test_missing_or_corrupt_saved_state_is_not_retried(tmp_path, monkeypatch):
    store = workflow.WorkflowStore(tmp_path, lambda *args: {})
    delays = []
    monkeypatch.setattr(workflow.time, "sleep", delays.append)
    run_id = "a" * 32
    with pytest.raises(FileNotFoundError):
        store.read(run_id)
    store.folder(run_id).mkdir()
    (store.folder(run_id) / "run.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        store.read(run_id)
    assert delays == []

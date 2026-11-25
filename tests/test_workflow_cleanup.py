from pathlib import Path

import pytest

from workflow_cleanup import cleanup_copy_artifacts, cleanup_eligible


class CopyStore:
    def __init__(self, folder, source=None, checked=None):
        self.run_folder = folder
        self.source = source or folder / "copies" / "student-copy.pdf"
        self.checked = checked or folder / "outputs" / "student-copy.pdf"
        self.report = folder / "reports" / "student-copy.json"

    def folder(self, run_id):
        return self.run_folder

    def copy_paths(self, run, submission_id):
        return self.source, self.checked, self.report


@pytest.fixture
def stored_copy(tmp_path):
    folder = tmp_path / "run"
    store = CopyStore(folder)
    for path, data in ((store.source, b"downloaded copy"), (store.checked, b"checked copy"),
                       (store.report, b'{"total_score":9}'), (folder / "question_paper.pdf", b"paper"),
                       (folder / "answer_key.pdf", b"key"), (folder / "run.json", b"saved run")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return store, {"id": "run", "layout_version": 3}, {"submission_id": "student-copy"}


@pytest.mark.parametrize("stage", ["uploaded", "rejection_submitted", "already_evaluated", "duplicate"])
def test_final_copy_removes_pdfs_and_retains_audit_and_shared_documents(stored_copy, stage):
    store, run, row = stored_copy
    row["stage"] = stage
    size = store.source.stat().st_size + store.checked.stat().st_size
    result = cleanup_copy_artifacts(store, run, row)
    assert result == {"eligible": True, "removed": 2, "bytes_freed": size, "errors": []}
    assert not store.source.exists() and not store.checked.exists()
    assert all(path.exists() for path in (store.report, store.run_folder / "question_paper.pdf",
                                         store.run_folder / "answer_key.pdf", store.run_folder / "run.json"))
    assert cleanup_copy_artifacts(store, run, row)["removed"] == 0


@pytest.mark.parametrize("row", [
    {"stage": "pending"}, {"stage": "downloaded"}, {"stage": "checked"}, {"stage": "faculty_review"},
    {"stage": "rejected"}, {"stage": "assignment_uncertain"}, {"stage": "upload_uncertain"},
    {"stage": "rejection_uncertain"}, {"stage": "skipped", "auto_retry": True, "error": "proxy failed"},
    {"stage": "skipped"},
])
def test_unfinished_retryable_or_unclassified_copy_retains_files(stored_copy, row):
    store, run, base = stored_copy
    result = cleanup_copy_artifacts(store, run, {**base, **row})
    assert not result["eligible"] and result["removed"] == 0
    assert store.source.exists() and store.checked.exists()


def test_permanently_unreadable_copy_can_be_cleaned(stored_copy):
    store, run, row = stored_copy
    row.update(stage="skipped", auto_retry=False, error="", note="Downloaded file is not a readable PDF")
    assert cleanup_copy_artifacts(store, run, row)["removed"] == 2
    assert not cleanup_eligible({**row, "error": "download temporarily failed"})


def test_paths_outside_run_and_shared_exam_pdfs_are_never_deleted(stored_copy, tmp_path):
    store, run, row = stored_copy
    outside = tmp_path / "original-upload.pdf"
    outside.write_bytes(b"user original")
    store.source, store.checked = outside, store.run_folder / "question_paper.pdf"
    result = cleanup_copy_artifacts(store, run, {**row, "stage": "uploaded"})
    assert result["removed"] == 0 and len(result["errors"]) == 2
    assert outside.read_bytes() == b"user original"
    assert store.checked.read_bytes() == b"paper"


def test_a_locked_pdf_remains_retryable_without_changing_copy_success(stored_copy, monkeypatch):
    store, run, row = stored_copy
    row["stage"] = "uploaded"
    original = Path.unlink

    def locked(path, *args, **kwargs):
        if path == store.source:
            raise PermissionError("file open in browser")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    result = cleanup_copy_artifacts(store, run, row)
    assert row["stage"] == "uploaded"
    assert result["removed"] == 1 and result["errors"] == ["student-copy.pdf: PermissionError"]
    assert store.source.exists() and not store.checked.exists()
    monkeypatch.setattr(Path, "unlink", original)
    assert cleanup_copy_artifacts(store, run, row)["removed"] == 1

"""Remove local copy PDFs only after their workflow outcome is final.

The run's JSON, lightweight grading report and shared exam documents stay available.
Failed, held and uncertain copies keep their PDFs so a retry can continue safely.
"""

from pathlib import Path


def cleanup_eligible(row):
    """Whether no remaining portal or checking step needs this copy's files."""
    stage = row.get("stage")
    if stage in ("uploaded", "rejection_submitted", "already_evaluated", "duplicate"):
        return True
    if stage == "superseded":
        return bool(row.get("replaced_by"))
    # "skipped" also describes exhausted transient failures. Those must retain
    # their downloads and checked output for the next run or Resume / retry.
    return stage == "skipped" and row.get("auto_retry") is False and not row.get("error")


def cleanup_copy_artifacts(store, run, row):
    """Best-effort removal of this run's downloaded and generated per-copy PDFs.

    Call after persisting the final row state. Returns cleanup counts and safe
    errors without changing the saved state or turning a successful upload into
    a copy error. Calling again retries locked files and tolerates missing files.
    No directories, shared documents, original user files or reports are deleted.
    """
    result = {"eligible": cleanup_eligible(row), "removed": 0, "bytes_freed": 0, "errors": []}
    if not result["eligible"]:
        return result
    try:
        if row.get("stage") == "superseded":
            # _create persists the replacement first, then retires the old row.
            # A half-finished migration must never destroy its only retry files.
            replacement = Path(store.folder(row["replaced_by"])) / "run.json"
            if row["replaced_by"] == run["id"] or not replacement.is_file():
                result["eligible"] = False
                return result
        folder = Path(store.folder(run["id"])).resolve()
        source, checked, _ = store.copy_paths(run, row["submission_id"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        result["errors"].append(f"Copy paths: {type(exc).__name__}")
        return result
    protected = {folder / "question_paper.pdf", folder / "answer_key.pdf"}
    for target in (Path(source), Path(checked)):
        try:
            resolved = target.resolve()
            # Resolve every deletion target before unlinking, including Windows
            # junctions/symlinks. Legacy flat paths need the shared PDF guard.
            if not resolved.is_relative_to(folder) or resolved in protected or resolved == folder:
                result["errors"].append(f"{target.name}: outside copy storage")
                continue
            if target.suffix.lower() != ".pdf":
                result["errors"].append(f"{target.name}: not a copy PDF")
                continue
            try:
                size = target.stat().st_size
            except FileNotFoundError:
                continue
            target.unlink()
            result["removed"] += 1
            result["bytes_freed"] += size
        except FileNotFoundError:
            pass
        except (OSError, RuntimeError) as exc:
            result["errors"].append(f"{target.name}: {type(exc).__name__}")
    return result

"""Frontend-neutral operations, with structured results and immutable evidence snapshots.

Cancellation is currently checked between stages, not during model/network calls.
The existing stage implementations remain shared and usable by headless callers.
"""

from __future__ import annotations

import json
import hashlib
import platform
import shutil
import sys
import traceback
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

from stimulus_harvester.project import Project, create_project, open_project, project_status
from stimulus_harvester.review.db import init_db
from stimulus_harvester.storage import ACTIVE_OPERATION, new_id, now, project_lock

__all__ = [
    "Project",
    "create_project",
    "open_project",
    "project_status",
    "run_stage",
    "OperationResult",
    "review_application",
    "candidate_evidence",
]


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    stage: str
    status: str
    result: object = None
    error: dict | None = None


def _invoke(project: Project, stage: str, options: dict):
    if stage == "search":
        from stimulus_harvester.search import run_search_stage

        return run_search_stage(
            project.config,
            project.root.name,
            project.paths.config_snapshot_path,
            project_root=project.root,
        )[1]
    if stage == "download":
        from stimulus_harvester.download import run_download_stage

        return run_download_stage(project.root, **options)
    if stage == "filter":
        from stimulus_harvester.filter import run_filter_stage

        return run_filter_stage(project.root)
    if stage == "score":
        from stimulus_harvester.score import run_clip_scoring, run_vlm_scoring

        result = {"clip": run_clip_scoring(project.root)}
        use_vlm = options.get("vlm")
        should_run_vlm = use_vlm if use_vlm is not None else project.config.vlm.backend != "noop"
        if should_run_vlm:
            result["vlm"] = run_vlm_scoring(project.root)
        return result
    if stage == "export":
        from stimulus_harvester.export import export_run

        return export_run(project.root, **options)
    raise ValueError(f"Unknown stage: {stage}")


def run_stage(
    path: Path | Project,
    stage: str,
    *,
    progress: Callable[[dict], None] | None = None,
    cancelled: Callable[[], bool] | None = None,
    **options,
) -> OperationResult:
    if stage not in {"search", "download", "filter", "score", "export"}:
        raise ValueError(f"Unknown stage: {stage}")
    project = path if isinstance(path, Project) else open_project(path)
    operation_id = new_id("operation")
    with project_lock(project.root):
        evidence = project.root / "operations" / operation_id
        evidence.mkdir(parents=True)
        from stimulus_harvester import __version__
        from stimulus_harvester.provenance import git_commit

        params = {
            "stage": stage,
            "options": options,
            "project_id": project.project_id,
            "software_version": __version__,
            "python": sys.version,
            "platform": platform.platform(),
            "git_commit": git_commit(),
            "schema_version": project.schema_version,
            "config_sha256": hashlib.sha256(
                project.paths.config_snapshot_path.read_bytes()
            ).hexdigest(),
        }
        (evidence / "request.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
        shutil.copyfile(project.paths.config_snapshot_path, evidence / "config_snapshot.yaml")
        conn = init_db(project.paths.db_path)
        conn.execute(
            "INSERT INTO operations VALUES (?, ?, ?, ?)",
            (operation_id, stage, now(), json.dumps(params)),
        )
        conn.execute(
            "INSERT INTO operation_events(operation_id,occurred_at,status,details_json) VALUES (?,?,?,?)",
            (operation_id, now(), "started", "{}"),
        )
        conn.commit()
        conn.close()
        token = ACTIVE_OPERATION.set(operation_id)
        interrupted = None
        try:
            if cancelled and cancelled():
                outcome = OperationResult(operation_id, stage, "cancelled")
            else:
                if progress:
                    progress({"operation_id": operation_id, "stage": stage, "status": "started"})
                outcome = OperationResult(
                    operation_id, stage, "succeeded", _invoke(project, stage, options)
                )
        except (KeyboardInterrupt, SystemExit) as exc:
            interrupted = exc
            outcome = OperationResult(
                operation_id,
                stage,
                "interrupted",
                error={"type": type(exc).__name__, "message": str(exc)},
            )
        except Exception as exc:
            outcome = OperationResult(
                operation_id,
                stage,
                "failed",
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            (evidence / "traceback.txt").write_text(traceback.format_exc(), encoding="utf-8")
        finally:
            ACTIVE_OPERATION.reset(token)
        # Snapshots isolate each operation from the mutable legacy stage files.
        try:
            if project.paths.manifests.exists():
                shutil.copytree(project.paths.manifests, evidence / "manifests")
            if stage == "export" and project.paths.exports.exists():
                shutil.copytree(project.paths.exports, evidence / "exports")
        except OSError as exc:
            outcome = OperationResult(
                operation_id,
                stage,
                "failed",
                result=outcome.result,
                error={"type": type(exc).__name__, "message": f"Evidence snapshot failed: {exc}"},
            )
        serialized = json.dumps(asdict(outcome), indent=2, default=str)
        (evidence / "result.json").write_text(serialized, encoding="utf-8")
        conn = init_db(project.paths.db_path)
        conn.execute(
            "INSERT INTO operation_events(operation_id,occurred_at,status,details_json) VALUES (?,?,?,?)",
            (operation_id, now(), outcome.status, serialized),
        )
        conn.commit()
        conn.close()
        if progress:
            progress({"operation_id": operation_id, "stage": stage, "status": outcome.status})
        if interrupted:
            raise interrupted
        return outcome


def review_application(path: Path | Project, *, reviewer_email: str | None = None):
    from stimulus_harvester.review.app import create_app

    project = path if isinstance(path, Project) else open_project(path)
    return create_app(project.root, project.config, reviewer_email=reviewer_email)


def candidate_evidence(path: Path | Project, local_id: str) -> dict:
    from stimulus_harvester.storage import candidate_evidence as read_evidence

    project = path if isinstance(path, Project) else open_project(path)
    conn = init_db(project.paths.db_path)
    try:
        return read_evidence(conn, local_id)
    finally:
        conn.close()

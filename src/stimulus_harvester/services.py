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
    "discovery_diagnostics",
    "screening_report",
    "freeze_validate",
    "frozen_artifact",
    "list_frozen_artifacts",
    "verify_frozen_package",
]


@dataclass(frozen=True)
class OperationResult:
    operation_id: str
    stage: str
    status: str
    result: object = None
    error: dict | None = None


def _invoke(project: Project, stage: str, options: dict):
    if stage == "freeze-create":
        from stimulus_harvester.frozen.snapshot import create

        return create(project, **options)
    if stage == "assemble":
        from stimulus_harvester.frozen.materialize import assemble

        return assemble(project, **options)
    if stage == "export-frozen":
        from stimulus_harvester.frozen.materialize import export_frozen

        return export_frozen(project, **options)
    if stage == "screen":
        from stimulus_harvester.screening.engine import run_screen

        return run_screen(project, **options)
    if stage == "screen-override":
        from stimulus_harvester.screening.engine import override

        return override(project, **options)
    if stage == "search":
        from stimulus_harvester.search import run_search_stage

        return run_search_stage(
            project.config,
            project.root.name,
            project.paths.config_snapshot_path,
            project_root=project.root,
            detailed=True,
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
    if stage not in {
        "search",
        "download",
        "filter",
        "score",
        "export",
        "screen",
        "screen-override",
        "freeze-create",
        "assemble",
        "export-frozen",
    }:
        raise ValueError(f"Unknown stage: {stage}")
    frozen_input = stage in {"assemble", "export-frozen"}
    project = (
        path if isinstance(path, Project) else open_project(path, validate_config=not frozen_input)
    )
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
            "input_mode": "immutable_artifact" if frozen_input else "current_project",
            "config_sha256": None
            if frozen_input
            else hashlib.sha256(project.paths.config_snapshot_path.read_bytes()).hexdigest(),
        }
        (evidence / "request.json").write_text(json.dumps(params, indent=2), encoding="utf-8")
        if not frozen_input:
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
                result = _invoke(project, stage, options)
                status = (
                    result["discovery_status"]
                    if stage == "search"
                    else result.get("stage_status", "succeeded")
                    if stage == "screen"
                    else "succeeded"
                )
                error = None
                if status == "failed":
                    error = {
                        "type": "DiscoveryFailure",
                        "message": "Discovery failed; inspect discovery-report for persisted request and setup diagnostics",
                    }
                outcome = OperationResult(operation_id, stage, status, result, error)
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
            if not frozen_input and project.paths.manifests.exists():
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
        result = read_evidence(conn, local_id)
        from stimulus_harvester.screening.engine import report_from_connection

        report = report_from_connection(conn, project)
        candidate = conn.execute(
            "SELECT candidate_id FROM legacy_ids WHERE local_id=?", (local_id,)
        ).fetchone()
        if candidate:
            result["screening"] = {
                key: [r for r in report[key] if r["candidate_id"] == candidate[0]]
                for key in ("candidates", "observations", "evaluations", "overrides")
            }
        return result
    finally:
        conn.close()


def discovery_diagnostics(path: Path | Project, *, operation_id=None, provider_order=None):
    from stimulus_harvester.search.coverage import coverage_report
    from stimulus_harvester.search.evidence import read_attempts

    project = path if isinstance(path, Project) else open_project(path)
    conn = init_db(project.paths.db_path)
    try:
        return {
            "coverage": coverage_report(conn, provider_order, operation_id),
            "events": read_attempts(conn, operation_id),
        }
    finally:
        conn.close()


def screening_report(path: Path | Project, **options):
    from stimulus_harvester.screening.engine import screening_report as report

    project = path if isinstance(path, Project) else open_project(path)
    return report(project, **options)


def freeze_validate(path: Path | Project, *, request):
    from stimulus_harvester.frozen.snapshot import validate

    project = path if isinstance(path, Project) else open_project(path)
    with project_lock(project.root):
        return validate(project, request=request)


def frozen_artifact(path: Path | Project, artifact_id: str):
    from stimulus_harvester.frozen.store import load

    project = path if isinstance(path, Project) else open_project(path, validate_config=False)
    conn = init_db(project.paths.db_path)
    try:
        manifest, root = load(conn, project, artifact_id)
        return {"path": str(root), "manifest": manifest}
    finally:
        conn.close()


def list_frozen_artifacts(path: Path | Project):
    from stimulus_harvester.frozen.store import ensure_schema

    project = path if isinstance(path, Project) else open_project(path, validate_config=False)
    conn = init_db(project.paths.db_path)
    try:
        ensure_schema(conn)
        return [
            dict(r)
            for r in conn.execute(
                "SELECT artifact_id,kind,freeze_id,parent_id,created_at,relative_path,manifest_sha256 FROM frozen_artifacts ORDER BY rowid"
            )
        ]
    finally:
        conn.close()


def verify_frozen_package(path, *, expected_sha256=None):
    from stimulus_harvester.frozen.store import verify_directory

    manifest = verify_directory(path, expected_sha256=expected_sha256)
    return {
        "valid": True,
        "artifact_id": manifest["artifact_id"],
        "kind": manifest["kind"],
        "freeze_id": manifest["freeze_id"],
        "files_verified": len(manifest["files"]),
        "expected_hash_checked": expected_sha256 is not None,
    }

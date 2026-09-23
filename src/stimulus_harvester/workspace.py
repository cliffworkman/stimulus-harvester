"""Frontend-neutral workspace projections and small audited configuration operations.

No Qt imports. Scientific decisions are delegated to the existing review/screen/freeze services.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from PIL import Image

from stimulus_harvester import __version__, services
from stimulus_harvester.config import HarvesterConfig, config_to_yaml_str
from stimulus_harvester.curation import project_projection
from stimulus_harvester.review.db import init_db
from stimulus_harvester.storage import ACTIVE_OPERATION, candidate_evidence, new_id


@lru_cache(maxsize=4096)
def _displayable(path, size, modified_ns):
    """Worker-side decode check cached by file metadata; no pixel copies retained."""
    try:
        with Image.open(path) as image:
            image.load()
        return True
    except (OSError, ValueError):
        return False


@dataclass(frozen=True)
class Workspace:
    path: str
    name: str
    project_id: str
    status: dict
    config: dict | None
    candidates: list[dict]
    screening: dict
    artifacts: list[dict]
    warnings: list[str]
    providers: dict
    recovery: list[dict]
    version: str = __version__


def default_configuration(name):
    return HarvesterConfig.model_validate(
        {
            "run_name": name,
            "search": {"seed_queries": [], "query_expansion": {"enabled": False}},
            "clip": {"positive_prompts": [], "negative_prompts": []},
        }
    )


def save_configuration(project, *, configuration):
    if not ACTIVE_OPERATION.get():
        raise ValueError("Configuration updates must run through shared services")
    config = HarvesterConfig.model_validate(configuration)
    target = project.paths.config_snapshot_path
    temporary = target.with_name(new_id("config") + ".yaml")
    try:
        temporary.write_text(config_to_yaml_str(config), encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"configuration": config.model_dump(mode="json"), "path": str(target)}


def provider_states(config):
    from stimulus_harvester.search import PROVIDERS

    result = {}
    for name, factory in PROVIDERS.items():
        settings = getattr(config.search, name)
        if not settings.enabled:
            result[name] = {"state": "disabled", "message": "Not selected"}
            continue
        try:
            adapter = factory(settings)
            adapter.close()
            result[name] = {
                "state": "configured",
                "message": "Configured; remote capability checked during discovery",
            }
        except Exception as exc:
            result[name] = {"state": "setup_error", "message": str(exc)}
    return result


def dependency_states(*, probe=False, ollama_url="http://localhost:11434"):
    import subprocess
    import sys

    result = {
        name: {
            "available": importlib.util.find_spec(module) is not None,
            "state": "installed (runtime not tested)"
            if importlib.util.find_spec(module)
            else "unavailable",
        }
        for name, module in (
            ("person_detector", "torchvision"),
            ("torch", "torch"),
            ("OpenCLIP", "open_clip"),
        )
    }
    result.update(
        core={"available": True, "state": "ready"},
        CUDA={"available": False, "state": "not checked"},
        Ollama={"available": False, "state": "not checked"},
    )
    result["person_detector"]["available"] &= result["torch"]["available"]
    if probe:
        if result["torch"]["available"]:
            try:
                run = subprocess.run(
                    [
                        sys.executable,
                        "-c",
                        "import json,torch,torchvision; print(json.dumps({'CUDA': torch.cuda.is_available()}))",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=20,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if run.returncode:
                    raise RuntimeError(run.stderr)
                cuda = json.loads(run.stdout.splitlines()[-1])["CUDA"]
                result["person_detector"] = {"available": True, "state": "ready"}
                result["CUDA"] = {"available": cuda, "state": "ready" if cuda else "unavailable"}
            except Exception as exc:
                result["person_detector"] = {
                    "available": False,
                    "state": "runtime unavailable",
                    "details": str(exc),
                }
        else:
            result["CUDA"]["state"] = "unavailable (PyTorch not installed)"
        try:
            import httpx

            with httpx.Client(timeout=2, trust_env=False) as client:
                response = client.get(ollama_url.rstrip("/") + "/api/tags")
                response.raise_for_status()
                models = response.json()["models"]
            result["Ollama"] = {"available": True, "state": f"ready; {len(models)} local models"}
        except Exception as exc:
            result["Ollama"] = {"available": False, "state": "unavailable", "details": str(exc)}
    return result


def recovery_diagnostics(project, artifacts):
    records = []
    lock = project.root / ".harvester.lock"
    if lock.exists():
        records.append(
            {
                "kind": "project_lock",
                "path": str(lock),
                "guidance": "A writer may still be active. Confirm all writers stopped before manual lock recovery; never delete automatically.",
            }
        )
    known = {r["relative_path"] for r in artifacts}
    for directory in ("freezes", "assemblies", "frozen_exports"):
        parent = project.root / directory
        if parent.exists():
            for path in sorted(parent.iterdir()):
                if path.is_dir() and path.relative_to(project.root).as_posix() not in known:
                    records.append(
                        {
                            "kind": "pending"
                            if path.name.startswith(".pending-")
                            else "unregistered_artifact",
                            "path": str(path),
                            "guidance": "Preserve this directory. Inspect operation logs and backups; automatic deletion/adoption is not supported.",
                        }
                    )
    return records


def workspace(path):
    project = services.open_project(Path(path), validate_config=False)
    warnings = []
    try:
        config = project.config
        configuration = config.model_dump(mode="json")
        name = config.run_name
        providers = provider_states(config)
    except Exception as exc:
        configuration, name, providers = None, project.root.name, {}
        warnings.append(
            f"Current configuration cannot be read: {exc}. Frozen artifacts remain inspectable."
        )
    status = services.project_status(project)
    screening = services.screening_report(project)
    if (
        configuration
        and screening["policy"]
        and screening["policy"]["config"] != configuration["screening"]
    ):
        warnings.append(
            "Current screening configuration differs from the active evidence policy. Existing observations and rules remain authoritative until a new policy is run."
        )
    latest = {}
    for obs in screening["observations"]:
        latest[(obs["candidate_id"], obs["asset_id"], obs["source"])] = obs
    current_assets = {c["candidate_id"]: c["asset_id"] for c in screening["candidates"]}
    screening["observation_counts"] = dict(
        Counter(
            o["status"]
            for o in latest.values()
            if current_assets.get(o["candidate_id"]) == o["asset_id"]
        )
    )
    screening["observation_count_basis"] = (
        "Latest saved observation per candidate/current asset/source; several detectors may contribute. Dispositions use the active policy."
    )
    by_id = {c["candidate_id"]: c for c in screening["candidates"]}
    conn = init_db(project.paths.db_path)
    try:
        curation = project_projection(conn, project.root)
        status["curation"] = {k: v for k, v in curation.items() if k != "rows"}
        human_rows = {r["candidate_id"]: r for r in curation["rows"]}
        candidates = []
        for row in conn.execute(
            "SELECT c.*,i.width,i.height,i.download_status,i.file_path,r.label,r.notes,r.reviewer_email FROM candidates c JOIN images i ON i.local_id=c.primary_local_id LEFT JOIN reviews r ON r.local_id=c.primary_local_id ORDER BY c.rowid"
        ):
            candidate = dict(row)
            candidate.update(human_rows[row["candidate_id"]])
            candidate["displayable"] = candidate["available"]
            if candidate["available"]:
                try:
                    path = project.resolve(candidate["file_path"])
                    stat = path.stat()
                    candidate["displayable"] = _displayable(
                        str(path), stat.st_size, stat.st_mtime_ns
                    )
                except (OSError, ValueError):
                    candidate["displayable"] = False
                if not candidate["displayable"]:
                    candidate["availability"] = "Unreadable local file"
            candidate["sources"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM discoveries WHERE candidate_id=? ORDER BY rowid",
                    (row["candidate_id"],),
                )
            ]
            candidate["screening"] = by_id[row["candidate_id"]]
            candidates.append(candidate)
        counts = Counter(c["asset_id"] for c in candidates if c["asset_id"])
        for c in candidates:
            c["shared_asset_candidates"] = counts[c["asset_id"]] if c["asset_id"] else 0
        summary = status["curation"]["summary"]
        unreadable = sum(c["available"] and not c["displayable"] for c in candidates)
        summary["unreadable_local"] = unreadable
        summary["available"] -= unreadable
        summary["unavailable"] += unreadable
        summary["unique_images"] = len({c["asset_id"] for c in candidates if c["displayable"]})
    finally:
        conn.close()
    artifacts = services.list_frozen_artifacts(project)
    recovery = recovery_diagnostics(project, artifacts)
    if recovery:
        warnings.append(
            f"Recovery attention: {len(recovery)} lock/pending/unregistered entries. Preserve evidence; see Project details."
        )
    if status["missing_assets"]:
        warnings.append(f"{len(status['missing_assets'])} recorded asset locations are missing.")
    return Workspace(
        str(project.root),
        name,
        project.project_id,
        status,
        configuration,
        candidates,
        screening,
        artifacts,
        warnings,
        providers,
        recovery,
    )


def inspect_candidate(path, candidate_id):
    project = services.open_project(Path(path), validate_config=False)
    conn = init_db(project.paths.db_path)
    try:
        row = conn.execute(
            "SELECT primary_local_id,asset_id FROM candidates WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Unknown candidate")
        local = row[0]
        evidence = candidate_evidence(conn, local)
        evidence["review"] = dict(
            conn.execute("SELECT * FROM reviews WHERE local_id=?", (local,)).fetchone()
        )
        evidence["review_events"] = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM review_events WHERE local_id=? ORDER BY id", (local,)
            )
        ]
        evidence["shared_candidates"] = [
            dict(r)
            for r in conn.execute(
                "SELECT c.candidate_id,c.primary_local_id,r.label FROM candidates c LEFT JOIN reviews r ON r.local_id=c.primary_local_id WHERE c.asset_id=?",
                (row[1],),
            )
        ]
        for shared in evidence["shared_candidates"]:
            shared["sources"] = [
                dict(r)
                for r in conn.execute(
                    "SELECT * FROM discoveries WHERE candidate_id=? ORDER BY rowid",
                    (shared["candidate_id"],),
                )
            ]
        image = conn.execute(
            "SELECT title,image_url,width,height,download_status,file_path,file_size_bytes,duplicate_of_local_id,phash FROM images WHERE local_id=?",
            (local,),
        ).fetchone()
        evidence["near_duplicate"] = dict(image)
        evidence["image"] = dict(image)
    finally:
        conn.close()
    report = services.screening_report(project)
    evidence["screening"] = {
        key: [r for r in report[key] if r["candidate_id"] == candidate_id]
        for key in ("candidates", "observations", "evaluations", "overrides")
    }
    preview = b""
    if image["file_path"]:
        try:
            with Image.open(project.resolve(image["file_path"])) as original:
                display = original.convert("RGB")
                display.thumbnail((1400, 1000))
                buffer = io.BytesIO()
                display.save(buffer, format="PNG")
                preview = buffer.getvalue()
        except (OSError, ValueError) as exc:
            evidence["preview_error"] = str(exc)
    return {"candidate_id": candidate_id, "evidence": evidence, "preview": preview}


def freeze_preparation(path, request):
    """Validated backend summary for a human confirmation, not GUI-derived inclusion."""
    from stimulus_harvester.frozen.snapshot import snapshot
    from stimulus_harvester.frozen.store import canonical, ensure_schema, sha
    from stimulus_harvester.storage import project_lock

    project = services.open_project(Path(path))
    with project_lock(project.root):
        conn = init_db(project.paths.db_path)
        try:
            ensure_schema(conn)
            conn.execute("BEGIN IMMEDIATE")
            data = snapshot(conn, project, request)
            return {
                **data["validation"],
                "snapshot_sha256": sha(canonical(data)),
                "scope": data["scope"],
                "duplicates": data["duplicates"],
                "membership": data["membership"],
                "summary": {
                    "scope_size": len(data["candidates"]),
                    "reviews": dict(Counter(c["review"]["label"] for c in data["candidates"])),
                    "screening_exclusions": sum(
                        c["screening"]["effective_disposition"] == "excluded"
                        for c in data["candidates"]
                    ),
                    "effective_overrides": sum(
                        bool(c["screening"]["override"]) for c in data["candidates"]
                    ),
                    "duplicate_adjudications": data["request"]["duplicate_resolutions"],
                    "parent_freeze_id": data["request"]["parent_freeze_id"],
                    "investigator": data["request"]["reviewer"],
                },
            }
        finally:
            conn.rollback()
            conn.close()


def create_desktop_fixture(path):
    """CP5 fixture at the unresolved review boundary; no actual model or network."""
    from stimulus_harvester.frozen.demo import config, screen, seed

    project = services.create_project(Path(path), config())
    a = seed(project, "accepted", label="accepted")
    seed(project, "duplicate", label="accepted", duplicate=a["path"], license="all-rights-reserved")
    seed(project, "conflict", label="rejected", duplicate=a["path"], license=None)
    seed(project, "rejected", label="rejected")
    seed(project, "excluded", label="rejected")
    seed(project, "overridden", label="maybe")
    screen(
        project,
        {"accepted": 0.25, "duplicate": 0.25, "conflict": 0.25, "excluded": 0.9, "overridden": 0.9},
    )
    return project


def configure_person_screening(project, *, model, low_threshold, high_threshold, policy, device):
    """Explicit user-chosen policy recipe; not a production detector/threshold default."""
    if policy not in {"strict_zero_human", "review_all"}:
        raise ValueError("Choose an explicit screening rule policy")
    detector = {
        "id": "desktop-human",
        "property": "human_presence",
        "model": model,
        "low_threshold": low_threshold,
        "high_threshold": high_threshold,
        "device": device,
    }
    outcomes = {
        s: "excluded" if s == "detected" and policy == "strict_zero_human" else "review_required"
        for s in ("detected", "uncertain", "not_detected", "error", "not_run")
    }
    config = project.config.model_dump(mode="json")
    config["screening"] = {
        "detectors": [detector],
        "rules": [
            {
                "id": policy,
                "version": "1",
                "property": "human_presence",
                "source": detector["id"],
                "outcomes": outcomes,
            }
        ],
    }
    return save_configuration(project, configuration=config)

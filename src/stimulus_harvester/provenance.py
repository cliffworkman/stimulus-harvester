"""Per-run reproducibility manifest.

Generic, task-agnostic environment/version capture written to
`runs/RUN_ID/manifests/reproducibility.json`. Each pipeline stage calls into this
module to record what it knows (config hash at `search` time; CLIP model/checkpoint
and GPU info at `score` time; VLM backend/model when the VLM stage runs; a completion
timestamp for every stage). The file is read-modify-written so later stages add to it
without clobbering what earlier stages recorded.
"""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any

from stimulus_harvester.config import ClipConfig, HarvesterConfig, config_to_yaml_str

_CORE_PACKAGES = [
    "click",
    "pydantic",
    "PyYAML",
    "httpx",
    "Pillow",
    "ImageHash",
    "jinja2",
    "fastapi",
    "uvicorn",
]
_CLIP_PACKAGES = ["torch", "open_clip_torch"]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def git_commit(cwd: Path | None = None) -> str | None:
    """Best-effort current git commit hash; None if unavailable (no git, not a repo)."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd or Path(__file__).resolve().parents[2],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _package_versions(package_names: list[str]) -> dict[str, str | None]:
    versions: dict[str, str | None] = {}
    for name in package_names:
        try:
            versions[name] = importlib_metadata.version(name)
        except importlib_metadata.PackageNotFoundError:
            versions[name] = None
    return versions


def torch_cuda_info() -> dict[str, Any]:
    """Lazy-imported torch/CUDA/GPU info; gracefully reports absence if torch isn't
    installed (the [clip] extra is optional)."""
    try:
        import torch
    except ImportError:
        return {
            "torch_installed": False,
            "torch_version": None,
            "cuda_available": False,
            "cuda_version": None,
            "gpu_name": None,
        }
    cuda_available = torch.cuda.is_available()
    return {
        "torch_installed": True,
        "torch_version": torch.__version__,
        "cuda_available": cuda_available,
        "cuda_version": torch.version.cuda if cuda_available else None,
        "gpu_name": torch.cuda.get_device_name(0) if cuda_available else None,
    }


def config_hash(config: HarvesterConfig) -> str:
    canonical = config_to_yaml_str(config)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_json_file(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def init_reproducibility_manifest(
    reproducibility_path: Path, *, run_id: str, config: HarvesterConfig, config_path: Path
) -> None:
    """Called once by `search`, at run creation time."""
    data = _read(reproducibility_path)
    data.update(
        {
            "run_id": run_id,
            "created_at": data.get("created_at", _now_iso()),
            "git_commit": git_commit(),
            "python_version": sys.version,
            "platform": platform.platform(),
            "package_versions": _package_versions(_CORE_PACKAGES),
            "config_hash": config_hash(config),
            "config_path": str(config_path),
        }
    )
    data.setdefault("stage_timestamps", {})
    _write(reproducibility_path, data)


def mark_stage_complete(reproducibility_path: Path, stage: str) -> None:
    data = _read(reproducibility_path)
    data.setdefault("stage_timestamps", {})
    data["stage_timestamps"][stage] = _now_iso()
    _write(reproducibility_path, data)


def record_clip_environment(
    reproducibility_path: Path, *, clip_config: ClipConfig, prompt_manifest_hash: str
) -> None:
    data = _read(reproducibility_path)
    data["clip"] = {
        "model_name": clip_config.model_name,
        "pretrained": clip_config.pretrained,
        "device_setting": clip_config.device,
        "prompt_manifest_hash": prompt_manifest_hash,
        "package_versions": _package_versions(_CLIP_PACKAGES),
        **torch_cuda_info(),
    }
    _write(reproducibility_path, data)


def record_vlm_environment(
    reproducibility_path: Path,
    *,
    backend_name: str,
    model_name: str | None,
    field_schema_hash: str | None,
) -> None:
    data = _read(reproducibility_path)
    data["vlm"] = {
        "backend_name": backend_name,
        "model_name": model_name,
        "field_schema_hash": field_schema_hash,
    }
    _write(reproducibility_path, data)

"""Run directory layout and run-id/config-snapshot plumbing.

Layout (see README for the full picture):

    runs/RUN_ID/
      manifests/   # config_snapshot.yaml, clip_prompts.json, vlm_field_schema.json,
                   # reproducibility.json, *.jsonl append logs
      raw_api/     # raw search API responses, for reproducibility
      images/{original, filtered, accepted}/
      exports/
      review/state.db
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from stimulus_harvester.config import HarvesterConfig, load_config


def slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return slug or "run"


def new_run_id(run_name: str, *, now: datetime | None = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%d_%H%M%S")
    return f"{slugify(run_name)}_{ts}"


@dataclass(frozen=True)
class RunPaths:
    root: Path

    @property
    def manifests(self) -> Path:
        return self.root / "manifests"

    @property
    def raw_api(self) -> Path:
        return self.root / "raw_api"

    @property
    def images_original(self) -> Path:
        return self.root / "images" / "original"

    @property
    def images_filtered(self) -> Path:
        return self.root / "images" / "filtered"

    @property
    def images_accepted(self) -> Path:
        return self.root / "images" / "accepted"

    @property
    def exports(self) -> Path:
        return self.root / "exports"

    @property
    def review_dir(self) -> Path:
        return self.root / "review"

    @property
    def db_path(self) -> Path:
        return self.review_dir / "state.db"

    @property
    def config_snapshot_path(self) -> Path:
        return self.manifests / "config_snapshot.yaml"

    @property
    def reproducibility_path(self) -> Path:
        return self.manifests / "reproducibility.json"

    @property
    def clip_prompts_path(self) -> Path:
        return self.manifests / "clip_prompts.json"

    @property
    def vlm_field_schema_path(self) -> Path:
        return self.manifests / "vlm_field_schema.json"

    @property
    def log_path(self) -> Path:
        return self.root / "run.log"

    def all_dirs(self) -> list[Path]:
        return [
            self.manifests,
            self.raw_api,
            self.images_original,
            self.images_filtered,
            self.images_accepted,
            self.exports,
            self.review_dir,
        ]

    def ensure(self) -> None:
        for d in self.all_dirs():
            d.mkdir(parents=True, exist_ok=True)


def run_paths_for(output_root: Path, run_id: str) -> RunPaths:
    return RunPaths(root=Path(output_root) / run_id)


def find_run_dir(run_id: str, *, search_roots: list[Path] | None = None) -> Path:
    """Locate an existing run directory by id, defaulting to ./runs."""
    roots = search_roots or [Path("runs")]
    for root in roots:
        candidate = root / run_id
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        f"No run directory found for run-id '{run_id}' under {[str(r) for r in roots]}. "
        "Did you run `search` first, and are you invoking from the same working directory?"
    )


def load_run_config(run_dir: Path) -> HarvesterConfig:
    paths = RunPaths(root=run_dir)
    if not paths.config_snapshot_path.exists():
        raise FileNotFoundError(
            f"No config snapshot at {paths.config_snapshot_path}. This run directory "
            "was not created by `search`, or is corrupted."
        )
    return load_config(paths.config_snapshot_path)

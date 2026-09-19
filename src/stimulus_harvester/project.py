"""Durable, relocatable Harvester projects; no registry or cwd-based identity."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from stimulus_harvester.config import HarvesterConfig, config_to_yaml_str
from stimulus_harvester.paths import RunPaths, load_run_config
from stimulus_harvester.review.db import init_db
from stimulus_harvester.storage import SCHEMA_VERSION, new_id, resolve_path


@dataclass(frozen=True)
class Project:
    root: Path
    project_id: str
    schema_version: int

    @property
    def paths(self) -> RunPaths:
        return RunPaths(self.root)

    @property
    def config(self) -> HarvesterConfig:
        return load_run_config(self.root)

    def resolve(self, value: str | Path) -> Path:
        return resolve_path(self.root, value)


def _write_identity(root: Path, metadata: dict) -> None:
    manifest = root / "project.json"
    if manifest.exists():
        existing = json.loads(manifest.read_text(encoding="utf-8"))
        if existing != metadata:
            raise ValueError("project.json does not match the authoritative database identity")
        return
    temporary = root / f".{new_id('identity')}.json"
    temporary.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest)


def open_project(path: Path) -> Project:
    root = Path(path).expanduser().resolve()
    paths = RunPaths(root)
    if not paths.db_path.is_file() or not paths.config_snapshot_path.is_file():
        raise ValueError(f"Not a Harvester project or legacy run: {root}")
    load_run_config(root)  # Validate before any migration.
    # Reject incompatible/tampered manifests before migrating a legacy database.
    if (root / "project.json").exists():
        manifest = json.loads((root / "project.json").read_text(encoding="utf-8"))
        if manifest.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("Unsupported project manifest schema")
    conn = init_db(paths.db_path)
    try:
        row = conn.execute("SELECT * FROM project_metadata WHERE singleton=1").fetchone()
        metadata = dict(row)
        metadata.pop("singleton")
    finally:
        conn.close()
    _write_identity(root, metadata)
    return Project(root, metadata["project_id"], metadata["schema_version"])


def create_project(path: Path, config: HarvesterConfig) -> Project:
    root = Path(path).expanduser().resolve()
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"Project destination must be new or empty: {root}")
    root.parent.mkdir(parents=True, exist_ok=True)
    staging = root.with_name(f".{root.name}.{new_id('creating')}")
    paths = RunPaths(staging)
    paths.ensure()
    paths.config_snapshot_path.write_text(config_to_yaml_str(config), encoding="utf-8")
    init_db(paths.db_path).close()
    project = open_project(staging)
    from stimulus_harvester.provenance import init_reproducibility_manifest

    init_reproducibility_manifest(
        paths.reproducibility_path,
        run_id=project.project_id,
        config=config,
        config_path=Path("manifests/config_snapshot.yaml"),
    )
    if root.exists():
        root.rmdir()  # Only the checked empty destination; never recursively remove user files.
    # Windows indexers/scanners can briefly hold a newly written SQLite folder.
    # Retry this same non-destructive rename only; never overwrite a destination.
    for attempt in range(6):
        try:
            os.rename(project.root, root)
            break
        except PermissionError:
            if os.name != "nt" or attempt == 5:
                raise
            time.sleep(0.1 * (attempt + 1))
    return open_project(root)


def project_status(project: Project) -> dict:
    conn = init_db(project.paths.db_path)
    try:
        counts = {
            table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("candidates", "assets", "discoveries", "acquisitions", "review_events")
        }
        reviews = {
            row[0]: row[1]
            for row in conn.execute("SELECT label, COUNT(*) FROM reviews GROUP BY label")
        }
        unresolved = []
        for row in conn.execute("SELECT path FROM asset_locations"):
            try:
                if not project.resolve(row[0]).is_file():
                    unresolved.append(row[0])
            except ValueError:
                unresolved.append(row[0])
        operations = [
            dict(row)
            for row in conn.execute(
                """SELECT o.*, e.status FROM operations o LEFT JOIN operation_events e
            ON e.event_id=(SELECT MAX(event_id) FROM operation_events WHERE operation_id=o.operation_id)
            ORDER BY o.started_at"""
            )
        ]
        return {
            "project_id": project.project_id,
            "schema_version": project.schema_version,
            "project_path": str(project.root),
            "identity_scope": "harvester-local/provisional",
            "counts": counts,
            "reviews": reviews,
            "missing_assets": unresolved,
            "migration_notes": [
                row[0] for row in conn.execute("SELECT message FROM migration_notes")
            ],
            "operations": operations,
            "accepted_folder": "legacy convenience output; not a frozen scientific artifact",
        }
    finally:
        conn.close()

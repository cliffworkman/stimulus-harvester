"""Append-only artifact registry with canonical manifests and verified file inventories."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from stimulus_harvester import __version__
from stimulus_harvester.provenance import git_commit
from stimulus_harvester.screening.detectors import file_hash
from stimulus_harvester.storage import ACTIVE_OPERATION, new_id, now


def canonical(value):
    return (
        json.dumps(
            value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        + "\n"
    ).encode("utf-8")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ensure_schema(conn):
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='frozen_schema'").fetchone():
        row = conn.execute("SELECT version FROM frozen_schema WHERE singleton=1").fetchone()
        if row is None or row[0] != 1:
            raise ValueError("Unsupported frozen artifact schema")
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "CREATE TABLE frozen_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO frozen_schema VALUES(1,1)")
        conn.execute("""CREATE TABLE frozen_artifacts(
            artifact_id TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK(kind IN ('freeze','assembly','frozen_export')),
            project_id TEXT NOT NULL, freeze_id TEXT NOT NULL REFERENCES frozen_artifacts(artifact_id),
            parent_id TEXT REFERENCES frozen_artifacts(artifact_id), created_at TEXT NOT NULL,
            operation_id TEXT NOT NULL REFERENCES operations(operation_id),
            relative_path TEXT NOT NULL UNIQUE, manifest_sha256 TEXT NOT NULL,
            manifest_json TEXT NOT NULL)""")
        for action in ("UPDATE", "DELETE"):
            conn.execute(
                f"CREATE TRIGGER frozen_no_{action.lower()} BEFORE {action} ON frozen_artifacts BEGIN SELECT RAISE(ABORT, 'Frozen artifacts are append-only'); END"
            )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def software(project, conn):
    package = Path(__file__).resolve().parents[1]
    versions = {"project": project.schema_version, "frozen": 1}
    for name in ("screening", "discovery"):
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name + "_schema",)).fetchone():
            versions[name] = conn.execute(
                f"SELECT version FROM {name}_schema WHERE singleton=1"
            ).fetchone()[0]
        else:
            versions[name] = None
    return {
        "version": __version__,
        "git_commit": git_commit(),
        "python": sys.version,
        "platform": platform.platform(),
        "schema_versions": versions,
        "package_python_sha256": {
            p.relative_to(package).as_posix(): file_hash(p) for p in sorted(package.rglob("*.py"))
        },
    }


def metadata(project, kind, *, freeze_id=None, parent_id=None):
    operation = ACTIVE_OPERATION.get()
    if not operation:
        raise ValueError("Artifact creation must run through shared services")
    artifact_id = new_id(kind)
    return {
        "format": "stimulus-harvester-frozen",
        "format_version": 1,
        "kind": kind,
        "artifact_id": artifact_id,
        "project_id": project.project_id,
        "freeze_id": freeze_id or artifact_id,
        "parent_id": parent_id,
        "created_at": now(),
        "operation_id": operation,
    }


def internal(root, relative):
    p = PurePosixPath(relative)
    if p.is_absolute() or ".." in p.parts or "\\" in relative or ":" in relative:
        raise ValueError("Unsafe packaged path")
    path = root.joinpath(*p.parts)
    if not path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("Packaged path escapes artifact")
    return path


def inventory(root):
    return [
        {"path": p.relative_to(root).as_posix(), "sha256": file_hash(p), "bytes": p.stat().st_size}
        for p in sorted(root.rglob("*"))
        if p.is_file()
    ]


def checksums(manifest, manifest_bytes):
    rows = {r["path"]: r["sha256"] for r in manifest["files"]}
    rows["manifest.json"] = sha(manifest_bytes)
    return "".join(f"{h}  {p}\n" for p, h in sorted(rows.items())).encode("utf-8")


@contextmanager
def staging(project, kind):
    parent = (
        project.root
        / {"freeze": "freezes", "assembly": "assemblies", "frozen_export": "frozen_exports"}[kind]
    )
    parent.mkdir(parents=True, exist_ok=True)
    path = parent / (".pending-" + new_id(kind))
    path.mkdir()
    try:
        yield path
    finally:
        # Delete only our verified, unpublished staging directory, never a user destination.
        if path.exists():
            if path.resolve().parent != parent.resolve() or not path.name.startswith(".pending-"):
                raise ValueError("Refusing cleanup outside artifact staging directory")
            shutil.rmtree(path)


def publish(conn, project, stage, manifest):
    manifest["files"] = inventory(stage)
    data = canonical(manifest)
    for name, content in (("manifest.json", data), ("checksums.sha256", checksums(manifest, data))):
        with (stage / name).open("xb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
    destination = stage.parent / manifest["artifact_id"]
    if destination.exists():
        raise FileExistsError("Artifact destination already exists")
    relative = destination.relative_to(project.root).as_posix()
    conn.execute(
        "INSERT INTO frozen_artifacts VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            manifest["artifact_id"],
            manifest["kind"],
            project.project_id,
            manifest["freeze_id"],
            manifest["parent_id"],
            manifest["created_at"],
            manifest["operation_id"],
            relative,
            sha(data),
            data.decode("utf-8"),
        ),
    )
    # Caller holds an IMMEDIATE transaction. Only complete, verified staging is published.
    # Windows indexers can briefly hold freshly written evidence. Retry only the
    # same non-overwriting rename, while retaining the same database transaction.
    for attempt in range(6):
        try:
            os.rename(stage, destination)
            break
        except PermissionError:
            if os.name != "nt" or attempt == 5:
                raise
            time.sleep(0.1 * (attempt + 1))
    conn.commit()
    return {
        "artifact_id": manifest["artifact_id"],
        "kind": manifest["kind"],
        "freeze_id": manifest["freeze_id"],
        "path": str(destination),
        "manifest_sha256": sha(data),
    }


def load(conn, project, artifact_id, *, kind=None):
    ensure_schema(conn)
    row = conn.execute(
        "SELECT * FROM frozen_artifacts WHERE artifact_id=?", (artifact_id,)
    ).fetchone()
    if row is None or (kind and row["kind"] != kind) or row["project_id"] != project.project_id:
        raise ValueError("Unknown artifact or wrong artifact kind/project")
    root = internal(project.root, row["relative_path"])
    data = (root / "manifest.json").read_bytes()
    if sha(data) != row["manifest_sha256"] or data.decode("utf-8") != row["manifest_json"]:
        raise ValueError("Immutable manifest integrity failure")
    return verify_directory(root, expected_sha256=row["manifest_sha256"]), root


def verify_directory(root, *, expected_sha256=None):
    """Portable package verification; an external expected hash supplies the trust anchor."""
    root = Path(root).resolve()
    data = (root / "manifest.json").read_bytes()
    if expected_sha256 and sha(data) != expected_sha256:
        raise ValueError("Manifest does not match the expected SHA-256")
    manifest = json.loads(data)
    if (
        manifest.get("format") != "stimulus-harvester-frozen"
        or manifest["format_version"] != 1
        or canonical(manifest) != data
    ):
        raise ValueError("Unsupported/noncanonical artifact manifest")
    expected = {"manifest.json", "checksums.sha256"}
    for entry in manifest["files"]:
        path = internal(root, entry["path"])
        if (
            entry["path"] in expected
            or not path.is_file()
            or file_hash(path) != entry["sha256"]
            or path.stat().st_size != entry["bytes"]
        ):
            raise ValueError(f"Artifact file integrity failure: {entry['path']}")
        expected.add(entry["path"])
    actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    if actual != expected or (root / "checksums.sha256").read_bytes() != checksums(manifest, data):
        raise ValueError("Artifact inventory/checksum integrity failure")
    return manifest


def copy_verified(source, destination, expected_sha256):
    if not source.is_file() or file_hash(source) != expected_sha256:
        raise ValueError(f"Missing/changed bytes: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(destination)
    shutil.copyfile(source, destination)
    if file_hash(destination) != expected_sha256 or file_hash(source) != expected_sha256:
        raise ValueError(f"Bytes changed while copying: {source}")

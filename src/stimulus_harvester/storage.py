"""Small, versioned lineage store alongside the legacy image/review projection.

The database is authoritative for project identity. Migration runs on a separate
SQLite copy and only replaces the original after validation. Legacy evidence files
and original image bytes are never rewritten by migration.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from uuid import uuid4

SCHEMA_VERSION = 1
ACTIVE_OPERATION: ContextVar[str | None] = ContextVar("harvester_operation", default=None)

_LINEAGE_SCHEMA = """
CREATE TABLE project_metadata (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    project_id TEXT NOT NULL UNIQUE, schema_version INTEGER NOT NULL,
    created_at TEXT NOT NULL, identity_scope TEXT NOT NULL
);
CREATE TABLE assets (
    asset_id TEXT PRIMARY KEY, sha256 TEXT NOT NULL UNIQUE,
    width INTEGER, height INTEGER, file_size_bytes INTEGER
);
CREATE TABLE candidates (
    candidate_id TEXT PRIMARY KEY,
    primary_local_id TEXT NOT NULL UNIQUE REFERENCES images(local_id),
    asset_id TEXT REFERENCES assets(asset_id)
);
CREATE TABLE legacy_ids (
    local_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id)
);
CREATE TABLE asset_locations (
    asset_id TEXT NOT NULL REFERENCES assets(asset_id),
    path TEXT NOT NULL, PRIMARY KEY(asset_id, path)
);
CREATE TABLE operations (
    operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, started_at TEXT NOT NULL,
    parameters_json TEXT NOT NULL
);
CREATE TABLE operation_events (
    event_id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id),
    occurred_at TEXT NOT NULL, status TEXT NOT NULL, details_json TEXT NOT NULL
);
CREATE TABLE discoveries (
    source_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    operation_id TEXT REFERENCES operations(operation_id),
    legacy_local_id TEXT NOT NULL, provider TEXT NOT NULL, provider_result_id TEXT,
    image_url TEXT NOT NULL, source_page_url TEXT, query TEXT, seed_query TEXT,
    rank INTEGER, page INTEGER, result_order INTEGER,
    retrieved_at TEXT, creator TEXT, license TEXT, license_url TEXT,
    raw_response_path TEXT, evidence_origin TEXT NOT NULL, record_json TEXT NOT NULL
);
CREATE INDEX discoveries_candidate ON discoveries(candidate_id);
CREATE TABLE acquisitions (
    acquisition_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
    operation_id TEXT REFERENCES operations(operation_id), asset_id TEXT REFERENCES assets(asset_id),
    status TEXT NOT NULL, recorded_at TEXT, record_json TEXT NOT NULL
);
CREATE TABLE migration_notes (id INTEGER PRIMARY KEY, message TEXT NOT NULL);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(kind: str) -> str:
    return f"{kind}_{uuid4().hex}"


def database_root(db_path: Path) -> Path:
    return db_path.parent.parent if db_path.parent.name == "review" else db_path.parent


def connection_root(conn: sqlite3.Connection) -> Path:
    return database_root(Path(conn.execute("PRAGMA database_list").fetchone()[2]))


@contextmanager
def project_lock(root: Path):
    """Exclude cooperating writers; never guess whether an existing lock is stale."""
    path = root / ".harvester.lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError(f"Project is busy or has an interrupted-operation lock: {path}") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps({"pid": os.getpid(), "created_at": now()}))
        yield
    finally:
        path.unlink()


def stored_path(root: Path, value: str | Path | None, *, legacy: bool = False) -> str | None:
    """Normalize owned paths; retain external legacy references explicitly.

    Legacy runs used cwd-relative and absolute paths. The known artifact-directory
    suffix allows a copied run to recover its own images without consulting cwd.
    Content hashes are checked independently during migration.
    """
    if value is None:
        return None
    raw = str(value).replace("\\", "/")
    root = root.resolve()
    p = Path(raw)
    if p.is_absolute():
        try:
            return p.resolve().relative_to(root).as_posix()
        except ValueError:
            if not legacy:
                return str(p)
    if legacy:
        parts = raw.split("/")
        for index, part in enumerate(parts):
            if part in {"images", "raw_api", "manifests", "operations"}:
                relative = Path(*parts[index:])
                if ".." in relative.parts:
                    raise ValueError(f"Unsafe artifact path: {value}")
                return relative.as_posix()
    if p.is_absolute() or PureWindowsPath(raw).is_absolute():
        return raw
    if ".." in p.parts:
        raise ValueError(f"Artifact path escapes project: {value}")
    return p.as_posix()


def resolve_path(root: Path, value: str | Path) -> Path:
    raw = str(value)
    p = Path(raw)
    if p.is_absolute():
        return p
    if PureWindowsPath(raw).is_absolute():
        raise ValueError(f"External Windows path cannot be resolved on this platform: {value}")
    resolved = (root / p).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"Artifact path escapes project: {value}")
    return resolved


def candidate_for(conn: sqlite3.Connection, local_id: str) -> str:
    row = conn.execute(
        "SELECT candidate_id FROM legacy_ids WHERE local_id=?", (local_id,)
    ).fetchone()
    if row:
        return row[0]
    candidate_id = new_id("candidate")
    conn.execute("INSERT INTO candidates VALUES (?, ?, NULL)", (candidate_id, local_id))
    conn.execute("INSERT INTO legacy_ids VALUES (?, ?)", (local_id, candidate_id))
    return candidate_id


def candidate_evidence(conn: sqlite3.Connection, local_id: str) -> dict:
    row = conn.execute(
        "SELECT c.* FROM candidates c JOIN legacy_ids l USING(candidate_id) WHERE l.local_id=?",
        (local_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"Unknown candidate/legacy ID: {local_id}")
    candidate = dict(row)
    candidate_id = candidate["candidate_id"]
    candidate["legacy_ids"] = [
        r[0]
        for r in conn.execute(
            "SELECT local_id FROM legacy_ids WHERE candidate_id=? ORDER BY local_id",
            (candidate_id,),
        )
    ]
    candidate["sources"] = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM discoveries WHERE candidate_id=? ORDER BY source_id", (candidate_id,)
        )
    ]
    candidate["acquisitions"] = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM acquisitions WHERE candidate_id=? ORDER BY acquisition_id",
            (candidate_id,),
        )
    ]
    candidate["asset"] = None
    if candidate["asset_id"]:
        candidate["asset"] = dict(
            conn.execute(
                "SELECT * FROM assets WHERE asset_id=?", (candidate["asset_id"],)
            ).fetchone()
        )
        candidate["asset"]["locations"] = [
            r[0]
            for r in conn.execute(
                "SELECT path FROM asset_locations WHERE asset_id=? ORDER BY path",
                (candidate["asset_id"],),
            )
        ]
    return candidate


def record_discovery(
    conn: sqlite3.Connection,
    record: dict,
    local_id: str,
    *,
    origin: str = "search",
    source_id: str | None = None,
) -> str:
    candidate_id = candidate_for(conn, local_id)
    legacy_id = record.get("local_id", local_id)
    conn.execute("INSERT OR IGNORE INTO legacy_ids VALUES (?, ?)", (legacy_id, candidate_id))
    source_id = source_id or new_id("source")
    raw_path = stored_path(connection_root(conn), record.get("raw_response_path"), legacy=True)
    conn.execute(
        """INSERT INTO discoveries VALUES
        (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            source_id,
            candidate_id,
            ACTIVE_OPERATION.get(),
            legacy_id,
            record.get("source_backend", "unknown"),
            record.get("external_id"),
            record.get("image_url", ""),
            record.get("landing_page_url"),
            record.get("search_query"),
            record.get("seed_query"),
            record.get("rank"),
            record.get("page"),
            record.get("result_order"),
            record.get("retrieved_at"),
            record.get("creator"),
            record.get("license"),
            record.get("license_url"),
            raw_path,
            origin,
            json.dumps(record, ensure_ascii=False, sort_keys=True),
        ),
    )
    return source_id


def record_acquisition(conn: sqlite3.Connection, record: dict, *, verify: bool = False) -> None:
    local_id = record["local_id"]
    candidate_id = candidate_for(conn, local_id)
    digest = record.get("sha256")
    path = stored_path(connection_root(conn), record.get("file_path"), legacy=True)
    asset_id = None
    if record.get("status") == "success" and digest:
        if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest.lower()):
            raise ValueError(f"Invalid SHA-256 for {local_id}")
        digest = digest.lower()
        if verify and path:
            disk = resolve_path(connection_root(conn), path)
            if disk.exists():
                from stimulus_harvester.download.integrity import sha256_of_file

                if sha256_of_file(disk) != digest:
                    raise ValueError(f"Content hash mismatch during migration: {local_id}")
            else:
                conn.execute(
                    "INSERT INTO migration_notes(message) VALUES (?)",
                    (f"Missing original for {local_id}: {path}; identity is historical metadata",),
                )
        asset_id = "sha256:" + digest
        conn.execute(
            "INSERT OR IGNORE INTO assets VALUES (?, ?, ?, ?, ?)",
            (
                asset_id,
                digest,
                record.get("width"),
                record.get("height"),
                record.get("file_size_bytes"),
            ),
        )
        if path:
            conn.execute("INSERT OR IGNORE INTO asset_locations VALUES (?, ?)", (asset_id, path))
        conn.execute(
            "UPDATE candidates SET asset_id=? WHERE candidate_id=?", (asset_id, candidate_id)
        )
    conn.execute(
        "INSERT INTO acquisitions VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            new_id("acquisition"),
            candidate_id,
            ACTIVE_OPERATION.get(),
            asset_id,
            record.get("status", "unknown"),
            record.get("downloaded_at"),
            json.dumps(record, ensure_ascii=False, sort_keys=True),
        ),
    )


def _populate_legacy(conn: sqlite3.Connection, root: Path) -> None:
    rows = conn.execute("SELECT * FROM images ORDER BY local_id").fetchall()
    for row in rows:
        record = dict(row)
        candidate_for(conn, row["local_id"])
        record_discovery(conn, record, row["local_id"], origin="legacy_database")
        if row["download_status"]:
            record["status"] = row["download_status"]
            record_acquisition(conn, record, verify=True)
        if row["file_path"]:
            path = stored_path(root, row["file_path"], legacy=True)
            conn.execute("UPDATE images SET file_path=? WHERE local_id=?", (path, row["local_id"]))
    evidence = root / "manifests" / "search_results.jsonl"
    if evidence.exists():
        for line_number, line in enumerate(evidence.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            record = json.loads(line)  # malformed evidence aborts migration, never silently skipped
            row = conn.execute(
                "SELECT local_id FROM images WHERE local_id=?", (record["local_id"],)
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT local_id FROM images WHERE image_url=? ORDER BY local_id",
                    (record["image_url"],),
                ).fetchone()
            if row is None:
                # Historical discovery may have been written before an interrupted DB insert.
                from stimulus_harvester.models import SearchResult
                from stimulus_harvester.review.db import upsert_image_from_search

                result = SearchResult.model_validate(record)
                upsert_image_from_search(conn, result, root.name, record_source=False, commit=False)
                local_id = result.local_id
            else:
                local_id = row[0]
            record_discovery(
                conn, record, local_id, origin=f"manifests/search_results.jsonl:{line_number}"
            )


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def connect_database(db_path: Path, legacy_schema: str) -> sqlite3.Connection:
    db_path = db_path.resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    root = database_root(db_path)
    if db_path.exists():
        conn = _connect(db_path)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == SCHEMA_VERSION:
            metadata = conn.execute("SELECT schema_version FROM project_metadata").fetchone()
            if metadata is None or metadata[0] != version:
                conn.close()
                raise ValueError("Project schema metadata disagrees with SQLite user_version")
            return conn
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        if version != 0:
            raise ValueError(f"Unsupported database schema {version}; supported: {SCHEMA_VERSION}")
        if journal_mode == "wal":
            raise ValueError(
                "Legacy WAL databases require an offline backup in DELETE journal mode before migration"
            )
    with project_lock(root):
        # Recheck after acquiring the lock.
        if db_path.exists():
            conn = _connect(db_path)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            conn.close()
            if version == SCHEMA_VERSION:
                return _connect(db_path)
            if version != 0:
                raise ValueError(f"Unsupported database schema {version}")
        migration_id = new_id("migration")
        staging = db_path.with_name(f".{migration_id}.db")
        backup = db_path.with_name(f"state.pre-v1.{migration_id}.db")
        existed = db_path.exists()
        if existed:
            with closing(_connect(db_path)) as original, closing(_connect(backup)) as saved:
                original.backup(saved)
            with closing(_connect(backup)) as saved, closing(_connect(staging)) as target:
                saved.backup(target)
        conn = _connect(staging)
        try:
            if not existed:
                conn.executescript(legacy_schema)
            else:
                for table in ("images", "reviews", "review_events"):
                    conn.execute(f"SELECT * FROM {table} LIMIT 0")
            conn.executescript(_LINEAGE_SCHEMA)
            conn.execute(
                "INSERT INTO project_metadata VALUES (1, ?, ?, ?, ?)",
                (new_id("project"), SCHEMA_VERSION, now(), "harvester-local/provisional"),
            )
            _populate_legacy(conn, root)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            if conn.execute("PRAGMA foreign_key_check").fetchall():
                raise ValueError("Migration produced invalid foreign keys")
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Migration failed SQLite integrity check")
            conn.commit()
        except BaseException:
            conn.close()
            staging.unlink(missing_ok=True)
            raise
        conn.close()
        os.replace(staging, db_path)
    return _connect(db_path)

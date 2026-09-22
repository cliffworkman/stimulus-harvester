"""Additive discovery evidence schema, separate from the stable project identity schema."""

from __future__ import annotations

import json
import os
from pathlib import Path

from stimulus_harvester.storage import now, new_id, stored_path

DISCOVERY_SCHEMA_VERSION = 1


def ensure_discovery_schema(conn):
    """Atomic additive migration: failures roll back every new table, never old evidence."""
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_schema'").fetchone()
    if exists:
        row = conn.execute("SELECT version FROM discovery_schema WHERE singleton=1").fetchone()
        if row is None or row[0] != DISCOVERY_SCHEMA_VERSION:
            raise ValueError("Unsupported discovery evidence schema")
        return
    statements = [
        "CREATE TABLE discovery_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)",
        """CREATE TABLE discovery_events(
            event_id TEXT PRIMARY KEY, operation_id TEXT REFERENCES operations(operation_id),
            attempt_id TEXT, query_id TEXT, provider TEXT NOT NULL, query TEXT,
            page INTEGER, attempt INTEGER, status TEXT NOT NULL, timestamp TEXT NOT NULL,
            raw_response_path TEXT, record_json TEXT NOT NULL)""",
        "CREATE INDEX discovery_events_operation ON discovery_events(operation_id)",
        "CREATE INDEX discovery_events_attempt ON discovery_events(attempt_id)",
        "INSERT INTO discovery_schema VALUES(1, 1)",
    ]
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


class Recorder:
    def __init__(self, conn, root: Path, operation_id: str):
        self.conn, self.root, self.operation_id = conn, root, operation_id
        self.directory = root / "operations" / operation_id / "discovery"
        self.directory.mkdir(parents=True, exist_ok=True)
        ensure_discovery_schema(conn)

    def prepare(self, event, *, write=True):
        event = dict(event)
        event.setdefault("event_id", new_id("discovery_event"))
        event.setdefault("timestamp", now())
        event["operation_id"] = self.operation_id
        event["raw_response_path"] = stored_path(
            self.root, event.get("raw_response_path"), legacy=True
        )
        encoded = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if write:
            self.write_artifact(event, encoded)
        return event, encoded

    def write_artifact(self, event, encoded):
        with (self.directory / (event["event_id"] + ".json")).open("x", encoding="utf-8") as handle:
            handle.write(encoded + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def insert(self, event, encoded):
        self.conn.execute(
            "INSERT INTO discovery_events VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                event["event_id"],
                self.operation_id,
                event.get("attempt_id"),
                event.get("query_id"),
                event["provider"],
                event.get("query"),
                event.get("page"),
                event.get("attempt"),
                event["status"],
                event["timestamp"],
                event.get("raw_response_path"),
                encoded,
            ),
        )

    def __call__(self, event):
        prepared = self.prepare(event)
        with self.conn:
            self.insert(*prepared)


def discovery_summary(conn, operation_id):
    records = [
        json.loads(row[0])
        for row in conn.execute(
            "SELECT record_json FROM discovery_events WHERE operation_id=? ORDER BY rowid",
            (operation_id,),
        )
    ]
    pages = [r for r in records if r["status"] == "page_committed"]
    failures = [r for r in records if r["status"] == "failed"]
    unresolved = [r for r in failures if not r.get("will_retry", False)]
    issues = unresolved + [r for r in records if r["status"] in {"engine_errors", "item_rejected"}]
    return {
        "operation_id": operation_id,
        "discovery_status": ("partial" if pages else "failed") if issues else "succeeded",
        "pages_committed": len(pages),
        "source_occurrences": sum(r["result_count"] for r in pages),
        "failed_attempts": len(failures),
        "unresolved_failures": len(unresolved),
        "issues": issues,
        "completions": [r for r in records if r["status"] in {"query_exhausted", "query_completed"}],
        "events": len(records),
    }


def read_attempts(conn, operation_id=None):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='discovery_events'").fetchone():
        return []
    sql = "SELECT record_json FROM discovery_events"
    params = ()
    if operation_id:
        sql += " WHERE operation_id=?"
        params = (operation_id,)
    return [json.loads(row[0]) for row in conn.execute(sql + " ORDER BY rowid", params)]

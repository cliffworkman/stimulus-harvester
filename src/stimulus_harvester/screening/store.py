"""Additive screening schema; observations, rules and adjudications are append-only."""

import json
from stimulus_harvester.screening.models import Observation


def ensure_schema(conn):
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name='screening_schema'").fetchone()
    if exists:
        row = conn.execute("SELECT version FROM screening_schema WHERE singleton=1").fetchone()
        if row is None or row[0] != 1:
            raise ValueError("Unsupported screening schema")
        return
    statements = [
        "CREATE TABLE screening_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL)",
        "INSERT INTO screening_schema VALUES(1,1)",
        "CREATE TABLE screening_policies(policy_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL REFERENCES operations(operation_id), timestamp TEXT NOT NULL, record_json TEXT NOT NULL)",
        """CREATE TABLE observations(observation_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id), asset_id TEXT REFERENCES assets(asset_id),
            property TEXT NOT NULL, source TEXT NOT NULL, status TEXT NOT NULL,
            operation_id TEXT NOT NULL REFERENCES operations(operation_id), timestamp TEXT NOT NULL, record_json TEXT NOT NULL)""",
        "CREATE INDEX observations_candidate ON observations(candidate_id,source)",
        """CREATE TABLE screening_evaluations(evaluation_id TEXT PRIMARY KEY,
            policy_id TEXT NOT NULL REFERENCES screening_policies(policy_id),
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id), asset_id TEXT REFERENCES assets(asset_id),
            rule_id TEXT NOT NULL, observation_id TEXT NOT NULL REFERENCES observations(observation_id),
            disposition TEXT NOT NULL, operation_id TEXT NOT NULL REFERENCES operations(operation_id),
            timestamp TEXT NOT NULL, record_json TEXT NOT NULL)""",
        "CREATE INDEX evaluations_candidate ON screening_evaluations(candidate_id,policy_id)",
        """CREATE TABLE screening_overrides(override_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id),
            operation_id TEXT NOT NULL REFERENCES operations(operation_id), timestamp TEXT NOT NULL, record_json TEXT NOT NULL)""",
    ]
    for table in (
        "observations",
        "screening_evaluations",
        "screening_overrides",
        "screening_policies",
    ):
        for action in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT, 'Screening evidence is append-only'); END"
            )
    conn.execute("BEGIN IMMEDIATE")
    try:
        for statement in statements:
            conn.execute(statement)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def append_observation(conn, record):
    obs = Observation.model_validate(record).model_dump(mode="json")
    conn.execute(
        "INSERT INTO observations VALUES(?,?,?,?,?,?,?,?,?)",
        (
            obs["observation_id"],
            obs["candidate_id"],
            obs["asset_id"],
            obs["property"],
            obs["source"],
            obs["status"],
            obs["operation_id"],
            obs["timestamp"],
            json.dumps(obs, allow_nan=False),
        ),
    )
    return obs


def records(conn, table, where="", params=()):
    if table not in {
        "observations",
        "screening_evaluations",
        "screening_overrides",
        "screening_policies",
    }:
        raise ValueError("Unknown screening table")
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
        return []
    return [
        json.loads(r[0])
        for r in conn.execute(f"SELECT record_json FROM {table} {where} ORDER BY rowid", params)
    ]

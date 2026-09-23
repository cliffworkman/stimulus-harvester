"""Candidate membership, independent reviews and deterministic scoped screening.

All evidence is append-only. Current state is a projection of ordered events.
Only shared services perform mutations; collection reads never migrate a database.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import Counter
from contextvars import ContextVar

from stimulus_harvester.storage import ACTIVE_OPERATION, connection_root, new_id, now

SEARCH_CONTEXT = ContextVar("collection_search_context", default=None)
TABLES = (
    "collection_events",
    "membership_events",
    "collection_reviews",
    "collection_policies",
    "collection_evaluations",
    "collection_overrides",
)


def exists(conn):
    present = bool(
        conn.execute("SELECT 1 FROM sqlite_master WHERE name='collections_schema'").fetchone()
    )
    if (
        present
        and conn.execute("SELECT version FROM collections_schema WHERE singleton=1").fetchone()[0]
        != 1
    ):
        raise ValueError("Unsupported collections schema")
    return present


def ensure_schema(conn):
    if exists(conn):
        if conn.execute("SELECT version FROM collections_schema").fetchone()[0] != 1:
            raise ValueError("Unsupported collections schema")
        return
    if conn.in_transaction:
        raise ValueError("Collection migration requires a transaction boundary")
    if (
        conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
        or conn.execute("PRAGMA foreign_key_check").fetchall()
    ):
        raise ValueError("Database integrity check failed before collections migration")
    root = connection_root(conn)
    backup = root / "migrations" / new_id("collections_v1")
    backup.mkdir(parents=True)
    path = backup / "before.sqlite"
    with sqlite3.connect(path) as copy:
        conn.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("Collection migration backup is invalid")
    before = {
        r[0]: conn.execute(f'SELECT count(*) FROM "{r[0]}"').fetchone()[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    receipt = {
        "schema": "collections",
        "version": 1,
        "timestamp": now(),
        "operation_id": ACTIVE_OPERATION.get(),
        "backup_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "before_counts": before,
        "status": "backed_up",
    }
    evidence = backup / "migration.json"
    evidence.write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "CREATE TABLE collections_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL CHECK(version=1))"
        )
        conn.execute("INSERT INTO collections_schema VALUES(1,1)")
        conn.execute("CREATE TABLE collections(collection_id TEXT PRIMARY KEY)")
        conn.execute("""CREATE TABLE collection_memberships(membership_id TEXT PRIMARY KEY,
            collection_id TEXT NOT NULL REFERENCES collections(collection_id),
            candidate_id TEXT NOT NULL REFERENCES candidates(candidate_id), UNIQUE(collection_id,candidate_id))""")
        conn.execute("""CREATE TABLE collection_search_contexts(operation_id TEXT PRIMARY KEY REFERENCES operations(operation_id),
            collection_id TEXT NOT NULL REFERENCES collections(collection_id), record_json TEXT NOT NULL)""")
        for table in TABLES:
            conn.execute(f"""CREATE TABLE {table}(sequence INTEGER PRIMARY KEY, event_id TEXT NOT NULL UNIQUE,
                collection_id TEXT NOT NULL REFERENCES collections(collection_id),
                candidate_id TEXT REFERENCES candidates(candidate_id),
                operation_id TEXT NOT NULL REFERENCES operations(operation_id), record_json TEXT NOT NULL)""")
            conn.execute(f"CREATE INDEX {table}_scope ON {table}(collection_id,candidate_id)")
        for table in (
            *TABLES,
            "collections",
            "collection_memberships",
            "collection_search_contexts",
        ):
            for action in ("UPDATE", "DELETE"):
                conn.execute(
                    f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'Collection evidence is append-only'); END"
                )
        assert all(
            conn.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] == n
            for t, n in before.items()
        )
        if (
            conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or conn.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise ValueError("Collection migration foreign-key check failed")
        conn.commit()
        receipt["status"] = "verified"
    except BaseException:
        conn.rollback()
        receipt["status"] = "rolled_back"
        raise
    finally:
        evidence.write_text(json.dumps(receipt, indent=2), encoding="utf-8")


def records(conn, table, collection_id=None, candidate_id=None):
    if table not in TABLES:
        raise ValueError("Unknown collection evidence table")
    if not exists(conn):
        return []
    conditions, params = [], []
    for key, value in (("collection_id", collection_id), ("candidate_id", candidate_id)):
        if value is not None:
            conditions.append(key + "=?")
            params.append(value)
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    return [
        json.loads(r[0])
        for r in conn.execute(f"SELECT record_json FROM {table}{where} ORDER BY sequence", params)
    ]


def append(conn, table, collection_id, *, candidate_id=None, **data):
    operation = ACTIVE_OPERATION.get()
    if not operation:
        raise ValueError("Collection mutations require shared services")
    record = dict(
        data,
        event_id=new_id("event"),
        collection_id=collection_id,
        candidate_id=candidate_id,
        operation_id=operation,
        timestamp=now(),
    )
    conn.execute(
        f"INSERT INTO {table}(event_id,collection_id,candidate_id,operation_id,record_json) VALUES(?,?,?,?,?)",
        (
            record["event_id"],
            collection_id,
            candidate_id,
            operation,
            json.dumps(record, allow_nan=False),
        ),
    )
    return record


def catalog(conn):
    current = {}
    for event in records(conn, "collection_events"):
        current[event["collection_id"]] = event
    return list(current.values())


def collection(conn, collection_id, *, active=False):
    item = next((c for c in catalog(conn) if c["collection_id"] == collection_id), None)
    if item is None:
        raise ValueError("Unknown collection")
    if active and item["archived"]:
        raise ValueError("Restore the archived collection before changing it")
    return item


def memberships(conn, collection_id=None, *, include_inactive=False, candidate_id=None):
    current = {}
    for event in records(conn, "membership_events", collection_id, candidate_id):
        mid = event["membership_id"]
        old = current.get(mid)
        if event["action"] == "add":
            current[mid] = dict(event, active=True, activation_id=event["event_id"])
        elif event["action"] == "remove":
            current[mid] = dict(old, active=False, removed_by=event["event_id"])
    return [r for r in current.values() if include_inactive or r["active"]]


def member(conn, collection_id, candidate_id):
    item = next((m for m in memberships(conn, collection_id, candidate_id=candidate_id)), None)
    if item is None:
        raise ValueError("Candidate is not an active member of this collection")
    return item


def membership_change(conn, collection_id, candidate_id, *, action, actor, reason, source_id=None):
    collection(conn, collection_id, active=True)
    if not conn.execute(
        "SELECT 1 FROM candidates WHERE candidate_id=?", (candidate_id,)
    ).fetchone():
        raise ValueError("Unknown candidate")
    row = conn.execute(
        "SELECT membership_id FROM collection_memberships WHERE collection_id=? AND candidate_id=?",
        (collection_id, candidate_id),
    ).fetchone()
    mid = row[0] if row else new_id("membership")
    if not row:
        conn.execute(
            "INSERT INTO collection_memberships VALUES(?,?,?)", (mid, collection_id, candidate_id)
        )
    history = records(conn, "membership_events", collection_id, candidate_id)
    if source_id and any(e.get("source_id") == source_id for e in history):
        return
    current = next((m for m in memberships(conn, collection_id, candidate_id=candidate_id)), None)
    if action == "remove" and current is None:
        raise ValueError("Membership is already inactive")
    return append(
        conn,
        "membership_events",
        collection_id,
        candidate_id=candidate_id,
        membership_id=mid,
        action="evidence" if action == "add" and current else action,
        actor=actor,
        reason=reason,
        origin="discovery" if source_id else "manual",
        source_id=source_id,
        search_operation_id=ACTIVE_OPERATION.get() if source_id else None,
    )


def discovered(conn, source_id, candidate_id):
    context = SEARCH_CONTEXT.get()
    if context:
        membership_change(
            conn,
            context["collection_id"],
            candidate_id,
            action="add",
            actor=context["actor"],
            reason="Found by this collection's search",
            source_id=source_id,
        )


def search_context(conn, collection_id, actor, expected_revision=None):
    c = collection(conn, collection_id, active=True)
    if expected_revision is not None and expected_revision != c["event_id"]:
        raise ValueError("Collection phrases changed; refresh before searching")
    if not c["phrases"]:
        raise ValueError("Add at least one search phrase to this collection")
    result = {
        "collection_id": collection_id,
        "name": c["name"],
        "revision": c["event_id"],
        "phrases": c["phrases"],
        "actor": actor,
        "operation_id": ACTIVE_OPERATION.get(),
    }
    conn.execute(
        "INSERT INTO collection_search_contexts VALUES(?,?,?)",
        (ACTIVE_OPERATION.get(), collection_id, json.dumps(result)),
    )
    conn.commit()
    return result


def identity(conn, collection_id, candidate_id):
    m = member(conn, collection_id, candidate_id)
    aid = conn.execute(
        "SELECT asset_id FROM candidates WHERE candidate_id=?", (candidate_id,)
    ).fetchone()[0]
    return member_identity(m, aid)


def member_identity(m, aid):
    return {
        "collection_id": m["collection_id"],
        "candidate_id": m["candidate_id"],
        "membership_id": m["membership_id"],
        "activation_id": m["activation_id"],
        "asset_id": aid,
    }


def current_review(conn, scope, history=None):
    history = (
        records(conn, "collection_reviews", scope["collection_id"], scope["candidate_id"])
        if history is None
        else history
    )
    return next(
        (r for r in reversed(history) if r["scope"] == scope),
        {
            "scope": scope,
            "label": "unrated",
            "notes": None,
            "reviewer_email": None,
            "event_id": None,
        },
    )


def review(conn, collection_id, selection, label, actor, notes=None, *, batch=False):
    allowed = {"maybe", "rejected"} if batch else {"accepted", "maybe", "rejected", "unrated"}
    if label not in allowed or not actor.strip() or not selection:
        raise ValueError("Explicit selection, investigator and valid decision required")
    if len({r["candidate_id"] for r in selection}) != len(selection):
        raise ValueError("Select distinct candidates")
    collection(conn, collection_id, active=True)
    checked = []
    for row in selection:
        scope = identity(conn, collection_id, row["candidate_id"])
        old = current_review(conn, scope)
        if row.get("scope") != scope or row.get("event_id") != old["event_id"]:
            raise ValueError("Collection review changed; refresh and select again")
        checked.append((scope, old))
    for scope, old in checked:
        append(
            conn,
            "collection_reviews",
            collection_id,
            candidate_id=scope["candidate_id"],
            scope=scope,
            label=label,
            notes=notes,
            reviewer_email=actor.strip(),
            created_at=now(),
            prior_event_id=old["event_id"],
        )
    return {"reviewed": len(checked), "label": label}


def screening_report(conn, project, collection_id):
    from stimulus_harvester.screening.store import records as screening_records

    collection(conn, collection_id)
    policies = records(conn, "collection_policies", collection_id)
    policy = policies[-1] if policies else None
    evaluations = records(conn, "collection_evaluations", collection_id)
    overrides = records(conn, "collection_overrides", collection_id)
    observations = screening_records(conn, "observations")
    required = [r["id"] for r in policy["config"]["rules"]] if policy else []
    rows = []
    assets = dict(conn.execute("SELECT candidate_id,asset_id FROM candidates"))
    for m in memberships(conn, collection_id):
        scope = member_identity(m, assets[m["candidate_id"]])
        selected = {
            e["rule_id"]: e
            for e in evaluations
            if e["membership_scope"] == scope and policy and e["policy_id"] == policy["policy_id"]
        }
        active = [selected[r] for r in required if r in selected]
        values = [e["disposition"] for e in active]
        base = (
            "excluded"
            if "excluded" in values
            else "review_required"
            if not required or len(active) != len(required) or "review_required" in values
            else "eligible"
        )
        exact = dict(
            scope,
            policy_id=policy["policy_id"] if policy else None,
            evaluation_ids=sorted(e["evaluation_id"] for e in active),
        )
        applied = next((o for o in reversed(overrides) if o["scope"] == exact), None)
        rows.append(
            {
                "candidate_id": m["candidate_id"],
                "asset_id": scope["asset_id"],
                "rule_disposition": base,
                "effective_disposition": applied["new_disposition"] if applied else base,
                "scope": exact,
                "override": applied,
                "evaluations": active,
                "observation_statuses": [e["observation_status"] for e in active],
                "error_or_not_run": not required
                or len(active) != len(required)
                or any(e["observation_status"] in {"error", "not_run"} for e in active),
                "reason": "Collection policy; no project-policy inheritance",
            }
        )
    counts = dict(Counter(r["effective_disposition"] for r in rows))
    for k in ("eligible", "excluded", "review_required"):
        counts.setdefault(k, 0)
    counts["error_or_not_run"] = sum(r["error_or_not_run"] for r in rows)
    return {
        "project_id": project.project_id,
        "policy": policy,
        "policies": policies,
        "candidates": rows,
        "observations": observations,
        "evaluations": evaluations,
        "overrides": overrides,
        "counts": counts,
        "observation_counts": dict(Counter(o["status"] for o in observations)),
        "observation_count_basis": "Reusable project observations; dispositions apply only to this collection.",
    }


def evaluate_policy(conn, project, collection_id, configuration):
    from stimulus_harvester.screening.config import ScreeningConfig
    from stimulus_harvester.screening.engine import evaluate
    from stimulus_harvester.screening.store import records as screening_records

    config = ScreeningConfig.model_validate(configuration)
    collection(conn, collection_id, active=True)
    policy_id = new_id("policy")
    append(
        conn,
        "collection_policies",
        collection_id,
        policy_id=policy_id,
        config=config.model_dump(mode="json"),
    )
    observations = screening_records(conn, "observations")
    detectors = {d.id: d.model_dump(mode="json") for d in config.detectors}
    assets = dict(conn.execute("SELECT candidate_id,asset_id FROM candidates"))
    for m in memberships(conn, collection_id):
        scope = member_identity(m, assets[m["candidate_id"]])
        for rule in config.rules:
            obs = next(
                (
                    o
                    for o in reversed(observations)
                    if o["candidate_id"] == m["candidate_id"]
                    and o["asset_id"] == scope["asset_id"]
                    and o["source"] == rule.source
                    and o["parameters"] == detectors[rule.source]
                ),
                None,
            )
            if obs is None:
                continue  # No invented observation; incomplete evaluation requires review.
            disposition, reason = evaluate(rule, obs)
            append(
                conn,
                "collection_evaluations",
                collection_id,
                candidate_id=m["candidate_id"],
                membership_scope=scope,
                evaluation_id=new_id("evaluation"),
                policy_id=policy_id,
                asset_id=scope["asset_id"],
                rule_id=rule.id,
                rule_version=rule.version,
                rule=rule.model_dump(mode="json"),
                observation_id=obs["observation_id"],
                observation_status=obs["status"],
                disposition=disposition,
                reason=reason,
            )
    return {"policy_id": policy_id, "inference_run": False}


def mutate(project, *, action, collection_id=None, actor="", **options):
    from stimulus_harvester.review.db import init_db

    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("Enter an investigator identity for this collection operation")
    conn = init_db(project.paths.db_path)
    try:
        ensure_schema(conn)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            if action == "create":
                collection_id = new_id("collection")
                conn.execute("INSERT INTO collections VALUES(?)", (collection_id,))
                old = {"name": "", "phrases": [], "archived": False}
            else:
                old = collection(conn, collection_id, active=action not in {"restore", "archive"})
            expected_revision = options.pop("expected_revision", None)
            if expected_revision is not None and expected_revision != old.get("event_id"):
                raise ValueError("Collection changed; refresh before saving")
            if action in {"create", "edit", "archive", "restore"}:
                name = options.get("name", old["name"]).strip()
                if not name:
                    raise ValueError("Collection name must not be blank")
                texts = options.get("phrases", [p["text"] for p in old["phrases"]])
                if not isinstance(texts, list) or any(
                    not isinstance(t, str) or not t.strip() for t in texts
                ):
                    raise ValueError("Search phrases must be nonblank strings")
                texts = list(dict.fromkeys(t.strip() for t in texts))
                prior = {p["text"]: p for p in old["phrases"]}
                phrases = [prior.get(t, {"phrase_id": new_id("phrase"), "text": t}) for t in texts]
                result = append(
                    conn,
                    "collection_events",
                    collection_id,
                    action=action,
                    name=name,
                    phrases=phrases,
                    archived=action == "archive"
                    if action in {"archive", "restore"}
                    else old["archived"],
                    actor=actor,
                    prior_event_id=old.get("event_id"),
                )
            elif action in {"add", "remove"}:
                ids = options["candidate_ids"]
                if not ids or len(set(ids)) != len(ids):
                    raise ValueError("Choose distinct candidates")
                if (
                    action == "remove"
                    and "expected_scopes" in options
                    and options["expected_scopes"]
                    != [identity(conn, collection_id, cid) for cid in ids]
                ):
                    raise ValueError("Membership changed; refresh before removing")
                result = [
                    membership_change(
                        conn,
                        collection_id,
                        cid,
                        action=action,
                        actor=actor,
                        reason=options.get("reason", "Explicit investigator assignment"),
                    )
                    for cid in ids
                ]
            elif action == "review":
                result = review(conn, collection_id, actor=actor, **options)
            elif action == "policy":
                result = evaluate_policy(conn, project, collection_id, options["configuration"])
            elif action == "override":
                from pydantic import TypeAdapter

                from stimulus_harvester.screening.config import Disposition

                disposition = TypeAdapter(Disposition).validate_python(options["disposition"])
                row = next(
                    r
                    for r in screening_report(conn, project, collection_id)["candidates"]
                    if r["candidate_id"] == options["candidate_id"]
                )
                if row["scope"] != options["expected_scope"] or not options.get("note", "").strip():
                    raise ValueError(
                        "Override requires a reason and unchanged collection evaluation scope"
                    )
                result = append(
                    conn,
                    "collection_overrides",
                    collection_id,
                    candidate_id=row["candidate_id"],
                    scope=row["scope"],
                    override_id=new_id("override"),
                    reviewer=actor,
                    note=options["note"],
                    new_disposition=disposition,
                    prior_disposition=row["effective_disposition"],
                )
            else:
                raise ValueError("Unknown collection action")
            return {"collection_id": collection_id, "result": result}
    finally:
        conn.close()


def evidence(conn, collection_id):
    c = collection(conn, collection_id)
    return {
        "collection": c,
        "memberships": memberships(conn, collection_id),
        "history": {t: records(conn, t, collection_id) for t in TABLES},
        "search_contexts": [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT record_json FROM collection_search_contexts WHERE collection_id=? ORDER BY rowid",
                (collection_id,),
            )
        ],
    }


def project_view(conn, candidates):
    """One pass over membership/review histories, independent of GUI rendering."""
    index = {c["candidate_id"]: c for c in candidates}
    groups = {
        c["collection_id"]: dict(
            c, found=0, available=0, failed=0, pending=0, reviewed=0, unique_images=set()
        )
        for c in catalog(conn)
    }
    latest = {}
    for r in records(conn, "collection_reviews"):
        latest[json.dumps(r["scope"], sort_keys=True)] = r
    for c in candidates:
        c["collections"] = []
    for m in memberships(conn):
        c = index[m["candidate_id"]]
        scope = member_identity(m, c["asset_id"])
        review = latest.get(
            json.dumps(scope, sort_keys=True),
            {
                "scope": scope,
                "label": "unrated",
                "notes": None,
                "reviewer_email": None,
                "event_id": None,
            },
        )
        g = groups[m["collection_id"]]
        c["collections"].append(
            {
                "collection_id": g["collection_id"],
                "name": g["name"],
                "archived": g["archived"],
                "scope": scope,
                "review": review,
            }
        )
        g["found"] += 1
        g["available"] += bool(c.get("displayable", c.get("available")))
        g["failed"] += c.get("download_status") == "failed"
        g["pending"] += c.get("download_status") is None
        g["reviewed"] += review["label"] != "unrated"
        if c.get("displayable", c.get("available")):
            g["unique_images"].add(c["asset_id"])
    for g in groups.values():
        g["unique_images"] = len(g["unique_images"])
        g["unavailable"] = g["found"] - g["available"]
    return list(groups.values())

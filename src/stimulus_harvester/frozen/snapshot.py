"""Validate and freeze one coherent SQLite view without changing investigator decisions."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Literal

import yaml
from pydantic import Field, field_validator, model_validator

from stimulus_harvester.config import HarvesterConfig
from stimulus_harvester.review.db import init_db
from stimulus_harvester.screening.config import StrictModel
from stimulus_harvester.screening.detectors import file_hash
from stimulus_harvester.screening.engine import report_from_connection
from stimulus_harvester.storage import candidate_evidence

from . import store


class Scope(StrictModel):
    mode: Literal["all_current", "candidates", "collection"]
    collection_id: str | None = None
    candidate_ids: list[str] = Field(default_factory=list)
    selection_rule: str = Field(min_length=1)

    @model_validator(mode="after")
    def explicit_scope(self):
        if (self.mode == "collection") != bool(self.collection_id):
            raise ValueError("Collection scope requires exactly one stable collection ID")
        if not self.selection_rule.strip():
            raise ValueError("Scope selection rule must not be blank")
        if len(self.candidate_ids) != len(set(self.candidate_ids)):
            raise ValueError("Duplicate scope IDs")
        if (self.mode == "candidates") != bool(self.candidate_ids):
            raise ValueError("Use explicit nonempty IDs for candidates, or all_current without IDs")
        return self


class DuplicateResolution(StrictModel):
    asset_id: str
    membership: Literal["include", "omit"]
    reviewer: str = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("reviewer", "reason")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Duplicate adjudication needs reviewer and reason")
        return value


class FreezeRequest(StrictModel):
    reviewer: str = Field(min_length=1)
    scope: Scope
    parent_freeze_id: str | None = None
    deduplicate_accepted: bool = False
    duplicate_resolutions: list[DuplicateResolution] = Field(default_factory=list)

    @field_validator("reviewer")
    @classmethod
    def reviewer_present(cls, value):
        if not value.strip():
            raise ValueError("Freeze investigator identity is required")
        return value

    @model_validator(mode="after")
    def unique_resolutions(self):
        if len({r.asset_id for r in self.duplicate_resolutions}) != len(self.duplicate_resolutions):
            raise ValueError("Duplicate resolution for asset")
        return self


def rows(conn, sql, params=()):
    return [dict(r) for r in conn.execute(sql, params)]


def locate(project, asset):
    """Only frozen locations: never infer identity from a filename or current DB state."""
    if asset["asset_id"] != "sha256:" + asset["sha256"]:
        raise ValueError("Canonical asset ID/hash mismatch")
    good = []
    for value in asset["locations"]:
        path = project.resolve(value)
        if path.exists():
            if not path.is_file() or file_hash(path) != asset["sha256"]:
                raise ValueError(f"Changed frozen asset bytes: {value}")
            good.append(path)
    if not good:
        raise ValueError(f"Missing frozen asset: {asset['asset_id']}")
    return good[0]


def snapshot(conn, project, request):
    request = FreezeRequest.model_validate(request)
    collection_context = None
    scoped = request.scope.mode == "collection"
    if scoped:
        from stimulus_harvester import collections

        collections.collection(conn, request.scope.collection_id, active=True)
        collection_context = collections.evidence(conn, request.scope.collection_id)
    all_rows = rows(conn, "SELECT * FROM candidates ORDER BY candidate_id")
    by_id = {r["candidate_id"]: r for r in all_rows}
    selected = sorted(by_id if request.scope.mode == "all_current" else request.scope.candidate_ids)
    if scoped:
        selected = sorted(m["candidate_id"] for m in collection_context["memberships"])
    blockers, warnings = [], []

    def block(code, message, **context):
        blockers.append({"code": code, "message": message, **context})

    if not selected:
        block("empty_scope", "Freeze scope contains no candidates")
    for cid in selected:
        if cid not in by_id:
            block("unknown_candidate", "Scope ID is not a canonical candidate ID", candidate_id=cid)
    selected = [cid for cid in selected if cid in by_id]
    screening = (
        collections.screening_report(conn, project, request.scope.collection_id)
        if scoped
        else report_from_connection(conn, project)
    )
    screen_by_id = {r["candidate_id"]: r for r in screening["candidates"]}
    scoped_assets = {by_id[c]["asset_id"] for c in selected} - {None}
    related_ids = sorted(
        c for c, r in by_id.items() if c not in selected and r["asset_id"] in scoped_assets
    )
    records = []
    for cid in selected + related_ids:
        c = candidate_evidence(conn, by_id[cid]["primary_local_id"])
        local = c["primary_local_id"]
        current = conn.execute("SELECT * FROM reviews WHERE local_id=?", (local,)).fetchone()
        c["review"] = dict(current) if current else {"local_id": local, "label": "unrated"}
        c["review_events"] = rows(
            conn, "SELECT * FROM review_events WHERE local_id=? ORDER BY id", (local,)
        )
        c["screening"] = screen_by_id.get(cid)
        if c["screening"] is None:
            c["screening"] = report_from_connection(conn, project, candidate_id=cid)["candidates"][
                0
            ]
        c["in_scope"] = cid in selected
        if scoped and c["in_scope"]:
            c["collection_review_scope"] = collections.identity(
                conn, request.scope.collection_id, cid
            )
            c["collection_review_events"] = collections.records(
                conn, "collection_reviews", request.scope.collection_id, cid
            )
            c["collection_review"] = collections.current_review(
                conn, c["collection_review_scope"], c["collection_review_events"]
            )
        c["image_projection"] = dict(
            conn.execute("SELECT * FROM images WHERE local_id=?", (local,)).fetchone()
        )
        records.append(c)
        if not c["in_scope"]:
            continue
        review = c["collection_review"] if scoped else c["review"]
        label = review["label"]
        if label not in {"accepted", "rejected"}:
            block(
                "unresolved_review",
                "Maybe/defer/unrated requires a final investigator decision",
                candidate_id=cid,
                label=label,
            )
        else:
            applicable = (
                [
                    e
                    for e in c["collection_review_events"]
                    if e["scope"] == c["collection_review_scope"]
                ]
                if scoped
                else c["review_events"]
            )
            last = applicable[-1] if applicable else None
            if not last or any(
                last[k] != review.get(k) for k in ("label", "notes", "reviewer_email")
            ):
                block(
                    "review_evidence_missing",
                    "Current decision must match its latest explicit review event",
                    candidate_id=cid,
                )
            if not review.get("reviewer_email") or not review["reviewer_email"].strip():
                block(
                    "reviewer_missing",
                    "Final review requires investigator identity",
                    candidate_id=cid,
                )
        if label == "accepted":
            if c['screening'].get('manual_completion_required'):
                block('manual_criterion_unresolved', 'Manual, partial, missing or failed automatic criteria need an attributed screening override for this exact policy after inspection; Accept is a separate suitability decision.', candidate_id=cid)
            if c["screening"]["effective_disposition"] == "excluded":
                block(
                    "screening_excluded",
                    "Accept does not supersede exclusion; use explicit screening override first",
                    candidate_id=cid,
                )
            if c["asset"] is None:
                block(
                    "asset_missing",
                    "Accepted candidate has no acquired canonical asset",
                    candidate_id=cid,
                )
    config_bytes = project.paths.config_snapshot_path.read_bytes()
    config = HarvesterConfig.model_validate(yaml.safe_load(config_bytes))
    # Config and last evaluated policy are separate facts; never reinterpret evidence here.
    policy = screening["policy"]
    if not scoped and policy and policy["config"] != config.screening.model_dump(mode="json"):
        warnings.append(
            {
                "code": "config_policy_difference",
                "message": "Current project screening config differs from last activated evidence policy; both are frozen. No rules are re-evaluated.",
            }
        )
    evidence_ids = {c["candidate_id"] for c in records}
    histories = {
        key: [r for r in screening[key] if r["candidate_id"] in evidence_ids]
        for key in ("observations", "evaluations", "overrides")
    }
    policy_ids = {e["policy_id"] for e in histories["evaluations"]} | {
        o["scope"]["policy_id"] for o in histories["overrides"]
    }
    if policy:
        policy_ids.add(policy["policy_id"])
    histories["policies"] = [p for p in screening["policies"] if p["policy_id"] in policy_ids]
    generalized = any(p.get('evidence_version') == 2 for p in histories['policies']) or any(o.get('format_version') == 2 for o in histories['observations'])
    artifact_version = 3 if generalized else 2 if scoped else 1
    groups = defaultdict(list)
    for c in records:
        if c["in_scope"] and c["asset_id"]:
            groups[c["asset_id"]].append(c)
    resolutions = {r.asset_id: r.model_dump(mode="json") for r in request.duplicate_resolutions}
    duplicates, members = [], []
    conflicts = set()
    for aid, group in sorted(groups.items()):
        accepted = [c for c in group if decision(c, 2 if scoped else 1)["label"] == "accepted"]
        rejected = [c for c in group if decision(c, 2 if scoped else 1)["label"] == "rejected"]
        conflict = bool(accepted and rejected)
        if conflict:
            conflicts.add(aid)
        resolution = resolutions.get(aid)
        outside = [c for c in records if not c["in_scope"] and c["asset_id"] == aid]
        if len(group) + len(outside) > 1:
            duplicates.append(
                {
                    "asset_id": aid,
                    "candidate_decisions": [
                        {
                            "candidate_id": c["candidate_id"],
                            "label": decision(c, 2 if scoped and c["in_scope"] else 1)["label"],
                            "in_scope": c["in_scope"],
                        }
                        for c in group + outside
                    ],
                    "membership_conflict": conflict,
                    "outside_scope_decisions_preserved": bool(outside),
                    "resolution": resolution,
                    "deduplicate_accepted": request.deduplicate_accepted,
                }
            )
        if conflict and not resolution:
            block(
                "duplicate_decision_conflict",
                "Accepted/rejected candidates share bytes; record include/omit adjudication",
                asset_id=aid,
            )
        if len(accepted) > 1 and not request.deduplicate_accepted:
            block(
                "duplicate_accepts",
                "Explicit deduplicate_accepted is required to materialize identical bytes once",
                asset_id=aid,
            )
        if not accepted or (resolution and resolution["membership"] == "omit"):
            continue
        asset = accepted[0]["asset"]
        # Include every retained source history for this exact asset, including outside scope.
        source_ids = sorted(
            s["source_id"] for c in records if c["asset_id"] == aid for s in c["sources"]
        )
        suffix = Path(asset["locations"][0]).suffix.lower() if asset["locations"] else ".bin"
        if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".tif", ".tiff", ".bmp"}:
            suffix = ".bin"
        members.append(
            {
                "asset_id": aid,
                "sha256": asset["sha256"],
                "filename": asset["sha256"] + suffix,
                "candidate_ids": sorted(c["candidate_id"] for c in accepted),
                "source_ids": source_ids,
            }
        )
        try:
            locate(project, asset)
        except (ValueError, OSError) as exc:
            block("asset_integrity", str(exc), asset_id=aid)
    for aid in resolutions.keys() - conflicts:
        block(
            "unused_resolution",
            "Duplicate adjudication must address an actual in-scope membership conflict",
            asset_id=aid,
        )
    # Every referenced historical asset ID is accompanied by its registered hash metadata.
    assets_needed = (
        {c["asset_id"] for c in records}
        | {a["asset_id"] for c in records for a in c["acquisitions"]}
        | {o["asset_id"] for o in histories["observations"]}
        | {e["asset_id"] for e in histories["evaluations"]}
        | {o["scope"]["asset_id"] for o in histories["overrides"]}
    )
    if scoped:
        assets_needed.update(
            event.get("asset_id") or event.get("scope", {}).get("asset_id")
            for events in collection_context["history"].values()
            for event in events
        )
    assets = {}
    for aid in sorted(assets_needed - {None}):
        row = conn.execute("SELECT * FROM assets WHERE asset_id=?", (aid,)).fetchone()
        if row is None or aid != "sha256:" + row["sha256"]:
            block("asset_identity", "Referenced asset ID/hash is inconsistent", asset_id=aid)
            continue
        assets[aid] = dict(row)
        assets[aid]["locations"] = [
            r[0]
            for r in conn.execute(
                "SELECT path FROM asset_locations WHERE asset_id=? ORDER BY path", (aid,)
            )
        ]
    source_ops = {s["operation_id"] for c in records for s in c["sources"]} - {None}
    operation_ids = (
        source_ops
        | {a["operation_id"] for c in records for a in c["acquisitions"]}
        | {r["operation_id"] for key in histories for r in histories[key]}
    )
    operation_ids.discard(None)
    if scoped:
        operation_ids.update(
            e["operation_id"] for events in collection_context["history"].values() for e in events
        )
        operation_ids.update(e["operation_id"] for e in collection_context["search_contexts"])
    operations = []
    discovery_events = []
    has_discovery = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE name='discovery_events'"
    ).fetchone()
    for op in sorted(operation_ids):
        operations.extend(rows(conn, "SELECT * FROM operations WHERE operation_id=?", (op,)))
        if has_discovery and op in source_ops:
            discovery_events.extend(
                rows(
                    conn,
                    "SELECT * FROM discovery_events WHERE operation_id=? ORDER BY rowid",
                    (op,),
                )
            )
    raw_paths = {s["raw_response_path"] for c in records for s in c["sources"]} | {
        e["raw_response_path"] for e in discovery_events
    }
    # Snapshot operation request/config evidence as well as extracted source claims.
    for op in operation_ids:
        for name in ("request.json", "config_snapshot.yaml"):
            path = f"operations/{op}/{name}"
            if (project.root / path).is_file():
                raw_paths.add(path)
    files = []
    for value in sorted(raw_paths - {None, ""}):
        try:
            path = project.resolve(value)
            if not path.is_file():
                raise ValueError(f"Missing referenced evidence: {value}")
            h = file_hash(path)
            files.append(
                {"original_path": value, "sha256": h, "packaged_path": f"evidence/{h}.bin"}
            )
        except (ValueError, OSError) as exc:
            block("evidence_missing", str(exc), path=value)
    if request.parent_freeze_id:
        try:
            parent, _ = store.load(conn, project, request.parent_freeze_id, kind="freeze")
            if parent["format_version"] != artifact_version or (
                scoped and parent["scope"].get("collection_id") != request.scope.collection_id
            ) or (parent['scope']['mode'] == 'collection') != scoped:
                raise ValueError("Parent freeze must have the same scientific scope and format")
        except (ValueError, OSError) as exc:
            block("parent_freeze_invalid", str(exc), parent_freeze_id=request.parent_freeze_id)
    request_data = request.model_dump(mode="json")
    if not scoped:
        request_data["scope"].pop("collection_id", None)
    data = {
        "request": request_data,
        "scope": {
            **request_data["scope"],
            "resolved_candidate_ids": selected,
            "outside_scope_candidate_ids": sorted(set(by_id) - set(selected)),
            "all_current_candidate_inventory": all_rows,
        },
        "candidates": [c for c in records if c["in_scope"]],
        "related_candidates": [c for c in records if not c["in_scope"]],
        "assets": assets,
        "screening": {"active_policy": policy, **histories},
        "configuration": {
            "sha256": store.sha(config_bytes),
            "yaml": config_bytes.decode("utf-8"),
            "parsed": config.model_dump(mode="json"),
        },
        "duplicates": duplicates,
        "membership": members,
        "evidence_files": files,
        "operations": operations,
        "discovery_events": discovery_events,
        "operation_events": [
            e
            for op in sorted(operation_ids)
            for e in rows(
                conn, "SELECT * FROM operation_events WHERE operation_id=? ORDER BY event_id", (op,)
            )
        ],
        "validation": {
            "valid": not blockers,
            "blockers": blockers,
            "warnings": warnings,
            "scope_count": len(selected),
            "assembled_asset_count": len(members),
        },
        "semantics": {
            "review_required_accept": "Explicit current investigator Accept resolves review_required; freeze investigator attests the captured decisions.",
            "excluded_accept": "Blocked until an explicit effective screening override removes exclusion.",
            "screening_default": "None; last activated policy evidence is frozen independently of current config.",
            "license_policy": "Preserve all source-specific claims; no license-based membership filtering.",
            "near_duplicates": "Distinct hashes remain distinct assets.",
        },
    }
    if scoped:
        data["collection_context"] = collection_context
        data["semantics"]["human_review_scope"] = (
            "Independent collection/candidate/membership activation/asset decision; project reviews are contextual history only."
        )
    if generalized:
        data.update(format_version=3, review_scope='collection' if scoped else 'project', screening_contract_version=2)
        data['semantics']['manual_criteria'] = 'Manual/partial/no-evidence criteria require an attributed effective eligible/excluded screening override bound to the current policy, independently of human suitability review.'
    return data


def decision(candidate, version):
    if version == 3:
        return decision(candidate, 2 if 'collection_review' in candidate else 1)
    if version == 1:
        return candidate["review"]
    if version != 2:
        raise ValueError("Unsupported scientific decision format")
    review = candidate["collection_review"]
    if (
        review["scope"] != candidate["collection_review_scope"]
        or review["scope"]["asset_id"] != candidate["asset_id"]
    ):
        raise ValueError("Collection review/asset scope mismatch")
    return review


def validate(project, *, request):
    conn = init_db(project.paths.db_path)
    try:
        store.ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        data = snapshot(conn, project, request)
        return {
            **data["validation"],
            "scope": data["scope"],
            "duplicates": data["duplicates"],
            "membership": data["membership"],
        }
    finally:
        conn.rollback()
        conn.close()


def create(project, *, request, expected_snapshot_sha256=None):
    conn = init_db(project.paths.db_path)
    try:
        store.ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        data = snapshot(conn, project, request)
        if (
            expected_snapshot_sha256
            and store.sha(store.canonical(data)) != expected_snapshot_sha256
        ):
            raise ValueError("Freeze state changed since confirmation; validate and confirm again")
        if not data["validation"]["valid"]:
            raise ValueError("Freeze blocked: " + json.dumps(data["validation"]["blockers"]))
        # Freeze the exact config used by validation, and detect out-of-band edits.
        if file_hash(project.paths.config_snapshot_path) != data["configuration"]["sha256"]:
            raise ValueError("Configuration changed during freeze; retry validation")
        manifest = {
            **store.metadata(project, "freeze", parent_id=data["request"]["parent_freeze_id"]),
            "format_version": 2 if "collection_context" in data else 1,
            **data,
            "software": store.software(project, conn),
        }
        with store.staging(project, "freeze") as stage:
            (stage / "config_snapshot.yaml").write_bytes(
                data["configuration"]["yaml"].encode("utf-8")
            )
            for file in data["evidence_files"]:
                destination = store.internal(stage, file["packaged_path"])
                if not destination.exists():
                    store.copy_verified(
                        project.resolve(file["original_path"]), destination, file["sha256"]
                    )
            return store.publish(conn, project, stage, manifest)
    finally:
        conn.rollback()
        conn.close()

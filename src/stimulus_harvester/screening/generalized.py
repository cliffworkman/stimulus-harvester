"""Criteria contract 1 over immutable object observations 2 and legacy storage.

Evaluations (including manual/no-evidence states) live in the immutable policy
record. A missing observation is a null reference, never fabricated machine data.
No DDL or historical observation rewriting is needed.
"""

import json
import platform
from collections import Counter, defaultdict

from stimulus_harvester.review.db import init_db
from stimulus_harvester.storage import ACTIVE_OPERATION, new_id, now

from . import capabilities
from .config import ScreeningConfig
from .detectors import file_hash, normalize
from .store import append_observation, ensure_schema, records

STATE_LABELS = {
    "not_run": "Not run",
    "not_detected": "No match detected",
    "detected": "Match detected",
    "uncertain": "Uncertain",
    "error": "Error",
    "manual_review_required": "Manual review required",
}


def scope_rows(conn, collection_id=None):
    from .engine import candidate_rows

    candidates = candidate_rows(conn)
    if not collection_id:
        return [
            (c, {"candidate_id": c["candidate_id"], "asset_id": c["asset_id"]}) for c in candidates
        ]
    from stimulus_harvester import collections as col

    col.collection(conn, collection_id)
    members = {m["candidate_id"]: m for m in col.memberships(conn, collection_id)}
    return [
        (c, col.member_identity(members[c["candidate_id"]], c["asset_id"]))
        for c in candidates
        if c["candidate_id"] in members
    ]


def compatible(observation, detector, candidate):
    if observation.get("format_version", 1) != 2 or observation["property"] != "coco_objects":
        return False  # Legacy person evidence stays readable only under its original semantics.
    if (
        observation["candidate_id"] != candidate["candidate_id"]
        or observation["asset_id"] != candidate["asset_id"]
    ):
        return False
    ignored = {"id", "enabled", "low_threshold", "high_threshold"}
    target = detector.model_dump(mode="json")
    if {k: v for k, v in target.items() if k not in ignored} != {
        k: v for k, v in observation["parameters"].items() if k not in ignored
    }:
        return False
    if observation["status"] != "recorded":
        return True  # Matching setup/error/not-run is explicit unsuccessful evidence, not absence.
    identity = observation["model_identity"]
    expected = capabilities.registry()["models"][detector.model]
    digest = identity.get("artifact_sha256", "")
    return (
        identity.get("adapter_version") == "2"
        and identity.get("model") == detector.model
        and identity.get("weights_url") == expected["url"]
        and str(identity.get("torchvision", "")).split("+")[0] == "0.23.0"
        and identity.get("categories") == capabilities.registry()["categories"]
        and digest == expected["artifact_sha256"]
    )


def evaluate_criterion(criterion, detector, observation):
    status, score = "not_run", None
    reason = (
        "No compatible object observation. Automatic screening not configured."
        if detector is None
        else "No compatible object observation; no inference was run."
    )
    if criterion.mode == "manual":
        status, reason = (
            "manual_review_required",
            "No automatic mechanism is assigned. Record an attributed screening override after human inspection.",
        )
    elif observation:
        status = observation["status"]
        reason = observation["reason"]
        if status == "recorded":
            raw = observation["raw"]
            normalize(
                raw, detector
            )  # Validate all coordinates/scores, including non-target detections.
            score = max(
                (d["score"] for d in raw["detections"] if d["class"] in criterion.targets),
                default=0.0,
            )
            if score >= detector.high_threshold:
                status, reason = (
                    "detected",
                    "A target detection meets the explicitly configured high boundary.",
                )
            elif len(raw["detections"]) >= detector.max_detections:
                status, reason = (
                    "uncertain",
                    "Output cap reached; target detections may have been suppressed.",
                )
            elif score >= detector.low_threshold:
                status, reason = (
                    "uncertain",
                    "A target score is within the configured uncertainty region.",
                )
            else:
                status, reason = (
                    "not_detected",
                    "No target detection was recorded above the low boundary; absence is not established.",
                )
    manual = (
        criterion.mode == "manual"
        or criterion.coverage == "partial"
        or status in {"not_run", "error"}
    )
    disposition = "excluded" if status == "detected" else "review_required"
    return dict(
        observation_status=status,
        state_label=STATE_LABELS[status],
        score=score,
        disposition=disposition,
        reason=reason,
        manual_completion_required=manual,
        score_semantics="Maximum retained target-class score; uncalibrated, not an absence probability"
        if score is not None
        else None,
    )


def activate(conn, project, configuration, collection_id=None, actor=""):
    config = ScreeningConfig.model_validate(configuration)
    if not config.criteria_version:
        raise ValueError("Explicit criteria contract required")
    if not actor.strip():
        raise ValueError("An investigator identity is required")
    ensure_schema(conn)
    op = ACTIVE_OPERATION.get()
    if not op:
        raise ValueError("Activate criteria through shared services")
    policy = dict(
        policy_id=new_id("policy"),
        operation_id=op,
        timestamp=now(),
        actor=actor.strip(),
        evidence_version=2,
        config=config.model_dump(mode="json"),
        capability_registry=capabilities.snapshot(),
        criterion_evaluations=[],
    )
    detector = next((d for d in config.detectors if d.enabled), None)
    observed = defaultdict(list)
    for obs in records(conn, "observations"):
        observed[(obs["candidate_id"], obs["asset_id"])].append(obs)
    for candidate, scope in scope_rows(conn, collection_id):
        obs = next(
            (
                o
                for o in reversed(observed[(candidate["candidate_id"], candidate["asset_id"])])
                if detector and compatible(o, detector, candidate)
            ),
            None,
        )
        for criterion in config.criteria:
            used = obs if criterion.mode == "automatic" else None
            event = dict(
                evaluation_id=new_id("evaluation"),
                policy_id=policy["policy_id"],
                candidate_id=candidate["candidate_id"],
                asset_id=candidate["asset_id"],
                membership_scope=scope,
                criterion_id=criterion.id,
                rule_id=criterion.id,
                rule_version="criteria-1",
                criterion=criterion.model_dump(mode="json"),
                observation_id=used["observation_id"] if used else None,
                timestamp=policy["timestamp"],
                operation_id=op,
                thresholds={"low": detector.low_threshold, "high": detector.high_threshold}
                if detector and criterion.mode == "automatic"
                else None,
                **evaluate_criterion(criterion, detector, used),
            )
            policy["criterion_evaluations"].append(event)
    if collection_id:
        from stimulus_harvester import collections as col

        col.append(
            conn,
            "collection_policies",
            collection_id,
            **{k: v for k, v in policy.items() if k not in {"timestamp", "operation_id"}},
        )
    else:
        conn.execute(
            "INSERT INTO screening_policies VALUES(?,?,?,?)",
            (policy["policy_id"], op, policy["timestamp"], json.dumps(policy)),
        )
    return {
        "policy_id": policy["policy_id"],
        "inference_run": False,
        "evaluations": len(policy["criterion_evaluations"]),
    }


def save_policy(project, *, configuration, collection_id=None, actor=""):
    from stimulus_harvester import collections as col
    from stimulus_harvester.workspace import save_configuration

    config = ScreeningConfig.model_validate(configuration)
    if not config.criteria_version or not actor.strip():
        raise ValueError("Explicit criteria contract and investigator required")
    conn = init_db(project.paths.db_path)
    try:
        if collection_id:
            col.ensure_schema(conn)
            col.collection(conn, collection_id, active=True)
        else:
            data = project.config.model_dump(mode="json")
            data["screening"] = config.model_dump(mode="json")
            save_configuration(project, configuration=data)
        with conn:
            return activate(conn, project, config, collection_id, actor)
    finally:
        conn.close()


def report(conn, project, policy, policies, collection_id=None, candidate_id=None):
    from stimulus_harvester import collections as col

    overrides = (
        col.records(conn, "collection_overrides", collection_id)
        if collection_id
        else records(conn, "screening_overrides")
    )
    observations = records(conn, "observations")
    grouped = defaultdict(list)
    for event in policy["criterion_evaluations"]:
        grouped[event["candidate_id"]].append(event)
    required = [c["id"] for c in policy["config"]["criteria"]]
    output = []
    for c, scope in scope_rows(conn, collection_id):
        if candidate_id and c["candidate_id"] != candidate_id:
            continue
        active = [e for e in grouped[c["candidate_id"]] if e["membership_scope"] == scope]
        missing = len(active) != len(required)
        exact = dict(
            scope,
            policy_id=policy["policy_id"],
            evaluation_ids=sorted(e["evaluation_id"] for e in active),
        )
        applied = next((o for o in reversed(overrides) if o["scope"] == exact), None)
        base = (
            "excluded" if any(e["disposition"] == "excluded" for e in active) else "review_required"
        )
        manual = missing or any(e["manual_completion_required"] for e in active)
        output.append(
            dict(
                candidate_id=c["candidate_id"],
                local_id=c["primary_local_id"],
                asset_id=c["asset_id"],
                rule_disposition=base,
                effective_disposition=applied["new_disposition"] if applied else base,
                scope=exact,
                override=applied,
                evaluations=active,
                observation_statuses=[e["observation_status"] for e in active],
                error_or_not_run=not required
                or missing
                or any(e["observation_status"] in {"error", "not_run"} for e in active),
                manual_completion_required=manual
                and not (
                    applied
                    and applied["new_disposition"] in {"eligible", "excluded"}
                    and str(applied.get("note") or "").strip()
                ),
                reason="; ".join(
                    f"{e['criterion']['text']}: {e['state_label']} — {e['reason']}" for e in active
                )
                or "No current criterion evaluations; review required.",
            )
        )
    counts = {
        s: sum(r["effective_disposition"] == s for r in output)
        for s in ("eligible", "excluded", "review_required")
    }
    counts["error_or_not_run"] = sum(r["error_or_not_run"] for r in output)
    return dict(
        project_id=project.project_id,
        policy=policy,
        policies=policies,
        candidates=output,
        evaluations=[e for p in policies for e in p.get("criterion_evaluations", [])],
        overrides=overrides,
        observations=observations,
        counts=counts,
        observation_counts=dict(Counter(o["status"] for o in observations)),
        observation_count_basis="Reusable saved machine evidence; criterion evaluations apply only to the selected scope.",
        interpretation="No match detected does not establish absence. Human suitability review is separate.",
    )


def run(project, config, collection_id=None):
    from stimulus_harvester import collections as col

    from .engine import build_detector

    conn = init_db(project.paths.db_path)
    counts = Counter()
    try:
        ensure_schema(conn)
        candidates = scope_rows(conn, collection_id)
        if collection_id:
            col.collection(conn, collection_id, active=True)
        for settings in config.detectors:
            detector, setup_error = None, None
            try:
                if settings.enabled and any(c["asset_id"] for c, _ in candidates):
                    try:
                        detector = build_detector(settings)
                    except Exception as exc:
                        setup_error = {
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "phase": "setup",
                        }
                for c, _ in candidates:
                    obs = dict(
                        format_version=2,
                        observation_id=new_id("observation"),
                        project_id=project.project_id,
                        candidate_id=c["candidate_id"],
                        asset_id=c["asset_id"],
                        property="coco_objects",
                        source=settings.id,
                        family="torchvision_object_detection",
                        model_identity={"model": settings.model, "loaded": False},
                        parameters=settings.model_dump(mode="json"),
                        thresholds={"low": settings.low_threshold, "high": settings.high_threshold},
                        runtime={"python": platform.python_version()},
                        timestamp=now(),
                        operation_id=ACTIVE_OPERATION.get(),
                        status="not_run",
                        score=None,
                        score_semantics=None,
                        raw={},
                        error=None,
                        reason="Detector disabled or no acquired asset",
                        derived_from=None,
                    )
                    if settings.enabled and c["asset_id"] and c["file_path"]:
                        try:
                            path = project.resolve(c["file_path"])
                            if "sha256:" + file_hash(path) != c["asset_id"]:
                                raise ValueError("Original bytes differ from registered asset")
                            if setup_error:
                                obs.update(
                                    status="error",
                                    error=setup_error,
                                    reason="Automatic mechanism setup failed",
                                )
                            else:
                                raw = detector.predict(path)
                                obs.update(
                                    raw=raw,
                                    model_identity=detector.identity,
                                    runtime=detector.runtime,
                                )
                                normalize(raw, settings)
                                vocabulary = capabilities.labels()
                                if any(
                                    d.get("class_id") != vocabulary.get(d.get("class"))
                                    for d in raw["detections"]
                                ):
                                    raise ValueError(
                                        "Detector labels differ from the published registry"
                                    )
                                obs.update(
                                    status="recorded",
                                    reason="Post-NMS object evidence recorded; no suitability or absence conclusion",
                                )
                                if not compatible(obs, settings, c):
                                    raise ValueError(
                                        "Unrecognized mechanism/weights/vocabulary identity"
                                    )
                        except Exception as exc:
                            obs.update(
                                status="error",
                                error={"type": type(exc).__name__, "message": str(exc)},
                                reason="Automatic mechanism/input failed",
                            )
                    try:
                        json.dumps(obs, allow_nan=False)
                    except (ValueError, TypeError):
                        obs.update(
                            status="error",
                            raw={},
                            error={
                                "type": "InvalidOutput",
                                "message": "Non-finite/non-JSON output",
                            },
                            reason="Invalid detector output",
                        )
                    with conn:
                        append_observation(conn, obs)
                    counts[obs["status"]] += 1
            finally:
                if detector:
                    detector.close()
        with conn:
            outcome = activate(conn, project, config, collection_id, "explicit screening run")
        outcome["inference_run"] = bool(counts["recorded"])
        return dict(
            **outcome,
            observations=dict(counts),
            stage_status="partial"
            if counts["error"] or counts["not_run"] or not counts
            else "succeeded",
        )
    finally:
        conn.close()

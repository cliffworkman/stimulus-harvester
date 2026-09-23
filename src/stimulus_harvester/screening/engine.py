"""Frontend-neutral screening with append-only evidence and explicit effective state."""

from __future__ import annotations

import json
import platform
from collections import Counter

from pydantic import TypeAdapter

from stimulus_harvester.review.db import init_db
from stimulus_harvester.screening.config import Disposition, RuleConfig, ScreeningConfig
from stimulus_harvester.screening.detectors import build_detector, file_hash, normalize
from stimulus_harvester.screening.store import append_observation, ensure_schema, records
from stimulus_harvester.storage import ACTIVE_OPERATION, new_id, now


def evaluate(rule: RuleConfig, observation: dict):
    if rule.property != observation["property"] or rule.source != observation["source"]:
        raise ValueError("Rule source/property does not match the observation")
    status = observation["status"]
    return (
        rule.outcomes[status],
        f"{rule.id}@{rule.version}: {rule.source}/{rule.property}={status} maps to {rule.outcomes[status]}",
    )


def candidate_rows(conn, candidate_id=None):
    return [
        dict(r)
        for r in conn.execute(
            """SELECT c.*,i.file_path,i.image_url FROM candidates c
        JOIN images i ON i.local_id=c.primary_local_id"""
            + (" WHERE c.candidate_id=?" if candidate_id is not None else "")
            + " ORDER BY c.candidate_id",
            (candidate_id,) if candidate_id is not None else (),
        )
    ]


def run_screen(project, *, screening_config=None, reuse_raw=False, collection_id=None):
    if collection_id:
        from stimulus_harvester import collections

        with init_db(project.paths.db_path) as lookup:
            collections.collection(lookup, collection_id, active=True)
            policies = collections.records(lookup, "collection_policies", collection_id)
            if screening_config is None:
                screening_config = policies[-1]["config"] if policies else {}
        lookup.close()
    config = (
        ScreeningConfig.model_validate(screening_config)
        if screening_config is not None
        else project.config.screening
    )
    operation = ACTIVE_OPERATION.get()
    if not operation:
        raise ValueError("Screening must run through shared services")
    conn = init_db(project.paths.db_path)
    detectors = []
    try:
        ensure_schema(conn)
        policy = {
            "policy_id": new_id("policy"),
            "operation_id": operation,
            "timestamp": now(),
            "config": config.model_dump(mode="json"),
        }
        if collection_id:
            policy["collection_id"] = collection_id
        with conn:
            conn.execute(
                "INSERT INTO screening_policies VALUES(?,?,?,?)",
                (policy["policy_id"], operation, policy["timestamp"], json.dumps(policy)),
            )
        (project.root / "operations" / operation / "screening_config.json").write_text(
            json.dumps(policy, indent=2), encoding="utf-8"
        )
        candidates = candidate_rows(conn)
        if collection_id:
            ids = {m["candidate_id"] for m in collections.memberships(conn, collection_id)}
            candidates = [c for c in candidates if c["candidate_id"] in ids]
        observed = {}
        counts = Counter()
        previous = records(conn, "observations") if reuse_raw else []
        for settings in config.detectors:
            detector, setup_error = None, None
            if (
                settings.enabled
                and not reuse_raw
                and any(c["asset_id"] and c["file_path"] for c in candidates)
            ):
                try:
                    detector = build_detector(settings)
                    detectors.append(detector)
                except Exception as exc:
                    setup_error = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "phase": "detector_setup",
                    }
            for candidate in candidates:
                parameters = settings.model_dump(mode="json")
                obs = {
                    "observation_id": new_id("observation"),
                    "project_id": project.project_id,
                    "candidate_id": candidate["candidate_id"],
                    "asset_id": candidate["asset_id"],
                    "property": settings.property,
                    "source": settings.id,
                    "family": "torchvision_object_detection",
                    "model_identity": {
                        "model": settings.model,
                        "requested_weights": settings.weights,
                        "loaded": False,
                    },
                    "parameters": parameters,
                    "thresholds": {"low": settings.low_threshold, "high": settings.high_threshold},
                    "runtime": {
                        "device_requested": settings.device,
                        "python": platform.python_version(),
                    },
                    "timestamp": now(),
                    "operation_id": operation,
                    "status": "not_run",
                    "score": None,
                    "score_semantics": "maximum retained COCO person class score; uncalibrated; not an absence probability",
                    "raw": {},
                    "error": None,
                    "reason": "Detector disabled",
                    "derived_from": None,
                }
                if detector:
                    obs.update(model_identity=detector.identity, runtime=detector.runtime)
                if settings.enabled:
                    try:
                        if not candidate["asset_id"] or not candidate["file_path"]:
                            obs["reason"] = "No acquired image asset"
                        else:
                            path = project.resolve(candidate["file_path"])
                            if not path.is_file():
                                raise FileNotFoundError("Acquired image file is missing")
                            if "sha256:" + file_hash(path) != candidate["asset_id"]:
                                raise ValueError(
                                    "Original bytes differ from the recorded asset hash"
                                )
                            if setup_error:
                                obs.update(
                                    status="error",
                                    error=setup_error,
                                    reason="Detector setup failed",
                                )
                            else:
                                raw = None
                                if reuse_raw:
                                    ignored = {"low_threshold", "high_threshold", "enabled"}
                                    target = {
                                        k: v for k, v in parameters.items() if k not in ignored
                                    }
                                    source = next(
                                        (
                                            r
                                            for r in reversed(previous)
                                            if r["candidate_id"] == candidate["candidate_id"]
                                            and r["asset_id"] == candidate["asset_id"]
                                            and r["source"] == settings.id
                                            and r["property"] == settings.property
                                            and r["status"]
                                            in {"detected", "not_detected", "uncertain"}
                                            and {
                                                k: v
                                                for k, v in r["parameters"].items()
                                                if k not in ignored
                                            }
                                            == target
                                        ),
                                        None,
                                    )
                                    if source:
                                        raw = source["raw"]
                                        obs.update(
                                            model_identity=source["model_identity"],
                                            runtime=dict(
                                                source["runtime"], reused_without_inference=True
                                            ),
                                            derived_from=source["observation_id"],
                                        )
                                    else:
                                        obs["reason"] = (
                                            "No compatible raw observation for these inference parameters and asset"
                                        )
                                else:
                                    raw = detector.predict(path)
                                if raw is not None:
                                    # Retain output even if normalization discovers an invalid detector record.
                                    obs["raw"] = raw
                                    status, score, reason = normalize(raw, settings)
                                    obs.update(status=status, score=score, reason=reason)
                    except Exception as exc:
                        obs.update(
                            status="error",
                            error={
                                "type": type(exc).__name__,
                                "message": str(exc),
                                "phase": "inference_or_input",
                            },
                            reason="Detector/input processing failed",
                            score=None,
                        )
                # Invalid non-JSON detector output must still yield explicit error evidence.
                try:
                    json.dumps(obs, allow_nan=False)
                except (TypeError, ValueError):
                    obs.update(
                        status="error",
                        error={
                            "type": "InvalidOutput",
                            "message": "Detector returned non-JSON/non-finite values",
                        },
                        reason="Invalid detector output",
                        score=None,
                        raw={"unserializable_repr": repr(obs["raw"])},
                    )
                with conn:
                    observation = append_observation(conn, obs)
                observed[(candidate["candidate_id"], settings.id)] = observation
                counts[observation["status"]] += 1
            if detector:
                detector.close()
                detectors.remove(detector)
        for candidate in [] if collection_id else candidates:
            with conn:
                for rule in config.rules:
                    observation = observed[(candidate["candidate_id"], rule.source)]
                    disposition, reason = evaluate(rule, observation)
                    event = {
                        "evaluation_id": new_id("evaluation"),
                        "policy_id": policy["policy_id"],
                        "candidate_id": candidate["candidate_id"],
                        "asset_id": candidate["asset_id"],
                        "rule_id": rule.id,
                        "rule_version": rule.version,
                        "rule": rule.model_dump(mode="json"),
                        "observation_id": observation["observation_id"],
                        "observation_status": observation["status"],
                        "disposition": disposition,
                        "timestamp": now(),
                        "operation_id": operation,
                        "reason": reason,
                    }
                    conn.execute(
                        "INSERT INTO screening_evaluations VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            event["evaluation_id"],
                            policy["policy_id"],
                            candidate["candidate_id"],
                            candidate["asset_id"],
                            rule.id,
                            observation["observation_id"],
                            disposition,
                            operation,
                            event["timestamp"],
                            json.dumps(event),
                        ),
                    )
        if collection_id:
            with conn:
                collections.evaluate_policy(
                    conn, project, collection_id, config.model_dump(mode="json")
                )
            report = collections.screening_report(conn, project, collection_id)
        else:
            report = report_from_connection(conn, project)
        result = {
            "policy_id": policy["policy_id"],
            "observations": dict(counts),
            "counts": report["counts"],
            "stage_status": "partial" if counts["error"] or counts["not_run"] else "succeeded",
        }
        if not config.detectors or not config.rules:
            result.update(
                stage_status="partial",
                reason="No complete screening policy configured; candidates require review",
            )
        return result
    finally:
        for detector in detectors:
            detector.close()
        conn.close()


def report_from_connection(conn, project, *, include_history=True, candidate_id=None):
    policies = records(conn, "screening_policies")
    policy = next((p for p in reversed(policies) if not p.get("collection_id")), None)
    required = [r["id"] for r in policy["config"]["rules"]] if policy else []
    where = "WHERE candidate_id=?" if candidate_id is not None else ""
    params = (candidate_id,) if candidate_id is not None else ()
    evaluations = records(conn, "screening_evaluations", where, params)
    overrides = records(conn, "screening_overrides", where, params)
    observations = records(conn, "observations", where, params)
    output = []
    for candidate in candidate_rows(conn, candidate_id):
        cid, aid = candidate["candidate_id"], candidate["asset_id"]
        selected = {
            e["rule_id"]: e
            for e in evaluations
            if policy
            and e["policy_id"] == policy["policy_id"]
            and e["candidate_id"] == cid
            and e["asset_id"] == aid
        }
        active = [selected[r] for r in required if r in selected]
        statuses = [e["observation_status"] for e in active]
        values = [e["disposition"] for e in active]
        if "excluded" in values:
            base = "excluded"
        elif not required or len(active) != len(required) or "review_required" in values:
            base = "review_required"
        else:
            base = "eligible"
        scope = {
            "policy_id": policy["policy_id"] if policy else None,
            "asset_id": aid,
            "evaluation_ids": sorted(e["evaluation_id"] for e in active),
        }
        applied = next(
            (o for o in reversed(overrides) if o["candidate_id"] == cid and o["scope"] == scope),
            None,
        )
        output.append(
            {
                "candidate_id": cid,
                "local_id": candidate["primary_local_id"],
                "asset_id": aid,
                "rule_disposition": base,
                "effective_disposition": applied["new_disposition"] if applied else base,
                "observation_statuses": statuses,
                "error_or_not_run": not required
                or len(active) != len(required)
                or any(s in {"error", "not_run"} for s in statuses),
                "scope": scope,
                "override": applied,
                "evaluations": active,
                "reason": "No configured/evaluated policy"
                if not required or len(active) != len(required)
                else "Configured rule dispositions aggregated; excluded takes precedence, then review_required",
            }
        )
    counts = {
        name: sum(r["effective_disposition"] == name for r in output)
        for name in ("eligible", "excluded", "review_required")
    }
    counts["error_or_not_run"] = sum(r["error_or_not_run"] for r in output)
    result = {
        "project_id": project.project_id,
        "policy": policy,
        "counts": counts,
        "candidates": output,
        "interpretation": "Screening eligibility is not investigator acceptance or confirmed human absence. Error/not-run count overlaps disposition queues.",
    }
    if include_history:
        result.update(
            observations=observations,
            evaluations=evaluations,
            overrides=overrides,
            policies=policies,
        )
    return result


def screening_report(project, *, disposition=None, include_history=True):
    if disposition:
        TypeAdapter(Disposition).validate_python(disposition)
    conn = init_db(project.paths.db_path)
    try:
        report = report_from_connection(conn, project, include_history=include_history)
        if disposition:
            report["candidates"] = [
                r for r in report["candidates"] if r["effective_disposition"] == disposition
            ]
        return report
    finally:
        conn.close()


def override(project, *, candidate_id, disposition, reviewer, note=None, expected_scope=None):
    TypeAdapter(Disposition).validate_python(disposition)
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("An explicit reviewer identity is required")
    operation = ACTIVE_OPERATION.get()
    if not operation:
        raise ValueError("Overrides must run through shared services")
    conn = init_db(project.paths.db_path)
    try:
        ensure_schema(conn)
        report = report_from_connection(conn, project, include_history=False)
        row = next(
            (r for r in report["candidates"] if candidate_id in (r["candidate_id"], r["local_id"])),
            None,
        )
        if row is None:
            raise ValueError("Unknown candidate")
        if expected_scope is not None and expected_scope != row["scope"]:
            raise ValueError("Screening changed since review; refresh before overriding")
        record = {
            "override_id": new_id("override"),
            "candidate_id": row["candidate_id"],
            "operation_id": operation,
            "timestamp": now(),
            "reviewer": reviewer.strip(),
            "note": note,
            "prior_disposition": row["effective_disposition"],
            "new_disposition": disposition,
            "scope": row["scope"],
            "prior_override_id": row["override"]["override_id"] if row["override"] else None,
        }
        with conn:
            conn.execute(
                "INSERT INTO screening_overrides VALUES(?,?,?,?,?)",
                (
                    record["override_id"],
                    row["candidate_id"],
                    operation,
                    record["timestamp"],
                    json.dumps(record),
                ),
            )
        return record
    finally:
        conn.close()

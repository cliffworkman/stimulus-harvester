"""Bounded, offline human-label validation and threshold analysis (no inference)."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from .config import DetectorConfig, StrictModel
from .detectors import normalize


class Annotation(StrictModel):
    annotation_id: str = Field(min_length=1)
    image_id: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    human_presence: Literal["present", "absent", "uncertain"]
    reviewer: str = Field(min_length=1)
    reviewed_at: datetime
    tags: list[str] = Field(default_factory=list)
    note: str = ""
    exposure: Literal[
        "none", "prior_discussion", "post_review_discussion", "model_outputs", "unspecified"
    ]
    kind: Literal["rating", "adjudication"] = "rating"
    adjudicates: list[str] = Field(default_factory=list)

    @field_validator("reviewer", "annotation_id", "image_id")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Identity cannot be blank")
        return value

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.utcoffset() is None:
            raise ValueError("Review timestamp must include a timezone")
        return value

    @field_validator("tags")
    @classmethod
    def valid_tags(cls, value):
        if any(not tag.strip() for tag in value) or len(value) != len(set(value)):
            raise ValueError("Tags must be nonblank and unique")
        return value


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def write_new(path, value):
    """Exclusive creation: never silently replace previous human evidence or analysis."""
    payload = json.dumps(value, indent=2, allow_nan=False)
    with Path(path).open("x", encoding="utf-8") as handle:
        handle.write(payload + "\n")


def resolve_labels(
    items, records, *, human_reviewer=None, ai_reviewers=(), adjudication_reviewers=()
):
    """Retain every rating; require explicit adjudication for disagreements."""
    expected = {i["image_id"]: i for i in items}
    if len(expected) != len(items):
        raise ValueError("Duplicate manifest image IDs")
    annotations = [Annotation.model_validate(r) for r in records]
    if human_reviewer is None and (ai_reviewers or adjudication_reviewers):
        raise ValueError("An explicit human reviewer is required for mixed-rater import")
    if human_reviewer in ai_reviewers:
        raise ValueError("Human and AI reviewer roles must be disjoint")
    ids = {a.annotation_id for a in annotations}
    if len(ids) != len(annotations):
        raise ValueError("Duplicate annotation IDs")
    grouped = defaultdict(list)
    for a in annotations:
        if a.image_id not in expected or a.sha256 != expected[a.image_id]["sha256"]:
            raise ValueError("Annotation image/hash does not match manifest")
        if human_reviewer is not None:
            allowed = (
                {human_reviewer, *ai_reviewers}
                if a.kind == "rating"
                else set(adjudication_reviewers)
            )
            if a.reviewer not in allowed:
                raise ValueError("Reviewer has no explicitly assigned provenance role")
        grouped[a.image_id].append(a)
    resolved = []
    for image_id, item in expected.items():
        rows = grouped[image_id]
        ratings = [a for a in rows if a.kind == "rating"]
        ground_ratings = (
            [a for a in ratings if a.reviewer == human_reviewer]
            if human_reviewer is not None
            else ratings
        )
        decisions = [a for a in rows if a.kind == "adjudication"]
        if not ground_ratings:
            raise ValueError(f"Missing human rating: {image_id}")
        if len({a.reviewer.strip().casefold() for a in ratings}) != len(ratings):
            raise ValueError("Multiple initial ratings by the same reviewer")
        if any(a.adjudicates for a in ratings) or len(decisions) > 1:
            raise ValueError("Invalid adjudication structure")
        if decisions:
            chosen = decisions[0]
            if (
                len(ratings) < 2
                or len(chosen.adjudicates) != len(ratings)
                or set(chosen.adjudicates) != {a.annotation_id for a in ratings}
                or chosen.reviewed_at < max(a.reviewed_at for a in ratings)
            ):
                raise ValueError("Adjudication must reference all prior ratings of this image")
            label, tags, state = chosen.human_presence, chosen.tags, "adjudicated"
        else:
            if len({a.human_presence for a in ground_ratings}) != 1:
                raise ValueError(f"Unresolved human disagreement: {image_id}")
            chosen = ground_ratings[0]
            label = chosen.human_presence
            tags = sorted({t for a in ground_ratings for t in a.tags})
            state = "single_reviewer" if len(ground_ratings) == 1 else "agreement"
        resolved.append(
            {
                "image_id": image_id,
                "sha256": item["sha256"],
                "cohort": item["cohort"],
                "human_presence": label,
                "tags": tags,
                "adjudication_status": state,
                "annotation_ids": [a.annotation_id for a in rows],
                "ground_truth_annotation_ids": [
                    a.annotation_id for a in ground_ratings + decisions
                ],
                "selected_annotation_id": chosen.annotation_id,
                "note": chosen.note,
            }
        )
    return {
        "manifest_digest": digest(items),
        "annotations": copy.deepcopy(records),
        "ground_truth_pathway": {
            "human_reviewer": human_reviewer,
            "ai_reviewers_excluded_from_ground_truth": list(ai_reviewers),
            "authorized_adjudication_reviewers": list(adjudication_reviewers),
            "policy": "Human ratings followed by explicit adjudications; AI ratings remain provenance only"
            if human_reviewer is not None
            else "All supplied initial reviewers designated human by caller",
        },
        "items": resolved,
    }


def make_split(labels, seed="cp4b-20260919"):
    """Assign identical image bytes together; no model evidence enters assignment."""
    groups = defaultdict(list)
    for item in labels["items"]:
        groups[item["sha256"]].append(item)
    strata = defaultdict(list)
    counts = Counter()
    for sha, rows in groups.items():
        truth = {r["human_presence"] for r in rows}
        if len(truth) != 1:
            raise ValueError("Conflicting human labels for identical bytes")
        label = next(iter(truth))
        counts[label] += 1
        strata[(tuple(sorted({r["cohort"] for r in rows})), label)].append(sha)
    # A small operational floor, explicitly NOT a confidence or acceptance guarantee.
    adequate = counts["present"] >= 20 and counts["absent"] >= 20
    assignment = {}
    for (_, label), hashes in sorted(strata.items()):
        ordered = sorted(hashes, key=lambda sha: digest([seed, sha]))
        for idx, sha in enumerate(ordered):
            assignment[sha] = (
                "heldout" if adequate and label != "uncertain" and idx % 2 else "development"
            )
    # Many sparse cohorts must not produce a nominal holdout with too few positives.
    held_counts = Counter(
        rows[0]["human_presence"] for sha, rows in groups.items() if assignment[sha] == "heldout"
    )
    if held_counts["present"] < 10 or held_counts["absent"] < 10:
        adequate = False
        assignment = dict.fromkeys(groups, "development")
    return {
        "labels_digest": digest(labels),
        "seed": seed,
        "mode": "heldout_available" if adequate else "exploratory_only",
        "criterion": "At least 20 unique present and 20 absent assets; at least 10 of each held out. Operational floor only, not statistical sufficiency for production.",
        "unique_asset_labels": dict(counts),
        "assignment_by_sha256": assignment,
    }


def split_items(labels, split, subset):
    if subset not in {"development", "heldout"}:
        raise ValueError("Unknown subset")
    if split["labels_digest"] != digest(labels):
        raise ValueError("Labels changed after split was locked")
    if digest(make_split(labels, split["seed"])) != digest(split):
        raise ValueError("Split differs from the deterministic, label-only assignment")
    assignment = split["assignment_by_sha256"]
    if set(assignment) != {i["sha256"] for i in labels["items"]}:
        raise ValueError("Split must cover exactly the labeled assets")
    if set(assignment.values()) - {"development", "heldout"}:
        raise ValueError("Invalid split assignment")
    return [i for i in labels["items"] if assignment[i["sha256"]] == subset]


def replay(predictions, saved_config, low, high):
    """Change only thresholds; retain model identity and all post-NMS constraints."""
    original = DetectorConfig.model_validate(saved_config)
    config = DetectorConfig.model_validate(
        {
            **original.model_dump(),
            "low_threshold": low,
            "high_threshold": high,
        }
    )
    result = []
    for p in predictions:
        row = dict(p)
        if p["status"] in {"error", "not_run"}:
            result.append(row)
            continue
        if p["status"] not in {"detected", "uncertain", "not_detected"}:
            raise ValueError("Unknown observation status")
        raw = p["raw"]
        for key in ("output_floor", "max_detections", "nms_threshold"):
            if raw[key] != getattr(original, key):
                raise ValueError(f"Raw output/config mismatch: {key}")
        row["status"], row["score"], row["reason"] = normalize(raw, config)
        result.append(row)
    return result


def metric_table(predictions, items):
    by_id = {p["image_id"]: p for p in predictions}
    if len(by_id) != len(predictions) or len({i["image_id"] for i in items}) != len(items):
        raise ValueError("Duplicate image IDs")
    matrix = {label: Counter() for label in ("present", "absent", "uncertain")}
    fn, fp, unresolved_positive = [], [], []
    statuses = Counter()
    for item in items:
        p = by_id.get(item["image_id"])
        if p is None or p["sha256"] != item["sha256"]:
            raise ValueError("Predictions missing or image/hash mismatch")
        status, truth = p["status"], item["human_presence"]
        if status not in {"detected", "uncertain", "not_detected", "error", "not_run"}:
            raise ValueError("Unknown observation status")
        matrix[truth][status] += 1
        statuses[status] += 1
        detail = {k: item[k] for k in ("image_id", "sha256", "tags")}
        if truth == "present" and status == "not_detected":
            fn.append(detail)
        if truth == "present" and status != "detected":
            unresolved_positive.append({**detail, "status": status})
        if truth == "absent" and status == "detected":
            fp.append(detail)
    pos, neg = matrix["present"], matrix["absent"]
    p_count, n_count = sum(pos.values()), sum(neg.values())
    tp, false_positive = pos["detected"], neg["detected"]
    fraction = lambda n, d: n / d if d else None
    by_tag = lambda rows: dict(Counter(t for r in rows for t in r["tags"]))
    return {
        "images": len(items),
        "human_present": p_count,
        "human_absent": n_count,
        "human_uncertain": sum(matrix["uncertain"].values()),
        "confusion_by_truth_and_status": {k: dict(v) for k, v in matrix.items()},
        "true_positives_detected": tp,
        "false_negatives_not_detected": len(fn),
        "positive_not_detected_or_deferred": p_count - tp,
        "detected_recall": fraction(tp, p_count),
        "detected_or_uncertain_recall": fraction(tp + pos["uncertain"], p_count),
        "safe_routing_rate": fraction(p_count - pos["not_detected"], p_count),
        "specificity_explicit_not_detected": fraction(neg["not_detected"], n_count),
        "false_positive_detected_rate": fraction(false_positive, n_count),
        "precision_detected_known_truth": fraction(tp, tp + false_positive),
        "detected_with_uncertain_truth": matrix["uncertain"]["detected"],
        "uncertain_count": statuses["uncertain"],
        "uncertain_rate": fraction(statuses["uncertain"], len(items)),
        "review_workload_triage": sum(statuses[s] for s in ("uncertain", "error", "not_run")),
        "review_workload_strict": len(items) - statuses["detected"],
        "false_negatives": fn,
        "false_negatives_by_tag": by_tag(fn),
        "positives_not_detected_automatically": unresolved_positive,
        "positives_not_detected_automatically_by_tag": by_tag(unresolved_positive),
        "false_positives": fp,
        "false_positives_by_tag": by_tag(fp),
        "interpretation": "Abstentions/errors are not binary negatives. Specificity is explicit not_detected / all absent; FPR is detected / all absent, so these need not sum to one. Precision excludes uncertain human truth. Safe routing includes errors sent to review, not detector success. Strict workload assumes detected excluded, all other states reviewed. Tag counts overlap; cohorts must remain separate.",
    }


def analyze(labels, split, predictions, config, policies, *, subset="development"):
    if subset == "heldout" and len(policies) != 1:
        raise ValueError("Held-out evaluation accepts one preselected policy, not a sweep")
    items = split_items(labels, split, subset)
    if not items:
        raise ValueError("No labeled images in requested subset")
    results = []
    for low, high in policies:
        normalized = replay(predictions, config, low, high)
        results.append(
            {
                "low": low,
                "high": high,
                "by_cohort": {
                    cohort: metric_table(normalized, [i for i in items if i["cohort"] == cohort])
                    for cohort in sorted({i["cohort"] for i in items})
                },
            }
        )
    return results


def choose_policy(development, low, high, rationale):
    if (
        development["subset"] != "development"
        or development["mode"] != "heldout_available"
        or not rationale.strip()
        or (low, high) not in {(r["low"], r["high"]) for r in development["results"]}
    ):
        raise ValueError("Choose a tested development policy with a rationale and valid holdout")
    return {
        "development_digest": digest(development),
        "split_digest": development["split_digest"],
        "model_identity": development["model_identity"],
        "low": low,
        "high": high,
        "rationale": rationale,
        "development": development,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import-labels")
    imp.add_argument("--manifest", required=True, type=Path)
    imp.add_argument("--annotations", required=True, type=Path, nargs="+")
    imp.add_argument("--human-reviewer")
    imp.add_argument("--ai-reviewer", action="append", default=[])
    imp.add_argument("--adjudication-reviewer", action="append", default=[])
    spl = sub.add_parser("split")
    spl.add_argument("--labels", required=True, type=Path)
    choice_parser = sub.add_parser("choose")
    choice_parser.add_argument("--development", required=True, type=Path)
    choice_parser.add_argument("--low", required=True, type=float)
    choice_parser.add_argument("--high", required=True, type=float)
    choice_parser.add_argument("--rationale", required=True)
    for name in ("develop", "evaluate"):
        p = sub.add_parser(name)
        p.add_argument("--labels", required=True, type=Path)
        p.add_argument("--split", required=True, type=Path)
        p.add_argument("--run", required=True, type=Path)
        if name == "evaluate":
            p.add_argument("--choice", required=True, type=Path)
    for p in (imp, spl, choice_parser, *[sub.choices[n] for n in ("develop", "evaluate")]):
        p.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    read = lambda p: json.loads(p.read_text(encoding="utf-8"))
    if args.command == "import-labels":
        result = resolve_labels(
            read(args.manifest)["items"],
            [r for path in args.annotations for r in read(path)["annotations"]],
            human_reviewer=args.human_reviewer,
            ai_reviewers=args.ai_reviewer,
            adjudication_reviewers=args.adjudication_reviewer,
        )
    elif args.command == "split":
        result = make_split(read(args.labels))
    elif args.command == "choose":
        from datetime import timezone

        result = choose_policy(read(args.development), args.low, args.high, args.rationale)
        result["chosen_at"] = datetime.now(timezone.utc).isoformat()
    else:
        labels, split, report = read(args.labels), read(args.split), read(args.run / "report.json")
        predictions = [
            json.loads(line) for line in (args.run / "predictions.jsonl").read_text().splitlines()
        ]
        policies = [(lo, hi) for lo in (0.01, 0.03, 0.05, 0.10, 0.20) for hi in (0.25, 0.50, 0.75)]
        choice = None
        if args.command == "evaluate":
            choice = read(args.choice)
            expected_choice = choose_policy(
                choice["development"], choice["low"], choice["high"], choice["rationale"]
            )
            if any(choice[k] != value for k, value in expected_choice.items()):
                raise ValueError("Recorded choice was altered after development")
            if (
                choice["split_digest"] != digest(split)
                or choice["model_identity"] != report["model_identity"]
                or choice["development"]["predictions_digest"] != digest(predictions)
                or not choice["rationale"].strip()
                or split["mode"] != "heldout_available"
            ):
                raise ValueError("Choice must be recorded for this model and locked split")
            policies = [(choice["low"], choice["high"])]
        result = {
            "labels_digest": digest(labels),
            "split_digest": digest(split),
            "model_identity": report["model_identity"],
            "config": report["config"],
            "predictions_digest": digest(predictions),
            "choice": choice,
            "subset": "heldout" if choice else "development",
            "mode": split["mode"],
            "results": analyze(
                labels,
                split,
                predictions,
                report["config"],
                policies,
                subset="heldout" if choice else "development",
            ),
        }
    write_new(args.output, result)


if __name__ == "__main__":
    main()

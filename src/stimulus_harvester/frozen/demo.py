"""Deterministic synthetic workflow: python -m stimulus_harvester.frozen.demo --output NEW_DIR.

Images and detector outputs are synthetic fixtures, not private stimuli or accuracy evidence.
Project/artifact IDs and timestamps are intentionally unique per run.
"""

import argparse
import hashlib
import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from stimulus_harvester import services
from stimulus_harvester.config import HarvesterConfig, config_to_yaml_str
from stimulus_harvester.models import DownloadRecord, DownloadStatus, SearchResult
from stimulus_harvester.review.db import (
    init_db,
    set_review_label,
    update_download,
    upsert_image_from_search,
)
from stimulus_harvester.screening.detectors import file_hash
from .store import inventory


def config():
    return HarvesterConfig.model_validate(
        {
            "run_name": "synthetic checkpoint 5",
            "search": {
                "seed_queries": ["synthetic fixture"],
                "query_expansion": {"enabled": False},
            },
            "clip": {"positive_prompts": ["fixture"], "negative_prompts": ["not fixture"]},
        }
    )


def strict_policy(version="1", low=0.15, high=0.60):
    return {
        "detectors": [
            {
                "id": "fixture-human",
                "property": "human_presence",
                "model": "fasterrcnn_resnet50_fpn_v2",
                "low_threshold": low,
                "high_threshold": high,
            }
        ],
        "rules": [
            {
                "id": "strict-zero-human",
                "version": version,
                "property": "human_presence",
                "source": "fixture-human",
                "outcomes": {
                    s: "excluded" if s == "detected" else "review_required"
                    for s in ["detected", "uncertain", "not_detected", "error", "not_run"]
                },
            }
        ],
    }


def seed(project, local_id, *, label=None, duplicate=None, license="cc-by", raw=True):
    image_url = f"https://example.invalid/{local_id}.png"
    raw_path = project.root / "raw_api" / f"{local_id}.json"
    if raw:
        raw_path.parent.mkdir(exist_ok=True, parents=True)
        raw_path.write_text(
            json.dumps({"synthetic": True, "local_id": local_id, "license_claim": license})
        )
    conn = init_db(project.paths.db_path)
    try:
        upsert_image_from_search(
            conn,
            SearchResult(
                local_id=local_id,
                source_backend="synthetic-fixture",
                search_query="synthetic fixture",
                seed_query="synthetic fixture",
                image_url=image_url,
                landing_page_url=image_url,
                license=license,
                creator=f"Fixture claim {local_id}",
                raw_response_path=str(raw_path) if raw else None,
                provider_metadata={"synthetic": True},
                query_lineage={"seed": "synthetic fixture", "expansion": False},
            ),
            project.project_id,
        )
        path = project.paths.images_original / f"{local_id}.png"
        if duplicate:
            path.write_bytes(duplicate.read_bytes())
        else:
            color = tuple(hashlib.sha256(local_id.encode()).digest()[:3])
            Image.new("RGB", (40, 30), color).save(path)
        update_download(
            conn,
            DownloadRecord(
                local_id=local_id,
                image_url=image_url,
                status=DownloadStatus.SUCCESS,
                file_path=str(path),
                sha256=file_hash(path),
                width=40,
                height=30,
                file_size_bytes=path.stat().st_size,
            ),
        )
        if label:
            set_review_label(
                conn,
                local_id,
                label=label,
                notes="Explicit synthetic investigator review",
                reviewer_email="fixture-investigator",
            )
        cid = conn.execute(
            "SELECT candidate_id FROM legacy_ids WHERE local_id=?", (local_id,)
        ).fetchone()[0]
        return {
            "candidate_id": cid,
            "asset_id": "sha256:" + file_hash(path),
            "path": path,
            "local_id": local_id,
        }
    finally:
        conn.close()


def review(project, local_id, label):
    conn = init_db(project.paths.db_path)
    try:
        set_review_label(
            conn,
            local_id,
            label=label,
            notes="Explicit later synthetic review",
            reviewer_email="fixture-investigator",
        )
    finally:
        conn.close()


def request(**updates):
    return {
        "reviewer": "fixture-investigator",
        "scope": {
            "mode": "all_current",
            "selection_rule": "All current synthetic fixture candidates",
        },
        **updates,
    }


def run(project, stage, **options):
    result = services.run_stage(project, stage, **options)
    if result.status != "succeeded":
        raise RuntimeError(result.error)
    return result.result


def screen(project, scores, policy=None):
    class FixtureDetector:
        def __init__(self, settings):
            self.identity = {
                "model": settings.model,
                "weights": "SYNTHETIC FIXTURE; no model loaded",
                "artifact_sha256": "0" * 64,
                "fixture": True,
            }
            self.runtime = {"device": "synthetic", "fixture": True}

        def predict(self, path):
            score = scores.get(path.stem, 0)
            return {
                "width": 40,
                "height": 30,
                "detections": [
                    {"class": "person", "class_id": 1, "score": score, "box_xyxy": [1, 1, 20, 25]}
                ]
                if score
                else [],
                "fixture": True,
            }

        def close(self):
            pass

    with patch("stimulus_harvester.screening.engine.build_detector", FixtureDetector):
        return run(project, "screen", screening_config=policy or strict_policy())


def demonstration(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    project = services.create_project(output / "project 研究", config())
    a = seed(project, "accepted", label="accepted")
    seed(project, "duplicate", label="accepted", duplicate=a["path"], license="all-rights-reserved")
    seed(project, "conflict", label="rejected", duplicate=a["path"], license=None)
    seed(project, "rejected", label="rejected")
    seed(project, "excluded", label="rejected")
    overridden = seed(project, "overridden", label="accepted")
    scores = {
        "accepted": 0.25,
        "duplicate": 0.25,
        "conflict": 0.25,
        "excluded": 0.9,
        "overridden": 0.9,
        "rejected": 0.9,
    }
    screen(project, scores)
    before = services.freeze_validate(project, request=request())
    assert not before["valid"]
    run(
        project,
        "screen-override",
        candidate_id=overridden["candidate_id"],
        disposition="review_required",
        reviewer="fixture-investigator",
        note="Explicit synthetic adjudication; retain for human assessment",
    )
    resolved = request(
        deduplicate_accepted=True,
        duplicate_resolutions=[
            {
                "asset_id": a["asset_id"],
                "membership": "include",
                "reviewer": "fixture-investigator",
                "reason": "Retain the accepted exact asset once while preserving the rejected source candidate history",
            }
        ],
    )
    freeze1 = run(project, "freeze-create", request=resolved)
    assembly1 = run(project, "assemble", freeze_id=freeze1["artifact_id"])
    old_hashes = inventory(Path(assembly1["path"]))
    freeze_hashes = inventory(Path(freeze1["path"]))
    review(project, "accepted", "rejected")
    review(project, "duplicate", "rejected")
    conn = init_db(project.paths.db_path)
    try:
        upsert_image_from_search(
            conn,
            SearchResult(
                local_id="later-source",
                source_backend="later-fixture",
                search_query="later query",
                seed_query="later seed",
                image_url="https://example.invalid/accepted.png",
                landing_page_url="https://example.invalid/later",
                creator="Later conflicting attribution",
                license="later-unknown-claim",
                provider_metadata={"synthetic": True},
            ),
            project.project_id,
        )
    finally:
        conn.close()
    later_policy = strict_policy(version="2", low=0.20, high=0.70)
    later = config().model_copy(update={"run_name": "Later mutable state"})
    data = later.model_dump(mode="json")
    data["screening"] = later_policy
    project.paths.config_snapshot_path.write_text(
        config_to_yaml_str(HarvesterConfig.model_validate(data)), encoding="utf-8"
    )
    screen(project, scores, later_policy)
    run(
        project,
        "screen-override",
        candidate_id=overridden["candidate_id"],
        disposition="review_required",
        reviewer="fixture-investigator",
        note="Reaffirm after the new policy/evaluations invalidated the prior override",
    )
    assembly2 = run(
        project,
        "assemble",
        freeze_id=freeze1["artifact_id"],
        parent_assembly_id=assembly1["artifact_id"],
    )
    freeze2 = run(
        project,
        "freeze-create",
        request=request(parent_freeze_id=freeze1["artifact_id"], deduplicate_accepted=True),
    )
    new_assembly = run(project, "assemble", freeze_id=freeze2["artifact_id"])
    exported = run(project, "export-frozen", assembly_id=assembly2["artifact_id"])
    assert inventory(Path(assembly1["path"])) == old_hashes
    assert inventory(Path(freeze1["path"])) == freeze_hashes
    assert assembly1["membership_sha256"] == assembly2["membership_sha256"]
    assert assembly1["stimulus_count"] == 2 and new_assembly["stimulus_count"] == 1
    assert services.verify_frozen_package(
        exported["path"], expected_sha256=exported["manifest_sha256"]
    )["valid"]
    result = {
        "fixture": "Synthetic images and mocked detector outputs; no accuracy or actual licensing claims",
        "initial_validation": before,
        "freeze1": freeze1,
        "assembly1": assembly1,
        "repeat_old_assembly_after_mutations": assembly2,
        "freeze2": freeze2,
        "new_assembly": new_assembly,
        "export": exported,
        "old_freeze_and_assembly_unchanged": True,
        "original_bytes_deterministic": True,
        "project_path": str(project.root),
    }
    (output / "demo-results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(demonstration(args.output), indent=2))


if __name__ == "__main__":
    main()

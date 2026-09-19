import copy
import os

import pytest

from stimulus_harvester import services
from stimulus_harvester.review.db import init_db
from stimulus_harvester.screening.benchmark import metrics
from stimulus_harvester.screening.config import RuleConfig
from stimulus_harvester.screening.engine import evaluate
from stimulus_harvester.screening.models import Observation
from test_screening import project, fake, seed, policy  # noqa: F401


def test_unlabeled_benchmark_does_not_manufacture_accuracy():
    prediction = {
        "image_id": "a",
        "sha256": "x",
        "status": "not_detected",
        "raw": {"inference_seconds": 1},
    }
    report = metrics([prediction], [{"image_id": "a", "sha256": "x", "human_presence": None}])
    assert report["human_reviewed"] == 0
    assert report["detected_recall"] is report["false_positive_detected_rate"] is None
    assert report["median_inference_seconds"] == 1


def test_benchmark_separates_detected_uncertain_and_missed_people():
    predictions = [
        {"image_id": str(i), "sha256": str(i), "status": status, "raw": None}
        for i, status in enumerate(
            ["detected", "uncertain", "not_detected", "detected", "uncertain", "not_detected"]
        )
    ]
    labels = [
        {
            "image_id": str(i),
            "sha256": str(i),
            "human_presence": "present" if i < 3 else "absent",
            "reviewer": "human",
            "reviewed_at": "2026-09-19",
        }
        for i in range(6)
    ]
    report = metrics(predictions, labels)
    assert report["detected_recall"] == report["false_positive_detected_rate"] == 1 / 3
    assert report["detected_or_uncertain_recall"] == 2 / 3
    assert report["false_negative_eligible_rate"] == report["uncertain_rate"] == 1 / 3
    labels[0]["sha256"] = "different"
    with pytest.raises(ValueError, match="same image bytes"):
        metrics(predictions, labels)


def test_observations_support_other_properties_without_comparing_scores(project, fake):
    seed(project)
    services.run_stage(project, "screen")
    record = copy.deepcopy(services.screening_report(project)["observations"][0])
    record.update(
        property="windows",
        source="human-annotator",
        family="manual",
        score=None,
        score_semantics=None,
    )
    observation = Observation.model_validate(record)
    rule = RuleConfig(
        id="window-rule",
        version="1",
        property="windows",
        source="human-annotator",
        outcomes={
            "detected": "eligible",
            "not_detected": "excluded",
            "uncertain": "review_required",
            "error": "review_required",
            "not_run": "review_required",
        },
    )
    assert evaluate(rule, observation.model_dump())[0] == "eligible"
    record.update(score=0.7)
    with pytest.raises(ValueError, match="score semantics"):
        Observation.model_validate(record)


def test_existing_vlm_and_human_review_are_not_replaced(project, fake):
    seed(project)
    conn = init_db(project.paths.db_path)
    conn.execute(
        "UPDATE images SET vlm_backend_name='ollama', vlm_model_name='other', vlm_fields=?",
        ('{"people_visible": false}',),
    )
    conn.execute("UPDATE reviews SET label='accepted',notes='investigator decision'")
    conn.commit()
    before = dict(conn.execute("SELECT * FROM images").fetchone())
    review = dict(conn.execute("SELECT * FROM reviews").fetchone())
    conn.close()
    services.run_stage(project, "screen")
    conn = init_db(project.paths.db_path)
    assert dict(conn.execute("SELECT * FROM images").fetchone()) == before
    assert dict(conn.execute("SELECT * FROM reviews").fetchone()) == review
    conn.close()
    assert services.screening_report(project)["counts"]["excluded"] == 1


def test_new_asset_invalidates_evaluations_and_override(project, fake):
    seed(project)
    services.run_stage(project, "screen")
    services.run_stage(
        project, "screen-override", candidate_id="a", disposition="eligible", reviewer="A"
    )
    conn = init_db(project.paths.db_path)
    conn.execute("UPDATE candidates SET asset_id=NULL")
    conn.commit()
    conn.close()
    report = services.screening_report(project)
    assert report["counts"]["review_required"] == 1
    assert report["candidates"][0]["override"] is None and len(report["overrides"]) == 1


def test_override_does_not_merge_same_asset_candidate_decisions(project, fake):
    seed(project, "a")
    seed(project, "b")
    services.run_stage(project, "screen")
    result = services.run_stage(
        project, "screen-override", candidate_id="a", disposition="eligible", reviewer=" "
    )
    assert result.status == "failed" and "reviewer" in result.error["message"]
    services.run_stage(
        project, "screen-override", candidate_id="a", disposition="eligible", reviewer="A"
    )
    report = services.screening_report(project)
    assert report["counts"]["eligible"] == report["counts"]["excluded"] == 1
    assert len({r["asset_id"] for r in report["candidates"]}) == 1


def test_detector_setup_failure_does_not_prevent_another_source(project, fake, monkeypatch):
    from stimulus_harvester.screening import engine

    seed(project)
    config = policy()
    config["detectors"].append(dict(config["detectors"][0], id="working"))
    config["rules"].append(dict(config["rules"][0], id="working", source="working"))

    def build(settings):
        if settings.id == "people":
            raise ImportError("first source fails")
        return fake

    monkeypatch.setattr(engine, "build_detector", build)
    result = services.run_stage(project, "screen", screening_config=config)
    assert result.status == "partial"
    report = services.screening_report(project)
    assert {o["status"] for o in report["observations"]} == {"error", "detected"}
    assert report["counts"]["excluded"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows transient folder locking")
def test_project_creation_retries_transient_windows_lock(tmp_path, monkeypatch):
    import stimulus_harvester.project as module
    from stimulus_harvester.config import HarvesterConfig

    original = module.os.rename
    calls = []

    def rename(a, b):
        calls.append((a, b))
        if len(calls) < 3:
            raise PermissionError("temporary scanner lock")
        return original(a, b)

    monkeypatch.setattr(module.os, "rename", rename)
    config = HarvesterConfig.model_validate(
        {
            "run_name": "retry",
            "search": {"seed_queries": []},
            "clip": {"positive_prompts": ["p"], "negative_prompts": ["n"]},
        }
    )
    result = services.create_project(tmp_path / "retry 研究", config)
    assert result.root.exists() and len(calls) == 3

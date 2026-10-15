"""Explicit synthetic evidence; no accuracy, absence or threshold validation claim."""

import copy
from pathlib import Path

import pytest
from test_collections import assign, freeze_request, label, make

from stimulus_harvester import collections as col
from stimulus_harvester import services
from stimulus_harvester.frozen.demo import config, request, seed
from stimulus_harvester.frozen.store import inventory
from stimulus_harvester.review.db import init_db
from stimulus_harvester.screening import capabilities as cap
from stimulus_harvester.screening import engine
from stimulus_harvester.screening.config import Criterion, ScreeningConfig


def criterion(text="people", manual=False):
    p = cap.propose(text)
    return dict(
        id="criterion-" + text.replace(" ", "-"),
        text=text,
        mode="manual" if manual else "automatic",
        coverage="manual" if manual else "partial" if p["support"] == "partial" else "exact",
        targets=[] if manual else p["targets"],
        mapping_id=p["mapping_id"],
        registry_version=p["registry_version"],
        limitations=p["explanation"],
        confirmed=True,
    )


def policy(*criteria, configured=True):
    return dict(
        criteria_version=1,
        criteria=list(criteria or [criterion()]),
        detectors=[
            dict(
                id="objects",
                property="coco_objects",
                model="fasterrcnn_mobilenet_v3_large_320_fpn",
                low_threshold=0.1,
                high_threshold=0.5,
            )
        ]
        if configured
        else [],
    )


def activate(project, configuration, cid=None):
    result = services.run_stage(
        project,
        "criteria-policy",
        configuration=configuration,
        collection_id=cid,
        actor="synthetic-investigator",
    )
    assert result.status == "succeeded", result.error
    return result.result


def report(project, cid=None):
    if cid:
        conn = init_db(project.paths.db_path)
        try:
            return col.screening_report(conn, project, cid)
        finally:
            conn.close()
    return services.screening_report(project)


class Detector:
    calls = 0
    detections = [("person", 0.9), ("dog", 0.3), ("car", 0.8), ("tv", 0.01)]

    def __init__(self, settings):
        self.settings = settings
        url = cap.registry()["models"][settings.model]["url"]
        self.identity = dict(
            model=settings.model,
            weights_url=url,
            weights="COCO_V1",
            artifact_sha256=cap.registry()['models'][settings.model]['artifact_sha256'],
            adapter_version="2",
            torchvision="0.23.0",
            categories=cap.registry()["categories"],
        )
        self.runtime = {"fixture": "synthetic, no inference"}

    def predict(self, path):
        type(self).calls += 1
        return dict(
            width=40,
            height=30,
            detections=[
                dict(
                    class_id=cap.labels()[name],
                    **{"class": name},
                    score=score,
                    box_xyxy=[0, 0, 20, 25],
                )
                for name, score in self.detections
            ],
        )

    def close(self):
        pass


@pytest.fixture
def project(tmp_path, monkeypatch):
    Detector.calls = 0
    monkeypatch.setattr(engine, "build_detector", Detector)
    p = services.create_project(tmp_path / "Criteria 研究 spaces", config())
    seed(p, "a", label="accepted")
    return p


@pytest.mark.parametrize(
    "text,targets,support",
    [
        ("people", ["person"], "automatic"),
        ("cars", ["car"], "automatic"),
        ("televisions", ["tv"], "automatic"),
        ("non-human animals", list(cap.ANIMALS), "partial"),
        ("fire exits", [], "unsupported"),
        ("exclude images containing dog", ["dog"], "automatic"),
    ],
)
def test_registry_proposals(text, targets, support):
    result = cap.propose(text)
    assert result["targets"] == targets and result["support"] == support
    assert len(cap.labels()) == 80 and cap.labels()["tv"] == 72


@pytest.mark.parametrize(
    "installed,configured,manual,expected",
    [
        (False, False, False, "unavailable"),
        (True, False, False, "not configured"),
        (True, True, False, "available"),
        (True, False, True, "Manual"),
    ],
)
def test_capability_states(installed, configured, manual, expected):
    c = Criterion.model_validate(criterion("fire exits" if manual else "people", manual))
    cfg = ScreeningConfig.model_validate(policy(c.model_dump(), configured=configured))
    assert expected in cap.capability(c, cfg.detectors, installed=installed)


def test_run_once_evaluate_many_and_reason_lineage(project):
    cfg = policy()
    activate(project, cfg)
    result = services.run_stage(project.root, "screen")
    assert result.status == "succeeded", result.error
    first = report(project)
    obs = first["observations"][0]
    assert (
        obs["status"] == "recorded"
        and len(obs["raw"]["detections"]) == 4
        and obs["format_version"] == 2
    )
    activate(
        project, policy(criterion(), criterion("dog"), criterion("cars"), criterion("televisions"))
    )
    after = report(project)
    events = after["candidates"][0]["evaluations"]
    assert [e["observation_status"] for e in events] == [
        "detected",
        "uncertain",
        "detected",
        "not_detected",
    ]
    assert {e["observation_id"] for e in events} == {obs["observation_id"]}
    assert Detector.calls == 1 and len(after["observations"]) == 1
    assert sum(e["disposition"] == "excluded" for e in events) == 2
    assert (
        "people" in after["candidates"][0]["reason"] and "cars" in after["candidates"][0]["reason"]
    )
    assert after["candidates"][0]["effective_disposition"] == "excluded"


@pytest.mark.parametrize(
    "change",
    [
        {"model": "fasterrcnn_resnet50_fpn_v2"},
        {"output_floor": 0.02},
        {"nms_threshold": 0.4},
        {"device": "cuda"},
    ],
)
def test_incompatible_inference_not_reused(project, change):
    activate(project, policy())
    assert services.run_stage(project.root, "screen").status == "succeeded"
    cfg = policy()
    cfg["detectors"][0].update(change)
    activate(project, cfg)
    e = report(project)["candidates"][0]["evaluations"][0]
    assert (
        e["observation_id"] is None and e["observation_status"] == "not_run" and Detector.calls == 1
    )


def test_threshold_re_evaluation_and_partial_coverage(project):
    activate(project, policy())
    services.run_stage(project.root, "screen")
    cfg = policy(criterion("non-human animals"))
    cfg["detectors"][0]["high_threshold"] = 0.2
    activate(project, cfg)
    row = report(project)["candidates"][0]
    assert row["evaluations"][0]["observation_status"] == "detected"
    assert row["manual_completion_required"] and Detector.calls == 1


@pytest.mark.parametrize("manual", [False, True])
def test_intent_without_detector_no_defaults_and_freeze_completion(project, manual):
    cfg = policy(criterion("fire exits", True) if manual else criterion(), configured=False)
    activate(project, cfg)
    saved = services.open_project(project.root).config.screening
    assert not saved.detectors
    row = report(project)["candidates"][0]
    assert row["manual_completion_required"] and not report(project)["observations"]
    blocked = services.freeze_validate(project, request=request())
    assert any(b["code"] == "manual_criterion_unresolved" for b in blocked["blockers"])
    result = services.run_stage(
        project,
        "screen-override",
        candidate_id=row["candidate_id"],
        disposition="eligible",
        reviewer="fixture",
        note="Inspected criterion manually",
        expected_scope=row["scope"],
    )
    assert result.status == "succeeded", result.error
    freeze = services.run_stage(project, "freeze-create", request=request())
    assert freeze.status == "succeeded", freeze.error
    assert (
        services.frozen_artifact(project, freeze.result["artifact_id"])["manifest"][
            "format_version"
        ]
        == 3
    )
    before = inventory(Path(freeze.result["path"]))
    activate(project, policy(criterion("new intent", True), configured=False))
    assert report(project)["candidates"][0]["override"] is None
    assembly = services.run_stage(project, "assemble", freeze_id=freeze.result["artifact_id"])
    assert assembly.status == "succeeded", assembly.error
    export = services.run_stage(
        project, "export-frozen", assembly_id=assembly.result["artifact_id"]
    )
    assert export.status == "succeeded", export.error
    assert services.verify_frozen_package(export.result["path"])["valid"]
    assert inventory(Path(freeze.result["path"])) == before


def test_collection_scope_reuses_observation_not_review(project):
    activate(project, policy())
    services.run_stage(project.root, "screen")
    row = report(project)["candidates"][0]
    a, b = make(project), make(project, "Other")
    assign(project, a, row)
    assign(project, b, row)
    activate(project, policy(criterion("cars")), a)
    activate(project, policy(criterion("televisions")), b)
    ra, rb = report(project, a)["candidates"][0], report(project, b)["candidates"][0]
    assert (
        ra["effective_disposition"] == "excluded"
        and rb["effective_disposition"] == "review_required"
    )
    assert ra["evaluations"][0]["observation_id"] == rb["evaluations"][0]["observation_id"]
    assert Detector.calls == 1
    label(project, a, row, "rejected")
    label(project, b, row, "accepted")
    f = services.run_stage(project, "freeze-create", request=freeze_request(b))
    assert f.status == "succeeded", f.error
    m = services.frozen_artifact(project, f.result["artifact_id"])["manifest"]
    assert m["format_version"] == 3 and m["review_scope"] == "collection"
    assert m["screening"]["active_policy"]["capability_registry"]["categories"][72] == "tv"
    assembly = services.run_stage(project, "assemble", freeze_id=f.result["artifact_id"])
    assert assembly.status == "succeeded", assembly.error
    export = services.run_stage(
        project, "export-frozen", assembly_id=assembly.result["artifact_id"]
    )
    assert export.status == "succeeded", export.error


def test_error_is_not_absence(project, monkeypatch):
    monkeypatch.setattr(
        engine,
        "build_detector",
        lambda settings: (_ for _ in ()).throw(RuntimeError("synthetic unavailable")),
    )
    activate(project, policy())
    result = services.run_stage(project.root, "screen")
    assert result.status == "partial", result.error
    row = report(project)["candidates"][0]
    assert (
        row["evaluations"][0]["observation_status"] == "error" and row["manual_completion_required"]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        {"targets": ["dragon"]},
        {"confirmed": False},
        {"mode": "manual"},
        {"targets": []},
        {"registry_version": "future"},
    ],
)
def test_invalid_interpretation_rejected(mutation):
    data = criterion()
    data.update(mutation)
    with pytest.raises(ValueError):
        Criterion.model_validate(data)


def test_legacy_observation_not_reinterpreted(project):
    from stimulus_harvester.frozen.demo import screen

    screen(project, {"a": 0.9})
    old = copy.deepcopy(report(project)["observations"])
    activate(project, policy())
    row = report(project)["candidates"][0]
    assert row["evaluations"][0]["observation_id"] is None
    assert report(project)["observations"] == old

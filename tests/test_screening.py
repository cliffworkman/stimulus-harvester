"""Mocked plumbing tests; these do not measure detector performance."""

import copy
import json
import sqlite3

import pytest
import yaml
from click.testing import CliRunner
from fastapi.testclient import TestClient
from PIL import Image

from stimulus_harvester import services
from stimulus_harvester.cli import cli
from stimulus_harvester.config import HarvesterConfig
from stimulus_harvester.models import SearchResult, DownloadRecord, DownloadStatus
from stimulus_harvester.review.db import init_db, upsert_image_from_search, update_download
from stimulus_harvester.screening import engine
from stimulus_harvester.screening.config import ScreeningConfig, DetectorConfig
from stimulus_harvester.screening.detectors import file_hash, normalize
from stimulus_harvester.screening.store import ensure_schema


def policy(**detector):
    return {
        "detectors": [
            {
                "id": "people",
                "property": "human_presence",
                "model": "fasterrcnn_resnet50_fpn_v2",
                "low_threshold": 0.1,
                "high_threshold": 0.5,
                **detector,
            }
        ],
        "rules": [
            {
                "id": "no-people",
                "version": "1",
                "property": "human_presence",
                "source": "people",
                "outcomes": {
                    "detected": "excluded",
                    "not_detected": "eligible",
                    "uncertain": "review_required",
                    "error": "review_required",
                    "not_run": "review_required",
                },
            }
        ],
    }


def seed(project, local="a", acquire=True):
    conn = init_db(project.paths.db_path)
    url = f"https://example.org/{local}.png"
    upsert_image_from_search(
        conn,
        SearchResult(
            local_id=local,
            source_backend="test",
            image_url=url,
            landing_page_url=url,
            search_query="q",
            seed_query="q",
        ),
        "run",
    )
    path = project.paths.images_original / f"{local}.png"
    if acquire:
        Image.new("RGB", (500, 400), "navy").save(path)
        update_download(
            conn,
            DownloadRecord(
                local_id=local,
                image_url=url,
                status=DownloadStatus.SUCCESS,
                file_path=str(path),
                sha256=file_hash(path),
                width=500,
                height=400,
                file_size_bytes=path.stat().st_size,
            ),
        )
    conn.close()
    return path


def raw(score=0.9):
    return {
        "width": 500,
        "height": 400,
        "detections": [
            {"class": "person", "class_id": 1, "score": score, "box_xyxy": [10, 15, 30, 100]}
        ],
        "count": 1,
    }


@pytest.fixture
def project(tmp_path):
    config = HarvesterConfig.model_validate(
        {
            "run_name": "screen",
            "search": {"seed_queries": ["q"]},
            "clip": {"positive_prompts": ["p"], "negative_prompts": ["n"]},
            "screening": policy(),
        }
    )
    return services.create_project(tmp_path / "screening 研究 with spaces", config)


@pytest.fixture
def fake(monkeypatch):
    class Detector:
        identity = {
            "model": "mock",
            "version": "1",
            "weights": "fixture",
            "artifact_sha256": "a" * 64,
        }
        runtime = {"device": "cpu", "test_fixture": True}
        output = raw()
        calls = 0

        def predict(self, path):
            self.calls += 1
            if isinstance(self.output, Exception):
                raise self.output
            return copy.deepcopy(self.output)

        def close(self):
            pass

    detector = Detector()
    monkeypatch.setattr(engine, "build_detector", lambda config: detector)
    return detector


@pytest.mark.parametrize(
    "score,status,disposition",
    [
        (0.9, "detected", "excluded"),
        (0.5, "detected", "excluded"),
        (0.2, "uncertain", "review_required"),
        (0.1, "uncertain", "review_required"),
        (0.02, "not_detected", "eligible"),
    ],
)
def test_states_rules_and_full_evidence(project, fake, score, status, disposition):
    path = seed(project)
    fake.output = raw(score)
    before = path.read_bytes()
    result = services.run_stage(project, "screen")
    assert result.status == "succeeded", result.error
    report = services.screening_report(project)
    obs, evaluation = report["observations"][0], report["evaluations"][0]
    assert obs["status"] == status and evaluation["disposition"] == disposition
    assert evaluation["observation_id"] == obs["observation_id"]
    assert obs["project_id"] == project.project_id and obs["operation_id"] == result.operation_id
    assert obs["model_identity"] == fake.identity and obs["runtime"] == fake.runtime
    assert obs["parameters"]["model"] == "fasterrcnn_resnet50_fpn_v2"
    assert obs["thresholds"] == {"low": 0.1, "high": 0.5}
    assert obs["raw"] == raw(score) and obs["score_semantics"]
    assert report["counts"][disposition] == 1 and path.read_bytes() == before
    assert services.candidate_evidence(project, "a")["screening"]["observations"] == [obs]
    assert services.project_status(project)["counts"]["discoveries"] == 1


@pytest.mark.parametrize(
    "mode,status",
    [
        ("disabled", "not_run"),
        ("unacquired", "not_run"),
        ("failure", "error"),
        ("missing", "error"),
        ("changed", "error"),
        ("setup", "error"),
    ],
)
def test_failure_not_run_and_missing_bytes_are_distinct(project, fake, monkeypatch, mode, status):
    path = seed(project, acquire=mode != "unacquired")
    config = policy(enabled=mode != "disabled")
    if mode == "missing":
        path.unlink()
    if mode == "changed":
        path.write_bytes(b"different bytes")
    if mode == "failure":
        fake.output = RuntimeError("fixture failure")
    if mode == "setup":

        def fail(config):
            raise ImportError("torch unavailable")

        monkeypatch.setattr(engine, "build_detector", fail)
    outcome = services.run_stage(project, "screen", screening_config=config)
    assert outcome.status == "partial"
    report = services.screening_report(project)
    obs = report["observations"][0]
    assert obs["status"] == status and bool(obs["error"]) == (status == "error")
    assert report["counts"]["review_required"] == report["counts"]["error_or_not_run"] == 1


def test_override_is_append_only_and_expires_on_new_evaluation(project, fake):
    seed(project)
    services.run_stage(project, "screen")
    before = services.screening_report(project)
    first = services.run_stage(
        project,
        "screen-override",
        candidate_id="a",
        disposition="review_required",
        reviewer="Investigator A",
        note="reflection needs review",
    )
    assert first.status == "succeeded", first.error
    second = services.run_stage(
        project,
        "screen-override",
        candidate_id="a",
        disposition="eligible",
        reviewer="Investigator B",
    )
    assert second.result["prior_disposition"] == "review_required"
    report = services.screening_report(project)
    assert (
        report["observations"] == before["observations"]
        and report["evaluations"] == before["evaluations"]
    )
    assert report["counts"]["eligible"] == 1 and len(report["overrides"]) == 2
    services.run_stage(project, "screen")
    after = services.screening_report(project)
    assert after["counts"]["excluded"] == 1 and len(after["overrides"]) == 2
    assert after["candidates"][0]["override"] is None
    stale = services.run_stage(
        project,
        "screen-override",
        candidate_id="a",
        disposition="eligible",
        reviewer="A",
        expected_scope=before["candidates"][0]["scope"],
    )
    assert stale.status == "failed" and "changed" in stale.error["message"]


@pytest.mark.parametrize(
    "table", ["observations", "screening_evaluations", "screening_policies", "screening_overrides"]
)
@pytest.mark.parametrize("action", ["UPDATE", "DELETE"])
def test_database_blocks_evidence_rewriting(project, fake, table, action):
    seed(project)
    services.run_stage(project, "screen")
    services.run_stage(
        project, "screen-override", candidate_id="a", disposition="review_required", reviewer="A"
    )
    conn = init_db(project.paths.db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(
                f"UPDATE {table} SET record_json='{{}}'"
                if action == "UPDATE"
                else f"DELETE FROM {table}"
            )
    finally:
        conn.close()


def test_rethreshold_reuses_raw_and_rule_change_is_separate(project, fake):
    seed(project)
    fake.output = raw(0.4)
    services.run_stage(project, "screen")
    config = policy(high_threshold=0.3)
    config["rules"][0]["version"] = "2"
    config["rules"][0]["outcomes"]["detected"] = "review_required"
    result = services.run_stage(project, "screen", screening_config=config, reuse_raw=True)
    assert result.status == "succeeded" and fake.calls == 1
    report = services.screening_report(project)
    a, b = report["observations"]
    assert a["status"] == "uncertain" and b["status"] == "detected"
    assert b["derived_from"] == a["observation_id"] and b["raw"] == a["raw"]
    assert report["evaluations"][-1]["rule_version"] == "2"
    assert report["evaluations"][-1]["disposition"] == "review_required"


def test_changed_model_cannot_reuse_another_model_output(project, fake):
    seed(project)
    services.run_stage(project, "screen")
    result = services.run_stage(
        project,
        "screen",
        screening_config=policy(model="fasterrcnn_mobilenet_v3_large_320_fpn"),
        reuse_raw=True,
    )
    assert result.status == "partial"
    assert services.screening_report(project)["observations"][-1]["status"] == "not_run"
    fake.identity = dict(fake.identity, version="2")
    services.run_stage(
        project, "screen", screening_config=policy(model="fasterrcnn_mobilenet_v3_large_320_fpn")
    )
    report = services.screening_report(project)
    assert report["observations"][0]["model_identity"]["version"] == "1"
    assert report["observations"][-1]["model_identity"]["version"] == "2"


def test_failure_isolated_candidates_and_review_inspectability(project, fake):
    a = seed(project, "a")
    b = seed(project, "b")

    def predict(path):
        if path.name == "a.png":
            raise RuntimeError("one file failure")
        return raw()

    fake.predict = predict
    result = services.run_stage(project, "screen")
    assert result.status == "partial"
    report = services.screening_report(project)
    assert report["counts"] == {
        "eligible": 0,
        "excluded": 1,
        "review_required": 1,
        "error_or_not_run": 1,
    }
    with TestClient(services.review_application(project)) as client:
        assert client.get("/images/b").status_code == 200
        assert client.get("/images/b/file").content == b.read_bytes()
    assert a.exists() and services.project_status(project)["counts"]["discoveries"] == 2


def test_cli_shared_service_agreement_and_report_filter(project, fake, tmp_path):
    seed(project)
    policy_file = tmp_path / "screen config.yaml"
    policy_file.write_text(yaml.safe_dump(policy()), encoding="utf-8")
    runner = CliRunner()
    result = runner.invoke(
        cli, ["screen", "--project", str(project.root), "--screening-config", str(policy_file)]
    )
    assert result.exit_code == 0, result.output
    report = runner.invoke(cli, ["screening-report", "--project", str(project.root)])
    assert report.exit_code == 0 and json.loads(report.output) == services.screening_report(project)
    changed = runner.invoke(
        cli,
        [
            "screening-override",
            "--project",
            str(project.root),
            "--candidate",
            "a",
            "--disposition",
            "eligible",
            "--reviewer",
            "Cliff",
        ],
    )
    assert changed.exit_code == 0, changed.output
    assert len(services.screening_report(project, disposition="eligible")["candidates"]) == 1
    assert services.screening_report(project, disposition="excluded")["candidates"] == []


@pytest.mark.parametrize(
    "change",
    [
        "missing_state",
        "bad_outcome",
        "unknown_source",
        "wrong_property",
        "duplicate_rule",
        "reversed_threshold",
        "floor",
        "nan",
        "typo",
    ],
)
def test_malformed_policies_fail_loudly(change):
    value = policy()
    if change == "missing_state":
        del value["rules"][0]["outcomes"]["error"]
    elif change == "bad_outcome":
        value["rules"][0]["outcomes"]["error"] = "accept"
    elif change == "unknown_source":
        value["rules"][0]["source"] = "other"
    elif change == "wrong_property":
        value["rules"][0]["property"] = "text"
    elif change == "duplicate_rule":
        value["rules"].append(copy.deepcopy(value["rules"][0]))
    elif change == "reversed_threshold":
        value["detectors"][0]["low_threshold"] = 0.6
    elif change == "floor":
        value["detectors"][0]["output_floor"] = 0.2
    elif change == "nan":
        value["detectors"][0]["low_threshold"] = float("nan")
    else:
        value["rulse"] = []
    with pytest.raises(ValueError):
        ScreeningConfig.model_validate(value)


def test_unconfigured_screening_does_not_imply_eligible(project):
    seed(project)
    assert services.screening_report(project)["counts"]["review_required"] == 1
    result = services.run_stage(project, "screen", screening_config={})
    assert result.status == "partial"
    assert services.screening_report(project)["counts"]["error_or_not_run"] == 1


def test_no_implicit_ensemble_and_rule_priority(project, fake):
    seed(project)
    config = policy()
    config["detectors"].append(dict(config["detectors"][0], id="second", enabled=False))
    config["rules"].append(dict(config["rules"][0], id="second-rule", source="second"))
    services.run_stage(project, "screen", screening_config=config)
    report = services.screening_report(project)
    assert len(report["observations"]) == len(report["evaluations"]) == 2
    assert report["counts"]["excluded"] == 1 and report["counts"]["error_or_not_run"] == 1


def test_empty_output_and_saturation_are_not_equivalent():
    settings = DetectorConfig.model_validate(policy(max_detections=1)["detectors"][0])
    assert normalize({"width": 500, "height": 400, "detections": []}, settings)[0] == "not_detected"
    assert normalize(raw(0.02), settings)[0] == "uncertain"


def test_invalid_raw_is_error_not_uncertainty(project, fake):
    seed(project)
    fake.output = raw(float("nan"))
    result = services.run_stage(project, "screen")
    assert result.status == "partial", result.error
    obs = services.screening_report(project)["observations"][0]
    assert obs["status"] == "error" and obs["raw"]["unserializable_repr"]


def test_screening_schema_failure_rolls_back_and_unknown_version_refused(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute("CREATE TABLE observations(existing TEXT)")
    conn.commit()
    with pytest.raises(sqlite3.OperationalError):
        ensure_schema(conn)
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name='screening_schema'").fetchone()
        is None
    )
    conn.execute("DROP TABLE observations")
    conn.commit()
    ensure_schema(conn)
    conn.execute("UPDATE screening_schema SET version=99")
    conn.commit()
    with pytest.raises(ValueError, match="Unsupported"):
        ensure_schema(conn)
    conn.close()

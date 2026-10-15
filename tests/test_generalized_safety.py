"""Compatibility boundaries and no cross-refresh file metadata reuse."""

import copy

import pytest
from test_generalized_screening import activate, criterion, policy, report
from test_generalized_screening import project as project_fixture

from stimulus_harvester import services, workspace
from stimulus_harvester.projection import file_projection, file_view
from stimulus_harvester.screening.config import Criterion, ScreeningConfig
from stimulus_harvester.screening.generalized import compatible, evaluate_criterion

project = project_fixture


@pytest.mark.parametrize(
    "field,value",
    [
        ("artifact_sha256", "0" * 64),
        ("adapter_version", "unknown"),
        ("torchvision", "0.99.0"),
        ("categories", ["person"]),
    ],
)
def test_incompatible_model_identity_not_reused(project, field, value):
    activate(project, policy())
    services.run_stage(project.root, "screen")
    original = report(project)["observations"][0]
    altered = copy.deepcopy(original)
    altered["model_identity"][field] = value
    detector = ScreeningConfig.model_validate(policy()).detectors[0]
    assert compatible(original, detector, original)
    assert not compatible(altered, detector, altered)


def test_output_cap_cannot_be_absence(project):
    activate(project, policy())
    services.run_stage(project.root, "screen")
    obs = report(project)["observations"][0]
    detector = (
        ScreeningConfig.model_validate(policy())
        .detectors[0]
        .model_copy(update={"max_detections": 4})
    )
    result = evaluate_criterion(Criterion.model_validate(criterion("televisions")), detector, obs)
    assert result["observation_status"] == "uncertain"


def test_override_requires_attributed_scope_and_does_not_review(project):
    activate(project, policy(configured=False))
    row = report(project)["candidates"][0]
    before = workspace.inspect_candidate(project.root, row["candidate_id"])["evidence"][
        "review_events"
    ]
    rejected = services.run_stage(
        project,
        "screen-override",
        candidate_id=row["candidate_id"],
        disposition="eligible",
        reviewer="fixture",
        expected_scope=row["scope"],
        note="",
    )
    assert rejected.status == "failed"
    accepted = services.run_stage(
        project,
        "screen-override",
        candidate_id=row["candidate_id"],
        disposition="eligible",
        reviewer="fixture",
        expected_scope=row["scope"],
        note="Inspected the recorded intent manually",
    )
    assert accepted.status == "succeeded", accepted.error
    assert (
        workspace.inspect_candidate(project.root, row["candidate_id"])["evidence"]["review_events"]
        == before
    )


def test_projection_cache_is_operation_local(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"first")
    with file_projection():
        assert file_view(tmp_path, "image.png")[1].st_size == 5
    path.write_bytes(b"new contents")
    with file_projection():
        assert file_view(tmp_path, "image.png")[1].st_size == 12
    path.unlink()
    with file_projection(), pytest.raises(OSError):
        file_view(tmp_path, "image.png")


def test_legacy_detector_cannot_select_generic_property_without_contract():
    config = policy()
    config.pop("criteria_version")
    config.pop("criteria")
    with pytest.raises(ValueError):
        ScreeningConfig.model_validate(config)

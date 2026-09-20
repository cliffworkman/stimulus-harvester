"""Scientific snapshot invariants with synthetic assets; no network or real detector."""

import csv
import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner

from stimulus_harvester import services
from stimulus_harvester.cli import cli
from stimulus_harvester.frozen import store
from stimulus_harvester.frozen.demo import (
    config,
    demonstration,
    request,
    review,
    run,
    screen,
    seed,
    strict_policy,
)
from stimulus_harvester.models import SearchResult
from stimulus_harvester.review.db import init_db, upsert_image_from_search
from stimulus_harvester.screening.detectors import file_hash


@pytest.fixture
def project(tmp_path):
    return services.create_project(tmp_path / "研究 project with spaces", config())


def freeze(project, **changes):
    return run(project, "freeze-create", request=request(**changes))


def manifest(project, result):
    return services.frozen_artifact(project, result["artifact_id"])["manifest"]


def blockers(project, **changes):
    return services.freeze_validate(project, request=request(**changes))


def test_complete_freeze_disabled_screening_keeps_explicit_human_decisions(project):
    a = seed(project, "a", label="accepted")
    r = seed(project, "r", label="rejected")
    f = freeze(project)
    m = manifest(project, f)
    assert m["project_id"] == project.project_id and m["operation_id"]
    assert m["request"]["reviewer"] == "fixture-investigator"
    assert m["screening"]["observations"] == [] and m["screening"]["active_policy"] is None
    assert m["configuration"]["parsed"]["screening"]["detectors"] == []
    assert m["scope"]["resolved_candidate_ids"] == sorted([a["candidate_id"], r["candidate_id"]])
    assert len(m["membership"]) == 1 and len(m["candidates"]) == 2
    assert m["candidates"][0]["review_events"]
    assert m["software"]["schema_versions"]["frozen"] == 1
    assert store.canonical(m) == (Path(f["path"]) / "manifest.json").read_bytes()


@pytest.mark.parametrize("label", [None, "maybe"])
def test_unresolved_reviews_block_without_publishing(project, label):
    seed(project, "unresolved", label=label)
    v = blockers(project)
    assert not v["valid"] and "unresolved_review" in {b["code"] for b in v["blockers"]}
    result = services.run_stage(project, "freeze-create", request=request())
    assert result.status == "failed"
    assert services.list_frozen_artifacts(project) == []


def test_legacy_label_without_event_or_identity_is_not_explicit_acceptance(project):
    seed(project, "a")
    conn = init_db(project.paths.db_path)
    conn.execute("UPDATE reviews SET label='accepted'")
    conn.commit()
    conn.close()
    assert {"review_evidence_missing", "reviewer_missing"} <= {
        b["code"] for b in blockers(project)["blockers"]
    }


def test_review_required_accept_satisfies_strict_screening_with_no_new_inference(
    project, monkeypatch
):
    seed(project, "a", label="accepted")
    screen(project, {"a": 0.25})
    monkeypatch.setattr(
        "stimulus_harvester.screening.engine.build_detector",
        lambda _: pytest.fail("Freeze ran inference"),
    )
    f = freeze(project)
    m = manifest(project, f)
    c = m["candidates"][0]
    assert c["screening"]["effective_disposition"] == "review_required"
    assert c["review"]["label"] == "accepted" and m["membership"]
    obs = m["screening"]["observations"][0]
    assert obs["thresholds"] == {"low": 0.15, "high": 0.6}
    assert obs["model_identity"]["fixture"] is True
    assert obs["parameters"]["model"] == "fasterrcnn_resnet50_fpn_v2"
    assert c["screening"]["scope"]["evaluation_ids"]


def test_excluded_accept_needs_current_explicit_override(project):
    a = seed(project, "a", label="accepted")
    screen(project, {"a": 0.9})
    assert "screening_excluded" in {b["code"] for b in blockers(project)["blockers"]}
    o = run(
        project,
        "screen-override",
        candidate_id=a["candidate_id"],
        disposition="review_required",
        reviewer="named reviewer",
        note="Retain after explicit adjudication",
    )
    f = freeze(project)
    m = manifest(project, f)
    assert m["candidates"][0]["screening"]["override"]["override_id"] == o["override_id"]
    assert m["candidates"][0]["screening"]["rule_disposition"] == "excluded"
    assert len(m["membership"]) == 1
    screen(project, {"a": 0.9})
    assert "screening_excluded" in {b["code"] for b in blockers(project)["blockers"]}
    assert manifest(project, f) == m


def test_eligible_is_not_investigator_acceptance(project):
    seed(project, "a")
    p = strict_policy()
    p["rules"][0]["outcomes"]["not_detected"] = "eligible"
    screen(project, {}, p)
    assert "unresolved_review" in {b["code"] for b in blockers(project)["blockers"]}


def test_subset_scope_preserves_outside_inventory_and_related_source_history(project):
    a = seed(project, "a", label="accepted")
    b = seed(project, "b", duplicate=a["path"], label="rejected", license=None)
    seed(project, "unrated")
    scope = {
        "mode": "candidates",
        "candidate_ids": [a["candidate_id"]],
        "selection_rule": "Explicit study subset",
    }
    v = blockers(project, scope=scope)
    assert v["valid"] and v["duplicates"][0]["outside_scope_decisions_preserved"]
    f = freeze(project, scope=scope)
    m = manifest(project, f)
    assert len(m["scope"]["outside_scope_candidate_ids"]) == 2
    assert m["related_candidates"][0]["candidate_id"] == b["candidate_id"]
    assert len(m["membership"][0]["source_ids"]) == 2


@pytest.mark.parametrize(
    "scope",
    [
        {"mode": "all_current", "candidate_ids": ["a"], "selection_rule": "bad"},
        {"mode": "candidates", "candidate_ids": [], "selection_rule": "bad"},
        {"mode": "all_current", "selection_rule": " "},
    ],
)
def test_scope_must_be_explicit_and_consistent(project, scope):
    with pytest.raises(ValueError):
        blockers(project, scope=scope)


def test_validation_reports_all_blockers_including_unknown_ids_and_missing_evidence(project):
    a = seed(project, "a", label="accepted")
    seed(project, "b", label="maybe")
    a["path"].write_bytes(b"changed")
    (project.root / "raw_api/a.json").unlink()
    v = blockers(project)
    assert {"asset_integrity", "unresolved_review", "evidence_missing"} <= {
        b["code"] for b in v["blockers"]
    }
    v = blockers(
        project,
        scope={
            "mode": "candidates",
            "candidate_ids": ["unknown"],
            "selection_rule": "Explicit unknown",
        },
    )
    assert "unknown_candidate" in {b["code"] for b in v["blockers"]}


def test_exact_accepted_duplicates_require_explicit_deduplication(project):
    a = seed(project, "a", label="accepted")
    b = seed(project, "b", label="accepted", duplicate=a["path"])
    assert "duplicate_accepts" in {b["code"] for b in blockers(project)["blockers"]}
    f = freeze(project, deduplicate_accepted=True)
    m = manifest(project, f)
    assert len(m["membership"]) == 1
    assert set(m["membership"][0]["candidate_ids"]) == {a["candidate_id"], b["candidate_id"]}
    assembly = run(project, "assemble", freeze_id=f["artifact_id"])
    assert len(list((Path(assembly["path"]) / "stimuli").iterdir())) == 1


@pytest.mark.parametrize("membership,count", [("include", 1), ("omit", 0)])
def test_conflicting_duplicate_decisions_need_auditable_resolution(project, membership, count):
    a = seed(project, "a", label="accepted")
    seed(project, "b", label="rejected", duplicate=a["path"])
    assert "duplicate_decision_conflict" in {b["code"] for b in blockers(project)["blockers"]}
    resolution = {
        "asset_id": a["asset_id"],
        "membership": membership,
        "reviewer": "investigator",
        "reason": "Explicit conflict decision",
    }
    f = freeze(project, duplicate_resolutions=[resolution])
    m = manifest(project, f)
    assert len(m["membership"]) == count
    assert {c["review"]["label"] for c in m["candidates"]} == {"accepted", "rejected"}
    assert m["duplicates"][0]["resolution"] == resolution


def test_near_duplicates_remain_distinct_assets(project):
    seed(project, "a", label="accepted")
    seed(project, "b", label="accepted")
    f = freeze(project)
    assert len(manifest(project, f)["membership"]) == 2


def test_frozen_registry_forbids_update_and_delete(project):
    seed(project, "a", label="accepted")
    freeze(project)
    conn = init_db(project.paths.db_path)
    for sql in [
        "DELETE FROM frozen_artifacts",
        "UPDATE frozen_artifacts SET manifest_sha256='bad'",
    ]:
        with pytest.raises(sqlite3.IntegrityError, match="append-only"):
            conn.execute(sql)
        conn.rollback()
    conn.close()


@pytest.mark.parametrize("target", ["manifest", "config", "extra", "checksums"])
def test_manifest_and_frozen_file_tampering_is_detected(project, target):
    seed(project, "a", label="accepted")
    f = freeze(project)
    root = Path(f["path"])
    name = {
        "manifest": "manifest.json",
        "config": "config_snapshot.yaml",
        "extra": "unrecorded.txt",
        "checksums": "checksums.sha256",
    }[target]
    (root / name).write_text("tampered")
    with pytest.raises(ValueError, match="integrity"):
        services.frozen_artifact(project, f["artifact_id"])
    assert services.run_stage(project, "assemble", freeze_id=f["artifact_id"]).status == "failed"


def test_old_freeze_survives_new_reviews_rules_observations_sources_and_config(tmp_path):
    result = demonstration(tmp_path / "synthetic complete demo")
    assert result["old_freeze_and_assembly_unchanged"]
    assert (
        result["assembly1"]["stimulus_count"] == 2 and result["new_assembly"]["stimulus_count"] == 1
    )
    project = services.open_project(Path(result["project_path"]))
    old = manifest(project, result["freeze1"])
    new = manifest(project, result["freeze2"])
    assert new["parent_id"] == old["freeze_id"]
    assert old["screening"]["active_policy"]["config"]["rules"][0]["version"] == "1"
    assert new["screening"]["active_policy"]["config"]["rules"][0]["version"] == "2"
    assert old["configuration"]["sha256"] != new["configuration"]["sha256"]
    assert (
        sum(len(c["sources"]) for c in new["candidates"])
        == sum(len(c["sources"]) for c in old["candidates"]) + 1
    )


@pytest.mark.parametrize("change", ["missing", "modified"])
def test_missing_or_modified_asset_fails_without_partial_assembly(project, change):
    a = seed(project, "a", label="accepted")
    seed(project, "b", label="accepted")
    f = freeze(project)
    if change == "missing":
        a["path"].unlink()
    else:
        a["path"].write_bytes(b"changed")
    result = services.run_stage(project, "assemble", freeze_id=f["artifact_id"])
    assert result.status == "failed"
    assert not any(a["kind"] == "assembly" for a in services.list_frozen_artifacts(project))
    assert not (project.root / "assemblies").exists() or not list(
        (project.root / "assemblies").iterdir()
    )


def test_copy_failure_cleans_staging_and_publishes_nothing(project, monkeypatch):
    seed(project, "a", label="accepted")
    seed(project, "b", label="accepted")
    f = freeze(project)
    real = store.copy_verified
    calls = []

    def failing(source, dest, digest):
        calls.append(source)
        if len(calls) == 2:
            raise OSError("simulated disk full")
        return real(source, dest, digest)

    monkeypatch.setattr(store, "copy_verified", failing)
    result = services.run_stage(project, "assemble", freeze_id=f["artifact_id"])
    assert result.status == "failed" and len(calls) == 2
    assert list((project.root / "assemblies").iterdir()) == []
    assert not any(a["kind"] == "assembly" for a in services.list_frozen_artifacts(project))


def test_assembly_ignores_live_reviews_accepted_folder_and_current_config(project):
    a = seed(project, "a", label="accepted")
    seed(project, "r", label="rejected")
    f = freeze(project)
    review(project, "a", "rejected")
    review(project, "r", "accepted")
    accepted = project.root / "images/accepted"
    accepted.mkdir(exist_ok=True)
    (accepted / "unrelated.png").write_bytes(b"not an original")
    project.paths.config_snapshot_path.unlink()
    assembly = run(project.root, "assemble", freeze_id=f["artifact_id"])
    m = manifest(project, assembly)
    assert [i["asset_id"] for i in m["membership"]] == [a["asset_id"]]
    assert file_hash(Path(assembly["path"]) / m["membership"][0]["path"]) == file_hash(a["path"])


def test_accepted_folder_cannot_rescue_missing_original(project):
    a = seed(project, "a", label="accepted")
    f = freeze(project)
    accepted = project.root / "images/accepted"
    accepted.mkdir(exist_ok=True)
    shutil.copyfile(a["path"], accepted / "a.png")
    a["path"].unlink()
    assert services.run_stage(project, "assemble", freeze_id=f["artifact_id"]).status == "failed"


def test_repeated_assembly_has_stable_names_bytes_and_explicit_lineage(project):
    seed(project, "a", label="accepted")
    f = freeze(project)
    first = run(project, "assemble", freeze_id=f["artifact_id"])
    first_bytes = (Path(first["path"]) / "manifest.json").read_bytes()
    second = run(
        project, "assemble", freeze_id=f["artifact_id"], parent_assembly_id=first["artifact_id"]
    )
    assert first["artifact_id"] != second["artifact_id"]
    assert first["membership_sha256"] == second["membership_sha256"]
    assert manifest(project, first)["membership"] == manifest(project, second)["membership"]
    assert manifest(project, second)["parent_id"] == first["artifact_id"]
    assert second["reproduction"]["verified_parent_bytes_and_membership_match"]
    assert (Path(first["path"]) / "manifest.json").read_bytes() == first_bytes


def test_export_preserves_conflicting_source_claims_and_consistent_relative_membership(
    project, tmp_path
):
    a = seed(project, "a", label="accepted", license="cc-by")
    seed(project, "b", label="accepted", duplicate=a["path"], license=None)
    conn = init_db(project.paths.db_path)
    upsert_image_from_search(
        conn,
        SearchResult(
            local_id="alias",
            source_backend="other",
            search_query="q2",
            seed_query="q2",
            image_url="https://example.invalid/a.png",
            landing_page_url="https://example.invalid/other",
            license="all-rights-reserved",
            creator="</pre><script>alert(1)</script>",
        ),
        project.project_id,
    )
    conn.close()
    f = freeze(project, deduplicate_accepted=True)
    assembly = run(project, "assemble", freeze_id=f["artifact_id"])
    exported = run(project, "export-frozen", assembly_id=assembly["artifact_id"])
    root = Path(exported["path"])
    m = manifest(project, exported)
    assert len(m["membership"]) == 1 and {
        c["license"] for c in m["membership"][0]["source_claims"]
    } == {"cc-by", None, "all-rights-reserved"}
    csv_rows = list(csv.DictReader((root / "stimuli.csv").open(encoding="utf-8")))
    json_rows = [json.loads(line) for line in (root / "stimuli.jsonl").read_text().splitlines()]
    page = (root / "index.html").read_text()
    summary = json.loads((root / "summary.json").read_text())
    paths = {r["path"] for r in m["membership"]}
    assert (
        paths
        == {r["path"] for r in csv_rows}
        == {r["path"] for r in json_rows}
        == set(re.findall(r'<img src="([^"]+)"', page))
    )
    assert paths == {p.relative_to(root).as_posix() for p in (root / "stimuli").iterdir()}
    assert summary["asset_ids"] == [a["asset_id"]] and summary["stimulus_count"] == 1
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert all(
        not Path(p).is_absolute() and (root / p).is_file()
        for p in re.findall(r'(?:src|href)="([^"]+)"', page)
    )
    portable = tmp_path / "standalone package 研究"
    shutil.copytree(root, portable)
    assert services.verify_frozen_package(portable, expected_sha256=exported["manifest_sha256"])[
        "valid"
    ]
    assert not list(portable.rglob("*.db"))
    (portable / next(iter(paths))).write_bytes(b"changed")
    with pytest.raises(ValueError):
        services.verify_frozen_package(portable)


def test_export_uses_assembly_bytes_after_originals_and_config_are_gone(project):
    a = seed(project, "a", label="accepted")
    f = freeze(project)
    assembly = run(project, "assemble", freeze_id=f["artifact_id"])
    a["path"].unlink()
    project.paths.config_snapshot_path.write_text("invalid: [")
    exported = run(project.root, "export-frozen", assembly_id=assembly["artifact_id"])
    assert services.verify_frozen_package(exported["path"])["valid"]


def test_frozen_export_can_start_from_freeze_and_rejects_silent_license_filter(project):
    seed(project, "a", label="accepted")
    f = freeze(project)
    exported = run(project, "export-frozen", freeze_id=f["artifact_id"])
    assert exported["assembly_id"] and exported["stimulus_count"] == 1
    result = services.run_stage(
        project, "export-frozen", freeze_id=f["artifact_id"], license_policy="open_only"
    )
    assert result.status == "failed"


def test_relocation_keeps_freezes_and_frozen_locations_usable(project, tmp_path):
    seed(project, "a", label="accepted")
    f = freeze(project)
    moved = tmp_path / "copied project 研究"
    shutil.copytree(project.root, moved)
    assert run(moved, "assemble", freeze_id=f["artifact_id"])["stimulus_count"] == 1


def test_cli_shared_service_agreement_and_portable_verification(project, tmp_path):
    seed(project, "a", label="accepted")
    path = tmp_path / "scope request.json"
    path.write_text(json.dumps(request()))
    runner = CliRunner()
    validation = runner.invoke(
        cli, ["freeze", "validate", "--project", str(project.root), "--request", str(path)]
    )
    assert validation.exit_code == 0, validation.output
    assert json.loads(validation.output) == blockers(project)
    created = runner.invoke(
        cli, ["freeze", "create", "--project", str(project.root), "--request", str(path)]
    )
    assert created.exit_code == 0, created.output
    f = services.list_frozen_artifacts(project)[0]
    assembly = runner.invoke(
        cli, ["assemble", "--project", str(project.root), "--freeze", f["artifact_id"]]
    )
    assert assembly.exit_code == 0, assembly.output
    a = services.list_frozen_artifacts(project)[-1]
    exported = runner.invoke(
        cli, ["export", "--project", str(project.root), "--assembly", a["artifact_id"]]
    )
    assert exported.exit_code == 0, exported.output
    e = services.list_frozen_artifacts(project)[-1]
    verify = runner.invoke(
        cli,
        [
            "verify-frozen",
            "--package",
            str(project.root / e["relative_path"]),
            "--sha256",
            e["manifest_sha256"],
        ],
    )
    assert verify.exit_code == 0 and json.loads(verify.output)["valid"]


def test_wrong_kind_parent_and_future_schema_are_refused(project):
    seed(project, "a", label="accepted")
    f = freeze(project)
    assert (
        services.run_stage(
            project, "assemble", freeze_id=f["artifact_id"], parent_assembly_id=f["artifact_id"]
        ).status
        == "failed"
    )
    conn = init_db(project.paths.db_path)
    conn.execute("UPDATE frozen_schema SET version=999")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="Unsupported"):
        services.frozen_artifact(project, f["artifact_id"])


def test_invalid_parent_is_reported_alongside_candidate_blockers(project):
    seed(project, "unresolved", label="maybe")
    report = blockers(project, parent_freeze_id="freeze-missing")
    assert {b["code"] for b in report["blockers"]} == {"unresolved_review", "parent_freeze_invalid"}


def test_source_change_during_copy_prevents_publication(project, monkeypatch):
    a = seed(project, "a", label="accepted")
    f = freeze(project)
    original_copy = store.shutil.copyfile

    def changing_copy(source, destination, *args, **kwargs):
        result = original_copy(source, destination, *args, **kwargs)
        if Path(source) == a["path"]:
            Path(source).write_bytes(b"changed during materialization")
        return result

    monkeypatch.setattr(store.shutil, "copyfile", changing_copy)
    result = services.run_stage(project, "assemble", freeze_id=f["artifact_id"])
    assert result.status == "failed" and "changed while copying" in result.error["message"]
    assert not list((project.root / "assemblies").iterdir())
    assert [r["kind"] for r in services.list_frozen_artifacts(project)] == ["freeze"]


def test_freeze_evidence_change_during_export_prevents_publication(project, monkeypatch):
    seed(project, "a", label="accepted")
    f = freeze(project)
    assembly = run(project, "assemble", freeze_id=f["artifact_id"])
    original_copy = store.copy_verified

    def changing_copy(source, destination, digest):
        original_copy(source, destination, digest)
        if source.is_relative_to(Path(assembly["path"])):
            (Path(f["path"]) / "config_snapshot.yaml").write_bytes(b"changed after validation")

    monkeypatch.setattr(store, "copy_verified", changing_copy)
    result = services.run_stage(project, "export-frozen", assembly_id=assembly["artifact_id"])
    assert result.status == "failed" and "Missing/changed bytes" in result.error["message"]
    assert not list((project.root / "frozen_exports").iterdir())
    assert [r["kind"] for r in services.list_frozen_artifacts(project)] == ["freeze", "assembly"]

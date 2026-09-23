"""Collection scientific contracts; synthetic images and observations only."""

import json
import sqlite3
from pathlib import Path

import httpx
import pytest
import respx

from stimulus_harvester import collections as col
from stimulus_harvester import services
from stimulus_harvester.frozen.demo import config, request, screen, seed, strict_policy
from stimulus_harvester.frozen.store import inventory
from stimulus_harvester.review.db import init_db


@pytest.fixture
def project(tmp_path):
    return services.create_project(tmp_path / "Collections 研究 spaces", config())


def action(project, action, **options):
    result = services.run_stage(
        project, "collection", action=action, actor="fixture-investigator", **options
    )
    assert result.status == "succeeded", result.error
    return result.result


def make(project, name="Prison interiors", phrases=None):
    return action(
        project, "create", name=name, phrases=phrases or ["prison cell", "prison interior"]
    )["collection_id"]


def assign(project, cid, *items):
    action(project, "add", collection_id=cid, candidate_ids=[x["candidate_id"] for x in items])


def selection(project, cid, item):
    conn = init_db(project.paths.db_path)
    try:
        scope = col.identity(conn, cid, item["candidate_id"])
        return {
            "candidate_id": item["candidate_id"],
            "scope": scope,
            "event_id": col.current_review(conn, scope)["event_id"],
        }
    finally:
        conn.close()


def label(project, cid, item, value):
    return action(
        project, "review", collection_id=cid, selection=[selection(project, cid, item)], label=value
    )


def freeze_request(cid):
    return request(
        scope={
            "mode": "collection",
            "collection_id": cid,
            "selection_rule": "Exact active collection",
        }
    )


def freeze(project, cid):
    result = services.run_stage(project, "freeze-create", request=freeze_request(cid))
    assert result.status == "succeeded", result.error
    return result.result


def test_independent_reviews_and_frozen_decisions(project):
    item = seed(project, "shared", label="maybe")
    a, b = make(project), make(project, "Church interiors")
    assign(project, a, item)
    assign(project, b, item)
    label(project, a, item, "accepted")
    label(project, b, item, "rejected")
    fa, fb = freeze(project, a), freeze(project, b)
    ma = services.frozen_artifact(project, fa["artifact_id"])["manifest"]
    mb = services.frozen_artifact(project, fb["artifact_id"])["manifest"]
    assert ma["format_version"] == mb["format_version"] == 2
    assert ma["candidates"][0]["collection_review"]["label"] == "accepted"
    assert mb["candidates"][0]["collection_review"]["label"] == "rejected"
    assert ma["candidates"][0]["review"]["label"] == "maybe"
    assert len(ma["membership"]) == 1 and mb["membership"] == []
    before = [inventory(Path(f["path"])) for f in (fa, fb)]
    label(project, a, item, "rejected")
    label(project, b, item, "accepted")
    for index, f in enumerate((fa, fb)):
        assert services.verify_frozen_package(f["path"])["valid"]
        assert inventory(Path(f["path"])) == before[index]
        assembled = services.run_stage(project, "assemble", freeze_id=f["artifact_id"])
        assert assembled.status == "succeeded", assembled.error
        exported = services.run_stage(
            project, "export-frozen", assembly_id=assembled.result["artifact_id"]
        )
        assert exported.status == "succeeded", exported.error
        assert services.verify_frozen_package(exported.result["path"])["valid"]


@pytest.mark.parametrize("legacy", ["accepted", "rejected", "maybe"])
def test_no_legacy_inheritance(project, legacy):
    item = seed(project, "old", label=legacy)
    cid = make(project)
    assign(project, cid, item)
    assert selection(project, cid, item)["event_id"] is None
    result = services.freeze_validate(project, request=freeze_request(cid))
    assert not result["valid"] and result["blockers"][0]["code"] == "unresolved_review"


def test_rename_archive_restore_and_reactivation(project):
    item = seed(project, "one")
    cid = make(project)
    assign(project, cid, item)
    label(project, cid, item, "accepted")
    before = selection(project, cid, item)
    action(project, "edit", collection_id=cid, name="Renamed", phrases=["chapel", "church"])
    action(project, "archive", collection_id=cid)
    action(project, "restore", collection_id=cid)
    assert selection(project, cid, item) == before
    action(project, "remove", collection_id=cid, candidate_ids=[item["candidate_id"]])
    assign(project, cid, item)
    after = selection(project, cid, item)
    assert after["scope"]["activation_id"] != before["scope"]["activation_id"]
    assert after["event_id"] is None
    conn = init_db(project.paths.db_path)
    assert len(col.records(conn, "collection_reviews", cid)) == 1
    assert len(col.catalog(conn)) == 1
    assert col.catalog(conn)[0]["phrases"][0]["text"] == "chapel"
    conn.close()


def test_changed_asset_invalidates_without_erasing_review(project):
    item = seed(project, "one")
    other = seed(project, "other")
    cid = make(project)
    assign(project, cid, item)
    label(project, cid, item, "accepted")
    conn = init_db(project.paths.db_path)
    conn.execute(
        "UPDATE candidates SET asset_id=? WHERE candidate_id=?",
        (other["asset_id"], item["candidate_id"]),
    )
    conn.commit()
    assert (
        col.current_review(conn, col.identity(conn, cid, item["candidate_id"]))["label"]
        == "unrated"
    )
    assert len(col.records(conn, "collection_reviews", cid)) == 1
    conn.close()


def test_shared_observation_different_scoped_policy_and_override(project):
    from stimulus_harvester import workspace

    item = seed(project, "shared")
    a, b = make(project), make(project, "B")
    assign(project, a, item)
    assign(project, b, item)
    screen(project, {"shared": 0.9})
    alternate = strict_policy()
    alternate["rules"][0]["outcomes"]["detected"] = "review_required"
    action(project, "policy", collection_id=a, configuration=strict_policy())
    action(project, "policy", collection_id=b, configuration=alternate)
    assert any("configuration differs" in w for w in workspace.workspace(project.root).warnings)
    assert not any(
        "configuration differs" in w for w in workspace.workspace(project.root, a).warnings
    )
    conn = init_db(project.paths.db_path)
    ra = col.screening_report(conn, project, a)["candidates"][0]
    rb = col.screening_report(conn, project, b)["candidates"][0]
    assert ra["effective_disposition"] == "excluded"
    assert rb["effective_disposition"] == "review_required"
    assert ra["evaluations"][0]["observation_id"] == rb["evaluations"][0]["observation_id"]
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    conn.close()
    label(project, a, item, "accepted")
    assert not services.freeze_validate(project, request=freeze_request(a))["valid"]
    action(
        project,
        "override",
        collection_id=a,
        candidate_id=item["candidate_id"],
        expected_scope=ra["scope"],
        disposition="review_required",
        note="Synthetic adjudication",
    )
    assert services.freeze_validate(project, request=freeze_request(a))["valid"]
    assert selection(project, b, item)["event_id"] is None
    frozen = freeze(project, a)
    assert services.verify_frozen_package(frozen["path"])["valid"]


def test_migration_backup_legacy_and_no_default(project):
    seed(project, "legacy", label="accepted")
    legacy = services.run_stage(project, "freeze-create", request=request())
    assert legacy.status == "succeeded"
    before = inventory(Path(legacy.result["path"]))
    make(project)
    migration = list((project.root / "migrations").glob("collections_v1*/migration.json"))
    assert len(migration) == 1 and json.loads(migration[0].read_text())["status"] == "verified"
    with sqlite3.connect(migration[0].parent / "before.sqlite") as backup:
        assert backup.execute("SELECT label FROM reviews").fetchone()[0] == "accepted"
        assert not backup.execute("SELECT 1 FROM sqlite_master WHERE name='collections'").fetchone()
    reopened = services.open_project(project.root)
    assert reopened.config.screening.detectors == []
    assert services.verify_frozen_package(legacy.result["path"])["valid"]
    assert inventory(Path(legacy.result["path"])) == before
    assert (
        services.run_stage(project, "assemble", freeze_id=legacy.result["artifact_id"]).status
        == "succeeded"
    )


def test_shared_asset_no_propagation_and_duplicate_conflict(project):
    item = seed(project, "first")
    other = seed(project, "second", duplicate=item["path"], license="conflicting-claim")
    cid = make(project)
    assign(project, cid, item, other)
    label(project, cid, item, "accepted")
    assert selection(project, cid, other)["event_id"] is None
    label(project, cid, other, "rejected")
    result = services.freeze_validate(project, request=freeze_request(cid))
    assert "duplicate_decision_conflict" in {b["code"] for b in result["blockers"]}
    req = freeze_request(cid)
    req["duplicate_resolutions"] = [
        {
            "asset_id": item["asset_id"],
            "membership": "include",
            "reviewer": "fixture",
            "reason": "Explicit duplicate decision",
        }
    ]
    frozen = services.run_stage(project, "freeze-create", request=req)
    assert frozen.status == "succeeded", frozen.error
    m = services.frozen_artifact(project, frozen.result["artifact_id"])["manifest"]
    assert len(m["membership"]) == 1 and len(m["membership"][0]["source_ids"]) == 2


def test_atomic_stale_batch_and_append_only(project):
    a, b = seed(project, "a"), seed(project, "b")
    cid = make(project)
    assign(project, cid, a, b)
    selected = [selection(project, cid, x) for x in (a, b)]
    label(project, cid, b, "accepted")
    result = services.run_stage(
        project,
        "collection",
        action="review",
        collection_id=cid,
        actor="fixture",
        selection=selected,
        label="rejected",
        batch=True,
    )
    assert result.status == "failed"
    assert selection(project, cid, a)["event_id"] is None
    conn = init_db(project.paths.db_path)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM collection_reviews")
    conn.close()


@respx.mock
def test_search_lineage_multiple_phrases_sources_and_manual_membership(project):
    cfg = project.config.model_dump(mode="json")
    cfg["search"]["openverse"]["max_pages_per_query"] = 1
    cfg["search"]["searxng"].update(
        enabled=True, endpoint_url="https://search.example.org", max_pages_per_query=1
    )
    assert services.run_stage(project, "configure", configuration=cfg).status == "succeeded"
    url = "https://images.example.org/shared.png"
    respx.get("https://api.openverse.org/v1/images/").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"url": url, "license": "by", "foreign_landing_url": "https://source.example/a"}
                ]
            },
        )
    )
    respx.get("https://search.example.org/search").mock(
        return_value=httpx.Response(
            200, json={"results": [{"img_src": url, "url": "https://source.example/b"}]}
        )
    )
    a, b = make(project), make(project, "Manual additions")
    result = services.run_stage(project, "search", collection_id=a, actor="fixture")
    assert result.status == "succeeded", result.error
    conn = init_db(project.paths.db_path)
    assert len(col.memberships(conn, a)) == 1
    sources = [dict(r) for r in conn.execute("SELECT * FROM discoveries ORDER BY rowid")]
    assert len(sources) == 4
    assert {r["query"] for r in sources} == {"prison cell", "prison interior"}
    assert len(col.records(conn, "membership_events", a)) == 4
    context = col.evidence(conn, a)["search_contexts"][0]
    assert context["name"] == "Prison interiors"
    item = {"candidate_id": sources[0]["candidate_id"]}
    assert conn.execute("SELECT count(*) FROM assets").fetchone()[0] == 0
    conn.close()
    assign(project, b, item)
    action(project, "edit", collection_id=a, name="Renamed", phrases=["changed"])
    action(project, "remove", collection_id=a, candidate_ids=[item["candidate_id"]])
    conn = init_db(project.paths.db_path)
    assert [dict(r) for r in conn.execute("SELECT * FROM discoveries ORDER BY rowid")] == sources
    assert col.evidence(conn, a)["search_contexts"][0] == context
    assert len(col.memberships(conn, b)) == 1 and not col.memberships(conn, a)
    assert col.records(conn, "membership_events", b)[0]["origin"] == "manual"
    conn.close()
    assert project.config.search.seed_queries == cfg["search"]["seed_queries"]


def test_missing_observation_is_not_fabricated_and_stale_override_rejected(project):
    item = seed(project, "one")
    cid = make(project)
    assign(project, cid, item)
    action(project, "policy", collection_id=cid, configuration=strict_policy())
    conn = init_db(project.paths.db_path)
    row = col.screening_report(conn, project, cid)["candidates"][0]
    assert row["effective_disposition"] == "review_required" and row["evaluations"] == []
    conn.close()
    action(project, "policy", collection_id=cid, configuration={})
    result = services.run_stage(
        project,
        "collection",
        action="override",
        actor="fixture",
        collection_id=cid,
        candidate_id=item["candidate_id"],
        expected_scope=row["scope"],
        disposition="eligible",
        note="stale",
    )
    assert result.status == "failed"


def test_collection_inference_does_not_activate_project_policy(project, monkeypatch):
    item = seed(project, "one")
    seed(project, "outside")
    cid = make(project)
    assign(project, cid, item)
    action(project, "policy", collection_id=cid, configuration=strict_policy())

    class Detector:
        def __init__(self):
            self.identity = {"fixture": True}
            self.runtime = {"fixture": True}

        def predict(self, path):
            assert path.stem == "one"
            return {"width": 40, "height": 30, "detections": []}

        def close(self):
            pass

    monkeypatch.setattr(
        "stimulus_harvester.screening.engine.build_detector", lambda settings: Detector()
    )
    result = services.run_stage(project, "screen", collection_id=cid)
    assert result.status == "succeeded", result.error
    report = services.screening_report(project)
    assert report["policy"] is None
    assert len(report["observations"]) == 1
    assert report["observations"][0]["candidate_id"] == item["candidate_id"]


def test_atomic_assignment_unknown_candidate_and_no_byte_copies(project):
    item = seed(project, "one")
    cid = make(project)
    before = inventory(project.paths.images_original)
    bad = services.run_stage(
        project,
        "collection",
        action="add",
        collection_id=cid,
        actor="fixture",
        candidate_ids=[item["candidate_id"], "not-a-candidate"],
    )
    assert bad.status == "failed"
    conn = init_db(project.paths.db_path)
    assert col.memberships(conn, cid) == []
    conn.close()
    assign(project, cid, item)
    assign(project, make(project, "Second"), item)
    assert inventory(project.paths.images_original) == before


def test_stale_edit_and_stale_removal_are_rejected(project):
    item = seed(project, "one")
    cid = make(project)
    assign(project, cid, item)
    old = selection(project, cid, item)["scope"]
    conn = init_db(project.paths.db_path)
    revision = col.collection(conn, cid)["event_id"]
    conn.close()
    action(project, "edit", collection_id=cid, name="Changed")
    result = services.run_stage(
        project,
        "collection",
        action="edit",
        actor="fixture",
        collection_id=cid,
        name="Stale",
        expected_revision=revision,
    )
    assert result.status == "failed"
    action(project, "remove", collection_id=cid, candidate_ids=[item["candidate_id"]])
    assign(project, cid, item)
    result = services.run_stage(
        project,
        "collection",
        action="remove",
        actor="fixture",
        collection_id=cid,
        candidate_ids=[item["candidate_id"]],
        expected_scopes=[old],
    )
    assert result.status == "failed"


def test_migration_failure_rolls_back_and_retains_backup(project):
    conn = init_db(project.paths.db_path)

    class Fault:
        def __getattr__(self, name):
            return getattr(conn, name)

        def execute(self, sql, *args):
            if "CREATE TABLE collection_reviews" in sql:
                raise RuntimeError("injected DDL failure")
            return conn.execute(sql, *args)

    with pytest.raises(RuntimeError, match="injected"):
        col.ensure_schema(Fault())
    assert not col.exists(conn)
    conn.close()
    receipts = list((project.root / "migrations").glob("*/migration.json"))
    assert json.loads(receipts[0].read_text())["status"] == "rolled_back"
    assert (receipts[0].parent / "before.sqlite").exists()
    make(project)
    assert services.open_project(project.root).project_id == project.project_id


@pytest.mark.parametrize("operation", ["review", "policy", "add"])
def test_archive_blocks_mutation(project, operation):
    item = seed(project, "one")
    cid = make(project)
    assign(project, cid, item)
    action(project, "archive", collection_id=cid)
    result = services.run_stage(
        project, "collection", action=operation, actor="fixture", collection_id=cid
    )
    assert result.status == "failed" and "archived" in result.error["message"]


def test_threshold_mismatch_does_not_reuse_normalized_observation(project):
    item = seed(project, "one")
    cid = make(project)
    assign(project, cid, item)
    screen(project, {"one": 0.9})
    policy = strict_policy(high=0.95)
    action(project, "policy", collection_id=cid, configuration=policy)
    conn = init_db(project.paths.db_path)
    row = col.screening_report(conn, project, cid)["candidates"][0]
    assert row["evaluations"] == [] and row["effective_disposition"] == "review_required"
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 1
    conn.close()


def test_collection_freeze_empty_and_cross_scope_parent(project):
    a, b = make(project), make(project, "B")
    assert not services.freeze_validate(project, request=freeze_request(a))["valid"]
    item = seed(project, "one")
    assign(project, a, item)
    assign(project, b, item)
    label(project, a, item, "accepted")
    label(project, b, item, "accepted")
    frozen = freeze(project, a)
    req = freeze_request(b)
    req["parent_freeze_id"] = frozen["artifact_id"]
    validation = services.freeze_validate(project, request=req)
    assert "parent_freeze_invalid" in {r["code"] for r in validation["blockers"]}

from __future__ import annotations

import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient
from PIL import Image

from stimulus_harvester import services, storage
from stimulus_harvester.cli import cli
from stimulus_harvester.config import HarvesterConfig, config_to_yaml_str
from stimulus_harvester.download.integrity import sha256_of_file
from stimulus_harvester.models import DownloadRecord, DownloadStatus, SearchResult
from stimulus_harvester.paths import RunPaths
from stimulus_harvester.review.db import init_db, update_download, upsert_image_from_search


@pytest.fixture
def config():
    return HarvesterConfig.model_validate(
        {
            "run_name": "test",
            "search": {"seed_queries": ["q"], "query_expansion": {"enabled": False}},
            "clip": {"positive_prompts": ["p"], "negative_prompts": ["n"]},
        }
    )


def source(local_id="old-a", provider="openverse", url="https://example.org/a.png", license="by"):
    return SearchResult(
        local_id=local_id,
        source_backend=provider,
        search_query="expanded",
        seed_query="seed",
        image_url=url,
        landing_page_url=f"https://{provider}.org/page",
        license=license,
        creator=f"{provider} creator",
        external_id="external",
        rank=2,
        page=1,
        result_order=1,
        query_lineage={"template": "example"},
    )


def seed_image(project, local_id="old-a", *, url="https://example.org/a.png"):
    conn = init_db(project.paths.db_path)
    upsert_image_from_search(conn, source(local_id=local_id, url=url), project.root.name)
    file = project.paths.images_original / f"{local_id}.png"
    Image.new("RGB", (500, 400), (20, 50, 80)).save(file)
    update_download(
        conn,
        DownloadRecord(
            local_id=local_id,
            image_url=url,
            status=DownloadStatus.SUCCESS,
            file_path=str(file),
            sha256=sha256_of_file(file),
            width=500,
            height=400,
            file_size_bytes=file.stat().st_size,
        ),
    )
    conn.close()
    return file


@pytest.mark.parametrize("folder", ["plain", "project with spaces", "研究 café Δ"])
def test_create_reopen_and_changed_cwd(tmp_path, monkeypatch, config, folder):
    project = services.create_project(tmp_path / folder, config)
    metadata = (project.root / "project.json").read_bytes()
    monkeypatch.chdir(tmp_path.parent)
    reopened = services.open_project(project.root)
    assert reopened == project
    assert (project.root / "project.json").read_bytes() == metadata
    status = services.project_status(reopened)
    assert status["counts"]["assets"] == 0
    assert status["schema_version"] == 1
    assert status["identity_scope"] == "harvester-local/provisional"


def test_create_refuses_nonempty_destination(tmp_path, config):
    folder = tmp_path / "existing"
    folder.mkdir()
    (folder / "evidence.txt").write_text("keep")
    with pytest.raises(ValueError, match="new or empty"):
        services.create_project(folder, config)
    assert (folder / "evidence.txt").read_text() == "keep"


def test_copy_move_reopen_and_stage_paths(tmp_path, monkeypatch, config):
    original = services.create_project(tmp_path / "old place", config)
    file = seed_image(original)
    digest = sha256_of_file(file)
    copied = tmp_path / "copied 研究"
    shutil.copytree(original.root, copied)
    # Move original away so any obsolete absolute reference cannot accidentally work.
    original.root.rename(tmp_path / "moved original")
    monkeypatch.chdir(tmp_path.parent)
    project = services.open_project(copied)
    assert project.project_id == original.project_id
    evidence = services.candidate_evidence(project, "old-a")
    assert evidence["asset"]["locations"] == ["images/original/old-a.png"]
    assert sha256_of_file(project.resolve(evidence["asset"]["locations"][0])) == digest
    assert services.project_status(project)["missing_assets"] == []
    assert services.run_stage(project, "filter").status == "succeeded"
    client = TestClient(services.review_application(project))
    assert (
        client.get("/images/old-a/file").content
        == project.resolve("images/original/old-a.png").read_bytes()
    )
    assert client.get("/images/old-a/file").status_code == 200
    assert client.get("/images/old-a").status_code == 200
    assert (
        client.post(
            "/images/old-a/label", json={"label": "accepted", "notes": "reviewed"}
        ).status_code
        == 200
    )
    result = services.run_stage(project, "export")
    assert result.status == "succeeded", result.error
    assert (
        project.paths.images_accepted.joinpath("old-a.png").read_bytes()
        == project.resolve("images/original/old-a.png").read_bytes()
    )
    assert "NON-AUTHORITATIVE" in (project.paths.images_accepted / "README.txt").read_text(
        encoding="utf-8"
    )
    assert "old-a.png" in (project.paths.exports / "contact_sheet.html").read_text()


def test_source_claims_survive_url_and_content_canonicalization(tmp_path, config):
    project = services.create_project(tmp_path / "project", config)
    seed_image(project)
    conn = init_db(project.paths.db_path)
    canonical = upsert_image_from_search(conn, source("other-id", "searxng", license=None), "run")
    assert canonical == "old-a"
    assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 1
    conn.close()
    # Different URL, identical bytes: one asset; distinct review state is not silently merged.
    seed_image(project, "old-b", url="https://elsewhere.org/b.png")
    conn = init_db(project.paths.db_path)
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM asset_locations").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM candidates").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0] == 3
    conn.close()
    evidence = services.candidate_evidence(project, "other-id")
    assert set(evidence["legacy_ids"]) == {"old-a", "other-id"}
    assert {(s["provider"], s["license"], s["creator"]) for s in evidence["sources"]} == {
        ("openverse", "by", "openverse creator"),
        ("searxng", None, "searxng creator"),
    }
    assert all(s["rank"] == 2 and s["page"] == 1 for s in evidence["sources"])
    result = services.run_stage(project, "export", label="all", formats=("jsonl",))
    assert result.status == "succeeded"
    assert "searxng creator" in Path(result.result["lineage"]).read_text()


@pytest.fixture
def legacy_run(tmp_path, config):
    paths = RunPaths(tmp_path / "legacy run 研究")
    paths.ensure()
    paths.config_snapshot_path.write_text(config_to_yaml_str(config), encoding="utf-8")
    conn = sqlite3.connect(paths.db_path)
    conn.executescript((Path(__file__).parent / "fixtures" / "legacy_v0.sql").read_text())
    original = paths.images_original / "legacy.png"
    Image.new("RGB", (600, 400), "gray").save(original)
    record = source("legacy-id").model_dump(mode="json")
    record.update({"raw_response_path": "runs/old-run/raw_api/openverse_q.json"})
    (paths.raw_api / "openverse_q.json").write_text('{"results": []}')
    # An obsolete Windows absolute path must relocate from its owned artifact suffix.
    file_path = "Z:\\old-machine\\runs\\old-run\\images\\original\\legacy.png"
    conn.execute(
        """INSERT INTO images(local_id,run_id,source_backend,search_query,image_url,
        landing_page_url,license,download_status,file_path,sha256,width,height)
        VALUES (?,?,?,?,?,?,?,'success',?,?,600,400)""",
        (
            "legacy-id",
            "old-run",
            "openverse",
            "expanded",
            record["image_url"],
            record["landing_page_url"],
            "by",
            file_path,
            sha256_of_file(original),
        ),
    )
    conn.execute(
        "INSERT INTO reviews(local_id,label,notes) VALUES ('legacy-id','accepted','keep decision')"
    )
    conn.execute(
        "INSERT INTO review_events(local_id,label,notes) VALUES ('legacy-id','maybe','first')"
    )
    conn.execute(
        "INSERT INTO review_events(local_id,label,notes) VALUES ('legacy-id','accepted','keep decision')"
    )
    conn.commit()
    conn.close()
    secondary = dict(record, local_id="secondary-id", source_backend="searxng", license=None)
    (paths.manifests / "search_results.jsonl").write_text(
        json.dumps(record) + "\n" + json.dumps(secondary) + "\n", encoding="utf-8"
    )
    (paths.manifests / "download_results.jsonl").write_text('{"legacy": "preserve verbatim"}\n')
    return paths


def test_legacy_migration_preserves_bytes_ids_reviews_and_evidence(legacy_run):
    paths = legacy_run
    before = {
        p.relative_to(paths.root): p.read_bytes() for p in paths.root.rglob("*") if p.is_file()
    }
    project = services.open_project(paths.root)
    conn = init_db(paths.db_path)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 1
    assert conn.execute("SELECT notes FROM reviews").fetchone()[0] == "keep decision"
    assert [row[0] for row in conn.execute("SELECT notes FROM review_events ORDER BY id")] == [
        "first",
        "keep decision",
    ]
    assert conn.execute("SELECT local_id FROM images").fetchone()[0] == "legacy-id"
    assert conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0] == 3
    conn.close()
    assert services.candidate_evidence(project, "secondary-id")["primary_local_id"] == "legacy-id"
    for relative, content in before.items():
        if relative != Path("review/state.db"):
            assert (paths.root / relative).read_bytes() == content
    backups = list(paths.review_dir.glob("state.pre-v1.*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as saved:
        assert saved.execute("PRAGMA user_version").fetchone()[0] == 0
        assert saved.execute("SELECT COUNT(*) FROM review_events").fetchone()[0] == 2
    assert services.open_project(paths.root).project_id == project.project_id
    assert len(list(paths.review_dir.glob("state.pre-v1.*.db"))) == 1


@pytest.mark.parametrize("failure", ["bad_evidence", "changed_bytes", "injected_error"])
def test_migration_failure_leaves_legacy_database_untouched(legacy_run, monkeypatch, failure):
    if failure == "bad_evidence":
        (legacy_run.manifests / "search_results.jsonl").write_text("{broken JSON")
    elif failure == "changed_bytes":
        (legacy_run.images_original / "legacy.png").write_bytes(b"changed bytes")
    else:

        def fail(*args):
            raise RuntimeError("injected migration failure")

        monkeypatch.setattr(storage, "_populate_legacy", fail)
    before = legacy_run.db_path.read_bytes()
    with pytest.raises((ValueError, RuntimeError)):
        services.open_project(legacy_run.root)
    assert legacy_run.db_path.read_bytes() == before
    assert not (legacy_run.root / "project.json").exists()
    assert not (legacy_run.root / ".harvester.lock").exists()
    assert list(legacy_run.review_dir.glob("state.pre-v1.*.db"))


def test_future_schema_rejected_without_mutation(tmp_path, config):
    project = services.create_project(tmp_path / "project", config)
    with sqlite3.connect(project.paths.db_path) as conn:
        conn.execute("PRAGMA user_version=999")
    before = project.paths.db_path.read_bytes()
    with pytest.raises(ValueError, match="Unsupported database schema"):
        services.open_project(project.root)
    assert project.paths.db_path.read_bytes() == before


def test_identity_recovered_after_manifest_write_interruption(tmp_path, config):
    project = services.create_project(tmp_path / "project", config)
    (project.root / "project.json").unlink()
    assert services.open_project(project.root).project_id == project.project_id


def test_operation_results_and_failure_history(tmp_path, config, monkeypatch):
    project = services.create_project(tmp_path / "project", config)
    progress = []
    first = services.run_stage(project, "download", progress=progress.append)
    second = services.run_stage(project, "download", cancelled=lambda: True)
    assert first.status == "succeeded" and second.status == "cancelled"
    assert first.operation_id != second.operation_id
    snapshot = project.root / "operations" / first.operation_id / "result.json"
    before = snapshot.read_bytes()

    def fail(*args):
        raise ValueError("fixture error")

    monkeypatch.setattr(services, "_invoke", fail)
    failed = services.run_stage(project, "filter")
    assert failed.status == "failed" and failed.error["message"] == "fixture error"
    assert snapshot.read_bytes() == before
    assert [p["status"] for p in progress] == ["started", "succeeded"]
    assert len(services.project_status(project)["operations"]) == 3


def test_cli_explicit_paths_use_shared_services(tmp_path, config, monkeypatch):
    runner = CliRunner()
    config_path = tmp_path / "config.yaml"
    config_path.write_text(config_to_yaml_str(config))
    root = tmp_path / "CLI café 研究"
    result = runner.invoke(
        cli, ["project", "create", "--project", str(root), "--config", str(config_path)]
    )
    assert result.exit_code == 0, result.output
    project_id = json.loads(result.output)["project_id"]
    monkeypatch.chdir(tmp_path.parent)
    result = runner.invoke(cli, ["project", "status", "--project", str(root)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["project_id"] == project_id
    invoked = []
    actual = services.run_stage

    def observe(path, stage, **options):
        invoked.append((path.project_id, stage))
        return actual(path, stage, **options)

    monkeypatch.setattr(services, "run_stage", observe)
    result = runner.invoke(cli, ["download", "--project", str(root)])
    assert result.exit_code == 0, result.output
    assert invoked == [(project_id, "download")]
    assert runner.invoke(cli, ["download", "--project", str(root), "--run-id", "x"]).exit_code != 0


def test_browser_resources_and_source_evidence(tmp_path, config):
    project = services.create_project(tmp_path / "project", config)
    file = seed_image(project)
    client = TestClient(services.review_application(project))
    assert client.get("/images").status_code == 200
    assert client.get("/static/style.css").status_code == 200
    assert client.get("/static/review.js").status_code == 200
    assert client.get("/images/old-a/file").content == file.read_bytes()
    assert "openverse creator" in client.get("/images/old-a").text
    assert client.get("/images/old-a/evidence").json()["legacy_ids"] == ["old-a"]
    with storage.project_lock(project.root):
        assert client.post("/images/old-a/label", json={"label": "accepted"}).status_code == 409


def test_project_path_escape_rejected(tmp_path, config):
    project = services.create_project(tmp_path / "project", config)
    with pytest.raises(ValueError, match="escapes"):
        project.resolve("../outside.png")


def test_missing_legacy_original_is_explicit(legacy_run):
    (legacy_run.images_original / "legacy.png").unlink()
    project = services.open_project(legacy_run.root)
    status = services.project_status(project)
    assert status["missing_assets"] == ["images/original/legacy.png"]
    assert "historical metadata" in status["migration_notes"][0]
    assert services.candidate_evidence(project, "legacy-id")["asset_id"].startswith("sha256:")


def test_migration_publish_failure_is_recoverable(legacy_run, monkeypatch):
    before = legacy_run.db_path.read_bytes()

    def fail(*args):
        raise OSError("injected replace failure")

    monkeypatch.setattr(storage.os, "replace", fail)
    with pytest.raises(OSError, match="replace failure"):
        services.open_project(legacy_run.root)
    assert legacy_run.db_path.read_bytes() == before
    assert list(legacy_run.review_dir.glob("state.pre-v1.*.db"))
    assert not (legacy_run.root / ".harvester.lock").exists()


def test_legacy_wal_migration_refuses_unsafe_replacement(legacy_run):
    conn = sqlite3.connect(legacy_run.db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()
    with pytest.raises(ValueError, match="WAL"):
        services.open_project(legacy_run.root)
    with sqlite3.connect(legacy_run.db_path) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM review_events").fetchone()[0] == 2


def test_project_manifest_mismatch_rejected(tmp_path, config):
    project = services.create_project(tmp_path / "project", config)
    path = project.root / "project.json"
    manifest = json.loads(path.read_text())
    manifest["project_id"] = "wrong"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="does not match"):
        services.open_project(project.root)


def test_legacy_run_id_cli_compatibility(tmp_path, config, monkeypatch):
    project = services.create_project(tmp_path / "runs" / "test-run", config)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(cli, ["download", "--run-id", "test-run"])
    assert result.exit_code == 0, result.output
    assert services.project_status(project)["operations"][0]["kind"] == "download"


def test_evidence_snapshot_error_is_structured(tmp_path, config, monkeypatch):
    project = services.create_project(tmp_path / "project", config)

    def fail(*args, **kwargs):
        raise OSError("fixture snapshot error")

    monkeypatch.setattr(services.shutil, "copytree", fail)
    result = services.run_stage(project, "download")
    assert result.status == "failed"
    assert "Evidence snapshot failed" in result.error["message"]
    assert services.project_status(project)["operations"][0]["status"] == "failed"


def test_legacy_conflicting_reviews_are_not_silently_merged(legacy_run):
    conn = sqlite3.connect(legacy_run.db_path)
    conn.execute("""INSERT INTO images(local_id,run_id,source_backend,search_query,image_url,
        download_status,file_path,sha256,width,height)
        SELECT 'duplicate-id',run_id,'searxng',search_query,'https://elsewhere.org/same.png',
        download_status,file_path,sha256,width,height FROM images WHERE local_id='legacy-id'""")
    conn.execute(
        "INSERT INTO reviews(local_id,label,notes) VALUES ('duplicate-id','rejected','different decision')"
    )
    conn.execute(
        "INSERT INTO review_events(local_id,label,notes) VALUES ('duplicate-id','rejected','different decision')"
    )
    conn.commit()
    conn.close()
    project = services.open_project(legacy_run.root)
    status = services.project_status(project)
    assert status["counts"]["candidates"] == 2
    assert status["counts"]["assets"] == 1
    assert status["reviews"] == {"accepted": 1, "rejected": 1}
    assert status["counts"]["review_events"] == 3


def test_review_event_history_survives_reopen(tmp_path, config):
    project = services.create_project(tmp_path / "project", config)
    seed_image(project)
    client = TestClient(services.review_application(project, reviewer_email="reviewer@example.org"))
    for label in ("accepted", "rejected", "maybe"):
        assert (
            client.post("/images/old-a/label", json={"label": label, "notes": label}).status_code
            == 200
        )
    project = services.open_project(project.root)
    conn = init_db(project.paths.db_path)
    assert [r[0] for r in conn.execute("SELECT label FROM review_events ORDER BY id")] == [
        "accepted",
        "rejected",
        "maybe",
    ]
    assert conn.execute("SELECT label FROM reviews").fetchone()[0] == "maybe"
    conn.close()


def test_legacy_uninserted_discovery_is_recovered(legacy_run):
    evidence = legacy_run.manifests / "search_results.jsonl"
    with evidence.open("a", encoding="utf-8") as handle:
        handle.write(source("orphan", url="https://source.org/orphan.png").model_dump_json() + "\n")
    project = services.open_project(legacy_run.root)
    recovered = services.candidate_evidence(project, "orphan")
    assert recovered["asset_id"] is None
    assert recovered["sources"][0]["evidence_origin"].endswith(":3")
    assert services.project_status(project)["counts"]["candidates"] == 2

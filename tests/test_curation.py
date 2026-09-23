"""Curation comprehension: metadata-only projection and destination request policy."""

import json
import sqlite3

import httpx
import pytest
import respx
from test_provider_recovery import add, records, response
from test_provider_recovery import project as project_fixture

from stimulus_harvester import services, workspace
from stimulus_harvester.curation import (
    coverage_text,
    failure_kind,
    project_projection,
    review_history_text,
    safe_link,
    summary_text,
)
from stimulus_harvester.download.policy import request_agent
from stimulus_harvester.frozen.store import inventory
from stimulus_harvester.gui.curation_demo import create
from stimulus_harvester.review.db import init_db
from stimulus_harvester.search.evidence import ensure_discovery_schema

project = project_fixture


@pytest.mark.parametrize(
    "provider,host,expected",
    [
        ("commons", "upload.wikimedia.org", True),
        ("openverse", "upload.wikimedia.org", True),
        ("openverse", "images.example.org", False),
        ("openverse", "wikimedia.org.evil.example", False),
        ("commons", "images.example.org", False),
    ],
)
@respx.mock
def test_host_identification_preserves_provenance(project, provider, host, expected):
    a = add(project, provider=provider, url=f"https://{host}/a.png")
    route = respx.get(a[1]).mock(return_value=response())
    assert services.run_stage(project, "download").result == (1, 0)
    agent = route.calls[0].request.headers["user-agent"]
    assert ("https://example.org/research" in agent) == expected
    assert agent == (
        "StimulusHarvesterBot/0.2 (https://example.org/research)"
        if expected
        else project.config.download.user_agent
    )
    saved = json.loads(records(project)[0]["record_json"])
    assert saved["providers"] == [provider]
    conn = init_db(project.paths.db_path)
    assert conn.execute("SELECT provider FROM discoveries").fetchone()[0] == provider
    conn.close()


@respx.mock
def test_missing_contact_preflights_before_any_jobs(project):
    config = project.config.model_dump(mode="json")
    config["search"]["commons"]["contact"] = None
    services.run_stage(project, "configure", configuration=config)
    add(project, "ordinary")
    add(project, "wikimedia", url="https://upload.wikimedia.org/a.png")
    outcome = services.run_stage(project, "download")
    assert outcome.status == "failed" and "contact" in str(outcome.error)
    assert len(respx.calls) == 0 and records(project) == []


@pytest.mark.parametrize("contact", [None, "https://example.org/research"])
@respx.mock
def test_redirect_rechecks_host_policy(project, contact):
    config = project.config.model_dump(mode="json")
    config["search"]["commons"]["contact"] = contact
    services.run_stage(project, "configure", configuration=config)
    a = add(project)
    respx.get(a[1]).mock(
        return_value=httpx.Response(302, headers={"location": "https://upload.wikimedia.org/a.png"})
    )
    wiki = respx.get("https://upload.wikimedia.org/a.png").mock(
        return_value=httpx.Response(
            302, headers={"location": "https://images.example.org/final.png"}
        )
    )
    final = respx.get("https://images.example.org/final.png").mock(return_value=response())
    outcome = services.run_stage(project, "download")
    if contact:
        assert outcome.result == (1, 0)
        assert contact in wiki.calls[0].request.headers["user-agent"]
        assert contact not in final.calls[0].request.headers["user-agent"]
    else:
        assert outcome.result == (0, 1)
        assert not wiki.called and not final.called


@respx.mock
def test_policy_retry_appends_without_rewriting_history(project):
    a = add(project, url="https://upload.wikimedia.org/a.png")
    route = respx.get(a[1]).mock(return_value=httpx.Response(403))
    services.run_stage(project, "download")
    before = records(project)
    route.mock(return_value=response())
    services.run_stage(project, "retry-downloads")
    assert records(project)[: len(before)] == before


def test_host_policy_never_invents_contact():
    with pytest.raises(ValueError, match="contact"):
        request_agent("https://upload.wikimedia.org/a", "generic", None)
    assert request_agent("https://example.org/a", "generic", None) == "generic"


@pytest.mark.parametrize(
    "record,expected",
    [
        ({"http_status": 403}, "access"),
        ({"http_status": 429}, "rate_limit"),
        ({"error_message": "Image exceeds the configured file size limit"}, "size"),
        (
            {
                "error_message": "Downloaded bytes are not a readable image",
                "attempts": [{"response_headers": {"content-type": "image/svg+xml"}}],
            },
            "format",
        ),
        ({"http_status": 504}, "temporary"),
        ({"http_status": 404}, "missing"),
        ({"error_message": "PermissionError: local image write failed"}, "local"),
        ({"error_message": "Downloaded bytes are not a readable image"}, "unreadable"),
        ({"error_message": "Unknown problem"}, "other"),
    ],
)
def test_actionable_failure_evidence(record, expected):
    before = json.dumps(record)
    assert failure_kind(record) == expected
    assert json.dumps(record) == before


def test_metadata_projection_is_read_only_and_not_configuration_evidence(tmp_path):
    p = create(tmp_path / "Metadata 研究 demo")
    before = inventory(p.root)
    conn = sqlite3.connect(p.paths.db_path.as_uri() + "?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    value = project_projection(conn, p.root)
    conn.close()
    assert inventory(p.root) == before
    s = value["summary"]
    assert (
        s["found"],
        s["available"],
        s["unique_images"],
        s["failed"],
        s["pending"],
        s["reviewed"],
        s["screened"],
    ) == (8, 4, 3, 3, 1, 1, 3)
    assert s["failures"] == {"rate_limit": 1, "size": 1, "format": 1}
    assert len(s["retry_ids"]) == 1
    blank = services.create_project(
        tmp_path / "No screening", workspace.default_configuration("blank")
    )
    conn = init_db(blank.paths.db_path)
    assert "Screening: Not run" in summary_text(project_projection(conn, blank.root)["summary"])
    conn.close()


def add_event(conn, event):
    event = dict(
        provider="openverse",
        query="church",
        timestamp="2026-09-22T12:00:00Z",
        operation_id="op",
        **event,
    )
    count = conn.execute("SELECT count(*) FROM discovery_events").fetchone()[0]
    conn.execute(
        "INSERT INTO discovery_events(event_id,provider,query,status,timestamp,record_json) VALUES(?,?,?,?,?,?)",
        (
            str(count),
            event["provider"],
            event["query"],
            event["status"],
            event["timestamp"],
            json.dumps(event),
        ),
    )
    conn.commit()


def test_saved_search_coverage_incomplete_and_configured_limit(project):
    result = services.run_stage(
        project, "configure", configuration=project.config.model_dump(mode="json")
    )
    conn = init_db(project.paths.db_path)
    ensure_discovery_schema(conn)
    add_event(conn, dict(status="page_committed", result_count=200))
    add_event(conn, dict(status="failed", http_status=504, will_retry=False))
    raw = project.root / "raw_api/old-commons.json"
    raw.write_text(json.dumps({"continue": {"gsroffset": 200}}), encoding="utf-8")
    # Legacy completion inferred from the saved envelope, not today's configuration.
    event = dict(
        operation_id=result.operation_id,
        provider="commons",
        query="chapel",
        status="page_committed",
        result_count=200,
        raw_response_path="raw_api/old-commons.json",
    )
    conn.execute(
        "INSERT INTO discovery_events(event_id,provider,query,status,timestamp,record_json) VALUES('commons','commons','chapel','page_committed','now',?)",
        (json.dumps(event),),
    )
    conn.commit()
    text = coverage_text(project_projection(conn, project.root)["coverage"])
    assert "Stopped early after 200" in text and "HTTP 504" in text
    assert "configured search limit" in text and "continuation is not implemented" in text
    conn.close()


def test_readable_history_and_safe_source_links():
    events = [
        dict(
            label="accepted",
            created_at="2026-09-22 12:00:00",
            reviewer_email="Researcher",
            notes="Initial",
        ),
        dict(
            label="maybe",
            created_at="2026-09-22 12:01:00",
            reviewer_email="Researcher",
            notes="Verify claim",
        ),
    ]
    text = review_history_text({"label": "maybe"}, events)
    assert "Current decision: Maybe" in text and text.index("Initial") < text.index("Verify claim")
    assert not any(s in text for s in ("{'", '"label":'))
    assert safe_link("https://example.org/image")
    assert not safe_link("file:///C:/private") and not safe_link("javascript:alert(1)")

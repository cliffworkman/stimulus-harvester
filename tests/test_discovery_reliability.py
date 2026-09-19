"""Offline provider contracts and durable discovery failure/overlap scenarios."""

from __future__ import annotations

import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest
import respx
from click.testing import CliRunner
from fastapi.testclient import TestClient
from PIL import Image

from stimulus_harvester import services
from stimulus_harvester.cli import cli
from stimulus_harvester.config import CommonsConfig, HarvesterConfig, SearxngConfig
from stimulus_harvester.review.db import init_db
from stimulus_harvester.search.commons import CommonsAdapter
from stimulus_harvester.search.evidence import ensure_discovery_schema
from stimulus_harvester.search.searxng import SearxngAdapter

SEARX = "https://search.example.org/search"
COMMONS = "https://commons.wikimedia.org/w/api.php"
OPENVERSE = "https://api.openverse.org/v1/images/"
QUIET = dict(request_delay_seconds=0, retry_backoff_seconds=0, max_retries=0)


def config(**providers):
    search = dict(
        seed_queries=["q"],
        query_expansion={"enabled": False},
        openverse={"enabled": False},
        searxng={"enabled": False},
    )
    search.update(providers)
    return HarvesterConfig.model_validate(
        {
            "run_name": "discovery",
            "search": search,
            "clip": {"positive_prompts": ["p"], "negative_prompts": ["n"]},
        }
    )


def searx(**options):
    return dict(enabled=True, endpoint_url="https://search.example.org") | QUIET | options


def commons(**options):
    return dict(enabled=True, contact="https://example.org/research") | QUIET | options


def file_item(url="https://upload.wikimedia.org/a.jpg", metadata=None, **extra):
    return dict(
        pageid=12,
        index=1,
        title="File:A.jpg",
        imageinfo=[
            {
                "url": url,
                "descriptionurl": "https://commons.wikimedia.org/wiki/File:A.jpg",
                "mime": "image/jpeg",
                "width": 900,
                "height": 600,
                "sha1": "provider-sha1",
                "timestamp": "2020-01-01T00:00:00Z",
                "extmetadata": metadata or {},
            }
        ],
        **extra,
    )


def rows(project, table):
    conn = init_db(project.paths.db_path)
    try:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "response,kind",
    [
        (httpx.Response(403, text="Forbidden"), "json_access_refused"),
        (httpx.Response(403, text="JSON output disabled"), "json_disabled"),
        (httpx.Response(200, text="<html>not JSON</html>"), "malformed_json"),
        (httpx.Response(502, text="upstream unavailable"), "http_error"),
        (httpx.Response(200, json={"results": {}}), "invalid_shape"),
        (httpx.Response(200, json=[]), "malformed_json"),
    ],
)
@respx.mock
def test_searx_capability_failures_are_durable(tmp_path, response, kind):
    route = respx.get(SEARX).mock(return_value=response)
    project = services.create_project(tmp_path / "project", config(searxng=searx()))
    result = services.run_stage(project, "search")
    assert result.status == "failed", result
    assert route.call_count == 1
    events = services.discovery_diagnostics(project)["events"]
    failure = next(e for e in events if e["status"] == "failed")
    assert failure["error"]["kind"] == kind
    assert failure["provider"] == "searxng" and failure["query"] == "q"
    assert failure["page"] == failure["attempt"] == 1
    assert failure["query_id"] and failure["attempt_id"] and failure["timestamp"]
    assert failure["query_lineage"]["origin"] == "seed"
    assert project.resolve(failure["raw_response_path"]).read_bytes() == response.content
    assert not rows(project, "discoveries")


@respx.mock
def test_timeout_retries_are_bounded_and_future_queries_deferred(tmp_path):
    route = respx.get(SEARX).mock(side_effect=httpx.ReadTimeout("simulated timeout"))
    project = services.create_project(
        tmp_path / "p", config(seed_queries=["first", "second"], searxng=searx(max_retries=1))
    )
    outcome = services.run_stage(project, "search")
    events = services.discovery_diagnostics(project)["events"]
    assert outcome.status == "failed" and route.call_count == 2
    failures = [e for e in events if e["status"] == "failed"]
    assert [e["attempt"] for e in failures] == [1, 2]
    assert all(e["error"]["kind"] == "timeout" for e in failures)
    assert len({e["attempt_id"] for e in failures}) == 2
    assert [e["query"] for e in events if e["status"] == "query_deferred"] == ["second"]


@respx.mock
def test_late_page_failure_retains_order_raw_and_occurrences(tmp_path):
    route = respx.get(SEARX).mock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "img_src": "https://images.org/a.jpg",
                            "engines": ["alpha", "beta"],
                            "positions": [1, 3],
                        },
                        {"img_src": "https://images.org/a.jpg"},
                    ]
                },
            ),
            httpx.Response(200, json={"results": [{"img_src": "https://images.org/b.jpg"}]}),
            httpx.Response(503, text="offline"),
        ]
    )
    project = services.create_project(tmp_path / "p", config(searxng=searx(max_pages_per_query=5)))
    outcome = services.run_stage(project, "search")
    assert outcome.status == "partial" and route.call_count == 3
    assert outcome.result["pages_committed"] == 2
    found = [json.loads(r["record_json"]) for r in rows(project, "discoveries")]
    assert [(r["page"], r["rank"], r["result_order"]) for r in found] == [
        (1, 1, 1),
        (1, 2, 2),
        (2, 3, 1),
    ]
    assert found[0]["provider_metadata"]["record"]["engines"] == ["alpha", "beta"]
    assert all(r["query_id"] and r["discovery_attempt_id"] for r in found)
    assert len(rows(project, "candidates")) == 2
    assert [int(call.request.url.params["pageno"]) for call in route.calls] == [1, 2, 3]


@respx.mock
def test_engine_errors_and_bad_image_items_do_not_discard_good_items(tmp_path):
    respx.get(SEARX).mock(
        return_value=httpx.Response(
            200,
            json={
                "unresponsive_engines": [["alpha", "timeout"]],
                "results": [
                    {"url": "https://source.org/page"},
                    {"img_src": "javascript:alert(1)"},
                    {"img_src": "https://source.org/page.html"},
                    None,
                    {"img_src": "https://images.org/ok.jpg"},
                ],
            },
        )
    )
    project = services.create_project(tmp_path / "p", config(searxng=searx()))
    outcome = services.run_stage(project, "search")
    assert outcome.status == "partial" and outcome.result["source_occurrences"] == 1
    events = services.discovery_diagnostics(project)["events"]
    assert len([e for e in events if e["status"] == "item_rejected"]) == 4
    assert next(e for e in events if e["status"] == "engine_errors")["engine_errors"] == [
        ["alpha", "timeout"]
    ]
    assert json.loads(rows(project, "discoveries")[0]["record_json"])["rank"] == 5


@pytest.mark.parametrize("missing_endpoint", [True, False])
@respx.mock
def test_provider_failure_does_not_stop_other_provider(tmp_path, missing_endpoint):
    if not missing_endpoint:
        respx.get(SEARX).mock(return_value=httpx.Response(403))
    respx.get(COMMONS).mock(
        return_value=httpx.Response(200, json={"query": {"pages": [file_item()]}})
    )
    project = services.create_project(
        tmp_path / "p",
        config(
            searxng=searx(endpoint_url=None) if missing_endpoint else searx(), commons=commons()
        ),
    )
    outcome = services.run_stage(project, "search")
    assert outcome.status == "partial"
    assert [r["provider"] for r in rows(project, "discoveries")] == ["commons"]
    assert outcome.result["issues"][0]["error"]["kind"] == (
        "setup_error" if missing_endpoint else "json_access_refused"
    )


@respx.mock
def test_retry_recovery_preserves_failed_attempt_and_raw_response(tmp_path):
    route = respx.get(SEARX).mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, text="slow"),
            httpx.Response(200, json={"results": [{"img_src": "https://images.org/a.jpg"}]}),
        ]
    )
    project = services.create_project(tmp_path / "p", config(searxng=searx(max_retries=1)))
    outcome = services.run_stage(project, "search")
    assert outcome.status == "succeeded" and outcome.result["failed_attempts"] == 1
    assert route.call_count == 2
    events = services.discovery_diagnostics(project)["events"]
    observed = [e for e in events if e["status"] in {"failed", "response_received"}]
    assert [e["attempt"] for e in observed] == [1, 2]
    assert len({e["raw_response_path"] for e in observed}) == 2


@pytest.mark.parametrize(
    "retry_after", ["120", format_datetime(datetime.now(timezone.utc) + timedelta(hours=1))]
)
@respx.mock
def test_retry_after_beyond_bound_defers_instead_of_hammering(tmp_path, retry_after):
    route = respx.get(SEARX).mock(
        return_value=httpx.Response(429, headers={"Retry-After": retry_after})
    )
    with SearxngAdapter(SearxngConfig(**searx(max_retries=3, max_backoff_seconds=1))) as adapter:
        events = []
        adapter.observe = events.append
        assert adapter.search("q", "q", raw_api_dir=tmp_path) == []
        assert route.call_count == 1 and adapter.unavailable
        assert events[-1]["deferred"]


@respx.mock
def test_response_body_limit_stops_normalization(tmp_path):
    respx.get(SEARX).mock(return_value=httpx.Response(200, text="x" * 2048))
    with SearxngAdapter(SearxngConfig(**searx(max_response_bytes=1024))) as adapter:
        events = []
        adapter.observe = events.append
        assert adapter.search("q", "q", raw_api_dir=tmp_path) == []
    assert events[-1]["error"]["kind"] == "response_too_large"
    assert len(next(tmp_path.glob("*.json")).read_bytes()) == 1024


@pytest.mark.parametrize(
    "metadata,state",
    [
        ({}, "missing"),
        ({"LicenseShortName": {"value": "CC BY or GFDL"}}, "ambiguous"),
        ({"LicenseShortName": {"value": ["CC BY", "GFDL"]}}, "ambiguous"),
        (
            {
                "LicenseShortName": {"value": "CC BY 4.0"},
                "LicenseUrl": {"value": "https://creativecommons.org/licenses/by/4.0/"},
            },
            "reported_unverified",
        ),
    ],
)
@respx.mock
def test_commons_preserves_heterogeneous_metadata_and_license_uncertainty(
    tmp_path, metadata, state
):
    metadata = metadata | {
        "Artist": {"value": '<a href="/wiki/User:A">A</a>'},
        "Credit": {"value": "archive"},
    }
    item = file_item(metadata=metadata)
    route = respx.get(COMMONS).mock(
        return_value=httpx.Response(200, json={"query": {"pages": [item]}})
    )
    with CommonsAdapter(CommonsConfig(**commons())) as adapter:
        results = adapter.search("q", "q", raw_api_dir=tmp_path)
    result = results[0]
    assert result.provider_metadata["record"] == item
    assert result.provider_metadata["license_metadata_status"] == state
    assert result.provider_metadata["provider_sha1"] == "provider-sha1"
    assert result.provider_metadata["provider_timestamp"] == "2020-01-01T00:00:00Z"
    assert result.creator == metadata["Artist"]["value"]
    assert (result.width, result.height, result.external_id) == (900, 600, "12")
    assert "https://example.org/research" in route.calls[0].request.headers["User-Agent"]
    assert route.calls[0].request.url.params["gsrnamespace"] == "6"


@respx.mock
def test_commons_continuation_keeps_generator_order_and_bounds_pages(tmp_path):
    first = file_item("https://upload.wikimedia.org/first.jpg")
    second = file_item("https://upload.wikimedia.org/second.jpg")
    second["index"] = 2
    token = {"continue": "gsroffset||", "gsroffset": 2}
    route = respx.get(COMMONS).mock(
        side_effect=[
            httpx.Response(200, json={"query": {"pages": [second, first]}, "continue": token}),
            httpx.Response(
                200,
                json={
                    "query": {"pages": [first]},
                    "continue": {"continue": "gsroffset||", "gsroffset": 3},
                },
            ),
        ]
    )
    with CommonsAdapter(CommonsConfig(**commons(max_pages_per_query=2))) as adapter:
        results = adapter.search("q", "q", raw_api_dir=tmp_path)
    assert [r.image_url for r in results] == [
        first["imageinfo"][0]["url"],
        second["imageinfo"][0]["url"],
        first["imageinfo"][0]["url"],
    ]
    assert [(r.page, r.rank) for r in results] == [(1, 1), (1, 2), (2, 3)]
    assert route.call_count == 2 and route.calls[1].request.url.params["gsroffset"] == "2"


@respx.mock
def test_commons_missing_contact_is_persisted_setup_failure(tmp_path):
    project = services.create_project(tmp_path / "p", config(commons=commons(contact=None)))
    outcome = services.run_stage(project, "search")
    assert outcome.status == "failed"
    assert outcome.result["issues"][0]["error"]["kind"] == "setup_error"
    assert not respx.calls


@respx.mock
def test_commons_maxlag_retries_without_losing_error(tmp_path):
    route = respx.get(COMMONS).mock(
        side_effect=[
            httpx.Response(200, json={"error": {"code": "maxlag", "info": "lag"}}),
            httpx.Response(200, json={"batchcomplete": True}),
        ]
    )
    with CommonsAdapter(CommonsConfig(**commons(max_retries=1))) as adapter:
        events = []
        adapter.observe = events.append
        assert adapter.search("q", "q", raw_api_dir=tmp_path) == []
    assert route.call_count == 2
    assert (
        next(e for e in events if e["status"] == "failed")["error"]["provider_error"]["code"]
        == "maxlag"
    )


@respx.mock
def test_interrupt_preserves_prior_page_and_operation_status(tmp_path):
    def respond(request):
        if request.url.params["pageno"] == "2":
            raise KeyboardInterrupt()
        return httpx.Response(200, json={"results": [{"img_src": "https://images.org/a.jpg"}]})

    respx.get(SEARX).mock(side_effect=respond)
    project = services.create_project(tmp_path / "p", config(searxng=searx(max_pages_per_query=3)))
    with pytest.raises(KeyboardInterrupt):
        services.run_stage(project, "search")
    assert len(rows(project, "discoveries")) == 1
    assert rows(project, "operation_events")[-1]["status"] == "interrupted"
    events = services.discovery_diagnostics(project)["events"]
    assert len([e for e in events if e["status"] == "page_committed"]) == 1
    assert events[-1]["status"] == "started" and events[-1]["page"] == 2


@respx.mock
def test_repeated_queries_providers_operations_preserve_sources_and_coverage(tmp_path):
    x, y, z = [f"https://images.org/{name}.png" for name in "xyz"]
    respx.get(OPENVERSE).mock(
        return_value=httpx.Response(
            200, json={"results": [{"url": x, "license": "by"}, {"url": y, "license": "by"}]}
        )
    )
    respx.get(SEARX).mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {"img_src": x, "license": "other-claim"},
                    {"img_src": z, "license": "other-claim"},
                ]
            },
        )
    )
    project = services.create_project(
        tmp_path / "p",
        config(seed_queries=["q", "q"], openverse=dict(enabled=True, **QUIET), searxng=searx()),
    )
    first = services.run_stage(project, "search")
    assert first.status == "succeeded" and first.result["source_occurrences"] == 8
    raw = {p: p.read_bytes() for p in project.paths.raw_api.rglob("*.json")}
    assert services.run_stage(project, "search").status == "succeeded"
    assert all(p.read_bytes() == data for p, data in raw.items())
    discoveries = rows(project, "discoveries")
    assert len(discoveries) == 16 and len(rows(project, "candidates")) == 3
    assert len({json.loads(r["record_json"])["query_id"] for r in discoveries}) == 8
    assert {
        json.loads(r["record_json"])["license"] for r in discoveries if r["image_url"] == x
    } == {"by", "other-claim"}
    ids = {r["image_url"]: r["local_id"] for r in rows(project, "images")}
    client = TestClient(services.review_application(project))
    assert (
        client.post(
            f"/images/{ids[y]}/label", json={"label": "accepted", "notes": "keep y"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/images/{ids[z]}/label", json={"label": "rejected", "notes": "exclude z"}
        ).status_code
        == 200
    )
    content = io.BytesIO()
    Image.new("RGB", (500, 400), "navy").save(content, format="PNG")
    for url in (x, y, z):
        respx.get(url).mock(
            return_value=httpx.Response(
                200, content=content.getvalue(), headers={"content-type": "image/png"}
            )
        )
    assert services.run_stage(project, "download").status == "succeeded"
    assert len(rows(project, "assets")) == 1 and len(rows(project, "candidates")) == 3
    decisions = {r["local_id"]: r["label"] for r in rows(project, "reviews")}
    assert decisions[ids[y]] == "accepted" and decisions[ids[z]] == "rejected"
    assert len(rows(project, "review_events")) == 2
    candidates = rows(project, "candidates")
    assert len({r["candidate_id"] for r in candidates}) == 3
    report = services.discovery_diagnostics(
        project, operation_id=first.operation_id, provider_order=["openverse", "searxng"]
    )["coverage"]
    assert report["total_unique_urls"] == 3 and report["total_acquired_assets"] == 1
    assert report["providers"]["openverse"]["source_occurrences"] == 4
    assert report["providers"]["openverse"]["incremental_unique_urls"] == 2
    assert report["providers"]["searxng"]["incremental_unique_urls"] == 1
    assert report["providers"]["searxng"]["incremental_unique_assets"] == 0
    assert report["pairwise_overlap"] == [
        {"providers": ["openverse", "searxng"], "shared_urls": 1, "shared_exact_assets": 1}
    ]
    response = CliRunner().invoke(
        cli,
        ["discovery-report", "--project", str(project.root), "--operation-id", first.operation_id],
    )
    assert response.exit_code == 0, response.output
    assert json.loads(response.output)["coverage"]["total_unique_urls"] == 3
    with pytest.raises(ValueError, match="Provider order"):
        services.discovery_diagnostics(project, provider_order=["openverse"])


def test_additive_schema_rolls_back_on_collision_and_rejects_unknown_version(tmp_path):
    conn = sqlite3.connect(tmp_path / "state.db")
    conn.execute("CREATE TABLE discovery_events(legacy TEXT)")
    conn.commit()
    with pytest.raises(sqlite3.OperationalError):
        ensure_discovery_schema(conn)
    assert (
        conn.execute("SELECT name FROM sqlite_master WHERE name='discovery_schema'").fetchone()
        is None
    )
    assert conn.execute("PRAGMA table_info(discovery_events)").fetchone()[1] == "legacy"
    conn.execute("DROP TABLE discovery_events")
    conn.commit()
    ensure_discovery_schema(conn)
    conn.execute("UPDATE discovery_schema SET version=99")
    conn.commit()
    with pytest.raises(ValueError, match="Unsupported"):
        ensure_discovery_schema(conn)
    conn.close()

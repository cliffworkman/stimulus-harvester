"""Provider limits, acquisition recovery and accounting without live-network dependency."""

import io
import json
from datetime import timedelta

import httpx
import pytest
import respx
from click.testing import CliRunner
from PIL import Image

from stimulus_harvester import services, workspace
from stimulus_harvester.cli import cli
from stimulus_harvester.config import DownloadConfig, OpenverseConfig
from stimulus_harvester.download.fetch import download_one, retry_delay
from stimulus_harvester.download.state import (
    format_acquisition_summary,
    select_downloads,
)
from stimulus_harvester.models import SearchResult, utcnow
from stimulus_harvester.review.db import init_db, upsert_image_from_search
from stimulus_harvester.search.openverse import OpenverseAdapter
from stimulus_harvester.search.presentation import discovery_text

OV = "https://api.openverse.org/v1/images/"
COMMONS = "https://commons.wikimedia.org/w/api.php"


def png(color="navy"):
    out = io.BytesIO()
    Image.new("RGB", (40, 30), color).save(out, format="PNG")
    return out.getvalue()


def response(color="navy"):
    return httpx.Response(200, content=png(color), headers={"content-type": "image/png"})


@pytest.fixture
def project(tmp_path):
    config = workspace.default_configuration("recovery")
    config.download.request_delay_seconds = 0
    config.download.retry_backoff_seconds = 0
    config.download.max_retries = 0
    config.search.commons.contact = "https://example.org/research"
    return services.create_project(tmp_path / "Recovery 研究 with spaces", config)


def add(project, local="a", provider="openverse", query="q", url=None):
    url = url or f"https://images.example.org/{local}.png"
    conn = init_db(project.paths.db_path)
    try:
        local = upsert_image_from_search(
            conn,
            SearchResult(
                local_id=local,
                source_backend=provider,
                search_query=query,
                seed_query=query,
                image_url=url,
                landing_page_url=url,
            ),
            project.root.name,
        )
        return (
            local,
            url,
            conn.execute(
                "SELECT candidate_id FROM candidates WHERE primary_local_id=?", (local,)
            ).fetchone()[0],
        )
    finally:
        conn.close()


def records(project):
    conn = init_db(project.paths.db_path)
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM acquisitions ORDER BY rowid")]
    finally:
        conn.close()


@pytest.mark.parametrize(
    "metadata,items,expected",
    [
        ({"page_count": 1, "result_count": 2, "page": 1, "page_size": 2}, 2, 1),
        ({"page_count": 1}, 2, 1),
        ({"result_count": 2}, 2, 1),
        ({}, 1, 1),
        ({"page_count": "invalid", "result_count": -1}, 2, 2),
    ],
)
def test_openverse_metadata_and_short_page_stop(tmp_path, metadata, items, expected):
    seen = []

    def handle(request):
        seen.append(int(request.url.params["page"]))
        return httpx.Response(
            200,
            json=metadata
            | {"results": [{"url": f"https://images.org/{i}.jpg"} for i in range(items)]},
        )

    events = []
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        with OpenverseAdapter(
            OpenverseConfig(page_size=2, max_pages_per_query=2, request_delay_seconds=0), client
        ) as adapter:
            adapter.observe = events.append
            result = adapter.search("q", "q", raw_api_dir=tmp_path)
    assert seen == list(range(1, expected + 1))
    assert len(result) == expected * items
    assert events[-1]["status"] in {"query_exhausted", "query_completed"}


@respx.mock
def test_page_twelve_full_commits_without_request_thirteen(project):
    config = project.config.model_dump(mode="json")
    config["search"]["seed_queries"] = ["prison cell"]
    config["search"]["openverse"].update(
        page_size=20, max_pages_per_query=20, request_delay_seconds=0
    )
    services.run_stage(project, "configure", configuration=config)

    def handle(request):
        page = int(request.url.params["page"])
        assert page <= 12
        return httpx.Response(
            200,
            json={
                "page": page,
                "page_count": 12,
                "page_size": 20,
                "result_count": 240,
                "results": [{"url": f"https://images.org/{page}-{i}.jpg"} for i in range(20)],
            },
        )

    route = respx.get(OV).mock(side_effect=handle)
    result = services.run_stage(project, "search")
    assert result.status == "succeeded" and route.call_count == 12
    assert result.result["pages_committed"] == 12 and result.result["source_occurrences"] == 240
    assert result.result["completions"][0]["reason"] == "anonymous_result_limit"
    assert "Search limit reached" in discovery_text(result.result, result.status)
    assert services.acquisition_summary(project)["pending"] == 240
    assert len(list(project.paths.raw_api.rglob("*.json"))) == 12


@pytest.mark.parametrize(
    "status,body,kind",
    [
        (
            401,
            {"detail": "pagination depth may not exceed 240 for anonymous requests"},
            "query_exhausted",
        ),
        (401, {"detail": "Invalid authentication credentials"}, "failed"),
        (429, {"detail": "pagination depth may not exceed 240 for anonymous requests"}, "failed"),
    ],
)
@respx.mock
def test_limit_classification_retains_pages_and_provider_isolation(project, status, body, kind):
    cfg = project.config.model_dump(mode="json")
    cfg["search"]["seed_queries"] = ["first", "second"]
    cfg["search"]["openverse"].update(
        page_size=1, max_pages_per_query=2, max_retries=0, request_delay_seconds=0
    )
    cfg["search"]["commons"].update(enabled=True, request_delay_seconds=0, max_retries=0)
    services.run_stage(project, "configure", configuration=cfg)
    route = respx.get(OV).mock(
        side_effect=lambda req: (
            httpx.Response(200, json={"results": [{"url": "https://images.org/a.jpg"}]})
            if req.url.params["page"] == "1"
            else httpx.Response(status, json=body)
        )
    )
    commons = respx.get(COMMONS).mock(
        return_value=httpx.Response(200, json={"batchcomplete": True})
    )
    outcome = services.run_stage(project, "search")
    events = services.discovery_diagnostics(project)["events"]
    terminal = [
        e
        for e in events
        if e.get("http_status") == status and e["status"] in {"failed", "query_exhausted"}
    ]
    assert terminal and terminal[0]["status"] == kind
    assert commons.call_count == 2
    assert route.call_count == (4 if kind == "query_exhausted" else 2)
    assert outcome.status == ("succeeded" if kind == "query_exhausted" else "partial")
    assert any(e["status"] == "page_committed" and e["provider"] == "openverse" for e in events)
    assert json.loads(project.resolve(terminal[0]["raw_response_path"]).read_text()) == body
    if status == 429:
        assert terminal[0]["error"]["kind"] == "rate_limited"


def test_mismatched_returned_page_is_not_committed(tmp_path):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200, json={"page": 2, "results": [{"url": "https://images.org/a.jpg"}]}
            )
        )
    ) as client:
        adapter = OpenverseAdapter(OpenverseConfig(request_delay_seconds=0), client)
        events = []
        adapter.observe = events.append
        assert adapter.search("q", "q", raw_api_dir=tmp_path) == []
    assert events[-1]["error"]["kind"] == "pagination_mismatch"


@respx.mock
def test_retry_recovers_without_rediscovery_or_success_redownload(project):
    a = add(project, "a")
    b = add(project, "b")
    good = respx.get(a[1]).mock(return_value=response())
    bad = respx.get(b[1]).mock(return_value=httpx.Response(403, text="Forbidden"))
    first = services.run_stage(project, "download")
    assert first.status == "partial" and first.result == (1, 1)
    before = records(project)
    failures = [r for r in before if r["status"] == "failed"]
    assert len(failures) == 1
    digest = (project.paths.images_original / "a.png").read_bytes()
    assert services.run_stage(project, "download").result == (0, 0)
    conn = init_db(project.paths.db_path)
    assert select_downloads(conn, retry_failed=True) == [(b[0], b[1])]
    conn.close()
    bad.mock(return_value=response())
    retry = services.run_stage(project, "retry-downloads")
    assert retry.result == (1, 0) and good.call_count == 1 and bad.call_count == 2
    after = records(project)
    assert after[: len(before)] == before
    assert after[-1]["operation_id"] == retry.operation_id
    assert (project.paths.images_original / "a.png").read_bytes() == digest
    summary = services.acquisition_summary(project)
    assert summary["successful_records"] == 2 and summary["available_images"] == 1
    assert summary["shared_image_records"] == 1 and summary["failed_downloads"] == 0
    assert services.run_stage(project, "retry-downloads").result == (0, 0)
    assert services.open_project(project.root).project_id == project.project_id


@respx.mock
def test_retry_failure_appends_acquisition_history(project):
    a = add(project)
    route = respx.get(a[1]).mock(return_value=httpx.Response(403, text="Forbidden"))
    assert services.run_stage(project, "download").status == "failed"
    first = records(project)
    assert services.run_stage(project, "retry-downloads").status == "failed"
    after = records(project)
    assert after[:1] == first and len(after) == 2 and route.call_count == 2
    assert after[0]["acquisition_id"] != after[1]["acquisition_id"]
    assert [json.loads(r["record_json"])["http_status"] for r in after] == [403, 403]


@pytest.mark.parametrize(
    "scope,chosen",
    [
        ({"provider": "commons"}, ["a", "c"]),
        ({"query": "prison"}, ["a", "b"]),
        ({"provider": "commons", "query": "church"}, ["c"]),
        ({"provider": "other"}, []),
        ({"candidate_ids": []}, []),
    ],
)
@respx.mock
def test_retry_scopes_intersect_without_affecting_other_records(project, scope, chosen):
    rows = [
        add(project, "a", "commons", "prison"),
        add(project, "b", "openverse", "prison"),
        add(project, "c", "commons", "church"),
    ]
    routes = {r[0]: respx.get(r[1]).mock(return_value=httpx.Response(403)) for r in rows}
    services.run_stage(project, "download")
    before = records(project)
    for route in routes.values():
        route.mock(return_value=response())
    result = services.run_stage(project, "retry-downloads", **scope)
    assert result.result == (len(chosen), 0)
    assert records(project)[: len(before)] == before
    assert {key: route.call_count for key, route in routes.items()} == {
        key: 2 if key in chosen else 1 for key in routes
    }


@respx.mock
def test_provider_and_query_must_match_same_source(project):
    a = add(project, "a", "openverse", "prison")
    add(project, "alias", "commons", "church", a[1])
    route = respx.get(a[1]).mock(return_value=httpx.Response(403))
    services.run_stage(project, "download")
    assert services.run_stage(
        project, "retry-downloads", provider="commons", query="prison"
    ).result == (0, 0)
    route.mock(return_value=response())
    assert services.run_stage(project, "retry-downloads", candidate_ids=[a[2]]).result == (1, 0)
    assert route.call_count == 2


@respx.mock
def test_commons_contact_and_source_ids_reach_acquisition_after_shared_url(project):
    a = add(project, "a", "openverse", url="https://upload.wikimedia.org/a.png")
    add(project, "alias", "commons", url=a[1])
    route = respx.get(a[1]).mock(return_value=response())
    result = services.run_stage(project, "download")
    assert result.status == "succeeded"
    assert (
        route.calls[0].request.headers["user-agent"]
        == "StimulusHarvesterBot/0.2 (https://example.org/research)"
    )
    record = json.loads(records(project)[0]["record_json"])
    assert record["providers"] == ["commons", "openverse"] and len(record["source_ids"]) == 2
    assert record["attempts"][0]["http_status"] == 200


@pytest.mark.parametrize("status,expected_calls", [(403, 1), (401, 1), (429, 3), (503, 3)])
def test_bounded_status_retry_and_safe_failure_evidence(tmp_path, status, expected_calls):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            text="Forbidden secret_token=user-private@example.org " + ("x" * 10000),
            headers={"Set-Cookie": "secret", "Content-Type": "text/html", "Retry-After": "0"},
        )

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        record = download_one(
            client,
            "a",
            "https://images.org/a",
            tmp_path,
            DownloadConfig(max_retries=2, retry_backoff_seconds=0, request_delay_seconds=0),
        )
    assert len(requests) == record.attempt_count == expected_calls
    assert record.http_status == status and record.status.value == "failed"
    encoded = record.model_dump_json()
    assert (
        "user-private" not in encoded
        and "secret_token" not in encoded
        and "Set-Cookie" not in encoded
    )
    for entry in record.attempts:
        assert entry["response_diagnostic"]["prefix_bytes"] == 4096
        assert entry["response_diagnostic"]["truncated"]
        assert not entry["response_diagnostic"]["body_retained"]


@respx.mock
def test_long_retry_after_defers_host_and_manual_retries(project):
    a = add(project, "a")
    b = add(project, "b")
    route = respx.get(a[1]).mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    forbidden = respx.get(b[1]).mock(return_value=response())
    first = services.run_stage(project, "download")
    assert first.result == (0, 2) and route.call_count == 1 and forbidden.call_count == 0
    assert [json.loads(r["record_json"])["attempt_count"] for r in records(project)] == [1, 0]
    assert services.run_stage(project, "retry-downloads").result == (0, 0)
    summary = services.acquisition_summary(project)
    assert (
        summary["pending"] == 0
        and summary["deferred_failures"] == 2
        and summary["retry_available"] == 0
    )


def test_transport_retry_then_success_keeps_each_request(tmp_path):
    count = 0

    def handle(request):
        nonlocal count
        count += 1
        if count == 1:
            raise httpx.ReadTimeout("sensitive request text")
        return response()

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        record = download_one(
            client,
            "a",
            "https://images.org/a",
            tmp_path,
            DownloadConfig(max_retries=1, retry_backoff_seconds=0, request_delay_seconds=0),
        )
    assert record.status.value == "success" and record.attempt_count == 2
    assert [r["outcome"] for r in record.attempts] == ["failed", "success"]
    assert "sensitive request text" not in record.model_dump_json()


@pytest.mark.parametrize("bad_bytes", [b"<html>not an image</html>", b""])
def test_unreadable_bytes_fail_without_repeated_denial_or_files(tmp_path, bad_bytes):
    with httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=bad_bytes))
    ) as client:
        record = download_one(
            client, "a", "https://images.org/a", tmp_path, DownloadConfig(request_delay_seconds=0)
        )
    assert (
        record.status.value == "failed" and record.http_status == 200 and record.attempt_count == 1
    )
    assert not list(tmp_path.iterdir())


@respx.mock
def test_accounting_overlap_pending_missing_and_exact_bytes(project):
    a = add(project, "a")
    b = add(project, "b")
    c = add(project, "c")
    add(project, "alias", "commons", url=a[1])
    respx.get(a[1]).mock(return_value=response())
    respx.get(b[1]).mock(return_value=response())
    respx.get(c[1]).mock(return_value=httpx.Response(403))
    services.run_stage(project, "download")
    add(project, "pending")
    summary = services.acquisition_summary(project)
    assert (
        summary["found_results"],
        summary["successful_records"],
        summary["failed_downloads"],
        summary["pending"],
    ) == (4, 2, 1, 1)
    assert summary["available_images"] == 1 and summary["shared_image_records"] == 1
    assert summary["providers"]["commons"]["found_results"] == 1
    assert sum(r["found_results"] for r in summary["providers"].values()) == 5
    assert "1 failed downloads" in format_acquisition_summary(summary)
    (project.paths.images_original / "a.png").unlink()
    summary = services.acquisition_summary(project)
    assert summary["unavailable_successful_records"] == 1 and summary["available_images"] == 1
    assert (
        summary["successful_records"] + summary["failed_downloads"] + summary["pending"]
        == summary["found_results"]
    )


@respx.mock
def test_cli_retry_and_summary_share_service_contract(project):
    a = add(project)
    route = respx.get(a[1]).mock(return_value=httpx.Response(403))
    runner = CliRunner()
    first = runner.invoke(cli, ["download", "--project", str(project.root)])
    assert first.exit_code == 1 and "1 failed downloads" in first.output
    route.mock(return_value=response())
    result = runner.invoke(
        cli,
        [
            "retry-downloads",
            "--project",
            str(project.root),
            "--provider",
            "openverse",
            "--query",
            "q",
            "--candidate-id",
            a[2],
        ],
    )
    assert result.exit_code == 0, result.output
    assert "Retried: 1 succeeded, 0 failed" in result.output
    report = runner.invoke(cli, ["acquisition-report", "--project", str(project.root), "--json"])
    assert report.exit_code == 0
    assert json.loads(report.output) == services.acquisition_summary(project)
    assert len(records(project)) == 2 and route.call_count == 2


def test_retry_after_date_and_invalid_values():
    from email.utils import format_datetime

    delay, deferred = retry_delay(format_datetime(utcnow() + timedelta(hours=1)), 2, 30)
    assert delay > 3500 and deferred
    assert retry_delay("invalid", 2, 30) == (2, False)
    assert retry_delay("NaN", 2, 30) == (2, False)


@respx.mock
def test_new_pending_image_respects_saved_host_wait(project):
    a = add(project, "a")
    respx.get(a[1]).mock(return_value=httpx.Response(429, headers={"Retry-After": "3600"}))
    services.run_stage(project, "download")
    b = add(project, "b")
    route = respx.get(b[1]).mock(return_value=response())
    assert services.run_stage(project, "download").result == (0, 0)
    summary = services.acquisition_summary(project)
    assert summary["pending"] == 1 and summary["pending_deferred"] == 1
    assert route.call_count == 0 and len(records(project)) == 1


@pytest.mark.parametrize("same", [True, False])
def test_existing_original_is_not_overwritten(tmp_path, same):
    destination = tmp_path / "a.png"
    original = png()
    destination.write_bytes(original)
    with httpx.Client(
        transport=httpx.MockTransport(lambda request: response("navy" if same else "red"))
    ) as client:
        record = download_one(
            client, "a", "https://images.org/a", tmp_path, DownloadConfig(request_delay_seconds=0)
        )
    assert record.status.value == ("success" if same else "failed")
    assert destination.read_bytes() == original
    assert len(list(tmp_path.iterdir())) == 1


@respx.mock
def test_existing_legacy_failure_needs_no_repair_to_retry(project):
    from stimulus_harvester.models import DownloadRecord
    from stimulus_harvester.review.db import update_download

    a = add(project)
    conn = init_db(project.paths.db_path)
    update_download(
        conn,
        DownloadRecord(
            local_id=a[0], image_url=a[1], status="failed", error_message="historical HTTP403"
        ),
    )
    old = json.loads(conn.execute("SELECT record_json FROM acquisitions").fetchone()[0])
    for key in ("providers", "source_ids", "attempts", "request_user_agent", "retry_not_before"):
        old.pop(key)
    old_text = json.dumps(old)
    conn.execute("UPDATE acquisitions SET record_json=?", (old_text,))
    conn.commit()
    conn.close()
    respx.get(a[1]).mock(return_value=response())
    assert services.run_stage(project, "retry-downloads").result == (1, 0)
    assert records(project)[0]["record_json"] == old_text
    assert len(records(project)) == 2

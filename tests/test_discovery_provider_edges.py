"""Provider boundary conditions and a controlled Commons incremental-coverage fixture."""

import json

import httpx
import pytest
import respx

from stimulus_harvester import services
from stimulus_harvester.config import HarvesterConfig


def project_at(path, **search):
    policy = {"request_delay_seconds": 0, "max_retries": 0}
    settings = {
        "seed_queries": ["q"],
        "query_expansion": {"enabled": False},
        "openverse": {"enabled": False},
        "commons": {"enabled": True, "contact": "https://example.org/research", **policy},
    }
    settings.update(search)
    return services.create_project(
        path,
        HarvesterConfig.model_validate(
            {
                "run_name": "edges",
                "search": settings,
                "clip": {"positive_prompts": ["p"], "negative_prompts": ["n"]},
            }
        ),
    )


@pytest.mark.parametrize(
    "payload,kind",
    [
        ({"query": []}, "invalid_shape"),
        ({"query": {"pages": []}, "continue": {"unknown": "token"}}, "invalid_continuation"),
        ({"query": {"pages": [{"pageid": 42}]}}, "normalization_error"),
        (
            {
                "query": {
                    "pages": [
                        {"imageinfo": [{"mime": "video/ogg", "url": "https://media.org/a.ogg"}]}
                    ]
                }
            },
            "normalization_error",
        ),
    ],
)
@respx.mock
def test_commons_bad_shapes_preserve_raw_and_structured_issue(tmp_path, payload, kind):
    respx.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json=payload)
    )
    project = project_at(tmp_path / "p")
    result = services.run_stage(project, "search")
    assert result.status in {"failed", "partial"}
    issue = result.result["issues"][0]
    assert issue["error"]["kind"] == kind
    assert json.loads(project.resolve(issue["raw_response_path"]).read_text()) == payload


@respx.mock
def test_commons_repeated_continuation_stops(tmp_path):
    route = respx.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(
            200,
            json={"query": {"pages": []}, "continue": {"continue": "gsroffset||", "gsroffset": 5}},
        )
    )
    project = project_at(tmp_path / "p")
    result = services.run_stage(project, "search")
    assert result.status == "partial" and route.call_count == 2
    assert result.result["issues"][0]["error"]["kind"] == "repeated_continuation"


@respx.mock
def test_commons_incremental_fixture_is_descriptive_not_a_live_claim(tmp_path):
    common_url, unique_url = "https://media.org/common.jpg", "https://media.org/commons-only.jpg"
    respx.get("https://api.openverse.org/v1/images/").mock(
        return_value=httpx.Response(200, json={"results": [{"url": common_url}]})
    )
    pages = [
        {"pageid": i, "title": f"File:{i}.jpg", "imageinfo": [{"url": url, "mime": "image/jpeg"}]}
        for i, url in enumerate([common_url, unique_url], 1)
    ]
    respx.get("https://commons.wikimedia.org/w/api.php").mock(
        return_value=httpx.Response(200, json={"query": {"pages": pages}})
    )
    project = project_at(
        tmp_path / "p", openverse={"enabled": True, "request_delay_seconds": 0, "max_retries": 0}
    )
    assert services.run_stage(project, "search").status == "succeeded"
    report = services.discovery_diagnostics(project, provider_order=["openverse", "commons"])[
        "coverage"
    ]
    assert report["providers"]["commons"]["unique_urls"] == 2
    assert report["providers"]["commons"]["incremental_unique_urls"] == 1
    assert report["pairwise_overlap"][0]["shared_urls"] == 1
    assert report["providers"]["commons"]["candidate_count_without_acquired_asset"] == 2
    reverse = services.discovery_diagnostics(project, provider_order=["commons", "openverse"])[
        "coverage"
    ]
    assert reverse["providers"]["commons"]["incremental_unique_urls"] == 2
    assert reverse["providers"]["openverse"]["incremental_unique_urls"] == 0
    assert (
        reverse["providers"]["commons"]["exclusive_urls"]
        == report["providers"]["commons"]["exclusive_urls"]
        == 1
    )


@respx.mock
def test_empty_query_plan_is_explicit_failure(tmp_path):
    project = project_at(tmp_path / "p", seed_queries=[])
    result = services.run_stage(project, "search")
    assert result.status == "failed"
    assert result.result["issues"][0]["error"]["kind"] == "setup_error"
    assert not respx.calls


@respx.mock
def test_openverse_late_failure_commits_earlier_page(tmp_path):
    respx.get("https://api.openverse.org/v1/images/").mock(
        side_effect=[
            httpx.Response(200, json={"results": [{"url": "https://media.org/a.jpg"}]}),
            httpx.Response(503),
        ]
    )
    project = project_at(
        tmp_path / "p",
        commons={"enabled": False},
        openverse={"enabled": True, "page_size": 1, "max_retries": 0, "request_delay_seconds": 0},
    )
    result = services.run_stage(project, "search")
    assert result.status == "partial" and result.result["source_occurrences"] == 1
    assert services.discovery_diagnostics(project)["coverage"]["total_unique_urls"] == 1

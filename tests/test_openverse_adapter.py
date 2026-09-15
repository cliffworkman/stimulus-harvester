from __future__ import annotations

from pathlib import Path

import httpx
import respx

from stimulus_harvester.config import OpenverseConfig
from stimulus_harvester.search.openverse import OpenverseAdapter

SAMPLE_PAGE = {
    "result_count": 2,
    "page_count": 1,
    "results": [
        {
            "id": "abc123",
            "title": "Cell interior",
            "foreign_landing_url": "https://example.com/photos/cell-interior",
            "url": "https://example.com/images/cell-interior.jpg",
            "creator": "Jane Doe",
            "license": "by",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "provider": "flickr",
            "tags": [{"name": "prison"}, {"name": "cell"}],
            "thumbnail": "https://example.com/thumbs/cell-interior.jpg",
            "width": 1024,
            "height": 768,
        },
        {
            # deliberately missing most optional fields
            "id": "def456",
            "url": "https://example.com/images/minimal.jpg",
        },
    ],
}


@respx.mock
def test_openverse_search_normalizes_results(tmp_path: Path) -> None:
    config = OpenverseConfig(page_size=20, max_pages_per_query=3, request_delay_seconds=0.0)
    route = respx.get("https://api.openverse.org/v1/images/").mock(
        return_value=httpx.Response(200, json=SAMPLE_PAGE)
    )

    adapter = OpenverseAdapter(config)
    raw_dir = tmp_path / "raw_api"
    results = adapter.search("prison cell interior", "prison cell interior", raw_api_dir=raw_dir)
    adapter.close()

    assert route.called
    assert len(results) == 2

    first = results[0]
    assert first.source_backend == "openverse"
    assert first.license == "by"
    assert first.creator == "Jane Doe"
    assert first.image_url == "https://example.com/images/cell-interior.jpg"
    assert first.landing_page_url == "https://example.com/photos/cell-interior"
    assert set(first.tags) == {"prison", "cell"}
    assert first.source_group_id == "example.com|Jane Doe"
    assert first.raw_response_path is not None
    assert Path(first.raw_response_path).exists()

    second = results[1]
    assert second.license is None
    assert second.creator is None
    assert second.landing_page_url == second.image_url
    assert second.source_group_id is None


@respx.mock
def test_openverse_pagination_stops_on_short_page(tmp_path: Path) -> None:
    config = OpenverseConfig(page_size=2, max_pages_per_query=5, request_delay_seconds=0.0)
    page1 = {"results": [{"url": "https://example.com/1.jpg"}, {"url": "https://example.com/2.jpg"}]}
    page2 = {"results": [{"url": "https://example.com/3.jpg"}]}
    route = respx.get("https://api.openverse.org/v1/images/").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )

    adapter = OpenverseAdapter(config)
    results = adapter.search("q", "q", raw_api_dir=tmp_path / "raw")
    adapter.close()

    assert route.call_count == 2
    assert len(results) == 3


@respx.mock
def test_openverse_respects_max_pages_per_query(tmp_path: Path) -> None:
    config = OpenverseConfig(page_size=1, max_pages_per_query=2, request_delay_seconds=0.0)
    always_full_page = {"results": [{"url": "https://example.com/x.jpg"}]}
    route = respx.get("https://api.openverse.org/v1/images/").mock(
        return_value=httpx.Response(200, json=always_full_page)
    )

    adapter = OpenverseAdapter(config)
    adapter.search("q", "q", raw_api_dir=tmp_path / "raw")
    adapter.close()

    assert route.call_count == 2

"""SearXNG JSON image search with bounded pages and explicit capability diagnostics."""

from __future__ import annotations

from urllib.parse import urlparse

from stimulus_harvester.models import SearchResult, make_local_id
from stimulus_harvester.search.http import PagedAdapter, SearchPage, http_url, image_url


class SearxngAdapter(PagedAdapter):
    name = "searxng"

    def __init__(self, config, client=None):
        if not http_url(config.endpoint_url):
            raise ValueError("SearXNG requires a valid HTTP(S) endpoint_url")
        self._init_http(config, client)

    def iter_pages(self, query, seed_query, *, raw_api_dir):
        offset = 0
        for page in range(1, self.config.max_pages_per_query + 1):
            fetched = self.request_page(
                self.config.endpoint_url.rstrip("/") + "/search",
                {
                    "q": query,
                    "format": "json",
                    "categories": self.config.categories,
                    "pageno": page,
                },
                query,
                seed_query,
                page,
                raw_api_dir,
            )
            if fetched is None:
                return
            payload, raw_path, evidence = fetched
            items = payload.get("results")
            if not isinstance(items, list):
                self.emit(
                    "failed",
                    error={
                        "kind": "invalid_shape",
                        "message": "SearXNG JSON lacks a results array",
                    },
                )
                return
            self.emit("capability", capability="json_image_search", available=True)
            engine_errors = payload.get("unresponsive_engines") or []
            if engine_errors:
                self.emit("engine_errors", engine_errors=engine_errors)
            items = items[: max(0, self.config.max_results_per_query - offset)]
            results = self.normalize_items(items, query, seed_query, raw_path, page, offset)
            yield SearchPage(results, evidence)
            offset += len(items)
            if not items or offset >= self.config.max_results_per_query:
                return

    def _normalize(self, item, *, query, seed_query, raw_path):
        # A source-page URL is never silently treated as a download URL.
        direct = image_url(item.get("img_src"))
        landing = item.get("url") or direct
        domain = urlparse(landing).netloc if http_url(landing) else urlparse(direct).netloc

        def text(key):
            return item[key] if isinstance(item.get(key), str) else None

        return SearchResult(
            local_id=make_local_id(self.name, direct),
            source_backend=self.name,
            search_query=query,
            seed_query=seed_query,
            image_url=direct,
            landing_page_url=landing,
            title=text("title"),
            creator=text("author") or text("creator"),
            license=text("license"),
            license_url=text("license_url"),
            external_id=str(item["id"]) if item.get("id") is not None else None,
            source_domain=domain,
            source_group_id=f"{domain}|{item.get('engine', '')}",
            thumbnail_url=text("thumbnail_src") or text("thumbnail"),
            width=item.get("width"),
            height=item.get("height"),
            raw_response_path=str(raw_path),
            provider_metadata={
                "record": item,
                "acquisition_validation": "provider_direct_url_unverified",
            },
        )

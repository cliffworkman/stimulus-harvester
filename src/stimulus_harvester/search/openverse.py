"""Openverse API adapter; successful pages are yielded before fetching later pages."""

from __future__ import annotations

from urllib.parse import urlparse

from stimulus_harvester.models import SearchResult, make_local_id
from stimulus_harvester.search.http import PagedAdapter, SearchPage, http_url, image_url


class OpenverseAdapter(PagedAdapter):
    name = "openverse"

    def __init__(self, config, client=None):
        if not http_url(config.base_url):
            raise ValueError("Openverse requires a valid HTTP(S) base_url")
        self._init_http(config, client)

    def iter_pages(self, query, seed_query, *, raw_api_dir):
        for page in range(1, self.config.max_pages_per_query + 1):
            fetched = self.request_page(
                self.config.base_url,
                {"q": query, "page": page, "page_size": self.config.page_size},
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
                        "message": "Openverse JSON lacks a results array",
                    },
                )
                return
            results = self.normalize_items(
                items, query, seed_query, raw_path, page, (page - 1) * self.config.page_size
            )
            yield SearchPage(results, evidence)
            if len(items) < self.config.page_size:
                return

    def _normalize(self, item, *, query, seed_query, raw_path):
        direct = image_url(item.get("url"))
        landing = item.get("foreign_landing_url") or direct
        domain = urlparse(landing).netloc if http_url(landing) else urlparse(direct).netloc
        creator = item.get("creator")
        tags = [
            t["name"] for t in (item.get("tags") or []) if isinstance(t, dict) and t.get("name")
        ]
        return SearchResult(
            local_id=make_local_id(self.name, direct),
            source_backend=self.name,
            search_query=query,
            seed_query=seed_query,
            external_id=item.get("id"),
            title=item.get("title"),
            creator=creator,
            license=item.get("license"),
            license_url=item.get("license_url"),
            source_domain=domain,
            landing_page_url=landing,
            thumbnail_url=item.get("thumbnail"),
            image_url=direct,
            width=item.get("width"),
            height=item.get("height"),
            tags=tags,
            raw_response_path=str(raw_path),
            source_group_id=f"{domain}|{creator}" if domain and creator else None,
            provider_metadata={
                "record": item,
                "acquisition_validation": "provider_direct_url_unverified",
            },
        )

"""Openverse API adapter; successful pages are yielded before fetching later pages."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from stimulus_harvester.models import SearchResult, make_local_id
from stimulus_harvester.search.http import PagedAdapter, SearchPage, http_url, image_url


class OpenverseAdapter(PagedAdapter):
    name = "openverse"

    def __init__(self, config, client=None):
        if not http_url(config.base_url):
            raise ValueError("Openverse requires a valid HTTP(S) base_url")
        self._init_http(config, client)

    def exhaustion_response(self, status, body):
        if status != 401:
            return None
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeError):
            return None
        detail = payload.get("detail") if isinstance(payload, dict) else None
        match = (
            re.fullmatch(
                r"pagination depth may not exceed (\d+) for anonymous requests\.?",
                detail.strip(),
                re.IGNORECASE,
            )
            if isinstance(detail, str)
            else None
        )
        if match:
            return {
                "reason": "anonymous_result_limit",
                "limit": int(match[1]),
                "message": "Search limit reached for anonymous Openverse access",
            }
        return None

    @staticmethod
    def _count(value):
        # Ignore malformed metadata, including bools and negative/fractional counts.
        return value if type(value) is int and value >= 0 else None

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
            returned_page = self._count(payload.get("page"))
            if returned_page is not None and returned_page != page:
                self.emit(
                    "failed",
                    error={
                        "kind": "pagination_mismatch",
                        "message": "Openverse returned a different page than requested",
                    },
                )
                return
            page_count = self._count(payload.get("page_count"))
            result_count = self._count(payload.get("result_count"))
            page_size = self._count(payload.get("page_size")) or self.config.page_size
            metadata = {
                key: payload[key]
                for key in ("page", "page_size", "page_count", "result_count")
                if key in payload
            }
            evidence = dict(evidence, pagination=metadata)
            results = self.normalize_items(
                items, query, seed_query, raw_path, page, (page - 1) * page_size
            )
            yield SearchPage(results, evidence)
            reached = (page_count is not None and page >= page_count) or (
                result_count is not None and page * page_size >= result_count
            )
            if reached or len(items) < page_size:
                # The official unauthenticated API currently advertises a 240-result
                # boundary. This affects wording only; stopping uses returned metadata.
                anonymous = (
                    reached
                    and result_count == 240
                    and urlparse(self.config.base_url).hostname == "api.openverse.org"
                )
                self.emit(
                    "query_exhausted",
                    pagination=metadata,
                    reason="anonymous_result_limit" if anonymous else "results_exhausted",
                    message="Search limit reached for anonymous Openverse access"
                    if anonymous
                    else "Available search results exhausted",
                )
                return
        self.emit(
            "query_completed",
            reason="configured_page_limit",
            message="Configured page limit reached; a new search starts at page 1",
        )

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

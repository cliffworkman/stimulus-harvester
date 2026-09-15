"""Openverse image search adapter.

Openverse (https://openverse.org) indexes openly-licensed and public-domain media and
exposes a free, keyless (rate-limited) JSON API -- a good first source since license
metadata is generally reliable and explicit.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from stimulus_harvester.config import OpenverseConfig
from stimulus_harvester.models import SearchResult, make_local_id
from stimulus_harvester.search.base import SearchAdapter

_USER_AGENT = "stimulus-harvester/0.1 (academic research)"


def _domain(url: str | None) -> str | None:
    if not url:
        return None
    try:
        return urlparse(url).netloc or None
    except ValueError:
        return None


def _slug_for_filename(text: str, max_len: int = 40) -> str:
    ascii_only = "".join(c if c.isalnum() else "-" for c in text.lower())
    slug = "-".join(filter(None, ascii_only.split("-")))[:max_len].strip("-") or "query"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{digest}"


class OpenverseAdapter(SearchAdapter):
    name = "openverse"

    def __init__(self, config: OpenverseConfig, client: httpx.Client | None = None):
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={"User-Agent": _USER_AGENT}, timeout=config.request_timeout_seconds
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "OpenverseAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def search(self, query: str, seed_query: str, *, raw_api_dir: Path) -> list[SearchResult]:
        raw_api_dir.mkdir(parents=True, exist_ok=True)
        results: list[SearchResult] = []
        page = 1
        while page <= self.config.max_pages_per_query:
            payload = self._fetch_page(query, page)
            raw_path = raw_api_dir / f"openverse_{_slug_for_filename(query)}_page{page}.json"
            raw_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

            page_results = payload.get("results", [])
            for item in page_results:
                results.append(
                    self._normalize(item, query=query, seed_query=seed_query, raw_path=raw_path)
                )

            if len(page_results) < self.config.page_size:
                break  # last page
            page += 1
            if page <= self.config.max_pages_per_query:
                time.sleep(self.config.request_delay_seconds)

        return results

    def _fetch_page(self, query: str, page: int) -> dict:
        response = self._client.get(
            self.config.base_url,
            params={"q": query, "page": page, "page_size": self.config.page_size},
        )
        response.raise_for_status()
        return response.json()

    def _normalize(
        self, item: dict, *, query: str, seed_query: str, raw_path: Path
    ) -> SearchResult:
        image_url = item.get("url") or ""
        landing_page_url = item.get("foreign_landing_url") or image_url
        source_domain = _domain(landing_page_url) or _domain(image_url)
        creator = item.get("creator")
        tags = [t.get("name") for t in (item.get("tags") or []) if isinstance(t, dict) and t.get("name")]

        source_group_id = None
        if source_domain and creator:
            source_group_id = f"{source_domain}|{creator}"

        return SearchResult(
            local_id=make_local_id(self.name, image_url),
            source_backend=self.name,
            search_query=query,
            seed_query=seed_query,
            external_id=item.get("id"),
            title=item.get("title"),
            creator=creator,
            license=item.get("license"),
            license_url=item.get("license_url"),
            source_domain=source_domain,
            landing_page_url=landing_page_url,
            thumbnail_url=item.get("thumbnail"),
            image_url=image_url,
            width=item.get("width"),
            height=item.get("height"),
            tags=tags,
            raw_response_path=str(raw_path),
            source_group_id=source_group_id,
            retrieved_at=datetime.now(timezone.utc),
        )

"""Optional SearXNG image search adapter.

Only used if `search.searxng.enabled: true` and an `endpoint_url` is configured.
Many public SearXNG instances disable `format=json` for abuse prevention -- if so,
point this at a self-hosted instance that has it enabled. License metadata is
generally not available from SearXNG results, so `license` will typically be None
(surfaced as "unknown" downstream, never silently dropped).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import httpx

from stimulus_harvester.config import SearxngConfig
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


class SearxngAdapter(SearchAdapter):
    name = "searxng"

    def __init__(self, config: SearxngConfig, client: httpx.Client | None = None):
        if not config.endpoint_url:
            raise ValueError("SearxngAdapter requires search.searxng.endpoint_url to be set")
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={"User-Agent": _USER_AGENT}, timeout=config.request_timeout_seconds
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "SearxngAdapter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def search(self, query: str, seed_query: str, *, raw_api_dir: Path) -> list[SearchResult]:
        raw_api_dir.mkdir(parents=True, exist_ok=True)
        response = self._client.get(
            f"{self.config.endpoint_url.rstrip('/')}/search",
            params={"q": query, "format": "json", "categories": self.config.categories},
        )
        response.raise_for_status()
        payload = response.json()

        raw_path = raw_api_dir / f"searxng_{_slug_for_filename(query)}.json"
        raw_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

        items = (payload.get("results") or [])[: self.config.max_results_per_query]
        return [
            self._normalize(item, query=query, seed_query=seed_query, raw_path=raw_path)
            for item in items
            if item.get("img_src") or item.get("url")
        ]

    def _normalize(
        self, item: dict, *, query: str, seed_query: str, raw_path: Path
    ) -> SearchResult:
        image_url = item.get("img_src") or item.get("url") or ""
        landing_page_url = item.get("url") or image_url
        source_domain = _domain(landing_page_url) or _domain(image_url)
        engine = item.get("engine")
        source_group_id = f"{source_domain}|{engine}" if source_domain and engine else source_domain

        return SearchResult(
            local_id=make_local_id(self.name, image_url),
            source_backend=self.name,
            search_query=query,
            seed_query=seed_query,
            external_id=None,
            title=item.get("title"),
            creator=None,
            license=None,
            license_url=None,
            source_domain=source_domain,
            landing_page_url=landing_page_url,
            thumbnail_url=item.get("thumbnail_src"),
            image_url=image_url,
            width=item.get("width"),
            height=item.get("height"),
            tags=[],
            raw_response_path=str(raw_path),
            source_group_id=source_group_id,
            retrieved_at=datetime.now(timezone.utc),
        )

"""Sequential, bounded API requests and page-oriented discovery evidence."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from stimulus_harvester.models import SearchResult
from stimulus_harvester.search.base import SearchAdapter
from stimulus_harvester.storage import new_id, now


def http_url(value) -> bool:
    if not isinstance(value, str) or any(c.isspace() for c in value):
        return False
    try:
        url = urlparse(value)
        return (
            url.scheme in {"https", "http"}
            and bool(url.hostname)
            and not url.username
            and not url.password
        )
    except ValueError:
        return False


def image_url(value) -> str:
    """Validate an alleged *direct* image URL; bytes are verified on acquisition."""
    if not http_url(value):
        raise ValueError(
            "Missing or unsuitable direct image URL (HTTP(S), host, no credentials required)"
        )
    if Path(urlparse(value).path).suffix.lower() in {".html", ".htm", ".php", ".asp", ".aspx"}:
        raise ValueError(
            "Direct image URL looks like a webpage; no page-to-image fallback is performed"
        )
    return value


@dataclass
class SearchPage:
    results: list[SearchResult]
    evidence: dict


class PagedAdapter(SearchAdapter):
    """Adapters yield pages; legacy callers may still collect a list with search()."""

    def _init_http(self, config, client=None):
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=config.request_timeout_seconds)
        self.observe = lambda event: None
        self.query_id = None
        self.query_lineage = {}
        self.unavailable = False
        self._last_request = None
        self._context = {}

    def close(self):
        if self._owns_client:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def search(self, query, seed_query, *, raw_api_dir):
        return [
            result
            for page in self.iter_pages(query, seed_query, raw_api_dir=raw_api_dir)
            for result in page.results
        ]

    def iter_pages(self, query, seed_query, *, raw_api_dir):
        raise NotImplementedError

    def emit(self, status, **details):
        event = dict(
            self._context,
            event_id=new_id("discovery_event"),
            timestamp=now(),
            status=status,
            **details,
        )
        self.observe(event)
        return event

    def _delay(self, retry_after, attempt):
        delay = min(
            self.config.max_backoff_seconds, self.config.retry_backoff_seconds * 2 ** (attempt - 1)
        )
        if retry_after:
            try:
                try:
                    requested = float(retry_after)
                except ValueError:
                    requested = (
                        parsedate_to_datetime(retry_after) - datetime.now(timezone.utc)
                    ).total_seconds()
                if requested > self.config.max_backoff_seconds:
                    return None  # Defer instead of shortening the server's requested wait.
                delay = max(delay, max(0, requested))
            except (TypeError, ValueError, OverflowError):
                pass
        return max(self.config.request_delay_seconds, delay)

    def request_page(self, url, params, query, seed_query, page, raw_api_dir):
        if not http_url(url):
            raise ValueError(f"Invalid provider endpoint: {url!r}")
        raw_api_dir.mkdir(parents=True, exist_ok=True)
        for attempt in range(1, self.config.max_retries + 2):
            if self._last_request is not None:
                time.sleep(
                    max(
                        0,
                        self.config.request_delay_seconds - (time.monotonic() - self._last_request),
                    )
                )
            attempt_id = new_id("attempt")
            self._context = {
                "provider": self.name,
                "query": query,
                "seed_query": seed_query,
                "query_id": self.query_id,
                "query_lineage": self.query_lineage,
                "page": page,
                "attempt": attempt,
                "attempt_id": attempt_id,
                "request_url": url,
                "request_parameters": params,
            }
            self.emit("started")  # Persist the attempt before sending it.
            raw_path = raw_api_dir / f"{self.name}_{attempt_id}.json"
            response = None
            body = bytearray()
            retryable = False
            error = None
            payload = None
            self._last_request = time.monotonic()
            try:
                with self._client.stream(
                    "GET",
                    url,
                    params=params,
                    headers={"User-Agent": self.config.user_agent, "Accept": "application/json"},
                    timeout=self.config.request_timeout_seconds,
                    follow_redirects=False,
                ) as response:
                    for chunk in response.iter_bytes():
                        remaining = self.config.max_response_bytes - len(body)
                        body.extend(chunk[:remaining])
                        if len(chunk) > remaining:
                            error = {
                                "kind": "response_too_large",
                                "message": "Response body truncated at configured limit",
                            }
                            break
                raw_path.write_bytes(body)
                self._context["raw_response_path"] = str(raw_path)
                self._context["http_status"] = response.status_code
                headers = {
                    key: response.headers[key]
                    for key in ("content-type", "retry-after", "etag", "last-modified")
                    if key in response.headers
                }
                raw_path.with_suffix(".http").write_text(
                    json.dumps(
                        {
                            "url": str(response.url),
                            "headers": headers,
                            "body_representation": "HTTP content decoded by httpx; not transport/compression bytes",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                if error is None and response.status_code != 200:
                    kind = "rate_limited" if response.status_code == 429 else "http_error"
                    message = f"HTTP {response.status_code}"
                    if self.name == "searxng" and response.status_code == 403:
                        kind = "json_access_refused"
                        message += "; JSON may be disabled, or access denied; check instance search.formats and access policy"
                        lower = bytes(body).lower()
                        if b"json" in lower and any(
                            word in lower
                            for word in (b"disabled", b"not enabled", b"invalid format")
                        ):
                            kind = "json_disabled"
                            message = "Endpoint explicitly reports JSON output disabled or invalid"
                    error = {"kind": kind, "message": message}
                    retryable = response.status_code in {429, 500, 502, 503, 504}
                    if response.status_code in {401, 403, 429}:
                        self.unavailable = True
                if error is None:
                    try:
                        payload = json.loads(body)
                        if not isinstance(payload, dict):
                            raise ValueError("API response must be a JSON object")
                    except (ValueError, UnicodeError) as exc:
                        error = {"kind": "malformed_json", "message": str(exc)}
                if error is None and payload.get("error"):
                    error = {
                        "kind": "api_error",
                        "message": str(payload["error"]),
                        "provider_error": payload["error"],
                    }
                    code = (
                        payload["error"].get("code") if isinstance(payload["error"], dict) else None
                    )
                    retryable = code in {"maxlag", "ratelimited", "readonly"}
                    if retryable:
                        self.unavailable = True
            except httpx.HTTPError as exc:
                if body:
                    raw_path.write_bytes(body)
                    self._context["raw_response_path"] = str(raw_path)
                error = {
                    "kind": "timeout"
                    if isinstance(exc, httpx.TimeoutException)
                    else "transport_error",
                    "message": str(exc),
                }
                retryable = True
            if error is None:
                self.unavailable = False
                event = self.emit("response_received")
                return payload, raw_path, event
            delay = self._delay(
                response.headers.get("retry-after") if response is not None else None, attempt
            )
            will_retry = retryable and attempt <= self.config.max_retries and delay is not None
            self.emit(
                "failed",
                error=error,
                will_retry=will_retry,
                retry_delay_seconds=delay if will_retry else None,
                deferred=retryable and delay is None,
            )
            if not will_retry:
                if retryable:
                    self.unavailable = True
                return None
            time.sleep(delay)
        return None

    def normalize_items(self, items, query, seed_query, raw_path, page, offset):
        results = []
        for order, item in enumerate(items, 1):
            try:
                if not isinstance(item, dict):
                    raise ValueError("Result is not an object")
                result = self._normalize(
                    item, query=query, seed_query=seed_query, raw_path=raw_path
                )
                result = result.model_copy(
                    update={
                        "page": page,
                        "rank": offset + order,
                        "result_order": order,
                        "query_lineage": self.query_lineage,
                        "discovery_attempt_id": self._context["attempt_id"],
                        "query_id": self.query_id,
                    }
                )
                results.append(result)
            except (ValueError, TypeError, KeyError, AttributeError) as exc:
                self.emit(
                    "item_rejected",
                    rank=offset + order,
                    result_order=order,
                    error={"kind": "normalization_error", "message": str(exc)},
                    provider_item=item,
                )
        return results

"""Bounded image acquisition with per-request evidence and respectful host pacing."""

from __future__ import annotations

import concurrent.futures
import hashlib
import math
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx

from stimulus_harvester.config import DownloadConfig
from stimulus_harvester.download.integrity import probe_image, sha256_of_file
from stimulus_harvester.download.policy import identified_stream
from stimulus_harvester.models import DownloadRecord, DownloadStatus, utcnow
from stimulus_harvester.storage import new_id

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _extension_for(content_type, url):
    # Untrusted URL suffixes never become executable/HTML output filenames.
    return _EXT_BY_CONTENT_TYPE.get((content_type or "").split(";")[0].lower(), ".bin")


def retry_delay(value, base, maximum):
    """Return delay and whether a server-requested wait exceeds this operation's budget."""
    requested = 0.0
    if value:
        try:
            try:
                requested = float(value)
            except ValueError:
                requested = (parsedate_to_datetime(value) - utcnow()).total_seconds()
            if not math.isfinite(requested):
                requested = 0.0
        except (ValueError, TypeError, OverflowError):
            requested = 0.0
    return max(min(base, maximum), max(0.0, requested)), requested > maximum


def safe_response_diagnostic(response):
    """Never persist arbitrary error HTML, echoed credentials, cookies or IP addresses.

    Read at most 4097 decoded bytes. Retain only recognized generic categories and
    a hash/count of the bounded prefix, not the untrusted text itself.
    """
    prefix = bytearray()
    for chunk in response.iter_bytes(chunk_size=1024):
        prefix.extend(chunk[: 4097 - len(prefix)])
        if len(prefix) >= 4097:
            break
    lower = bytes(prefix[:4096]).lower()
    category = "Response text withheld; inspect status and selected headers"
    if any(s in lower for s in (b"rate limit", b"too many requests", b"ratelimit")):
        category = "Response indicates request throttling"
    elif any(s in lower for s in (b"access denied", b"forbidden", b"permission denied")):
        category = "Response indicates access denied"
    return {
        "summary": category,
        "prefix_bytes": len(lower),
        "prefix_sha256": hashlib.sha256(prefix[:4096]).hexdigest(),
        "truncated": len(prefix) > 4096,
        "body_retained": False,
    }


class HostPacing:
    """One serial worker per original host; other hosts may download concurrently."""

    def __init__(self, delay):
        self.delay = delay
        self.last_request = None
        self.not_before = None

    def wait(self, maximum):
        if self.not_before:
            remaining = (self.not_before - utcnow()).total_seconds()
            if remaining > maximum:
                return self.not_before.isoformat()
            if remaining > 0:
                time.sleep(remaining)
        if self.last_request is not None:
            time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
        self.last_request = time.monotonic()
        return None


def download_one(client, local_id, image_url, dest_dir, config, *, context=None, pacing=None):
    context = context or {}
    pacing = pacing or HostPacing(config.request_delay_seconds)
    agent = context.get("user_agent", config.user_agent)
    attempts = []
    last_status = None
    last_error = None
    not_before = None
    evidence = dict(
        providers=context.get("providers", []),
        source_ids=context.get("source_ids", []),
        request_user_agent=agent,
    )
    for attempt in range(1, config.max_retries + 2):
        not_before = pacing.wait(config.max_backoff_seconds)
        if not_before:
            last_error = "Host requested a longer wait; retry after the recorded time"
            break
        temporary = None
        response = None
        retryable = False
        entry = {
            "attempt_id": new_id("download_request"),
            "attempt": attempt,
            "timestamp": utcnow().isoformat(),
            "http_status": None,
            "response_headers": {},
            "will_retry": False,
        }
        attempts.append(entry)
        last_status = None
        try:
            with identified_stream(client, image_url, config, context, entry) as response:
                evidence["request_user_agent"] = entry["request_hosts"][0]["user_agent"]
                last_status = response.status_code
                entry["http_status"] = last_status
                # Restrict headers, bound lengths, and omit cookies/auth/location data.
                entry["response_headers"] = {
                    key: response.headers[key][:256]
                    for key in ("content-type", "retry-after")
                    if key in response.headers
                }
                if response.is_error or response.is_redirect:
                    entry["response_diagnostic"] = safe_response_diagnostic(response)
                    retryable = last_status in {429, 500, 502, 503, 504}
                    raise ValueError(f"HTTP {last_status}")
                content_type = response.headers.get("content-type")
                temporary = dest_dir / (new_id("download") + ".part")
                size = 0
                with temporary.open("xb") as handle:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > config.max_file_size_bytes:
                            raise ValueError("Image exceeds the configured file size limit")
                        handle.write(chunk)
                dims = probe_image(temporary)
                if dims is None:
                    raise ValueError("Downloaded bytes are not a readable image")
                digest = sha256_of_file(temporary)
                destination = dest_dir / f"{local_id}{_extension_for(content_type, image_url)}"
                if destination.exists():
                    if sha256_of_file(destination) != digest:
                        raise ValueError(
                            "Existing original has different bytes; preserve it for investigation"
                        )
                    temporary.unlink()
                else:
                    os.replace(temporary, destination)
                entry["outcome"] = "success"
                return DownloadRecord(
                    local_id=local_id,
                    image_url=image_url,
                    status=DownloadStatus.SUCCESS,
                    http_status=last_status,
                    file_path=str(destination),
                    sha256=digest,
                    width=dims[0],
                    height=dims[1],
                    file_size_bytes=size,
                    content_type=content_type,
                    attempt_count=len(attempts),
                    attempts=attempts,
                    **evidence,
                )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            if entry.get("request_hosts"):
                evidence["request_user_agent"] = entry["request_hosts"][0]["user_agent"]
            if temporary is not None:
                temporary.unlink(missing_ok=True)
            # HTTP exceptions may echo credential-bearing URLs; do not retain their text.
            if isinstance(exc, httpx.HTTPError):
                retryable = isinstance(exc, httpx.TransportError)
                last_error = type(exc).__name__ + ": image request failed"
            elif isinstance(exc, OSError):
                last_error = type(exc).__name__ + ": local image write failed"
            else:
                last_error = str(exc)
            delay, deferred = retry_delay(
                response.headers.get("retry-after") if response is not None else None,
                config.retry_backoff_seconds * 2 ** (attempt - 1),
                config.max_backoff_seconds,
            )
            will_retry = retryable and attempt <= config.max_retries and not deferred
            # All same-original-host requests respect explicit waits, including later
            # candidates in this operation. Persist the deadline for later manual retries.
            if response is not None and (response.headers.get("retry-after") or last_status == 429):
                try:
                    pacing.not_before = utcnow() + timedelta(seconds=delay)
                except OverflowError:
                    pacing.not_before = datetime.max.replace(tzinfo=timezone.utc)
                not_before = pacing.not_before.isoformat()
            entry.update(
                outcome="failed",
                error=last_error,
                retryable=retryable,
                will_retry=will_retry,
                deferred=deferred,
                retry_delay_seconds=delay if will_retry else None,
                retry_not_before=not_before,
            )
            if not will_retry:
                break
            time.sleep(delay)
    return DownloadRecord(
        local_id=local_id,
        image_url=image_url,
        status=DownloadStatus.FAILED,
        http_status=last_status,
        error_message=last_error,
        attempt_count=len(attempts),
        attempts=attempts,
        retry_not_before=not_before,
        **evidence,
    )


def download_all(candidates, dest_dir: Path, config: DownloadConfig, *, contexts=None):
    """Candidate pairs remain compatible; provider/source context is supplied separately.

    Hosts are grouped to avoid concurrent requests to the same original hostname.
    Redirect destinations are not independently scheduled; this is not a crawler.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    contexts = contexts or {}
    groups = defaultdict(list)
    for local_id, url in candidates:
        groups[urlparse(url).hostname].append((local_id, url))

    def group_download(group):
        pacing = HostPacing(config.request_delay_seconds)
        with httpx.Client(follow_redirects=True) as client:
            return [
                download_one(
                    client,
                    local_id,
                    url,
                    dest_dir,
                    config,
                    context=contexts.get(local_id),
                    pacing=pacing,
                )
                for local_id, url in group
            ]

    records = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=config.concurrency) as pool:
        for group_records in pool.map(group_download, groups.values()):
            records.extend(group_records)
    return records

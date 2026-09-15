"""Threaded image downloader with full per-item provenance.

A small ThreadPoolExecutor-based downloader was chosen over img2dataset: this project
needs a queryable, never-silently-discarded failure row per candidate (original URL,
landing page, sha256, error message) tied into the same manifest/review schema as
every other stage -- a shape img2dataset (built for bulk WebDataset-shard creation)
doesn't provide.
"""

from __future__ import annotations

import concurrent.futures
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from stimulus_harvester.config import DownloadConfig
from stimulus_harvester.download.integrity import probe_image, sha256_of_file
from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.models import DownloadRecord, DownloadStatus, utcnow

logger = get_logger("download")

_EXT_BY_CONTENT_TYPE = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "image/tiff": ".tiff",
}


def _extension_for(content_type: str | None, url: str) -> str:
    url_ext = Path(urlparse(url).path).suffix
    if url_ext and len(url_ext) <= 6:
        return url_ext
    ct = (content_type or "").split(";")[0].strip().lower()
    return _EXT_BY_CONTENT_TYPE.get(ct, ".bin")


def download_one(
    client: httpx.Client, local_id: str, image_url: str, dest_dir: Path, config: DownloadConfig
) -> DownloadRecord:
    last_error: str | None = None
    max_attempts = config.max_retries + 1
    for attempt in range(1, max_attempts + 1):
        dest_path: Path | None = None
        try:
            with client.stream(
                "GET",
                image_url,
                timeout=config.timeout_seconds,
                headers={"User-Agent": config.user_agent},
            ) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type")
                dest_path = dest_dir / f"{local_id}{_extension_for(content_type, image_url)}"
                size = 0
                with dest_path.open("wb") as fh:
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > config.max_file_size_bytes:
                            raise ValueError(
                                f"file exceeds max_file_size_bytes ({config.max_file_size_bytes})"
                            )
                        fh.write(chunk)

            dims = probe_image(dest_path)
            if dims is None:
                dest_path.unlink(missing_ok=True)
                raise ValueError("downloaded file is not a readable image")

            return DownloadRecord(
                local_id=local_id,
                image_url=image_url,
                status=DownloadStatus.SUCCESS,
                http_status=response.status_code,
                file_path=str(dest_path),
                sha256=sha256_of_file(dest_path),
                width=dims[0],
                height=dims[1],
                file_size_bytes=size,
                content_type=content_type,
                attempt_count=attempt,
                downloaded_at=utcnow(),
            )
        except Exception as exc:  # noqa: BLE001 - every failure must be recorded, never crash the batch
            last_error = f"{type(exc).__name__}: {exc}"
            if dest_path is not None:
                dest_path.unlink(missing_ok=True)
            logger.warning("Attempt %d/%d failed for %s: %s", attempt, max_attempts, image_url, last_error)
            if attempt < max_attempts:
                time.sleep(config.retry_backoff_seconds)

    return DownloadRecord(
        local_id=local_id,
        image_url=image_url,
        status=DownloadStatus.FAILED,
        error_message=last_error,
        attempt_count=max_attempts,
        downloaded_at=utcnow(),
    )


def download_all(
    candidates: list[tuple[str, str]], dest_dir: Path, config: DownloadConfig
) -> list[DownloadRecord]:
    """`candidates` is a list of (local_id, image_url) pairs."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    records: list[DownloadRecord] = []
    with httpx.Client(follow_redirects=True) as client:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, config.concurrency)) as pool:
            futures = [
                pool.submit(download_one, client, local_id, url, dest_dir, config)
                for local_id, url in candidates
            ]
            for future in concurrent.futures.as_completed(futures):
                records.append(future.result())
    return records

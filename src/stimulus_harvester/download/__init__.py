"""Download stage orchestration."""

from __future__ import annotations

from pathlib import Path

from stimulus_harvester.download.fetch import download_all
from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.paths import RunPaths, load_run_config
from stimulus_harvester.provenance import mark_stage_complete
from stimulus_harvester.review.db import init_db, list_pending_downloads, update_download

logger = get_logger("download")


def run_download_stage(run_dir: Path, *, concurrency_override: int | None = None) -> tuple[int, int]:
    """Returns (success_count, failure_count)."""
    paths = RunPaths(root=run_dir)
    config = load_run_config(run_dir)
    if concurrency_override is not None:
        config.download.concurrency = concurrency_override
    conn = init_db(paths.db_path)

    candidates = list_pending_downloads(conn)
    logger.info("Downloading %d pending candidate(s)", len(candidates))
    if not candidates:
        conn.close()
        return 0, 0

    records = download_all(candidates, paths.images_original, config.download)

    results_path = paths.manifests / "download_results.jsonl"
    success = failure = 0
    with results_path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(record.model_dump_json() + "\n")
            update_download(conn, record)
            if record.status.value == "success":
                success += 1
            else:
                failure += 1
                logger.warning("Download failed for %s: %s", record.image_url, record.error_message)

    conn.close()
    mark_stage_complete(paths.reproducibility_path, "download")
    logger.info("Download stage complete: %d succeeded, %d failed", success, failure)
    return success, failure

"""Download stage orchestration."""

from __future__ import annotations

from pathlib import Path

from stimulus_harvester.download.fetch import download_all
from stimulus_harvester.download.state import select_downloads
from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.paths import RunPaths, load_run_config
from stimulus_harvester.provenance import mark_stage_complete
from stimulus_harvester.review.db import init_db, update_download

logger = get_logger("download")


def run_download_stage(
    run_dir: Path,
    *,
    concurrency_override: int | None = None,
    retry_failed=False,
    provider=None,
    query=None,
    candidate_ids=None,
) -> tuple[int, int]:
    """Returns (success_count, failure_count)."""
    paths = RunPaths(root=run_dir)
    config = load_run_config(run_dir)
    if concurrency_override is not None:
        config.download = type(config.download).model_validate(
            config.download.model_dump() | {"concurrency": concurrency_override}
        )
    conn = init_db(paths.db_path)

    candidates = select_downloads(
        conn, retry_failed=retry_failed, provider=provider, query=query, candidate_ids=candidate_ids
    )
    logger.info(
        "Downloading %d %s result(s)", len(candidates), "failed" if retry_failed else "pending"
    )
    if not candidates:
        conn.close()
        return 0, 0

    contexts = {}
    try:
        for local_id, _ in candidates:
            sources = conn.execute(
                "SELECT d.source_id,d.provider FROM discoveries d JOIN candidates c USING(candidate_id) WHERE c.primary_local_id=?",
                (local_id,),
            ).fetchall()
            providers = sorted({r["provider"] for r in sources})
            agent = config.download.user_agent
            if "commons" in providers:
                from stimulus_harvester.search.commons import commons_user_agent

                agent = commons_user_agent(config.search.commons.contact)
            contexts[local_id] = dict(
                providers=providers, source_ids=[r["source_id"] for r in sources], user_agent=agent
            )
        records = download_all(
            candidates, paths.images_original, config.download, contexts=contexts
        )
    except BaseException:
        conn.close()
        raise
    from stimulus_harvester.storage import stored_path

    records = [
        record.model_copy(update={"file_path": stored_path(run_dir, record.file_path, legacy=True)})
        for record in records
    ]

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
    mark_stage_complete(
        paths.reproducibility_path, "retry-downloads" if retry_failed else "download"
    )
    logger.info("Download stage complete: %d succeeded, %d failed", success, failure)
    return success, failure

"""Search stage orchestration: expand queries, run enabled backends, persist results."""

from __future__ import annotations

from pathlib import Path

import httpx

from stimulus_harvester.config import HarvesterConfig, config_to_yaml_str
from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.paths import RunPaths, run_paths_for
from stimulus_harvester.provenance import init_reproducibility_manifest, mark_stage_complete
from stimulus_harvester.review.db import init_db, upsert_image_from_search
from stimulus_harvester.search.base import SearchAdapter
from stimulus_harvester.search.openverse import OpenverseAdapter
from stimulus_harvester.search.query_expansion import expand_queries_from_config
from stimulus_harvester.search.searxng import SearxngAdapter

logger = get_logger("search")


def _build_adapters(config: HarvesterConfig) -> list[SearchAdapter]:
    adapters: list[SearchAdapter] = []
    if config.search.openverse.enabled:
        adapters.append(OpenverseAdapter(config.search.openverse))
    if config.search.searxng.enabled:
        adapters.append(SearxngAdapter(config.search.searxng))
    if not adapters:
        raise ValueError("No search backends enabled -- enable at least one in your config.")
    return adapters


def run_search_stage(config: HarvesterConfig, run_id: str, config_path: Path) -> tuple[RunPaths, int]:
    """Run the search stage end to end. Returns (run_paths, total_results_found)."""
    paths = run_paths_for(config.output_root, run_id)
    paths.ensure()
    paths.config_snapshot_path.write_text(config_to_yaml_str(config), encoding="utf-8")

    init_reproducibility_manifest(
        paths.reproducibility_path, run_id=run_id, config=config, config_path=config_path
    )

    conn = init_db(paths.db_path)
    expanded = expand_queries_from_config(config.search.query_expansion, config.search.seed_queries)
    logger.info("Expanded %d seed queries to %d total queries", len(config.search.seed_queries), len(expanded))

    adapters = _build_adapters(config)
    results_path = paths.manifests / "search_results.jsonl"
    total = 0
    duplicate_urls = 0
    # Cross-backend URL dedup: local_id is a hash of (backend, url), so the same URL
    # returned by two different backends would otherwise create two DB rows and two
    # wasted downloads. Every result is still appended to search_results.jsonl
    # regardless (full provenance of everything found), only the DB insert is skipped.
    seen_urls: set[str] = set()

    try:
        with results_path.open("a", encoding="utf-8") as fh:
            for adapter in adapters:
                for seed_label, query in expanded:
                    try:
                        results = adapter.search(query, seed_label, raw_api_dir=paths.raw_api)
                    except httpx.HTTPError as exc:
                        logger.warning("[%s] query %r failed: %s", adapter.name, query, exc)
                        continue
                    for result in results:
                        fh.write(result.model_dump_json() + "\n")
                        if result.image_url in seen_urls:
                            duplicate_urls += 1
                            continue
                        seen_urls.add(result.image_url)
                        upsert_image_from_search(conn, result, run_id)
                    total += len(results)
                    logger.info("[%s] %r -> %d result(s)", adapter.name, query, len(results))
    finally:
        for adapter in adapters:
            adapter.close()
        conn.close()

    mark_stage_complete(paths.reproducibility_path, "search")
    logger.info(
        "Search stage complete: %d total result(s) found, %d duplicate URL(s) skipped, for run %s",
        total,
        duplicate_urls,
        run_id,
    )
    return paths, total

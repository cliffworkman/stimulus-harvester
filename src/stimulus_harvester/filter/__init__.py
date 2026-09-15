"""Filter stage orchestration.

Hard-exclusion here is limited strictly to non-semantic integrity/quality/duplicate
criteria (see FilterReason). No semantic score of any kind may cause exclusion in this
stage or anywhere else in the automated pipeline -- CLIP/VLM outputs are review aids
applied later, never gatekeepers.
"""

from __future__ import annotations

from pathlib import Path

from stimulus_harvester.config import FilterConfig
from stimulus_harvester.filter.duplicates import compute_phash, find_exact_duplicate, find_near_duplicate
from stimulus_harvester.filter.image_quality import check_image_quality
from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.models import FilterReason, ImageQualityResult
from stimulus_harvester.paths import RunPaths, load_run_config
from stimulus_harvester.provenance import mark_stage_complete
from stimulus_harvester.review.db import (
    init_db,
    list_all_known_hashes,
    list_downloaded_for_filter,
    update_filter,
)

logger = get_logger("filter")


def run_filter_stage(run_dir: Path) -> tuple[int, int]:
    """Returns (passed_count, excluded_count)."""
    paths = RunPaths(root=run_dir)
    config = load_run_config(run_dir)
    conn = init_db(paths.db_path)

    pending = list_downloaded_for_filter(conn)
    logger.info("Filtering %d downloaded candidate(s)", len(pending))

    # Seed dedup indexes from images already filtered in a prior run of this stage,
    # so reruns (e.g. after downloading more candidates) stay idempotent and still
    # catch duplicates against the full history of this run.
    seen_sha256: dict[str, str] = {}
    seen_phash: dict[str, str] = {}
    for row in list_all_known_hashes(conn):
        if row["sha256"]:
            seen_sha256.setdefault(row["sha256"], row["local_id"])
        if row["phash"]:
            seen_phash.setdefault(row["local_id"], row["phash"])

    results_path = paths.manifests / "filter_results.jsonl"
    passed = excluded = 0

    with results_path.open("a", encoding="utf-8") as fh:
        for row in pending:
            local_id = row["local_id"]
            file_path = Path(row["file_path"]) if row["file_path"] else None

            if file_path is None or not file_path.exists():
                result = ImageQualityResult(
                    local_id=local_id, passed=False, reasons=[FilterReason.UNREADABLE]
                )
            else:
                result = _evaluate(local_id, file_path, row["sha256"], config.filter, seen_sha256, seen_phash)

            fh.write(result.model_dump_json() + "\n")
            update_filter(conn, result)

            if result.passed:
                passed += 1
                dest = paths.images_filtered / file_path.name  # type: ignore[union-attr]
                if not dest.exists():
                    dest.write_bytes(file_path.read_bytes())  # type: ignore[union-attr]
            else:
                excluded += 1
                logger.info("Excluded %s: %s", local_id, [r.value for r in result.reasons])

    conn.close()
    mark_stage_complete(paths.reproducibility_path, "filter")
    logger.info(
        "Filter stage complete: %d passed, %d excluded (non-semantic reasons only)", passed, excluded
    )
    return passed, excluded


def _evaluate(
    local_id: str,
    file_path: Path,
    sha256: str | None,
    filter_config: FilterConfig,
    seen_sha256: dict[str, str],
    seen_phash: dict[str, str],
) -> ImageQualityResult:
    reasons, width, height, aspect_ratio = check_image_quality(file_path, filter_config)
    phash = None
    duplicate_of: str | None = None

    if FilterReason.UNREADABLE not in reasons:
        phash = compute_phash(file_path, filter_config.phash_size)
        exact = find_exact_duplicate(sha256, seen_sha256)
        if exact is not None:
            reasons.append(FilterReason.EXACT_DUPLICATE_SHA256)
            duplicate_of = exact
        else:
            near = find_near_duplicate(phash, seen_phash, filter_config.phash_hamming_threshold)
            if near is not None:
                reasons.append(FilterReason.NEAR_DUPLICATE_PHASH)
                duplicate_of = near

    result = ImageQualityResult(
        local_id=local_id,
        passed=len(reasons) == 0,
        reasons=reasons,
        width=width,
        height=height,
        aspect_ratio=aspect_ratio,
        sha256=sha256,
        phash=phash,
        duplicate_of_local_id=duplicate_of,
    )

    if result.passed:
        if sha256:
            seen_sha256.setdefault(sha256, local_id)
        if phash:
            seen_phash.setdefault(local_id, phash)

    return result

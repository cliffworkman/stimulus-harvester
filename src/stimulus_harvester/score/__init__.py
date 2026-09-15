"""Scoring stage orchestration: CLIP relevance ranking and optional VLM adjudication.

Both are review aids. Neither excludes a candidate from the run -- low CLIP scores
sort to the bottom of the review queue and VLM fields surface as facets a reviewer can
filter/sort by, but nothing here deletes or hides a row.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from statistics import mean

from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.models import ClipScoreResult, VLMResult
from stimulus_harvester.paths import RunPaths, load_run_config
from stimulus_harvester.provenance import (
    mark_stage_complete,
    record_clip_environment,
    record_vlm_environment,
)
from stimulus_harvester.review.db import (
    init_db,
    list_filter_passed_for_scoring,
    list_images_for_vlm,
    update_clip,
    update_vlm,
)
from stimulus_harvester.score.rubric import compute_composite
from stimulus_harvester.score.vlm import build_backend

logger = get_logger("score")


def run_clip_scoring(run_dir: Path) -> int:
    """Returns number of images scored."""
    from stimulus_harvester.score.clip_ranker import ClipRanker  # lazy: needs [clip] extra

    paths = RunPaths(root=run_dir)
    config = load_run_config(run_dir)
    conn = init_db(paths.db_path)

    pending = list_filter_passed_for_scoring(conn)
    logger.info("CLIP-scoring %d candidate(s)", len(pending))
    if not pending:
        conn.close()
        return 0

    positive_prompts = config.clip.positive_prompts
    negative_prompts = config.clip.negative_prompts

    paths.clip_prompts_path.write_text(
        json.dumps({"positive": positive_prompts, "negative": negative_prompts}, indent=2),
        encoding="utf-8",
    )
    prompt_manifest_hash = hashlib.sha256(paths.clip_prompts_path.read_bytes()).hexdigest()
    record_clip_environment(
        paths.reproducibility_path, clip_config=config.clip, prompt_manifest_hash=prompt_manifest_hash
    )

    ranker = ClipRanker(config.clip.model_name, config.clip.pretrained, config.clip.device)
    text_embeds = ranker.embed_texts(positive_prompts + negative_prompts)

    local_ids = [row["local_id"] for row in pending]
    image_paths = [Path(row["file_path"]) for row in pending]
    image_embeds = ranker.embed_images(image_paths, batch_size=config.clip.batch_size)

    similarities = image_embeds @ text_embeds.T
    n_pos = len(positive_prompts)

    results_path = paths.manifests / "clip_scores.jsonl"
    scored = 0
    with results_path.open("a", encoding="utf-8") as fh:
        for i, local_id in enumerate(local_ids):
            positive_scores = {p: float(similarities[i, j]) for j, p in enumerate(positive_prompts)}
            negative_scores = {
                p: float(similarities[i, n_pos + j]) for j, p in enumerate(negative_prompts)
            }
            raw_score, relevance_score = compute_composite(
                list(positive_scores.values()),
                list(negative_scores.values()),
                temperature=config.clip.squash_temperature,
            )
            result = ClipScoreResult(
                local_id=local_id,
                model_name=config.clip.model_name,
                pretrained=config.clip.pretrained,
                positive_prompt_scores=positive_scores,
                negative_prompt_scores=negative_scores,
                positive_mean=mean(positive_scores.values()),
                negative_mean=mean(negative_scores.values()),
                raw_score=raw_score,
                relevance_score=relevance_score,
            )
            fh.write(result.model_dump_json() + "\n")
            update_clip(conn, result)
            scored += 1

    conn.close()
    mark_stage_complete(paths.reproducibility_path, "score_clip")
    logger.info("CLIP scoring complete: %d image(s) scored", scored)
    return scored


def run_vlm_scoring(run_dir: Path) -> int:
    """Returns number of images evaluated. A no-op if config.vlm.backend == 'noop'
    (though this can still be called explicitly, e.g. via `score --vlm`, and will run
    the NoOpVLM backend, recording status="skipped" for each image)."""
    paths = RunPaths(root=run_dir)
    config = load_run_config(run_dir)
    conn = init_db(paths.db_path)

    limit = config.vlm.max_images_to_evaluate
    pending = list_images_for_vlm(conn, limit)
    logger.info("VLM-adjudicating %d candidate(s) with backend=%s", len(pending), config.vlm.backend)
    if not pending:
        conn.close()
        return 0

    field_schema = [f.model_dump() for f in config.vlm.fields]
    paths.vlm_field_schema_path.write_text(json.dumps(field_schema, indent=2), encoding="utf-8")
    field_schema_hash = hashlib.sha256(paths.vlm_field_schema_path.read_bytes()).hexdigest()

    backend = build_backend(config.vlm)
    record_vlm_environment(
        paths.reproducibility_path,
        backend_name=backend.name,
        model_name=getattr(backend, "model", None),
        field_schema_hash=field_schema_hash,
    )

    results_path = paths.manifests / "vlm_results.jsonl"
    evaluated = 0
    try:
        with results_path.open("a", encoding="utf-8") as fh:
            for row in pending:
                result: VLMResult = backend.evaluate(
                    Path(row["file_path"]), row["local_id"], config.vlm.fields
                )
                fh.write(result.model_dump_json() + "\n")
                update_vlm(conn, result)
                evaluated += 1
                if result.status == "error":
                    logger.warning("VLM error for %s: %s", row["local_id"], result.error_message)
    finally:
        backend.close()
        conn.close()

    mark_stage_complete(paths.reproducibility_path, "score_vlm")
    logger.info("VLM adjudication complete: %d image(s) evaluated", evaluated)
    return evaluated

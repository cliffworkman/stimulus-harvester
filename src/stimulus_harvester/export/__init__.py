"""Export stage orchestration."""

from __future__ import annotations

import json
from pathlib import Path

from stimulus_harvester.export.manifest import build_manifest_rows, filter_rows, write_csv, write_jsonl
from stimulus_harvester.export.package import build_contact_sheet, sync_accepted_images, write_summary
from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.paths import RunPaths, load_run_config
from stimulus_harvester.provenance import mark_stage_complete
from stimulus_harvester.review.db import init_db

logger = get_logger("export")


def export_run(
    run_dir: Path, *, label: str = "accepted", license_filter: str = "all", formats: tuple[str, ...] = ("csv", "jsonl", "html", "summary")
) -> dict[str, str]:
    paths = RunPaths(root=run_dir)
    config = load_run_config(run_dir)
    conn = init_db(paths.db_path)
    run_id = run_dir.name

    all_rows = build_manifest_rows(conn, run_id, config.export.open_license_allowlist)
    from stimulus_harvester.storage import candidate_evidence
    lineage = {row.local_id: candidate_evidence(conn, row.local_id) for row in all_rows}
    conn.close()

    accepted_synced = sync_accepted_images(all_rows, paths.images_accepted, root=run_dir)
    logger.info("Synced %d accepted image(s) into %s", accepted_synced, paths.images_accepted)

    export_rows = filter_rows(all_rows, label=label, license_filter=license_filter)
    logger.info(
        "Exporting %d/%d candidate(s) (label=%s, license_filter=%s)",
        len(export_rows), len(all_rows), label, license_filter,
    )

    if paths.clip_prompts_path.exists():
        prompt_manifest = json.loads(paths.clip_prompts_path.read_text(encoding="utf-8"))
        positive_prompts = prompt_manifest.get("positive", [])
        negative_prompts = prompt_manifest.get("negative", [])
    else:
        positive_prompts = config.clip.positive_prompts
        negative_prompts = config.clip.negative_prompts
    vlm_field_names = [field.name for field in config.vlm.fields]

    paths.exports.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    if "csv" in formats:
        csv_path = paths.exports / f"manifest_{label}.csv"
        write_csv(
            export_rows, csv_path,
            positive_prompts=positive_prompts, negative_prompts=negative_prompts, vlm_field_names=vlm_field_names,
        )
        written["csv"] = str(csv_path)

    if "jsonl" in formats:
        jsonl_path = paths.exports / f"manifest_{label}.jsonl"
        write_jsonl(export_rows, jsonl_path)
        written["jsonl"] = str(jsonl_path)
        lineage_path = paths.exports / f"lineage_{label}.jsonl"
        with lineage_path.open("w", encoding="utf-8") as handle:
            for row in export_rows:
                handle.write(json.dumps(lineage[row.local_id], ensure_ascii=False) + "\n")
        written["lineage"] = str(lineage_path)

    if "html" in formats:
        html_path = paths.exports / "contact_sheet.html"
        build_contact_sheet(
            export_rows, html_path, root=run_dir,
            columns=config.export.contact_sheet_columns, thumbnail_px=config.export.contact_sheet_thumbnail_px,
        )
        written["html"] = str(html_path)

    if "summary" in formats:
        summary_path = paths.exports / "summary.json"
        write_summary(all_rows, summary_path)
        written["summary"] = str(summary_path)

    mark_stage_complete(paths.reproducibility_path, "export")
    logger.info("Export complete: %s", written)
    return written

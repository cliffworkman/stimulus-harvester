"""Build ManifestRow objects from the run DB and write CSV/JSONL exports.

License is preserved and reported for every row regardless of export filter -- the
`--license-filter` CLI flag controls what leaves the pipeline at export time; nothing
upstream ever drops a candidate because of its license.
"""

from __future__ import annotations

import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path

from stimulus_harvester.models import LicenseFlag, ManifestRow, ReviewLabel


def compute_license_flag(license_value: str | None, open_license_allowlist: list[str]) -> LicenseFlag:
    if not license_value or not license_value.strip():
        return LicenseFlag.UNKNOWN
    normalized = license_value.strip().lower()
    allowlist_normalized = {item.strip().lower() for item in open_license_allowlist}
    return LicenseFlag.OPEN if normalized in allowlist_normalized else LicenseFlag.RESTRICTED


def build_manifest_rows(
    conn: sqlite3.Connection, run_id: str, open_license_allowlist: list[str]
) -> list[ManifestRow]:
    db_rows = conn.execute(
        """
        SELECT images.*, reviews.label AS review_label, reviews.notes AS review_notes,
               reviews.scene_cluster_id AS scene_cluster_id
        FROM images LEFT JOIN reviews USING (local_id)
        ORDER BY images.local_id
        """
    ).fetchall()

    rows: list[ManifestRow] = []
    for db_row in db_rows:
        clip_positive = json.loads(db_row["clip_positive_scores"]) if db_row["clip_positive_scores"] else None
        clip_negative = json.loads(db_row["clip_negative_scores"]) if db_row["clip_negative_scores"] else None
        vlm_fields = json.loads(db_row["vlm_fields"]) if db_row["vlm_fields"] else None
        downloaded_at = (
            datetime.fromisoformat(db_row["downloaded_at"]) if db_row["downloaded_at"] else None
        )
        rows.append(
            ManifestRow(
                run_id=run_id,
                local_id=db_row["local_id"],
                source_backend=db_row["source_backend"],
                search_query=db_row["search_query"],
                image_url=db_row["image_url"],
                landing_page_url=db_row["landing_page_url"] or "",
                creator=db_row["creator"],
                license=db_row["license"],
                license_flag=compute_license_flag(db_row["license"], open_license_allowlist),
                source_domain=db_row["source_domain"],
                source_group_id=db_row["source_group_id"],
                scene_cluster_id=db_row["scene_cluster_id"],
                file_path=db_row["file_path"],
                sha256=db_row["sha256"],
                width=db_row["width"],
                height=db_row["height"],
                phash=db_row["phash"],
                duplicate_of_local_id=db_row["duplicate_of_local_id"],
                clip_model_name=db_row["clip_model_name"],
                clip_positive_scores=clip_positive,
                clip_negative_scores=clip_negative,
                clip_raw_score=db_row["clip_raw_score"],
                clip_relevance_score=db_row["clip_relevance_score"],
                final_relevance_score=db_row["clip_relevance_score"],
                vlm_backend_name=db_row["vlm_backend_name"],
                vlm_model_name=db_row["vlm_model_name"],
                vlm_status=db_row["vlm_status"],
                vlm_fields=vlm_fields,
                review_label=ReviewLabel(db_row["review_label"] or "unrated"),
                review_notes=db_row["review_notes"],
                timestamp_downloaded=downloaded_at,
            )
        )
    return rows


def filter_rows(
    rows: list[ManifestRow], *, label: str, license_filter: str
) -> list[ManifestRow]:
    out = rows
    if label != "all":
        out = [r for r in out if r.review_label.value == label]
    if license_filter == "open_only":
        out = [r for r in out if r.license_flag == LicenseFlag.OPEN]
    return out


def _flatten_for_csv(
    row: ManifestRow, positive_prompts: list[str], negative_prompts: list[str], vlm_field_names: list[str]
) -> dict[str, object]:
    flat = row.model_dump(
        mode="json", exclude={"clip_positive_scores", "clip_negative_scores", "vlm_fields"}
    )
    for i, prompt in enumerate(positive_prompts, start=1):
        flat[f"clip_pos_{i}"] = (row.clip_positive_scores or {}).get(prompt)
    for i, prompt in enumerate(negative_prompts, start=1):
        flat[f"clip_neg_{i}"] = (row.clip_negative_scores or {}).get(prompt)
    for name in vlm_field_names:
        value = (row.vlm_fields or {}).get(name)
        flat[f"vlm_{name}"] = json.dumps(value) if isinstance(value, list) else value
    return flat


def write_csv(
    rows: list[ManifestRow],
    path: Path,
    *,
    positive_prompts: list[str],
    negative_prompts: list[str],
    vlm_field_names: list[str],
) -> None:
    base_fields = [
        f for f in ManifestRow.model_fields if f not in {"clip_positive_scores", "clip_negative_scores", "vlm_fields"}
    ]
    fieldnames = (
        base_fields
        + [f"clip_pos_{i}" for i in range(1, len(positive_prompts) + 1)]
        + [f"clip_neg_{i}" for i in range(1, len(negative_prompts) + 1)]
        + [f"vlm_{name}" for name in vlm_field_names]
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(_flatten_for_csv(row, positive_prompts, negative_prompts, vlm_field_names))


def write_jsonl(rows: list[ManifestRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(row.model_dump_json() + "\n")

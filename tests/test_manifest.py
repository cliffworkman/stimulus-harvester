from __future__ import annotations

import csv
from pathlib import Path

from stimulus_harvester.export.manifest import (
    build_manifest_rows,
    compute_license_flag,
    filter_rows,
    write_csv,
    write_jsonl,
)
from stimulus_harvester.models import ClipScoreResult, LicenseFlag, SearchResult
from stimulus_harvester.review.db import init_db, set_review_label, update_clip, upsert_image_from_search

ALLOWLIST = ["cc0", "by"]


def _search_result(local_id: str, image_url: str, license_value: str | None) -> SearchResult:
    return SearchResult(
        local_id=local_id,
        source_backend="openverse",
        search_query="q",
        seed_query="q",
        landing_page_url="https://example.com/land",
        image_url=image_url,
        license=license_value,
    )


def test_license_flag_classification() -> None:
    assert compute_license_flag("CC0", ALLOWLIST) == LicenseFlag.OPEN
    assert compute_license_flag(None, ALLOWLIST) == LicenseFlag.UNKNOWN
    assert compute_license_flag("", ALLOWLIST) == LicenseFlag.UNKNOWN
    assert compute_license_flag("all-rights-reserved", ALLOWLIST) == LicenseFlag.RESTRICTED


def _seeded_db(tmp_path: Path):
    conn = init_db(tmp_path / "state.db")
    upsert_image_from_search(conn, _search_result("a1", "https://example.com/a.jpg", "cc0"), "run1")
    upsert_image_from_search(conn, _search_result("a2", "https://example.com/b.jpg", None), "run1")
    upsert_image_from_search(
        conn, _search_result("a3", "https://example.com/c.jpg", "all-rights-reserved"), "run1"
    )
    update_clip(
        conn,
        ClipScoreResult(
            local_id="a1",
            model_name="ViT-B-32",
            pretrained="laion2b_s34b_b79k",
            positive_prompt_scores={"p": 0.3},
            negative_prompt_scores={"n": 0.1},
            positive_mean=0.3,
            negative_mean=0.1,
            raw_score=0.2,
            relevance_score=0.7,
        ),
    )
    set_review_label(conn, "a1", label="accepted", notes="looks good")
    set_review_label(conn, "a3", label="accepted", notes=None)
    return conn


def test_build_manifest_rows_and_csv_jsonl_roundtrip(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    rows = build_manifest_rows(conn, "run1", ALLOWLIST)
    conn.close()

    by_id = {row.local_id: row for row in rows}
    assert by_id["a1"].license_flag == LicenseFlag.OPEN
    assert by_id["a1"].review_label.value == "accepted"
    assert by_id["a1"].clip_relevance_score == 0.7
    assert by_id["a2"].license_flag == LicenseFlag.UNKNOWN
    assert by_id["a2"].review_label.value == "unrated"
    assert by_id["a3"].license_flag == LicenseFlag.RESTRICTED

    csv_path = tmp_path / "out.csv"
    jsonl_path = tmp_path / "out.jsonl"
    write_csv(rows, csv_path, positive_prompts=["p"], negative_prompts=["n"], vlm_field_names=[])
    write_jsonl(rows, jsonl_path)

    with csv_path.open(encoding="utf-8") as fh:
        csv_rows = list(csv.DictReader(fh))
    assert len(csv_rows) == 3
    a1_csv = next(r for r in csv_rows if r["local_id"] == "a1")
    assert a1_csv["clip_pos_1"] == "0.3"
    assert a1_csv["clip_neg_1"] == "0.1"
    assert a1_csv["license_flag"] == "open"

    jsonl_lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(jsonl_lines) == 3


def test_filter_rows_by_label_and_license(tmp_path: Path) -> None:
    conn = _seeded_db(tmp_path)
    rows = build_manifest_rows(conn, "run1", ALLOWLIST)
    conn.close()

    accepted_all = filter_rows(rows, label="accepted", license_filter="all")
    assert {r.local_id for r in accepted_all} == {"a1", "a3"}

    accepted_open_only = filter_rows(rows, label="accepted", license_filter="open_only")
    assert {r.local_id for r in accepted_open_only} == {"a1"}

    everything = filter_rows(rows, label="all", license_filter="all")
    assert len(everything) == 3

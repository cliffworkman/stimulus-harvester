"""SQLite-backed run state: search/download/filter/score results plus human review.

Plain stdlib `sqlite3`, no ORM -- the schema is meant to be directly inspectable
(`sqlite3 runs/RUN_ID/review/state.db`). `images` accumulates columns as each pipeline
stage runs; `reviews` holds the current human label/notes/scene-cluster-id per image;
`review_events` is an append-only audit log of every review change, never overwritten.

Nothing here hard-codes a research task's vocabulary -- CLIP/VLM columns are either
generic numeric scores or a JSON blob (`vlm_fields`) whose keys are whatever the
run's config declared.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from stimulus_harvester.models import (
    ClipScoreResult,
    DownloadRecord,
    ImageQualityResult,
    ReviewLabel,
    SearchResult,
    VLMResult,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS images (
    local_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    source_backend TEXT NOT NULL,
    search_query TEXT NOT NULL,
    seed_query TEXT,
    external_id TEXT,
    title TEXT,
    image_url TEXT NOT NULL,
    landing_page_url TEXT,
    thumbnail_url TEXT,
    creator TEXT,
    license TEXT,
    license_url TEXT,
    source_domain TEXT,
    source_group_id TEXT,
    tags TEXT,

    download_status TEXT,
    file_path TEXT,
    width INTEGER,
    height INTEGER,
    file_size_bytes INTEGER,
    sha256 TEXT,
    downloaded_at TEXT,
    download_error TEXT,

    filter_passed INTEGER,
    filter_reasons TEXT,
    aspect_ratio REAL,
    phash TEXT,
    duplicate_of_local_id TEXT,

    clip_model_name TEXT,
    clip_pretrained TEXT,
    clip_positive_scores TEXT,
    clip_negative_scores TEXT,
    clip_raw_score REAL,
    clip_relevance_score REAL,

    vlm_backend_name TEXT,
    vlm_model_name TEXT,
    vlm_status TEXT,
    vlm_error_message TEXT,
    vlm_fields TEXT,

    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_images_run_id ON images(run_id);
CREATE INDEX IF NOT EXISTS idx_images_clip_relevance ON images(clip_relevance_score);
CREATE INDEX IF NOT EXISTS idx_images_filter_passed ON images(filter_passed);
CREATE INDEX IF NOT EXISTS idx_images_license ON images(license);
CREATE INDEX IF NOT EXISTS idx_images_source_backend ON images(source_backend);

CREATE TABLE IF NOT EXISTS reviews (
    local_id TEXT PRIMARY KEY REFERENCES images(local_id) ON DELETE CASCADE,
    label TEXT NOT NULL DEFAULT 'unrated'
        CHECK (label IN ('unrated', 'accepted', 'maybe', 'rejected')),
    notes TEXT,
    scene_cluster_id TEXT,
    reviewer_email TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS review_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_id TEXT NOT NULL REFERENCES images(local_id) ON DELETE CASCADE,
    label TEXT NOT NULL,
    notes TEXT,
    scene_cluster_id TEXT,
    reviewer_email TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_review_events_local_id ON review_events(local_id);
"""

_SORT_COLUMNS = {
    "score_desc": "clip_relevance_score DESC NULLS LAST, images.created_at DESC",
    "score_asc": "clip_relevance_score ASC NULLS LAST, images.created_at DESC",
    "recent": "images.created_at DESC",
}


def init_db(db_path: Path) -> sqlite3.Connection:
    from stimulus_harvester.storage import connect_database

    return connect_database(db_path, _SCHEMA)


def upsert_image_from_search(
    conn: sqlite3.Connection,
    result: SearchResult,
    run_id: str,
    *,
    record_source: bool = True,
    commit: bool = True,
) -> str:
    """Keep the first-source legacy projection while appending every source claim.

    A shared URL reuses its existing candidate. Original provider-derived IDs stay
    available as aliases, including those whose URL was historically deduplicated.
    """
    from stimulus_harvester.storage import candidate_for, record_discovery

    original = result
    # Same URL reuses the working candidate; every discovery keeps its own claims.
    existing = conn.execute(
        "SELECT local_id FROM images WHERE local_id=?", (result.local_id,)
    ).fetchone()
    if existing is None:
        existing = conn.execute(
            "SELECT local_id FROM images WHERE image_url=? ORDER BY local_id", (result.image_url,)
        ).fetchone()
    if existing:
        result = result.model_copy(update={"local_id": existing[0]})
    conn.execute(
        """
        INSERT OR IGNORE INTO images (
            local_id, run_id, source_backend, search_query, seed_query, external_id,
            title, image_url, landing_page_url, thumbnail_url, creator, license,
            license_url, source_domain, source_group_id, tags
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.local_id,
            run_id,
            result.source_backend,
            result.search_query,
            result.seed_query,
            result.external_id,
            result.title,
            result.image_url,
            result.landing_page_url,
            result.thumbnail_url,
            result.creator,
            result.license,
            result.license_url,
            result.source_domain,
            result.source_group_id,
            json.dumps(result.tags),
        ),
    )
    conn.execute(
        "INSERT OR IGNORE INTO reviews (local_id, label) VALUES (?, 'unrated')", (result.local_id,)
    )
    candidate_for(conn, result.local_id)
    if record_source:
        record_discovery(conn, original.model_dump(mode="json"), result.local_id)
    if commit:
        conn.commit()
    return result.local_id


def list_pending_downloads(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """local_id/image_url pairs never attempted yet -- makes `download` reruns
    idempotent (already-succeeded or already-failed rows are left alone)."""
    rows = conn.execute(
        "SELECT local_id, image_url FROM images WHERE download_status IS NULL"
    ).fetchall()
    return [(r["local_id"], r["image_url"]) for r in rows]


def list_downloaded_for_filter(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Successfully downloaded images not yet filtered."""
    return conn.execute(
        "SELECT local_id, file_path, sha256 FROM images "
        "WHERE download_status = 'success' AND filter_passed IS NULL"
    ).fetchall()


def list_filter_passed_for_scoring(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Images that passed non-semantic filtering and have no CLIP score yet."""
    return conn.execute(
        "SELECT local_id, file_path FROM images "
        "WHERE filter_passed = 1 AND clip_relevance_score IS NULL"
    ).fetchall()


def list_images_for_vlm(conn: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    """Filter-passed images with no VLM result yet, ranked by CLIP relevance so an
    optional `max_images_to_evaluate` cap covers the most promising candidates first.
    This ordering is purely a triage convenience -- it does not exclude anything."""
    sql = (
        "SELECT local_id, file_path FROM images "
        "WHERE filter_passed = 1 AND file_path IS NOT NULL AND vlm_status IS NULL "
        "ORDER BY clip_relevance_score DESC NULLS LAST"
    )
    if limit is not None:
        sql += " LIMIT ?"
        return conn.execute(sql, (limit,)).fetchall()
    return conn.execute(sql).fetchall()


def list_all_known_hashes(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """sha256/phash of every already-filtered image, for cross-batch duplicate checks
    within this run (filter stage may run multiple times as new images arrive)."""
    return conn.execute(
        "SELECT local_id, sha256, phash FROM images WHERE filter_passed IS NOT NULL"
    ).fetchall()


def update_download(conn: sqlite3.Connection, record: DownloadRecord) -> None:
    from stimulus_harvester.storage import connection_root, record_acquisition, stored_path

    record = record.model_copy(
        update={"file_path": stored_path(connection_root(conn), record.file_path, legacy=True)}
    )
    conn.execute(
        """
        UPDATE images SET
            download_status = ?, file_path = ?, width = ?, height = ?,
            file_size_bytes = ?, sha256 = ?, downloaded_at = ?, download_error = ?,
            updated_at = datetime('now')
        WHERE local_id = ?
        """,
        (
            record.status.value,
            record.file_path,
            record.width,
            record.height,
            record.file_size_bytes,
            record.sha256,
            record.downloaded_at.isoformat(),
            record.error_message,
            record.local_id,
        ),
    )
    record_acquisition(conn, record.model_dump(mode="json"))
    conn.commit()


def update_filter(conn: sqlite3.Connection, result: ImageQualityResult) -> None:
    conn.execute(
        """
        UPDATE images SET
            filter_passed = ?, filter_reasons = ?, aspect_ratio = ?, phash = ?,
            duplicate_of_local_id = ?, updated_at = datetime('now')
        WHERE local_id = ?
        """,
        (
            int(result.passed),
            json.dumps([r.value for r in result.reasons]),
            result.aspect_ratio,
            result.phash,
            result.duplicate_of_local_id,
            result.local_id,
        ),
    )
    conn.commit()


def update_clip(conn: sqlite3.Connection, result: ClipScoreResult) -> None:
    conn.execute(
        """
        UPDATE images SET
            clip_model_name = ?, clip_pretrained = ?, clip_positive_scores = ?,
            clip_negative_scores = ?, clip_raw_score = ?, clip_relevance_score = ?,
            updated_at = datetime('now')
        WHERE local_id = ?
        """,
        (
            result.model_name,
            result.pretrained,
            json.dumps(result.positive_prompt_scores),
            json.dumps(result.negative_prompt_scores),
            result.raw_score,
            result.relevance_score,
            result.local_id,
        ),
    )
    conn.commit()


def update_vlm(conn: sqlite3.Connection, result: VLMResult) -> None:
    conn.execute(
        """
        UPDATE images SET
            vlm_backend_name = ?, vlm_model_name = ?, vlm_status = ?,
            vlm_error_message = ?, vlm_fields = ?, updated_at = datetime('now')
        WHERE local_id = ?
        """,
        (
            result.backend_name,
            result.model_name,
            result.status,
            result.error_message,
            json.dumps(result.fields),
            result.local_id,
        ),
    )
    conn.commit()


def set_review_label(
    conn: sqlite3.Connection,
    local_id: str,
    *,
    label: ReviewLabel | str,
    notes: str | None,
    scene_cluster_id: str | None = None,
    reviewer_email: str | None = None,
    commit: bool = True,
) -> sqlite3.Row:
    label_value = label.value if isinstance(label, ReviewLabel) else label
    conn.execute(
        """
        INSERT INTO reviews (local_id, label, notes, scene_cluster_id, reviewer_email, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(local_id) DO UPDATE SET
            label = excluded.label,
            notes = excluded.notes,
            scene_cluster_id = COALESCE(excluded.scene_cluster_id, reviews.scene_cluster_id),
            reviewer_email = excluded.reviewer_email,
            updated_at = datetime('now')
        """,
        (local_id, label_value, notes, scene_cluster_id, reviewer_email),
    )
    conn.execute(
        """
        INSERT INTO review_events (local_id, label, notes, scene_cluster_id, reviewer_email)
        VALUES (?, ?, ?, ?, ?)
        """,
        (local_id, label_value, notes, scene_cluster_id, reviewer_email),
    )
    if commit:
        conn.commit()
    row = get_image(conn, local_id)
    assert row is not None
    return row


def get_image(conn: sqlite3.Connection, local_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT images.*, reviews.label AS review_label, reviews.notes AS review_notes,
               reviews.scene_cluster_id AS scene_cluster_id,
               reviews.reviewer_email AS review_reviewer_email
        FROM images LEFT JOIN reviews USING (local_id)
        WHERE images.local_id = ?
        """,
        (local_id,),
    ).fetchone()


def get_duplicate_cluster(conn: sqlite3.Connection, local_id: str) -> list[sqlite3.Row]:
    row = conn.execute(
        "SELECT local_id, duplicate_of_local_id FROM images WHERE local_id = ?", (local_id,)
    ).fetchone()
    if row is None:
        return []
    root = row["duplicate_of_local_id"] or row["local_id"]
    return conn.execute(
        """
        SELECT images.*, reviews.label AS review_label
        FROM images LEFT JOIN reviews USING (local_id)
        WHERE images.local_id = ? OR images.duplicate_of_local_id = ?
        ORDER BY images.local_id
        """,
        (root, root),
    ).fetchall()


def query_images(
    conn: sqlite3.Connection,
    *,
    query: str | None = None,
    source: str | None = None,
    license_value: str | None = None,
    status: str | None = None,
    min_score: float | None = None,
    sort: str = "score_desc",
    page: int = 1,
    page_size: int = 60,
) -> tuple[list[sqlite3.Row], int]:
    where: list[str] = []
    params: list[Any] = []

    if query:
        where.append("(images.search_query LIKE ? OR images.title LIKE ?)")
        params.extend([f"%{query}%", f"%{query}%"])
    if source:
        where.append("images.source_backend = ?")
        params.append(source)
    if license_value:
        where.append("images.license = ?")
        params.append(license_value)
    if status:
        where.append("reviews.label = ?")
        params.append(status)
    if min_score is not None:
        where.append("images.clip_relevance_score >= ?")
        params.append(min_score)

    where_sql = f"WHERE {' AND '.join(where)}" if where else ""
    order_sql = _SORT_COLUMNS.get(sort, _SORT_COLUMNS["score_desc"])
    offset = max(page - 1, 0) * page_size

    base_from = "FROM images LEFT JOIN reviews USING (local_id)"
    total = conn.execute(f"SELECT COUNT(*) AS n {base_from} {where_sql}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT images.*, reviews.label AS review_label, reviews.notes AS review_notes,
               reviews.scene_cluster_id AS scene_cluster_id
        {base_from} {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    ).fetchall()
    return rows, total


def summary_counts(conn: sqlite3.Connection) -> dict[str, Any]:
    by_label = {
        r["label"]: r["n"]
        for r in conn.execute(
            "SELECT COALESCE(reviews.label, 'unrated') AS label, COUNT(*) AS n "
            "FROM images LEFT JOIN reviews USING (local_id) GROUP BY label"
        ).fetchall()
    }
    by_source = {
        r["source_backend"]: r["n"]
        for r in conn.execute(
            "SELECT source_backend, COUNT(*) AS n FROM images GROUP BY source_backend"
        ).fetchall()
    }
    by_license = {
        (r["license"] or "unknown"): r["n"]
        for r in conn.execute(
            "SELECT license, COUNT(*) AS n FROM images GROUP BY license"
        ).fetchall()
    }
    total = conn.execute("SELECT COUNT(*) AS n FROM images").fetchone()["n"]
    return {"by_label": by_label, "by_source": by_source, "by_license": by_license, "total": total}

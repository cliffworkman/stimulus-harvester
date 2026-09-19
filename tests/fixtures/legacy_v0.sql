-- Unversioned schema from audited commit 30634d0. Do not update with current schema.

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

"""Core pipeline data models.

These models are deliberately generic: nothing here encodes assumptions about any
particular research task (e.g. prison-cell imagery). Task-specific vocabulary (search
queries, CLIP prompts, VLM observable-property schemas) lives entirely in YAML config
files under `configs/`, not in these classes.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def make_local_id(source_backend: str, image_url: str) -> str:
    """Stable, deterministic key for a candidate, independent of search order."""
    digest = hashlib.sha256(f"{source_backend}:{image_url}".encode("utf-8")).hexdigest()
    return digest[:16]


class SearchResult(BaseModel):
    """Normalized schema shared by every search backend (Openverse, SearXNG, ...)."""

    model_config = ConfigDict(extra="forbid")

    local_id: str
    source_backend: str
    search_query: str
    seed_query: str
    external_id: str | None = None
    title: str | None = None
    creator: str | None = None
    license: str | None = None
    license_url: str | None = None
    source_domain: str | None = None
    landing_page_url: str
    thumbnail_url: str | None = None
    image_url: str
    width: int | None = None
    height: int | None = None
    tags: list[str] = Field(default_factory=list)
    raw_response_path: str | None = None
    # Best-effort, system-derived grouping hint (e.g. same source domain + gallery id).
    # Not authoritative -- a heuristic starting point for a human to notice that several
    # candidates may share one physical scene or photo session, not a claim they do.
    source_group_id: str | None = None
    retrieved_at: datetime = Field(default_factory=utcnow)
    # Optional until the provider reports these; never fabricate legacy ordering.
    rank: int | None = None
    page: int | None = None
    result_order: int | None = None
    query_lineage: dict = Field(default_factory=dict)
    provider_metadata: dict = Field(default_factory=dict)
    discovery_attempt_id: str | None = None
    query_id: str | None = None


class DownloadStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"


class DownloadRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str
    image_url: str
    status: DownloadStatus
    http_status: int | None = None
    file_path: str | None = None
    sha256: str | None = None
    width: int | None = None
    height: int | None = None
    file_size_bytes: int | None = None
    content_type: str | None = None
    error_message: str | None = None
    attempt_count: int = 1
    # Additive JSON evidence; old records remain valid and are never rewritten.
    providers: list[str] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)
    request_user_agent: str | None = None
    attempts: list[dict] = Field(default_factory=list)
    retry_not_before: str | None = None
    downloaded_at: datetime = Field(default_factory=utcnow)


class FilterReason(str, Enum):
    """Non-semantic, purely mechanical reasons a candidate can be hard-excluded.

    Nothing in this pipeline may add a semantic/relevance-based reason here -- CLIP and
    VLM outputs are review aids, never automated exclusion criteria.
    """

    UNREADABLE = "unreadable_file"
    TOO_SMALL = "too_small"
    EXTREME_ASPECT_RATIO = "extreme_aspect_ratio"
    ICON_LIKE = "icon_like"
    # Applied at search-ingestion time (before a DB row/download even exists), not in
    # the filter stage -- see search/__init__.py's cross-backend URL dedup. Kept here
    # for schema completeness / for a future backend that needs it applied later.
    DUPLICATE_URL = "duplicate_url"
    EXACT_DUPLICATE_SHA256 = "exact_duplicate_sha256"
    NEAR_DUPLICATE_PHASH = "near_duplicate_phash"


class ImageQualityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    local_id: str
    passed: bool
    reasons: list[FilterReason] = Field(default_factory=list)
    width: int | None = None
    height: int | None = None
    aspect_ratio: float | None = None
    sha256: str | None = None
    phash: str | None = None
    duplicate_of_local_id: str | None = None
    checked_at: datetime = Field(default_factory=utcnow)


class ClipScoreResult(BaseModel):
    """All component scores are stored, not just the composite -- keeps ranking
    interpretable rather than opaque."""

    model_config = ConfigDict(extra="forbid")

    local_id: str
    model_name: str
    pretrained: str
    positive_prompt_scores: dict[str, float]
    negative_prompt_scores: dict[str, float]
    positive_mean: float
    negative_mean: float
    raw_score: float
    relevance_score: float
    scored_at: datetime = Field(default_factory=utcnow)


VlmFieldValue = bool | float | str | list[str] | None


class VLMResult(BaseModel):
    """Structured VLM adjudication output.

    The set of observable-property fields being asked about is entirely config-driven
    (see `config.VlmFieldSpec`) -- this class only fixes the generic bookkeeping
    envelope, plus the free-form `fields` dict holding whatever task-specific
    properties this run's config declared. The VLM backend is contractually limited to
    observable scene properties; it must never be asked to (and never emits) anything
    about psychological constructs such as "self relevance".
    """

    model_config = ConfigDict(extra="forbid")

    local_id: str
    backend_name: Literal["noop", "ollama"]
    model_name: str | None = None
    status: Literal["ok", "skipped", "error"] = "skipped"
    error_message: str | None = None
    fields: dict[str, VlmFieldValue] = Field(default_factory=dict)
    evaluated_at: datetime = Field(default_factory=utcnow)


class ReviewLabel(str, Enum):
    UNRATED = "unrated"
    ACCEPTED = "accepted"
    MAYBE = "maybe"
    REJECTED = "rejected"


class LicenseFlag(str, Enum):
    OPEN = "open"
    UNKNOWN = "unknown"
    RESTRICTED = "restricted"


class ManifestRow(BaseModel):
    """One flattened row per candidate, as written to CSV/JSONL exports."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    local_id: str
    source_backend: str
    search_query: str
    image_url: str
    landing_page_url: str
    creator: str | None
    license: str | None
    license_flag: LicenseFlag
    source_domain: str | None
    source_group_id: str | None
    scene_cluster_id: str | None
    file_path: str | None
    sha256: str | None
    width: int | None
    height: int | None
    phash: str | None
    duplicate_of_local_id: str | None
    clip_model_name: str | None
    clip_positive_scores: dict[str, float] | None
    clip_negative_scores: dict[str, float] | None
    clip_raw_score: float | None
    clip_relevance_score: float | None
    # Always identical to clip_relevance_score. VLM fields are exposed separately
    # (below) as filterable/sortable facets, never blended into this number -- the
    # ranking stays interpretable and semantic outputs stay advisory, not exclusionary.
    final_relevance_score: float | None
    vlm_backend_name: str | None
    vlm_model_name: str | None
    vlm_status: str | None
    vlm_fields: dict[str, VlmFieldValue] | None
    review_label: ReviewLabel
    review_notes: str | None
    timestamp_downloaded: datetime | None

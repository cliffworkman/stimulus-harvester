"""Config-driven pipeline settings.

Every field here is generic pipeline machinery. Task-specific content -- search
queries, CLIP prompts, the VLM observable-property schema -- lives in the *values*
supplied by a YAML config file (see `configs/prison_inside_cell.yaml`), not in the
shape of these classes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from stimulus_harvester.screening.config import ScreeningConfig
from pydantic import BaseModel, ConfigDict, Field


class ProviderRequestConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_timeout_seconds: float = Field(15.0, gt=0, le=300)
    request_delay_seconds: float = Field(1.0, ge=0, le=60)
    max_retries: int = Field(2, ge=0, le=5)
    retry_backoff_seconds: float = Field(1.0, ge=0, le=60)
    max_backoff_seconds: float = Field(30.0, ge=0, le=60)
    max_response_bytes: int = Field(10_000_000, ge=1024, le=100_000_000)
    user_agent: str = "StimulusHarvester/0.1 (local research discovery)"


class OpenverseConfig(ProviderRequestConfig):
    enabled: bool = True
    base_url: str = "https://api.openverse.org/v1/images/"
    page_size: int = Field(20, ge=1, le=500)
    max_pages_per_query: int = Field(3, ge=1, le=100)


class SearxngConfig(ProviderRequestConfig):
    enabled: bool = False
    endpoint_url: str | None = None
    categories: str = "images"
    max_results_per_query: int = Field(40, ge=1, le=10000)
    max_pages_per_query: int = Field(1, ge=1, le=100)


class CommonsConfig(ProviderRequestConfig):
    enabled: bool = False
    base_url: str = "https://commons.wikimedia.org/w/api.php"
    # Required when enabled: Wikimedia asks for a descriptive agent with operator contact.
    contact: str | None = None
    page_size: int = Field(5, ge=1, le=10)
    max_pages_per_query: int = Field(3, ge=1, le=100)
    maxlag: int = Field(5, ge=1, le=60)
    retry_backoff_seconds: float = Field(5.0, ge=0, le=60)


class QueryExpansionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    max_expanded_queries: int = 60
    synonym_groups: list[list[str]] = Field(default_factory=list)
    phrase_templates: list[str] = Field(default_factory=list)


class SearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    seed_queries: list[str]
    openverse: OpenverseConfig = Field(default_factory=OpenverseConfig)
    searxng: SearxngConfig = Field(default_factory=SearxngConfig)
    commons: CommonsConfig = Field(default_factory=CommonsConfig)
    query_expansion: QueryExpansionConfig = Field(default_factory=QueryExpansionConfig)


class DownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    concurrency: int = 8
    timeout_seconds: float = 20.0
    max_retries: int = 2
    retry_backoff_seconds: float = 2.0
    user_agent: str = (
        "stimulus-harvester/0.1 (academic research; contact: set REVIEWER_EMAIL in .env)"
    )
    max_file_size_bytes: int = 25_000_000


class FilterConfig(BaseModel):
    """All thresholds here are non-semantic/mechanical only -- see FilterReason."""

    model_config = ConfigDict(extra="forbid")

    min_width: int = 400
    min_height: int = 400
    max_aspect_ratio: float = 3.0
    icon_max_dimension: int = 64
    phash_hamming_threshold: int = 8
    phash_size: int = 16


class ClipConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_name: str = "ViT-B-32"
    pretrained: str = "laion2b_s34b_b79k"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    batch_size: int = 16
    squash_temperature: float = 4.0
    positive_prompts: list[str]
    negative_prompts: list[str]


class VlmFieldSpec(BaseModel):
    """One observable-property question the VLM will be asked about an image.

    This is where task-specific adjudication vocabulary lives -- e.g. for the prison
    cell task, fields like `bars_visible` or `camera_inside_cell`. The generic VLM
    backend code never hardcodes these; it only knows how to render this spec into a
    prompt and validate a response against it.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    type: Literal["bool", "float", "str", "list_str"]
    description: str
    required: bool = True


class VlmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backend: Literal["noop", "ollama"] = "noop"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llava:7b"
    timeout_seconds: float = 60.0
    max_retries: int = 2
    max_images_to_evaluate: int | None = None
    fields: list[VlmFieldSpec] = Field(default_factory=list)


class ReviewConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    host: str = "127.0.0.1"
    port: int = 8000
    page_size: int = 60


class ExportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    open_license_allowlist: list[str] = Field(
        default_factory=lambda: ["cc0", "pdm", "by", "by-sa", "by-nc", "by-nc-sa"]
    )
    contact_sheet_columns: int = 6
    contact_sheet_thumbnail_px: int = 256


class HarvesterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_name: str
    output_root: Path = Path("runs")
    search: SearchConfig
    download: DownloadConfig = Field(default_factory=DownloadConfig)
    filter: FilterConfig = Field(default_factory=FilterConfig)
    clip: ClipConfig
    vlm: VlmConfig = Field(default_factory=VlmConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    export: ExportConfig = Field(default_factory=ExportConfig)
    screening: ScreeningConfig = Field(default_factory=ScreeningConfig)


def load_config(path: Path) -> HarvesterConfig:
    """Parse and validate a YAML config file into a HarvesterConfig."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if raw is None:
        raise ValueError(f"Config file is empty: {path}")
    return HarvesterConfig.model_validate(raw)


def config_to_yaml_str(config: HarvesterConfig) -> str:
    """Canonical YAML serialization used for run snapshots and config hashing."""
    data = config.model_dump(mode="json")
    return yaml.safe_dump(data, sort_keys=True)

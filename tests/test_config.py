from __future__ import annotations

from pathlib import Path

from stimulus_harvester.config import HarvesterConfig, load_config

CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "prison_inside_cell.yaml"

EXPECTED_SEED_QUERIES = [
    "inside prison cell looking through bars",
    "inside jail cell looking outward",
    "prison cell interior looking into corridor",
    "jail cell point of view",
    "empty prison cell interior",
    "solitary confinement cell interior",
    "prison cell door from inside",
    "view from inside a prison cell",
]

EXPECTED_VLM_FIELD_NAMES = {
    "is_photograph",
    "likely_prison_environment",
    "camera_inside_cell",
    "looking_out_from_cell",
    "confinement_cues_visible",
    "bars_visible",
    "locked_door_visible",
    "bed_or_bunk_visible",
    "toilet_or_sink_visible",
    "corridor_or_outside_visible",
    "people_present",
    "faces_present",
    "text_or_watermark_present",
    "obvious_non_prison_confounds",
    "image_quality_adequate",
    "confidence",
    "brief_scene_description",
}


def test_shipped_config_parses():
    config = load_config(CONFIG_PATH)
    assert config.run_name == "prison_inside_cell"


def test_seed_queries_match_spec():
    config = load_config(CONFIG_PATH)
    assert config.search.seed_queries == EXPECTED_SEED_QUERIES


def test_clip_prompt_counts_match_spec():
    config = load_config(CONFIG_PATH)
    assert len(config.clip.positive_prompts) == 5
    assert len(config.clip.negative_prompts) == 8


def test_vlm_field_schema_is_complete_and_observable_only():
    config = load_config(CONFIG_PATH)
    names = {field.name for field in config.vlm.fields}
    assert names == EXPECTED_VLM_FIELD_NAMES

    joined = " ".join(names).lower()
    for forbidden in ("self_relevance", "self-relevance", "selfrelevance", "emotion", "valence"):
        assert forbidden not in joined


def test_vlm_defaults_to_noop_backend():
    config = load_config(CONFIG_PATH)
    assert config.vlm.backend == "noop"


def test_minimal_config_fills_in_optional_defaults():
    minimal = {
        "run_name": "x",
        "search": {"seed_queries": ["a query"]},
        "clip": {"positive_prompts": ["p"], "negative_prompts": ["n"]},
    }
    config = HarvesterConfig.model_validate(minimal)
    assert config.download.concurrency == 8
    assert config.filter.min_width == 400
    assert config.vlm.backend == "noop"
    assert config.review.port == 8000
    assert config.export.open_license_allowlist  # non-empty default

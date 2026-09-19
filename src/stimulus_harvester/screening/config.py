"""Explicit project policies; no selected detector or study constraint by default."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ObservationStatus = Literal["detected", "not_detected", "uncertain", "error", "not_run"]
Disposition = Literal["eligible", "excluded", "review_required"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class DetectorConfig(StrictModel):
    id: str = Field(min_length=1, pattern=r"^[a-zA-Z0-9_-]+$")
    property: str = Field(min_length=1)
    model: Literal["fasterrcnn_resnet50_fpn_v2", "fasterrcnn_mobilenet_v3_large_320_fpn"]
    weights: Literal["COCO_V1"] = "COCO_V1"
    enabled: bool = True
    device: Literal["cpu", "cuda"] = "cpu"
    threads: int = Field(2, ge=1, le=32)
    # Required, deliberately not advertised as empirically calibrated defaults.
    low_threshold: float = Field(ge=0, le=1)
    high_threshold: float = Field(ge=0, le=1)
    output_floor: float = Field(0.01, ge=0, le=1)
    max_detections: int = Field(300, ge=1, le=1000)
    nms_threshold: float = Field(0.5, gt=0, le=1)

    @model_validator(mode="after")
    def valid_thresholds(self):
        if not self.output_floor <= self.low_threshold < self.high_threshold:
            raise ValueError("Require output_floor <= low_threshold < high_threshold")
        if self.property != "human_presence":
            raise ValueError("These two torchvision adapters observe human_presence only")
        return self


class RuleConfig(StrictModel):
    id: str = Field(min_length=1)
    version: str = Field(min_length=1)
    property: str = Field(min_length=1)
    source: str = Field(min_length=1, description="One explicit detector ID; no implicit ensemble")
    outcomes: dict[ObservationStatus, Disposition]

    @model_validator(mode="after")
    def all_states(self):
        if set(self.outcomes) != {"detected", "not_detected", "uncertain", "error", "not_run"}:
            raise ValueError("Rule must explicitly map all five observation states")
        return self


class ScreeningConfig(StrictModel):
    detectors: list[DetectorConfig] = Field(default_factory=list)
    rules: list[RuleConfig] = Field(default_factory=list)
    aggregation: Literal["excluded_then_review_required"] = "excluded_then_review_required"

    @model_validator(mode="after")
    def valid_references(self):
        sources = {d.id: d for d in self.detectors}
        if len(sources) != len(self.detectors) or len({r.id for r in self.rules}) != len(
            self.rules
        ):
            raise ValueError("Detector and rule IDs must be unique within their lists")
        for rule in self.rules:
            if rule.source not in sources or sources[rule.source].property != rule.property:
                raise ValueError(
                    f"Rule {rule.id} must reference a configured source of the same property"
                )
        return self

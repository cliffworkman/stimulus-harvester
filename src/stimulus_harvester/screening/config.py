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
        if self.property not in {"human_presence", "coco_objects"}:
            raise ValueError("Supported observation contracts: human_presence and coco_objects")
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


class Criterion(StrictModel):
    id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    version: Literal[1] = 1
    mode: Literal["automatic", "manual"]
    targets: list[str] = Field(default_factory=list)
    coverage: Literal["exact", "partial", "manual"]
    mapping_id: str = Field(min_length=1)
    registry_version: str = Field(min_length=1)
    confirmed: Literal[True]
    limitations: str = ""

    @model_validator(mode="after")
    def explicit_interpretation(self):
        from .capabilities import labels, registry, ANIMALS

        if not self.text.strip() or self.registry_version != registry()["registry_version"]:
            raise ValueError("Criterion text and a supported registry version are required")
        if len(set(self.targets)) != len(self.targets) or any(
            t not in labels() for t in self.targets
        ):
            raise ValueError("Choose distinct exact published observable labels")
        if self.mode == "manual":
            if self.targets or self.coverage != "manual":
                raise ValueError("Manual criteria have no automatic targets")
        elif not self.targets or self.coverage == "manual":
            raise ValueError("Automatic criteria require explicit confirmed targets")
        if self.coverage == "partial" and not self.limitations.strip():
            raise ValueError("Partial coverage requires its limitations")
        if (
            self.mapping_id in {"alias:animals", "alias:non-human animals"}
            and self.mode != "manual"
        ):
            if self.coverage != "partial" or set(self.targets) != set(ANIMALS):
                raise ValueError(
                    "Animal alias means the explicit partial ten-class mapping; use a custom interpretation for edited targets"
                )
        return self


class ScreeningConfig(StrictModel):
    detectors: list[DetectorConfig] = Field(default_factory=list)
    rules: list[RuleConfig] = Field(default_factory=list)
    criteria_version: Literal[1] | None = None
    criteria: list[Criterion] = Field(default_factory=list)
    aggregation: Literal["excluded_then_review_required"] = "excluded_then_review_required"

    @model_validator(mode="after")
    def valid_references(self):
        if self.criteria_version:
            if (
                self.rules
                or len(self.detectors) > 1
                or any(d.property != "coco_objects" for d in self.detectors)
            ):
                raise ValueError(
                    "Criteria contract uses one explicitly configured object mechanism and no legacy rules"
                )
            if len({c.id for c in self.criteria}) != len(self.criteria):
                raise ValueError("Criterion IDs must be unique")
        elif self.criteria:
            raise ValueError("Criteria require explicit criteria_version")
        elif any(d.property == 'coco_objects' for d in self.detectors):
            raise ValueError('Object mechanism requires the explicit criteria contract')
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

"""Immutable evidence, with source-specific score semantics rather than calibration claims."""

from typing import Any, Literal
from pydantic import Field, model_validator
from stimulus_harvester.screening.config import ObservationStatus, StrictModel


class Observation(StrictModel):
    format_version: Literal[1, 2] = 1
    observation_id: str
    project_id: str
    candidate_id: str
    asset_id: str | None
    property: str
    source: str
    family: str
    model_identity: dict[str, Any]
    parameters: dict[str, Any]
    thresholds: dict[str, float]
    runtime: dict[str, Any]
    timestamp: str
    operation_id: str
    status: ObservationStatus | Literal["recorded"]
    score: float | None = None
    score_semantics: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    reason: str
    derived_from: str | None = None

    @model_validator(mode="after")
    def coherent(self):
        if self.format_version == 2:
            if self.property != "coco_objects" or self.status not in {
                "recorded",
                "error",
                "not_run",
            }:
                raise ValueError("Object observations record evidence, not criterion decisions")
        elif self.status == "recorded" or self.property == "coco_objects":
            raise ValueError("Object evidence requires format version 2")
        if self.score is not None and not self.score_semantics:
            raise ValueError("Numerical observations require explicit score semantics")
        if self.status == "error" and not self.error:
            raise ValueError("Error observations require error information")
        if self.status != "error" and self.error:
            raise ValueError("Error information must not be disguised as another state")
        return self

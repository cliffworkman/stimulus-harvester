"""Non-semantic image quality checks: readability, dimensions, aspect ratio.

Every check here is purely mechanical -- no relevance judgment. See FilterReason for
the exhaustive list of reasons a candidate can be hard-excluded by this module.
"""

from __future__ import annotations

from pathlib import Path

from stimulus_harvester.config import FilterConfig
from stimulus_harvester.download.integrity import probe_image
from stimulus_harvester.models import FilterReason


def check_image_quality(
    path: Path, config: FilterConfig
) -> tuple[list[FilterReason], int | None, int | None, float | None]:
    """Returns (reasons, width, height, aspect_ratio). Empty reasons means "passed
    these mechanical checks" (duplicate checks are applied separately)."""
    dims = probe_image(path)
    if dims is None:
        return [FilterReason.UNREADABLE], None, None, None

    width, height = dims
    reasons: list[FilterReason] = []

    if width < config.min_width or height < config.min_height:
        reasons.append(FilterReason.TOO_SMALL)
    if max(width, height) <= config.icon_max_dimension:
        reasons.append(FilterReason.ICON_LIKE)

    aspect_ratio = max(width, height) / max(1, min(width, height))
    if aspect_ratio > config.max_aspect_ratio:
        reasons.append(FilterReason.EXTREME_ASPECT_RATIO)

    return reasons, width, height, aspect_ratio

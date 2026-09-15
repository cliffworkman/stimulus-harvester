"""Interpretable CLIP scoring rubric.

Pure math, no torch/open_clip import -- this module (and everything that only needs
it) stays testable without any ML dependency installed. CLIP/VLM scores are review
aids only: nothing in this pipeline uses `relevance_score` to exclude a candidate.
"""

from __future__ import annotations

import math
from statistics import mean


def compute_composite(
    positive_sims: list[float], negative_sims: list[float], *, temperature: float = 4.0
) -> tuple[float, float]:
    """Returns (raw_score, relevance_score).

    raw_score = mean(positive_sims) - mean(negative_sims)
    relevance_score = sigmoid(temperature * raw_score), squashed into (0, 1)

    CLIP cosine similarities for photographic prompts typically fall in a narrow band
    (roughly 0.15-0.35), so raw_score differences between good and bad matches are
    small. `temperature` spreads that narrow band across more of [0, 1] so
    relevance_score is discriminative. At raw_score == 0, relevance_score == 0.5
    exactly. This value is a ranking aid for human reviewers, not a pass/fail gate.
    """
    if not positive_sims or not negative_sims:
        raise ValueError("positive_sims and negative_sims must both be non-empty")
    raw_score = mean(positive_sims) - mean(negative_sims)
    relevance_score = 1.0 / (1.0 + math.exp(-temperature * raw_score))
    return raw_score, relevance_score

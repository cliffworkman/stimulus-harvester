from __future__ import annotations

import math

import pytest

from stimulus_harvester.score.rubric import compute_composite


def test_matches_hand_computed_values() -> None:
    positive = [0.30, 0.28, 0.25, 0.32, 0.29]
    negative = [0.10] * 8
    raw_score, relevance_score = compute_composite(positive, negative, temperature=4.0)

    expected_raw = sum(positive) / len(positive) - sum(negative) / len(negative)
    assert raw_score == pytest.approx(expected_raw)
    expected_relevance = 1.0 / (1.0 + math.exp(-4.0 * expected_raw))
    assert relevance_score == pytest.approx(expected_relevance)


def test_zero_raw_score_maps_to_exactly_half() -> None:
    raw_score, relevance_score = compute_composite([0.2], [0.2], temperature=4.0)
    assert raw_score == pytest.approx(0.0)
    assert relevance_score == pytest.approx(0.5)


def test_relevance_score_is_monotonic_in_raw_score() -> None:
    _, low = compute_composite([0.15], [0.2], temperature=4.0)
    _, mid = compute_composite([0.2], [0.2], temperature=4.0)
    _, high = compute_composite([0.3], [0.2], temperature=4.0)
    assert low < mid < high


def test_relevance_score_stays_within_unit_interval() -> None:
    _, relevance_score = compute_composite([0.9], [-0.9], temperature=4.0)
    assert 0.0 < relevance_score < 1.0


def test_empty_positive_or_negative_raises() -> None:
    with pytest.raises(ValueError):
        compute_composite([], [0.1])
    with pytest.raises(ValueError):
        compute_composite([0.1], [])

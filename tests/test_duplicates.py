from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from stimulus_harvester.download.integrity import sha256_of_file
from stimulus_harvester.filter.duplicates import (
    compute_phash,
    find_exact_duplicate,
    find_near_duplicate,
    hamming_distance,
)

THRESHOLD = 8


def _pattern_image(
    path: Path, bg: tuple[int, int, int], fg: tuple[int, int, int], box: tuple[int, int, int, int]
) -> Path:
    img = Image.new("RGB", (256, 256), bg)
    ImageDraw.Draw(img).rectangle(box, fill=fg)
    img.save(path)
    return path


def test_identical_files_have_equal_sha256_and_phash(tmp_path: Path) -> None:
    a = _pattern_image(tmp_path / "a.png", (240, 240, 240), (10, 10, 10), (20, 20, 120, 120))
    b = _pattern_image(tmp_path / "b.png", (240, 240, 240), (10, 10, 10), (20, 20, 120, 120))
    assert sha256_of_file(a) == sha256_of_file(b)
    assert compute_phash(a) == compute_phash(b)
    assert hamming_distance(compute_phash(a), compute_phash(b)) == 0


def test_near_identical_patterns_are_within_threshold(tmp_path: Path) -> None:
    a = _pattern_image(tmp_path / "a.png", (240, 240, 240), (10, 10, 10), (20, 20, 120, 120))
    # same layout, barely different fill color -- a near-duplicate re-encode/crop stand-in
    b = _pattern_image(tmp_path / "b.png", (240, 240, 240), (18, 18, 18), (20, 20, 120, 120))
    assert hamming_distance(compute_phash(a), compute_phash(b)) <= THRESHOLD


def test_very_different_patterns_exceed_threshold(tmp_path: Path) -> None:
    a = _pattern_image(tmp_path / "a.png", (255, 255, 255), (0, 0, 0), (10, 10, 100, 100))
    c = _pattern_image(tmp_path / "c.png", (0, 0, 0), (255, 255, 255), (150, 150, 240, 240))
    assert hamming_distance(compute_phash(a), compute_phash(c)) > THRESHOLD


def test_find_exact_duplicate() -> None:
    seen = {"hash1": "local_a"}
    assert find_exact_duplicate("hash1", seen) == "local_a"
    assert find_exact_duplicate("hash2", seen) is None
    assert find_exact_duplicate(None, seen) is None


def test_find_near_duplicate(tmp_path: Path) -> None:
    a = _pattern_image(tmp_path / "a.png", (240, 240, 240), (10, 10, 10), (20, 20, 120, 120))
    c = _pattern_image(tmp_path / "c.png", (0, 0, 0), (255, 255, 255), (150, 150, 240, 240))
    seen = {"local_a": compute_phash(a)}
    assert find_near_duplicate(compute_phash(a), seen, THRESHOLD) == "local_a"
    assert find_near_duplicate(compute_phash(c), seen, THRESHOLD) is None

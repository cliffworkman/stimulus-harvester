"""Exact (sha256) and near (perceptual hash) duplicate detection.

This is deliberately non-semantic: it flags images that are literally the same file,
or near-identical crops/re-encodes of the same file. It does NOT attempt to notice
that two different photographs depict the same physical scene -- that is a much
harder, human-judgment problem, supported instead by the reviewer-assignable
`scene_cluster_id` field (see review app) plus preserved provenance
(`source_domain`, `source_group_id`, `landing_page_url`).
"""

from __future__ import annotations

from pathlib import Path

import imagehash
from PIL import Image


def compute_phash(path: Path, hash_size: int = 16) -> str:
    with Image.open(path) as img:
        return str(imagehash.phash(img.convert("RGB"), hash_size=hash_size))


def hamming_distance(hash_a: str, hash_b: str) -> int:
    return imagehash.hex_to_hash(hash_a) - imagehash.hex_to_hash(hash_b)


def find_exact_duplicate(sha256: str | None, seen_sha256: dict[str, str]) -> str | None:
    """`seen_sha256` maps sha256 -> local_id of the first-seen image with that hash."""
    if not sha256:
        return None
    return seen_sha256.get(sha256)


def find_near_duplicate(
    phash: str, seen_phashes: dict[str, str], threshold: int
) -> str | None:
    """`seen_phashes` maps local_id -> phash of already-accepted (non-duplicate)
    images. Returns the local_id of the first near-duplicate found, if any."""
    for local_id, other_phash in seen_phashes.items():
        if hamming_distance(phash, other_phash) <= threshold:
            return local_id
    return None

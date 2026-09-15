"""File integrity checks: hashing and corruption detection.

Kept independent of any pipeline-specific decisions -- this module only answers "is
this a readable image file" and "what is its hash," nothing about relevance.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from PIL import Image, UnidentifiedImageError


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_image(path: Path) -> tuple[int, int] | None:
    """Return (width, height) if `path` is a readable, non-corrupt image, else None."""
    try:
        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img2:  # verify() consumes the file handle; reopen for .size
            return img2.size
    except (UnidentifiedImageError, OSError, ValueError):
        return None

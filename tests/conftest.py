from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture
def tiny_image(tmp_path: Path):
    """Factory: generate a small solid-color PNG on disk, no network involved."""

    def _make(
        name: str = "img.png", size: tuple[int, int] = (500, 400), color: tuple[int, int, int] = (200, 50, 50)
    ) -> Path:
        path = tmp_path / name
        Image.new("RGB", size, color).save(path)
        return path

    return _make


@pytest.fixture
def tmp_run_dir(tmp_path: Path):
    from stimulus_harvester.paths import RunPaths

    paths = RunPaths(root=tmp_path / "runs" / "test_run")
    paths.ensure()
    return paths

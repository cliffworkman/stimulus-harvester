"""Build and install the actual wheel, then exercise resources away from the checkout."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile


def test_installed_wheel_browser_resources(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    source = tmp_path / "build source"
    source.mkdir()
    shutil.copytree(
        repo / "src", source / "src", ignore=shutil.ignore_patterns("*.egg-info", "__pycache__")
    )
    for name in ("pyproject.toml", "README.md", "LICENSE"):
        shutil.copyfile(repo / name, source / name)
    wheels = tmp_path / "wheels"
    build = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "wheel",
            "--no-cache-dir",
            "--no-deps",
            "--no-build-isolation",
            "--no-index",
            "--wheel-dir",
            str(wheels),
            str(source),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert build.returncode == 0, build.stdout + build.stderr
    wheel = next(wheels.glob("*.whl"))
    with ZipFile(wheel) as archive:
        files = archive.namelist()
        for suffix in (
            "review/templates/base.html",
            "review/templates/grid.html",
            "review/templates/detail.html",
            "review/templates/404.html",
            "review/static/style.css",
            "review/static/review.js",
            "resources/minimal.yaml",
            "py.typed",
        ):
            assert "stimulus_harvester/" + suffix in files
        assert any(name.endswith("/LICENSE") for name in files)
    installed = tmp_path / "installed package"
    install = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "--isolated",
            "--disable-pip-version-check",
            "install",
            "--no-cache-dir",
            "--no-index",
            "--no-deps",
            "--target",
            str(installed),
            str(wheel),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    script = r"""
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import stimulus_harvester
assert Path(stimulus_harvester.__file__).is_relative_to(Path(sys.argv[1]))
from importlib.resources import files
from fastapi.testclient import TestClient
from stimulus_harvester import services
from stimulus_harvester.config import load_config
resource = files('stimulus_harvester').joinpath('resources/minimal.yaml')
project = services.create_project(Path(sys.argv[2]), load_config(Path(str(resource))))
client = TestClient(services.review_application(project))
assert client.get('/images').status_code == 200
assert 'Stimulus Harvester' in client.get('/images').text
assert client.get('/static/style.css').status_code == 200
assert client.get('/static/review.js').status_code == 200
assert client.get('/images/missing').status_code == 404
print('installed wheel browser smoke passed')
"""
    smoke = subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed), str(tmp_path / "wheel project")],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert smoke.returncode == 0, smoke.stdout + smoke.stderr
    assert "installed wheel browser smoke passed" in smoke.stdout

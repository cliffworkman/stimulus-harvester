from __future__ import annotations

import io
import json

import httpx
import respx
from PIL import Image

from stimulus_harvester import services
from stimulus_harvester.config import HarvesterConfig
from stimulus_harvester.review.db import init_db


@respx.mock
def test_existing_providers_share_project_lineage_and_operation_evidence(tmp_path):
    config = HarvesterConfig.model_validate(
        {
            "run_name": "sources",
            "search": {
                "seed_queries": ["q"],
                "query_expansion": {"enabled": False},
                "searxng": {"enabled": True, "endpoint_url": "https://search.example.org"},
            },
            "clip": {"positive_prompts": ["p"], "negative_prompts": ["n"]},
        }
    )
    url = "https://images.example.org/a.png"
    respx.get("https://api.openverse.org/v1/images/").mock(
        return_value=httpx.Response(
            200,
            json={
                "results": [
                    {
                        "url": url,
                        "license": "by",
                        "creator": "A",
                        "foreign_landing_url": "https://source.org/a",
                    }
                ]
            },
        )
    )
    respx.get("https://search.example.org/search").mock(
        return_value=httpx.Response(
            200, json={"results": [{"img_src": url, "url": "https://source.org/b"}]}
        )
    )
    content = io.BytesIO()
    Image.new("RGB", (500, 400), "navy").save(content, format="PNG")
    respx.get(url).mock(
        return_value=httpx.Response(
            200, content=content.getvalue(), headers={"content-type": "image/png"}
        )
    )
    project = services.create_project(tmp_path / "multi source", config)
    first = services.run_stage(project, "search")
    assert first.status == "succeeded", first.error
    raw_before = {p: p.read_bytes() for p in project.paths.raw_api.rglob("*.json")}
    second = services.run_stage(project, "search")
    assert second.status == "succeeded"
    assert len(list(project.paths.raw_api.rglob("*.json"))) == 4
    assert all(p.read_bytes() == data for p, data in raw_before.items())
    assert services.run_stage(project, "download").status == "succeeded"
    conn = init_db(project.paths.db_path)
    assert conn.execute("SELECT COUNT(*) FROM images").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM discoveries").fetchone()[0] == 4
    assert conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 1
    assert {r[0] for r in conn.execute("SELECT operation_id FROM discoveries")} == {
        first.operation_id,
        second.operation_id,
    }
    for row in conn.execute("SELECT raw_response_path FROM discoveries"):
        assert not row[0].startswith(str(project.root))
        assert project.resolve(row[0]).exists()
    conn.close()
    manifest = project.paths.manifests / "download_results.jsonl"
    record = json.loads(manifest.read_text().splitlines()[0])
    assert record["file_path"].startswith("images/original/")

"""Synthetic curation demo. Saved mock screening is illustrative, never accuracy evidence.

Run: python -m stimulus_harvester.gui.curation_demo --output NEW_DIRECTORY
"""

import argparse
from pathlib import Path

from stimulus_harvester import services, workspace
from stimulus_harvester.frozen.demo import review, screen, seed
from stimulus_harvester.models import DownloadRecord, DownloadStatus, SearchResult
from stimulus_harvester.review.db import (
    init_db,
    update_download,
    upsert_image_from_search,
)


def create(path):
    project = services.create_project(
        path, workspace.default_configuration("Synthetic curation demo")
    )
    a = seed(project, "atrium", label="accepted", license="CC BY")
    seed(project, "atrium-other-source", duplicate=a["path"], license="CC BY-SA")
    seed(project, "chapel", license="CC0")
    screen(project, {"atrium": 0.8, "atrium-other-source": 0.8, "chapel": 0.3})
    # Added after the mock run to show actual absence of screening evidence.
    seed(project, "unreviewed-room", license=None)
    review(project, "atrium", "maybe")
    conn = init_db(project.paths.db_path)
    try:
        for local, provider, query, license, error, status, mime in (
            ("waiting", "openverse", "chapel interior", "CC0", None, None, None),
            ("rate-limit", "openverse", "prison interior", "CC BY", "HTTP 429", 429, None),
            (
                "large-original",
                "commons",
                "church interior",
                "CC BY-SA",
                "Image exceeds the configured file size limit",
                200,
                "image/jpeg",
            ),
            (
                "vector",
                "commons",
                "prison cell",
                None,
                "Downloaded bytes are not a readable image",
                200,
                "image/svg+xml",
            ),
        ):
            url = f"https://example.invalid/{local}.png"
            upsert_image_from_search(
                conn,
                SearchResult(
                    local_id=local,
                    source_backend=provider,
                    search_query=query,
                    seed_query=query,
                    image_url=url,
                    landing_page_url=url,
                    title=local.replace("-", " ").title() + " (synthetic)",
                    license=license,
                ),
                project.project_id,
            )
            if error:
                update_download(
                    conn,
                    DownloadRecord(
                        local_id=local,
                        image_url=url,
                        status=DownloadStatus.FAILED,
                        http_status=status,
                        error_message=error,
                        attempts=[
                            dict(http_status=status, response_headers={"content-type": mime or ""})
                        ],
                    ),
                )
        # An additional independent discovery relationship, not replacement metadata.
        upsert_image_from_search(
            conn,
            SearchResult(
                local_id="atrium-source",
                source_backend="openverse",
                search_query="church interior",
                seed_query="church interior",
                image_url="https://example.invalid/atrium.png",
                landing_page_url="https://example.org/source",
                title="Atrium study (synthetic)",
                creator="Synthetic artist",
                license="CC BY",
            ),
            project.project_id,
        )
    finally:
        conn.close()
    return project


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(create(parser.parse_args().output).root)

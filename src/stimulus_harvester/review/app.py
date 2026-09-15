"""Local review app: FastAPI + Jinja2, backed by the run's SQLite DB.

Fast, legible, no-frills -- static-ish templates, a little vanilla JS for keyboard
shortcuts, no heavy front-end stack. Nothing here has any prison-specific knowledge:
the detail template renders whatever VLM fields the run's config declared, generically.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from stimulus_harvester.config import HarvesterConfig
from stimulus_harvester.models import ReviewLabel
from stimulus_harvester.paths import RunPaths
from stimulus_harvester.review import db as db_module

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"

_JSON_COLUMNS = ("tags", "filter_reasons", "clip_positive_scores", "clip_negative_scores", "vlm_fields")


class LabelUpdate(BaseModel):
    label: str
    notes: str | None = None
    scene_cluster_id: str | None = None


def _is_http_url(value: str | None) -> bool:
    """Templates must never render an href/src straight from external API data
    without this check -- an untrusted `landing_page_url`/`license_url` could
    otherwise carry a `javascript:` (or other) scheme and execute in the reviewer's
    browser when clicked."""
    return bool(value) and value.strip().lower().startswith(("http://", "https://"))


def row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in _JSON_COLUMNS:
        if data.get(key):
            try:
                data[key] = json.loads(data[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return data


def create_app(run_dir: Path, config: HarvesterConfig, reviewer_email: str | None = None) -> FastAPI:
    paths = RunPaths(root=run_dir)
    app = FastAPI(title=f"Stimulus Harvester Review - {run_dir.name}")
    app.state.paths = paths
    app.state.config = config
    app.state.reviewer_email = reviewer_email
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    templates.env.filters["is_http_url"] = _is_http_url

    def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(request.app.state.paths.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse(url="/images")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "run_id": run_dir.name}

    @app.get("/images", response_class=HTMLResponse)
    def images_grid(
        request: Request,
        query: str | None = None,
        source: str | None = None,
        license: str | None = None,
        status: str | None = None,
        min_score: float | None = None,
        sort: str = "score_desc",
        page: int = 1,
        conn: sqlite3.Connection = Depends(get_conn),
    ) -> HTMLResponse:
        page = max(page, 1)
        rows, total = db_module.query_images(
            conn,
            query=query,
            source=source,
            license_value=license,
            status=status,
            min_score=min_score,
            sort=sort,
            page=page,
            page_size=config.review.page_size,
        )
        total_pages = max(1, -(-total // config.review.page_size))
        return templates.TemplateResponse(
            request,
            "grid.html",
            {
                "run_id": run_dir.name,
                "images": [row_to_dict(r) for r in rows],
                "total": total,
                "page": page,
                "total_pages": total_pages,
                "filters": {
                    "query": query or "",
                    "source": source or "",
                    "license": license or "",
                    "status": status or "",
                    "min_score": "" if min_score is None else min_score,
                    "sort": sort,
                },
            },
        )

    @app.get("/images/{local_id}", response_class=HTMLResponse)
    def image_detail(
        request: Request, local_id: str, conn: sqlite3.Connection = Depends(get_conn)
    ) -> HTMLResponse:
        row = db_module.get_image(conn, local_id)
        if row is None:
            return templates.TemplateResponse(
                request, "404.html", {"run_id": run_dir.name, "local_id": local_id}, status_code=404
            )
        cluster_rows = db_module.get_duplicate_cluster(conn, local_id)
        return templates.TemplateResponse(
            request,
            "detail.html",
            {
                "run_id": run_dir.name,
                "image": row_to_dict(row),
                "cluster": [row_to_dict(r) for r in cluster_rows if r["local_id"] != local_id],
            },
        )

    @app.get("/images/{local_id}/file")
    def image_file(local_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> FileResponse:
        row = db_module.get_image(conn, local_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown local_id")
        file_path = row["file_path"]
        if not file_path or not Path(file_path).exists():
            raise HTTPException(status_code=404, detail="file not found on disk")
        return FileResponse(file_path)

    @app.post("/images/{local_id}/label")
    def set_label(
        local_id: str, update: LabelUpdate, conn: sqlite3.Connection = Depends(get_conn)
    ) -> JSONResponse:
        valid_labels = {label.value for label in ReviewLabel}
        if update.label not in valid_labels:
            raise HTTPException(status_code=400, detail=f"invalid label: {update.label!r}")
        existing = db_module.get_image(conn, local_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="unknown local_id")

        row = db_module.set_review_label(
            conn,
            local_id,
            label=update.label,
            notes=update.notes,
            scene_cluster_id=update.scene_cluster_id,
            reviewer_email=app.state.reviewer_email,
        )
        if update.label == ReviewLabel.ACCEPTED.value and row["file_path"]:
            _mirror_accepted(paths, row["file_path"])
        return JSONResponse(row_to_dict(row))

    @app.get("/api/summary")
    def api_summary(conn: sqlite3.Connection = Depends(get_conn)) -> dict[str, Any]:
        return db_module.summary_counts(conn)

    return app


def _mirror_accepted(paths: RunPaths, file_path: str) -> None:
    src = Path(file_path)
    if not src.exists():
        return
    dest = paths.images_accepted / src.name
    if not dest.exists():
        paths.images_accepted.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(src.read_bytes())

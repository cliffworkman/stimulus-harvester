"""Package accepted images, an HTML contact sheet, and a review summary."""

from __future__ import annotations

import json
import os
from html import escape
from collections import Counter
from pathlib import Path

from stimulus_harvester.models import ManifestRow, ReviewLabel
from stimulus_harvester.storage import resolve_path

_CONTACT_SHEET_STYLE = """
body { font-family: system-ui, sans-serif; background: #111; color: #eee; margin: 0; padding: 24px; }
h1 { font-size: 1.1rem; font-weight: 600; margin: 0 0 16px; }
.grid { display: grid; gap: 12px; }
.cell { background: #1b1b1b; border-radius: 6px; padding: 8px; overflow: hidden; }
.cell img { width: 100%; height: auto; display: block; border-radius: 4px; }
.cell .caption { font-size: 0.72rem; color: #aaa; margin-top: 6px; word-break: break-all; }
"""


def sync_accepted_images(rows: list[ManifestRow], accepted_dir: Path, *, root: Path | None = None) -> int:
    """Ensure `images/accepted/` reflects the current DB state (adds any missing
    accepted files; does not remove files for images no longer labeled accepted)."""
    accepted_dir.mkdir(parents=True, exist_ok=True)
    (accepted_dir / "README.txt").write_text(
        "LEGACY CONVENIENCE COPIES — NON-AUTHORITATIVE\n"
        "This folder can contain images from earlier review decisions. Files are not removed\n"
        "after rejection. Consult the current review database and export manifest.\n"
        "This is not a frozen or assembled research stimulus set.\n", encoding="utf-8")
    count = 0
    for row in rows:
        if row.review_label != ReviewLabel.ACCEPTED or not row.file_path:
            continue
        src = resolve_path(root, row.file_path) if root else Path(row.file_path)
        if not src.exists():
            continue
        dest = accepted_dir / src.name
        if not dest.exists():
            dest.write_bytes(src.read_bytes())
        count += 1
    return count


def build_contact_sheet(
    rows: list[ManifestRow], path: Path, *, columns: int, thumbnail_px: int, root: Path | None = None
) -> None:
    accepted = [r for r in rows if r.review_label == ReviewLabel.ACCEPTED and r.file_path]
    path.parent.mkdir(parents=True, exist_ok=True)

    cells = []
    for row in accepted:
        src = resolve_path(root, row.file_path) if root else Path(row.file_path)
        from urllib.parse import quote
        rel = escape(quote(os.path.relpath(src, start=path.parent).replace("\\", "/")))
        score = f"{row.clip_relevance_score:.3f}" if row.clip_relevance_score is not None else "n/a"
        cells.append(
            f'<div class="cell"><img src="{rel}" loading="lazy" alt="{row.local_id}">'
            f'<div class="caption">{row.local_id} &middot; score {score} &middot; {row.license_flag.value}</div></div>'
        )

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Accepted stimuli contact sheet</title>
<style>{_CONTACT_SHEET_STYLE}</style></head>
<body>
<h1>Accepted stimuli ({len(accepted)} image{'s' if len(accepted) != 1 else ''})</h1>
<div class="grid" style="grid-template-columns: repeat({columns}, minmax(0, 1fr));">
{''.join(cells)}
</div>
</body></html>
"""
    path.write_text(html, encoding="utf-8")


def build_summary(rows: list[ManifestRow]) -> dict:
    by_label = Counter(r.review_label.value for r in rows)
    by_source = Counter(r.source_backend for r in rows)
    by_license_flag = Counter(r.license_flag.value for r in rows)
    scene_clusters = Counter(r.scene_cluster_id for r in rows if r.scene_cluster_id)
    return {
        "total_candidates": len(rows),
        "by_label": dict(by_label),
        "by_source_backend": dict(by_source),
        "by_license_flag": dict(by_license_flag),
        "scene_clusters": {
            "distinct_clusters_assigned": len(scene_clusters),
            "cluster_sizes": dict(scene_clusters),
            "note": (
                "Reviewer-assigned scene_cluster_id groups: images sharing a cluster id "
                "are believed to depict the same physical scene and should not be treated "
                "as independent exemplars in downstream stimulus-set design."
            ),
        },
    }


def write_summary(rows: list[ManifestRow], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    summary = build_summary(rows)
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

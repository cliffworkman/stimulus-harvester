"""Download selection and accounting; source relationships do not imply extra files."""

from __future__ import annotations

import json
from datetime import datetime
from urllib.parse import urlparse

from stimulus_harvester.models import utcnow
from stimulus_harvester.storage import connection_root, resolve_path


def host_retry_deadlines(conn):
    """Honor saved server waits across operations, including new records on that host."""
    deadlines = {}
    for row in conn.execute("SELECT record_json FROM acquisitions"):
        record = json.loads(row[0])
        value = record.get("retry_not_before")
        if value:
            try:
                deadline = datetime.fromisoformat(value)
                host = urlparse(record.get("image_url", "")).hostname
                if host and deadline > utcnow():
                    deadlines[host] = max(deadline, deadlines.get(host, deadline))
            except (ValueError, TypeError):
                pass
    return deadlines


def deferred_until(conn, candidate_id):
    row = conn.execute(
        "SELECT record_json FROM acquisitions WHERE candidate_id=? ORDER BY rowid DESC LIMIT 1",
        (candidate_id,),
    ).fetchone()
    if row:
        value = json.loads(row[0]).get("retry_not_before")
        if value:
            try:
                if datetime.fromisoformat(value) > utcnow():
                    return value
            except (ValueError, TypeError):
                pass
    return None


def select_downloads(
    conn,
    *,
    retry_failed=False,
    provider=None,
    query=None,
    candidate_ids=None,
    allow_pending_scope=False,
):
    """Intersect explicit scope with failed OR pending state, never include successes.

    Provider and query must match the same historical source relationship. Query
    matching is exact, not substring. An empty explicit ID list selects nothing.
    """
    if (
        not retry_failed
        and not allow_pending_scope
        and any(v is not None for v in (provider, query, candidate_ids))
    ):
        raise ValueError(
            "Scopes are supported on retry-downloads; ordinary download selects pending records"
        )
    rows = conn.execute(
        "SELECT c.candidate_id,i.local_id,i.image_url FROM candidates c JOIN images i ON i.local_id=c.primary_local_id WHERE "
        + ("i.download_status='failed'" if retry_failed else "i.download_status IS NULL")
        + " ORDER BY c.rowid"
    ).fetchall()
    selected = []
    waits = host_retry_deadlines(conn)
    for row in rows:
        if urlparse(row["image_url"]).hostname in waits:
            continue
        if candidate_ids is not None and row["candidate_id"] not in candidate_ids:
            continue
        sources = conn.execute(
            "SELECT provider,query FROM discoveries WHERE candidate_id=?", (row["candidate_id"],)
        ).fetchall()
        if (provider is not None or query is not None) and not any(
            (provider is None or s["provider"] == provider)
            and (query is None or s["query"] == query)
            for s in sources
        ):
            continue
        if retry_failed and deferred_until(conn, row["candidate_id"]):
            continue
        selected.append((row["local_id"], row["image_url"]))
    return selected


def acquisition_summary(conn):
    root = connection_root(conn)
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT c.candidate_id,c.asset_id,i.download_status,i.file_path,i.file_size_bytes,i.image_url FROM candidates c JOIN images i ON i.local_id=c.primary_local_id"
        )
    ]
    by_provider = {}
    waits = host_retry_deadlines(conn)
    for r in rows:
        r["available"] = False
        if r["download_status"] == "success" and r["asset_id"] and r["file_path"]:
            try:
                path = resolve_path(root, r["file_path"])
                r["available"] = path.is_file() and (
                    r["file_size_bytes"] is None or path.stat().st_size == r["file_size_bytes"]
                )
            except (OSError, ValueError):
                pass
        r["host_wait"] = urlparse(r["image_url"]).hostname in waits
        r["deferred"] = r["download_status"] == "failed" and (
            r["host_wait"] or bool(deferred_until(conn, r["candidate_id"]))
        )
        for source in conn.execute(
            "SELECT DISTINCT provider FROM discoveries WHERE candidate_id=?", (r["candidate_id"],)
        ):
            by_provider.setdefault(source[0], []).append(r)

    def counts(group):
        successful = [r for r in group if r["download_status"] == "success"]
        available = [r for r in successful if r["available"]]
        assets = {r["asset_id"] for r in available}
        failed = sum(r["download_status"] == "failed" for r in group)
        deferred = sum(r["deferred"] for r in group)
        return {
            "found_results": len(group),
            "successful_records": len(successful),
            "available_records": len(available),
            "available_images": len(assets),
            "unavailable_successful_records": len(successful) - len(available),
            "failed_downloads": failed,
            "pending": sum(r["download_status"] is None for r in group),
            "pending_deferred": sum(r["download_status"] is None and r["host_wait"] for r in group),
            "deferred_failures": deferred,
            "retry_available": failed - deferred,
            "shared_image_records": len(available) - len(assets),
        }

    return dict(
        counts(rows),
        providers={p: counts(group) for p, group in sorted(by_provider.items())},
        count_basis="Found/successful/failed/pending count distinct found records; available images count unique acquired SHA-256 identities with an existing size-matching current file. This quick view does not rehash files. Provider rows overlap when a result has multiple providers and must not be summed.",
    )


def format_acquisition_summary(value):
    lines = [
        f"{value['found_results']} found results · {value['successful_records']} downloaded results · "
        f"{value['failed_downloads']} failed downloads · {value['pending']} pending (not attempted)",
        f"{value['available_images']} unique image files available now · "
        f"{value['shared_image_records']} additional downloaded results share those same images",
    ]
    if value["unavailable_successful_records"]:
        lines.append(
            f"{value['unavailable_successful_records']} previously downloaded results have missing or size-changed files; restore the recorded originals."
        )
    if value.get("pending_deferred"):
        lines.append(
            f"{value['pending_deferred']} pending results are waiting for a server-requested delay; they have not been attempted."
        )
    if value["failed_downloads"]:
        lines.append(
            f"Retry failed downloads: {value['retry_available']} can be requested now; "
            f"{value['deferred_failures']} waiting for a server-requested delay. No search is repeated; denied resources may still fail."
        )
    for provider, row in value["providers"].items():
        lines.append(
            f"{provider}: {row['found_results']} found results · {row['successful_records']} downloaded results · "
            f"{row['failed_downloads']} failed · {row['pending']} pending · {row['available_images']} unique files available"
        )
    lines.append(
        "Provider rows can overlap. Found results are records; available files are unique images, not extra copies for each source."
    )
    return "\n".join(lines)

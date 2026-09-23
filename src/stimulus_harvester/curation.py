"""Read-only, frontend-neutral descriptions. No decisions, inference or schema writes.

The metadata projection never opens image bytes. Availability is a quick check of
the successfully decoded acquisition's current file and recorded size, not a new
integrity attestation. The desktop checks decoding separately in its worker.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlparse

from stimulus_harvester.download.state import host_retry_deadlines
from stimulus_harvester.storage import resolve_path

FAILURES = {
    "access": (
        "Access denied / request setup",
        "Check request contact and host access policy. An explicit retry may still be denied.",
        True,
    ),
    "rate_limit": (
        "Temporarily rate-limited",
        "Wait until the server's recorded retry time before trying again.",
        True,
    ),
    "size": (
        "Too large for current size limit",
        "Review the configured size limit. An unchanged retry will not help.",
        False,
    ),
    "format": (
        "Unsupported image format",
        "The current decoder cannot read this format. An unchanged retry will not help.",
        False,
    ),
    "unreadable": (
        "Unreadable image response",
        "Inspect the source and technical details before retrying.",
        False,
    ),
    "temporary": (
        "Temporary server or network failure",
        "A later explicit retry may succeed. Respect any recorded wait.",
        True,
    ),
    "missing": (
        "No longer available at source",
        "Check the source page; an unchanged retry is unlikely to help.",
        False,
    ),
    "local": ("Local file problem", "Check disk access and space before retrying.", False),
    "other": (
        "Needs investigation",
        "Inspect technical details before deciding whether to retry.",
        False,
    ),
}


def failure_kind(record):
    status = record.get("http_status")
    attempts = record.get("attempts") or []
    last = attempts[-1] if attempts else {}
    status = status or last.get("http_status")
    message = str(record.get("error_message") or last.get("error") or "").lower()
    mime = (
        record.get("content_type") or last.get("response_headers", {}).get("content-type") or ""
    ).lower()
    if "size limit" in message or "max_file_size" in message:
        return "size"
    if status in {401, 403} or "commons.contact" in message:
        return "access"
    if status == 429 or "longer wait" in message:
        return "rate_limit"
    if "readable image" in message or "identify image" in message:
        return (
            "format"
            if "svg" in mime or urlparse(record.get("image_url", "")).path.lower().endswith(".svg")
            else "unreadable"
        )
    if status in {404, 410}:
        return "missing"
    if (
        status in {500, 502, 503, 504}
        or last.get("retryable")
        or any(s in message for s in ("timeout", "connecterror", "networkerror"))
    ):
        return "temporary"
    if "local image write" in message or "different bytes" in message:
        return "local"
    return "other"


def available_file(root, row):
    if (
        row.get("download_status") != "success"
        or not row.get("asset_id")
        or not row.get("file_path")
    ):
        return False
    try:
        path = resolve_path(root, row["file_path"])
        return path.is_file() and (
            row.get("file_size_bytes") is None or path.stat().st_size == row["file_size_bytes"]
        )
    except (OSError, ValueError):
        return False


def title_for(row):
    for source in row.get("sources", []):
        title = json.loads(source.get("record_json") or "{}").get("title")
        if title:
            return plain_metadata(title)
    return (
        row.get("title")
        or unquote(Path(urlparse(row.get("image_url") or "").path).name)
        or "Untitled image"
    )


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def plain_metadata(value):
    """Display provider HTML as text; never run markup or load remote resources."""
    parser = _Text()
    parser.feed(str(value or ""))
    return " ".join(parser.parts).strip() or "Not supplied"


def safe_link(value):
    parsed = urlparse(value or "")
    return (
        parsed.scheme in {"https", "http"}
        and bool(parsed.hostname)
        and not parsed.username
        and not parsed.password
    )


def screening_label(observations):
    states = {o["status"] for o in observations}
    if not states or states == {"not_run"}:
        return "Not run"
    labels = {
        "detected": "Detected",
        "not_detected": "Not detected (not confirmed absence)",
        "uncertain": "Uncertain",
        "error": "Run error",
        "not_run": "Not run",
    }
    return "; ".join(labels.get(s, s.replace("_", " ")) for s in sorted(states))


def project_projection(conn, root):
    """Works on mode=ro connections, including projects without screening tables."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT c.*,i.title,i.image_url,i.width,i.height,i.download_status,i.file_path,i.file_size_bytes,r.label "
            "FROM candidates c JOIN images i ON i.local_id=c.primary_local_id LEFT JOIN reviews r ON r.local_id=c.primary_local_id ORDER BY c.rowid"
        )
    ]
    acquisitions = {}
    for record in conn.execute("SELECT candidate_id,record_json FROM acquisitions ORDER BY rowid"):
        acquisitions[record[0]] = json.loads(record[1])
    latest = {}
    if "observations" in tables:
        for record in conn.execute("SELECT record_json FROM observations ORDER BY rowid"):
            obs = json.loads(record[0])
            latest[(obs["candidate_id"], obs["asset_id"], obs["source"])] = obs
    sources = defaultdict(list)
    for record in conn.execute("SELECT * FROM discoveries ORDER BY rowid"):
        sources[record["candidate_id"]].append(dict(record))
    waits = host_retry_deadlines(conn)
    for row in rows:
        cid = row["candidate_id"]
        row["sources"] = sources[cid]
        row["title"] = title_for(row)
        row["available"] = available_file(root, row)
        row["availability"] = (
            "Available"
            if row["available"]
            else "Pending"
            if row["download_status"] is None
            else "Failed"
            if row["download_status"] == "failed"
            else "Missing / changed file"
        )
        row["review_text"] = {"accepted": "Accepted", "maybe": "Maybe", "rejected": "Rejected"}.get(
            row["label"], "Unreviewed"
        )
        obs = [v for (c, a, _), v in latest.items() if c == cid and a == row["asset_id"]]
        row["screening_text"] = screening_label(obs)
        row["failure_kind"] = (
            failure_kind(acquisitions.get(cid, {})) if row["download_status"] == "failed" else None
        )
        row["retry_eligible"] = bool(
            row["failure_kind"]
            and FAILURES[row["failure_kind"]][2]
            and urlparse(row["image_url"]).hostname not in waits
        )
        row["failure_evidence"] = acquisitions.get(cid) if row["failure_kind"] else None
    counts = Counter(r["failure_kind"] for r in rows if r["failure_kind"])
    summary = dict(
        found=len(rows),
        available=sum(r["available"] for r in rows),
        unique_images=len({r["asset_id"] for r in rows if r["available"]}),
        failed=sum(r["download_status"] == "failed" for r in rows),
        unavailable=sum(not r["available"] and r["download_status"] is not None for r in rows),
        pending=sum(r["download_status"] is None for r in rows),
        reviewed=sum(r["review_text"] != "Unreviewed" for r in rows),
        screened=sum(r["screening_text"] != "Not run" for r in rows),
        failures=dict(counts),
        retry_ids=[r["candidate_id"] for r in rows if r["retry_eligible"]],
    )
    return {"summary": summary, "rows": rows, "coverage": search_coverage(conn, root)}


def summary_text(summary):
    s = summary
    available = f"{s['available']:,} available"
    if s["unique_images"] != s["available"]:
        available += f" ({s['unique_images']:,} unique image files)"
    screening = (
        "Not run"
        if not s["screened"]
        else f"Recorded for {s['screened']:,} of {s['found']:,} results (including run errors)"
    )
    return (
        f"{s['found']:,} found · {available} · {s['unavailable']:,} unavailable · {s['pending']:,} waiting to download\n"
        f"Screening: {screening} · Human review: {s['reviewed']:,} of {s['found']:,} reviewed"
    )


def failure_text(summary):
    lines = [f"{summary['failed']:,} failed downloads"]
    for kind, count in summary["failures"].items():
        label, guidance, _ = FAILURES[kind]
        lines.append(f"{label}: {count:,}\n  {guidance}")
    if summary["unavailable"] > summary["failed"]:
        lines.append(
            "Some previously downloaded files are missing, changed or unreadable. Restore the recorded originals."
        )
    lines.append(
        f"Retry eligible failures: {len(summary['retry_ids']):,} can be requested now. Size/format failures are excluded; successful downloads are not repeated."
    )
    return "\n".join(lines)


def search_coverage(conn, root):
    from stimulus_harvester.search.evidence import read_attempts

    groups = {}
    for event in read_attempts(conn):
        key = (event.get("provider"), event.get("query"))
        op = event.get("operation_id")
        if key not in groups or groups[key][0] != op:
            groups[key] = (op, [])
        groups[key][1].append(event)
    result = []
    for (provider, query), (op, events) in groups.items():
        pages = [e for e in events if e["status"] == "page_committed"]
        total = sum(e.get("result_count", 0) for e in pages)
        failures = [
            e
            for e in events
            if e["status"] in {"failed", "engine_errors"} and not e.get("will_retry")
        ]
        completions = [e for e in events if e["status"] in {"query_completed", "query_exhausted"}]
        reason, evidence_basis = "unconfirmed", "saved events"
        if failures:
            reason = "incomplete"
            message = f"Stopped early after {total:,} results; more may remain."
            status = failures[-1].get("http_status")
            if status:
                message += f" HTTP {status}."
        elif completions:
            reason = completions[-1].get("reason", "results_exhausted")
            message = {
                "anonymous_result_limit": "Search limit reached normally (anonymous access)",
                "configured_page_limit": "Stopped at your configured search limit",
                "results_exhausted": "Available search results exhausted",
            }.get(reason, completions[-1].get("message", "Search ended"))
        elif (
            pages
            and provider == "commons"
            and op
            and conn.execute(
                "SELECT 1 FROM operation_events WHERE event_id=(SELECT MAX(event_id) FROM operation_events WHERE operation_id=?) AND status IN ('succeeded','partial')",
                (op,),
            ).fetchone()
        ):
            # rc4 did not save an explicit completion event. Read the last saved
            # API envelope, never image bytes or today's possibly edited config.
            try:
                path = resolve_path(root, pages[-1]["raw_response_path"])
                payload = json.loads(path.read_text(encoding="utf-8"))
                reason = "configured_page_limit" if payload.get("continue") else "results_exhausted"
                message = (
                    "Stopped at your configured search limit"
                    if payload.get("continue")
                    else "Available search results exhausted"
                )
                evidence_basis = "rc4 saved response continuation; no unresolved failure"
            except (OSError, ValueError, KeyError, TypeError):
                message = "Saved pages found; completion is not confirmed"
        else:
            message = (
                "Saved pages found; completion is not confirmed"
                if pages
                else "No completed pages recorded"
            )
        result.append(
            dict(
                provider=provider,
                query=query or "Historical provider setup",
                timestamp=events[-1].get("timestamp"),
                operation_id=op,
                results=total,
                status=reason,
                message=message,
                evidence_basis=evidence_basis,
            )
        )
    return result


def coverage_text(rows):
    lines = ["Latest search per provider and phrase (saved history)"]
    for row in rows:
        lines.append(
            f"{row['query']} — {row['provider']}: {row['message']} · {row['results']:,} results saved (may repeat images)\nRecorded: {row.get('timestamp') or 'Time not supplied'}"
        )
    if not rows:
        lines.append("No saved search coverage yet.")
    lines.append("Search continuation is not implemented. Starting again begins at the first page.")
    return "\n\n".join(lines)


def review_history_text(review, events):
    names = {
        "accepted": "Accepted",
        "maybe": "Maybe",
        "rejected": "Rejected",
        "unrated": "Unreviewed",
    }
    lines = ["Current decision: " + names.get((review or {}).get("label"), "Unreviewed")]
    if not events:
        lines.append("No recorded review decisions. Viewing an image does not review it.")
    for event in events:
        stamp = event.get("created_at") or "Time not supplied"
        try:
            dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            stamp = dt.astimezone().strftime("%b %d, %Y %I:%M %p %Z")
        except ValueError:
            pass
        lines.append(
            f"{stamp}\n{event.get('reviewer_email') or 'Reviewer not supplied'} · {names.get(event.get('label'), event.get('label'))}\n{event.get('notes') or 'No note'}"
        )
    return "\n\n".join(lines)

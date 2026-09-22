"""Small readable projections for provider recovery; raw evidence remains unchanged."""


def issue_message(event):
    error = event.get("error") or {}
    if not isinstance(error, dict):
        return str(error)
    kind = error.get("kind")
    if kind == "rate_limited":
        return "Provider requested fewer requests. Wait before starting another search."
    if kind == "http_error" and event.get("http_status") == 401:
        return "Provider rejected access. Check provider access requirements; this is not a confirmed result limit."
    if kind == "http_error" and event.get("http_status") == 403:
        return "Provider denied access. Check provider settings and access policy."
    return error.get("message") or event.get("reason") or event.get("status", "Provider issue")


def discovery_text(value, status):
    heading = (
        "Partial discovery"
        if status == "partial"
        else "Discovery failed"
        if status == "failed"
        else "Discovery complete"
    )
    lines = [
        f"{heading}: {value.get('pages_committed', 0)} pages saved; "
        f"{value.get('source_occurrences', 0)} search-result occurrences retained (may repeat images)."
    ]
    for event in value.get("completions", []):
        lines.append(
            f"{event['provider']} — {event.get('query', '')}: complete — {event['message']}"
        )
    for event in value.get("issues", []):
        lines.append(
            f"{event.get('provider', 'provider')} — {event.get('query', 'setup')}: {issue_message(event)}"
        )
    lines.append(
        "Start a new search begins at page 1. Search continuation is not implemented. Download retry does not repeat searches."
    )
    return "\n".join(lines)

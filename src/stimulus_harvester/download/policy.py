"""Destination request policy, independent of discovery attribution."""

from contextlib import contextmanager
from urllib.parse import urlparse

from stimulus_harvester.search.commons import commons_user_agent


def wikimedia_host(url):
    host = (urlparse(str(url)).hostname or "").lower().rstrip(".")
    return host == "wikimedia.org" or host.endswith(".wikimedia.org")


def request_agent(url, generic_agent, contact):
    return commons_user_agent(contact) if wikimedia_host(url) else generic_agent


@contextmanager
def identified_stream(client, url, config, context, entry):
    """Re-evaluate every redirect destination before sending a request.

    No provider identities are rewritten; no denial is retried here. Only HTTP(S)
    redirects are followed, with no URL credentials or inherited contact header.
    Existing scheduling remains per original host (not a cross-host crawler).
    """
    import httpx

    target = httpx.URL(url)
    for _ in range(21):
        if target.scheme not in {"http", "https"} or target.username or target.password:
            raise ValueError("Image request requires an HTTP(S) URL without credentials")
        agent = request_agent(str(target), config.user_agent, context.get("commons_contact"))
        entry.setdefault("request_hosts", []).append(dict(host=target.host, user_agent=agent))
        with client.stream(
            "GET",
            target,
            timeout=config.timeout_seconds,
            headers={"User-Agent": agent},
            follow_redirects=False,
        ) as response:
            if response.status_code in {301, 302, 303, 307, 308} and response.headers.get(
                "location"
            ):
                target = target.join(response.headers["location"])
                continue
            yield response
            return
    raise ValueError("Too many image redirects")

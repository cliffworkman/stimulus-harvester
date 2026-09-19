# Reliable multi-source discovery

Search ingests each completed page into the project before requesting the next.
Openverse remains enabled by default. SearXNG and Wikimedia Commons are optional.
Example additions to a project's starting YAML:

```yaml
search:
  seed_queries: ["library interior"]
  query_expansion:
    enabled: false
  searxng:
    enabled: true
    endpoint_url: "http://localhost:8080"
    max_pages_per_query: 3
    max_results_per_query: 40
  commons:
    enabled: true
    contact: "https://your-actual-project.example/contact"
    page_size: 5
    max_pages_per_query: 3
```

Replace the example contact with your own operator email or project URL. There is no
invented default identity. A missing endpoint/contact produces a persisted setup
failure; other providers still run. Existing projects use their saved config snapshot.

## Requests and failures

Each provider sends requests serially. Defaults: one second between request starts,
15-second HTTP phase timeout, at most two retries, 30-second maximum retry backoff,
and a 10 MB decoded response limit. All are configurable under each provider using
`request_delay_seconds`, `request_timeout_seconds`, `max_retries`,
`retry_backoff_seconds`, `max_backoff_seconds`, and `max_response_bytes`.
Backoff starts at one second (five for Commons). Paging defaults to three pages for
Openverse/Commons and one for SearXNG. Commons batches are capped at ten files because
extended metadata is expensive. Request timeouts are httpx phase timeouts, not a
total operation deadline.

429, selected transient 5xx responses, transport/timeouts, and Commons overload
errors can retry within bounds. Numeric/date `Retry-After` values are honored; a
wait beyond the configured bound defers the provider instead of shortening the
server's wait. Exhausted transient failures or denied access defer later queries
for that provider in this operation. Redirects are recorded as HTTP failures; use
the actual API endpoint. There is no proxy rotation or HTML scraping fallback.

SearXNG's first real query probes JSON image search. A valid results array records
capability success. An explicit disabled-JSON response records `json_disabled`;
a generic 403 records `json_access_refused`, because access policy can cause the same
status. Engine failures remain visible even when the response also has useful images.
Malformed JSON, unexpected structures, rejected individual records, and setup
failures are structured evidence. A bad record does not discard other records in
the page. Result/page caps bound ingestion; the full returned response is retained.

Image URLs must be HTTP(S), have a host, contain no credentials/whitespace, and not
look like common HTML/script page paths. SearXNG requires `img_src`; its source-page
`url` is never substituted. Commons also requires declared `image/*` MIME. These
checks establish suitability for an acquisition attempt, not proof of image bytes.
The existing downloader verifies decoded image content and records unreadable media.
Some legitimate image-serving script URLs will be conservatively rejected.

## Evidence and identity

The existing project schema remains version 1. An independent, versioned additive
discovery extension creates `discovery_schema` and `discovery_events` in one SQLite
transaction. It does not alter candidate IDs, reviews, assets, or existing source
rows. Unknown discovery-extension versions are rejected.

Each request has an attempt UUID, query UUID, provider, seed/generated-query recipe,
page/attempt counters, timestamp and status. A `started` record is committed before
the request. Responses/errors have operation-relative raw references. Each valid
source record includes page, rank, result order, query and attempt links, plus full
provider metadata. `item_rejected` records retain the malformed provider item and
its order; it is not silently turned into a candidate.

Raw responses live under `raw_api/<operation-id>/` with unique attempt filenames.
The `.json` extension is used even for non-JSON error bodies; accompanying `.http`
files explain response headers and httpx's decoded-body representation. Responses
over the byte cap are explicitly truncated. Events also have individual JSON
artifacts under `operations/<operation-id>/discovery/`.

Every valid result occurrence is appended to `discoveries`, even if its URL already
exists. Same URL: shared candidate, separate source claims. Different URLs with
identical acquired SHA-256: shared canonical asset, separate candidate IDs and review
histories. Commons' reported SHA-1 never substitutes for acquired SHA-256. Commons
page IDs, media URLs, timestamps, dimensions and extended metadata are retained.
Creator/credit can contain HTML or free text. License metadata is explicitly
`missing`, `ambiguous`, or `reported_unverified`; none asserts permission to reuse.
Legacy candidate-level license summaries are not a reconciliation of source claims.

## Interruption and status

Candidate/source inserts and `page_committed` events share one database transaction.
Earlier committed pages survive a later HTTP failure or keyboard interruption.
Operations finish `succeeded`, `partial`, or `failed`; the CLI warns on partial
completion and permits the user to keep working with retained results. An operation
with successful empty pages and later errors can also be partial. Recovered retries
remain recorded but do not make an otherwise complete operation partial.

SQLite is authoritative. The compatibility JSONL manifest is flushed before its
page transaction, so a crash can leave extra manifest lines not committed to SQLite.
A crash after the page commit can leave a missing event JSON sidecar; the database
event remains available. File writes and SQLite are not one atomic filesystem unit.
Keyboard interruption is recorded by the shared service; a killed process can leave
an attempt/operation at `started`. There is no automatic checkpoint-token resume or
repair utility: rerunning begins a new operation and preserves repeated occurrences.

## Descriptive coverage

```console
stimulus-harvester discovery-report --project "my research project"
stimulus-harvester discovery-report --project "my research project" --operation-id OPERATION_ID
stimulus-harvester discovery-report --project "my research project" --provider-order openverse --provider-order searxng --provider-order commons
```

JSON contains request/failure events, source occurrence and candidate counts, unique
URLs, acquired unique assets, pairwise overlaps, exclusive discoveries, and incremental
counts. All observed providers must appear once in an explicit order. Default order
is alphabetical. Incremental counts depend on that order; exclusive counts do not.
Operation scoping selects source occurrences, while asset coverage uses current
acquisition state. Before download, exact-byte overlap is unknown. Zero acquired
overlap is not proof of different content. These counts are descriptive, not scores
of source quality, construct validity, or scientific eligibility.

## API references and limitations

- [SearXNG search API](https://docs.searxng.org/dev/search_api.html) and
  [image result fields](https://docs.searxng.org/dev/result_types/main/image.html):
  pagination/engines depend on the configured instance and upstream services.
- [MediaWiki search](https://www.mediawiki.org/wiki/API:Search) and
  [imageinfo](https://www.mediawiki.org/wiki/API:Imageinfo): generator search in file
  namespace 6, bounded continuation, raw extended metadata alongside normalized claims.
- [MediaWiki API etiquette](https://www.mediawiki.org/wiki/API:Etiquette) and
  [Wikimedia User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy):
  descriptive application/contact agent, small serial requests, maxlag and backoff.

No live Commons or SearXNG validation is implied by the mocked contract tests. Provider
schemas, instance access, corpus overlap, and search ordering can change. Discovery
does not freeze remote data or guarantee complete enumeration. There is no new GUI,
screening rule, detector, dataset freeze, assembly, or installer in this checkpoint.

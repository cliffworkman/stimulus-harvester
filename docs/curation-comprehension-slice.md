# Bounded curation comprehension slice

Baseline: `a0ab811c060835c85cfb2b63f213993997b4b86c` (0.2.0rc4), clean working
tree. Baseline verification: 308 ordinary pytest cases, including 74 desktop
cases, and 9 release-build cases. This is development source for review; the
version remains rc4 and no replacement rc4 or automatic rc5 build is produced.

## Contribution lineage

Cliff exercised the rc4 search/download/retry/image-viewing workflow and reported
that it worked but remained too bloated and difficult to interpret. His need for
a developer to read the saved outputs motivated this comprehension slice; it
does not recast his finding as a failed workflow execution. The subsequent
Cliff/Lucien (ChatGPT; exact model not inferred) direction bounded this work to
#7, #8, #9, narrow #21, #22 and the destination-identification extension of #4.
Codex's metadata audit identified that request identification followed discovery
provider instead of the destination host. Codex chose and implemented the pure
projections, source-matching filters, tabbed inspector, destination policy and
verification described here. Private project contents are not included here.

## Destination policy, distinct from discovery provenance

`download/policy.py` selects descriptive Wikimedia identification for
`wikimedia.org` and its subdomains, regardless of discovery provider. It uses
`search.commons.contact`; no personal identity or inferred default contact is
embedded. Other destinations retain the configured generic download User-Agent.
An explicit Commons relationship no longer applies Wikimedia headers to an
unrelated download host. This intentionally supersedes that rc4 shortcut.

The downloader preflights all initial destinations before launching jobs.
Redirects are followed in a bounded HTTP(S) chain, with host policy reevaluated
before each request. A redirect requiring a missing contact is stopped before
the Wikimedia request; earlier redirect responses may already have occurred.
Contacts are not carried into unrelated redirect destinations. Request-host/header
evidence is additive. Discovery provider, source records and historical attempts
are unchanged. There is no denial bypass or automatic retry of old failures.

One public `Example.jpg` probe succeeded with HTTP 200 (9,022 bytes), through
Commons redirects to `upload.wikimedia.org`, using the public repository URL as
the explicitly supplied probe contact. This is new bounded evidence, not proof
that identification caused historical denials. Scheduling remains per original
host; this change does not introduce a global redirect-host scheduler.

## Count definitions and actions

- **Found**: distinct current found records, retaining independent source histories.
- **Available**: successfully decoded downloads with current existing size-matching
  files. The metadata-only projection stops at file existence/size; the desktop
  additionally checks decoding in its worker, cached by path/size/modification time.
  Neither check is a new hash or publisher-identity attestation.
- **Unique image files**: distinct acquired SHA-256 identities among available
  records; shown separately when source records share the same image.
- **Unavailable**: failed, missing/changed or locally unreadable downloaded results.
  Pending results are counted separately.
- **Waiting to download**: never-attempted results. A server wait may defer attempts.
- **Screening**: latest recorded state per current image/source, never inferred
  from configuration. Run errors count as recorded attempts, not successful inference.
- **Human review**: current Accept/Maybe/Reject decisions. Viewing, sorting and
  filtering do not record a decision or impose a requirement to review everything.

Failure grouping uses HTTP status, saved response content type, retry evidence
and known bounded downloader messages. Access denied (401/403) means inspect
setup/access policy; success is not promised. Rate limits (429) require waiting.
Server/network failures may be retried later. Size-policy, unsupported SVG,
unreadable responses, missing source (404/410), local write problems and unknown
failures need investigation or a relevant change first. The desktop's **Retry
eligible failures** submits only access/rate-limit/temporary failures whose saved
host wait has elapsed; the service still rechecks state/waits and never requests
successful downloads. Explicit CLI retry semantics remain compatible.

## Search coverage

Coverage is reconstructed on opening from the latest saved search for each
provider/phrase. Provider exhaustion, normal anonymous access limits, configured
limits and unresolved failures are separate. Commons now saves explicit completion
events. For rc4 Commons runs, a saved final response continuation plus a terminal
successful/partial operation is used to infer a configured-limit stop. Interrupted
operations without that evidence remain unconfirmed. Historical provider setup
errors retain their timestamps; they are not claims about today's settings.
No Resume control or continuation contract is introduced. Starting again begins
at the first page.

## Image workspace

Screen and Review start with **Available images**; Discover retains all results
for recovery. Availability choices, visible/total counts and active filter counts
remain visible. Additional filters cover exact provider, phrase, human decision,
recorded screening state, exact license claim, failure category and minimum width/
height. Provider/phrase/license constraints must match the *same source record*.
Missing claims are explicit; no permissive claim is selected automatically.
Headers sort text or numeric dimensions with an identity tie-breaker. Clear
additional filters preserves the explicitly selected availability view.

The horizontal splitter keeps the table and image/details together. A compact
filter dialog avoids increasing the minimum review height. Source, License,
Screening, Review history and Technical tabs separate readable evidence from raw
records. Safe HTTP(S) source links activate with Enter or double click. Provider
HTML is rendered as plain text. Model identities/scores/full IDs remain in
Technical; saved boxes remain optional. Multiple/differing source claims and
exact-byte source relationships are preserved. Review events show chronological
time, reviewer, decision and note, independent of screening and overrides.

Accept/Maybe/Reject and notes remain below the table; keyboard shortcuts remain
Alt+A/M/R and are exposed in button tooltips. Advanced screening overrides remain
reachable on a separate review tab. Long settings and freeze preparation scroll
internally rather than imposing their height on image review. Synthetic native
Windows checks cover 850×690 in Light and Dark modes. This is not a screen-reader,
large-text or human accessibility certification.

Project status is readable, screening policy JSON is opt-in, operation diagnostics
no longer automatically open, and copyable raw evidence/diagnostics remain available.
Advanced configuration deliberately remains an explicit JSON editor. Untouched
freeze validation/export technical surfaces are outside this slice.

## Demo and boundaries

Create a separate fixture with:

```powershell
python -m stimulus_harvester.gui.curation_demo --output "NEW DEMO DIRECTORY"
python -m stimulus_harvester.gui.app --project "NEW DEMO DIRECTORY"
```

It contains 8 results: 4 available records representing 3 unique images, 3 failed,
1 pending, multiple providers/queries/licenses, one current reviewed result with
two chronological decisions, exact-byte duplicates, three mock-screened results
and later results without screening. Mock detector settings are fixture-only;
they do not become production defaults. No project schema migration is needed.

No scientific semantics, freeze scope, immutable membership, or observation/rule/
adjudication separation changed. No real-project downloads, searches or reviews
were performed. Prior release bytes and historical frozen packages were checked
read-only. No tagging, release build, publication or next UX slice is authorized
by this change. Checkpoint 8 remains open, including human acceptance.

## Remaining issue scope

#4 retains broader provider acceptance and uncertainty about historical denials.
#7 retains full six-stage provenance and future collection context. #8 retains
broader facets, large-scale navigation and future collection filtering. #9's
bounded available-image queue is implemented pending human acceptance; recovery
of missing/corrupt originals remains manual. #21 retains progress/cancellation
and broader operation UX. #22 retains large-text, display-scaling and human
accessibility acceptance. These issues remain open; no duplicate issue is needed.
All other deferred issues remain deferred, including #3 durable continuation.

After approval, an rc5 human-review build is recommended because runtime code
changed. A subsequent bounded relevance-triage/navigation slice (#10) should be
chosen from Cliff's next workflow findings, not started automatically.

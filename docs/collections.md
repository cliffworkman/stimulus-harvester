# Collections, independent decisions and immutable research sets

Collections organize candidates by discovery or explicit investigator assignment.
They do not own image files. A candidate can belong to multiple collections; its
acquired SHA-256 asset and every source/license claim remain shared evidence.

Use the collection selector above the workflow. All results retains project-level
review semantics. In a collection, reviews, screening and freeze preparation apply
to that collection. Enter an investigator in the shared identity field. Collections
can be created or renamed and given multiple phrases from the Collections menu.
Their phrases appear in Discover; provider configuration remains project-wide.
Searching records the collection's exact name/phrase revision and joins committed
source records to membership in the same transaction as discovery pages. Searches
do not overwrite the project's saved All results phrases. Search continuation is
not introduced by collections.

Select an image in Discover, Screen or Review, or mark several in Review, then use
Add images to. Remove from collection retains the candidate, sources, acquired
asset, prior reviews, historical membership and other collections. Archive/Restore
preserves the same activation and reviews; archived collections remain inspectable
but cannot be searched or changed until restored. There is no destructive delete.
Renaming never rewrites earlier discovery context. Details show membership and
independent decisions; exact event/source IDs are available under Technical and
Collections → Inspect history.

## Review identity

Each review binds collection ID, candidate ID, membership ID, activation event and
registered asset ID. New membership starts Unreviewed, regardless of prior project,
other-collection or exact-asset-neighbor reviews. Remove/re-add gives a new activation
and a new unreviewed state. A changed registered acquired asset likewise makes old
reviews inapplicable. Rename and archive/restore do not change activation. Earlier
review events remain attributable and append-only. External modification of original
bytes is detected by freeze integrity checks; a file edit is not a newly registered
canonical acquisition. Viewing or assigning never reviews an image.

Single reviews and explicit Maybe/Reject batches use optimistic state checks and
atomic writes. A stale activation, asset or prior review event rejects the operation
without partial decisions. Legacy reviews remain contextual evidence. They do not
satisfy collection-reviewed eligibility.

## Screening

Collection policy is independent of project policy. Saving an explicit policy or
Evaluate saved observations evaluates compatible stored evidence without inference.
Run configured screening is the separate, explicit inference operation and observes
only this collection's candidates. It does not activate a project policy. Optional
ML dependencies are needed for inference only. No detector/model/threshold default
is provided.

Evaluations reference the observation ID, rule/version, immutable policy ID and
exact membership activation/asset scope. The same observation can support different
collection outcomes. Reuse requires matching candidate, asset and detector parameters,
including normalized thresholds. Missing compatible evidence produces incomplete
evaluation/review-required, not invented absence. Cross-candidate automatic observation
reuse solely because two candidates share bytes is not implemented.

Overrides require investigator, reason and unchanged exact evaluation scope. They
do not create an Accept/Maybe/Reject review. Accept does not remove an excluded
screening disposition. With no configured/evaluated policy, explicit human acceptance
can resolve review-required, retaining the existing reviewed-freeze semantics.

## Collection freezes

With a collection selected, Prepare freeze validates every active member, regardless
of thumbnail/table filters or temporary marks. All members need an attributed final
Accept/Reject; Maybe/Unreviewed block. Accepted exclusions need an explicit screening
override. Unavailable accepted assets and integrity failures block. Nothing unresolved
is silently dropped. Double-click candidate blockers to return to that collection's
review workspace. Exact-byte accepts/conflicts retain the existing explicit
deduplication/include-or-omit adjudication rules, using collection decisions.

Collection freezes use artifact format 2. They capture collection identity/name,
active membership/activation, membership and search histories, candidate/asset/source
relationships, collection reviews, policies/evaluations/overrides, configuration,
operation evidence and reproducibility anchors. Project reviews remain separate
context. Assembly and export dispatch on artifact version and use frozen scoped
decisions. Later live edits never revise either artifact version.

All results continues to create format-1 project freezes. Existing v1 files are
neither migrated nor reinterpreted; v1 verification/materialization remains supported.
Old releases reject v2 rather than treat it as v1. Revisions require the same format
and collection identity. One collection per freeze is supported; multi-collection
composed packages and unreviewed snapshots remain separate future work.

## Additive storage and recovery

Collections schema 1 is created only at first use. The operation validates SQLite
integrity and foreign keys, creates and verifies a SQLite backup under
`migrations/collections_v1_*/before.sqlite`, applies additive DDL in a transaction,
checks old row counts and foreign keys, and writes `migration.json` with backup
SHA-256 and outcome. Core project schema/identity and old artifact files stay intact.
Opening an old project does not fabricate collection membership.

`collections` and `collection_memberships` hold stable identities. Append-only
`collection_events` holds name/phrase/lifecycle revisions; phrases retain IDs for
unchanged text within edits. Editing phrase text creates a new phrase identity while
prior revisions survive. Immutable `collection_search_contexts` binds operations to
name/phrase revisions. `membership_events` records add/evidence/remove with exact
source or manual origin. Discovery during an inactive membership creates a new
activation; refresh never replays old sources into active membership.

`collection_reviews`, `collection_policies`, `collection_evaluations` and
`collection_overrides` hold separate immutable histories. Current states are derived,
with explicit scope checks. An interrupted migration retains its verified backup
and outcome evidence. Do not automatically delete locks, backups or unregistered
artifact directories.

Current limits: collection context is session-local; reopening defaults to All results.
Archive inspection shares the selector with active collections, marked archived.
The initial project projection still loads project metadata and checks image
readability; collection filtering is not a replacement for a future paged database
workspace. Technical history remains detailed. Generalized screening, Basic/Advanced
redesign, search resume, unreviewed snapshots and composed packages are not part of
this arc. Windows signing remains intentionally deferred across the suite.

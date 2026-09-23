# Arc B collection design and scientific-contract boundary

Status: pre-implementation design; stopped at the expressly reserved review/freeze
fork. No collection tables, migrations, UI, or scientific behavior have been
implemented. Baseline: `2c48b01ede55b68b4467c21361d86cdd43155407` (rc5).

The supplied Arc B direction approves provenance-based candidate membership and
collection-specific screening. It also explicitly requires a stop before choosing
new review semantics or a materially changed immutable-artifact contract. This
document separates the settled membership design from those remaining choices.

## Concrete boundary found

`review/db.py` has one current `reviews` row per `local_id` and chronological
`review_events` with no collection scope. Same-URL discovery reuses a candidate,
even when another provider/query discovers it. `gui/pages.py:ReviewPage.review`
submits a local ID, label, note and investigator, with no review context.
`review/service.py:record_batch` likewise validates candidate, asset and prior
label, not membership context.

`frozen/snapshot.py:Scope` accepts only `all_current` or explicit candidate IDs.
The free-text selection rule is explanatory text, not an executable policy or a
scientific scope identifier. `snapshot()` reads that candidate's single review
and `screening.engine.report_from_connection()`'s latest project policy.
`frozen/materialize.py:assemble` checks those same frozen candidate-level fields.
`frozen/store.py:verify_directory` accepts only artifact format version 1.

Thus adding a collection filter alone cannot support “accept this candidate in
Prison interiors, reject it in Church interiors.” Recording the second decision
replaces the candidate's current first decision, while retaining its audit event.
Using two candidate-subset freezes with different descriptions still uses the
same current review and project policy. These are correct rc5 behaviors; the bug
would be presenting them as independent collection decisions.

A collection-only organizational view with clearly labeled legacy project reviews
is technically safe. It would have to withhold collection review and collection
freeze/export. That is a valid smaller scope, but would not satisfy the full #12
and #13 milestone, whose acceptance includes scoped review/export and frozen
policy provenance. This is why the boundary is being presented before a partial
CRUD implementation or a silent scientific-contract change.

## Settled membership implementation design

Use an additive `collections_schema` version 1 alongside the current schemas.
Create it only when collections are first used, under the project writer lock.
Before changing an existing database, create a SQLite backup through the backup
API, verify it, then apply the versioned DDL atomically. Do not edit historical
discovery rows, project identity, reviews, assets, or registered artifacts. Reads
must tolerate absence of this schema. All results is a view over existing
candidates, never a fabricated historical collection.

| Proposed record | Identity and meaning |
| --- | --- |
| collections | Stable `collection_id`; current name, active/archived state; revision number |
| collection_events | Append-only event ID, collection ID, action, previous/new values, operation, investigator and time |
| collection_phrases | Stable phrase ID within a collection; editable current phrase/enabled state with revision history |
| collection_search_contexts | Immutable operation-to-collection association with name and phrase revision/text snapshots, expansion recipe and run context |
| collection_memberships | Stable relationship ID for one `(collection_id, candidate_id)` pair; unique pair; current state derived from ordered events |
| collection_membership_events | Append-only add/evidence/remove event; relationship ID, origin kind, actor, reason, operation/time, optional exact discovery source ID and search context reference |

Origins include `discovery` and `manual`; future import origins can reference a
separate import event without masquerading as discovery. Unsupported origins are
rejected until their validators exist. No authoritative asset-to-collection table
or per-collection directory is created.

### Transaction and history rules

1. Create validates a nonblank name and records a stable ID and create event.
   Duplicate display names may be disambiguated by ID; they never imply identity.
2. Rename updates current name and appends an event. Search contexts retain their
   names/text as recorded. Phrase edits/deactivation affect future searches only.
3. One search operation targets one active collection, with one or more phrases.
   Snapshot that context before requests. Existing provider query IDs, expansion,
   request/page events, and source IDs remain authoritative historical records.
4. Each committed result adds its exact source reference as membership evidence
   in the same transaction as candidate/source/page commit. Do not infer membership
   later by matching mutable phrase text or URLs. Source records survive if the
   candidate already exists. A failed or interrupted search retains committed
   membership alongside its committed pages; no new continuation feature is added.
5. Manual assignment validates an explicit candidate selection and actor. It
   creates/reactivates a relationship with a manual event, never a fake query.
   Atomic batch validation prevents partial assignment on stale/unknown IDs.
6. Removing membership appends a removal event for the relationship and makes it
   inactive, without deleting earlier reasons. A later explicit assignment or a
   newly committed discovery in that collection can reactivate it with a new
   activation event. A refresh/reopen cannot reactivate it from old source rows.
7. Multiple discoveries while active append reasons without creating duplicate
   current membership. Repeated identical source references are idempotent.
8. Archive is the ordinary non-destructive removal of a collection from active
   navigation. Retain its relationships and all events. Prevent writes/searches
   into archived collections; inspection and restore remain available. Restore
   exposes the retained active relationships. No hard-delete command is planned.

All events have deterministic sequence order; timestamps alone do not order
ties. Membership state can be rebuilt from its log. Protect evidence against
UPDATE/DELETE with the existing append-only pattern. Mutation uses an expected
revision to reject stale dialog/selection submissions.

### Derived image membership and counts

Join active relationships to candidates, then candidate.asset_id to assets. One
candidate may appear in many collections. Several candidates in a collection may
share one SHA-256 asset. Membership writes never copy original image bytes or
create new asset locations. Retain all source/license claims and reviews for all
underlying candidates; no conflict is resolved by grouping.

Show distinct candidate counts as found, available, failed and pending, using
existing availability semantics. Also show unique acquired asset count separately
so several candidate records sharing bytes are not presented as several unique
images. Unavailable candidates can have memberships and inspectable histories.
Image-displayability remains distinct from successful acquisition.

### UI integration

Use one collection selector with All results on the existing discovery/curation
surfaces, and compact controls for create/rename/archive/restore and phrases.
Selecting a collection filters Arc A's shared table/grid order, navigation and
temporary selection; changing context clears marks and stale inspection work.
Preserve the other facets and show zero-result explanations. The details panel
lists active and archived membership names, with human-readable origin summaries
and exact IDs/history under Technical.

Assignment/removal explicitly previews affected candidates and destination; it
never records a review or screening override. No collection default is invented
when the user operates from All results. No multi-collection search fan-out is
implied by overlapping phrase text.

## Scoped screening design within the approved policy contract

Observation collection remains independent of collection policies. Existing
observations retain exact candidate, asset, mechanism/inference parameters,
threshold normalization, raw evidence and provenance. Deterministic collection
evaluation references a compatible observation ID instead of invoking inference
or appending a duplicate observation solely because another policy exists.
Changing normalization thresholds is not a pure disposition mapping and must not
silently reuse an incompatible normalized status. Re-normalization, when explicit,
retains its derived-from raw evidence reference under existing rules.

Add explicit policy scope `{kind: collection, collection_id}` and immutable policy
versions. Evaluation scope also binds membership ID and activation event, current
asset ID, exact observation ID, rule version and policy ID. Removing/re-adding
membership cannot revive an old override accidentally. Missing compatible evidence
requires review rather than inventing an observation. A policy change creates new
evaluations; old evaluations remain interpretable. Different collections may have
different outcomes for the same observation ID.

Project and collection policies are parallel scopes, with no implicit inheritance
or precedence cascade. No collection policy means not configured/review required,
not a silently inherited project policy. The legacy project path stays available
and explicitly labeled. Selecting a collection selects its policy scope; All
results must not collapse conflicting collection dispositions into one scientific
answer. Scoped overrides require identity/reason and expected exact evaluation
scope; a stale scope is rejected. Preserve project overrides without promoting
them to collections. No detector/model/threshold default is added.

## Decision required: review scope and corresponding immutable format

Recommended next contract, NOT IMPLEMENTED OR ASSUMED:

- Preserve existing current reviews and events as project-scope evidence.
- Add independent collection review events. New collection membership starts
  unreviewed regardless of project or other-collection acceptance/rejection.
- Bind each collection review to candidate, collection, membership activation,
  asset, investigator, label, note and time. Changing bytes or reactivating removed
  membership requires a new decision; renaming does not invalidate the decision.
- Do not automatically copy project reviews, propagate by asset, or propagate
  between collections. Show legacy evidence explicitly as reference history.
- Collection batch review targets one exact scope with stale-state checks; keep
  the existing Maybe/Reject-only batch restriction.
- A collection-aware freeze requires final scoped reviews and scoped effective
  screening for its selected memberships. Preserve the existing rule that an
  Accept does not override an excluded screening disposition.

This recommendation deliberately chooses new scientific decision identity and
invalidation rules. Those choices need the human contract decision reserved in
the Arc B instructions. The alternative is an explicitly organizational Arc B
with collection review/freeze withheld, legacy review available only in a clearly
labeled project context, and #12/#13 left partial. A label-only UI warning cannot
make the current single review field hold two simultaneous decisions.

### Proposed immutable-format compatibility plan

Use a separately versioned collection-aware artifact format (proposed version 2),
retaining a version-1 reader/materializer/export path with unchanged semantics.
The existing registry schema need not be rewritten simply to register a new
artifact's canonical manifest. Do not convert or regenerate any historical
manifest, checksum, asset or export. Old rc5 readers should reject the unsupported
new format explicitly rather than misread it as a legacy candidate-only selection.

Start with one collection per freeze. Multi-collection composed packages remain
outside Arc B. The new snapshot must capture stable collection ID/name revision,
exact active membership/activation events and their reasons, relevant immutable
search contexts/source/page histories, candidate-to-asset mappings, all retained
source claims, compatible observation references, exact scoped policy versions,
evaluations/overrides, scoped review events and duplicate adjudications. Freeze
validation binds their exact revisions before confirmation. Later live edits have
no effect on the captured records.

Deduplicate bytes within that snapshot while retaining all underlying candidate
relationships and source conflicts. Do not rewrite candidate.review to smuggle a
collection review into version 1. Version-2 assembly/export must validate and
materialize from explicit frozen scoped decisions, never today's live state or
legacy project decisions. A version-2 child must not silently treat a version-1
parent as a collection ancestor; cross-format lineage would require an explicitly
documented reference-only relationship, not a claimed reproduction.

Before enabling writes: fixture-test both versions, checksum-preserving reopen,
assembly/export after live rename/removal/policy/review changes, failure recovery,
unsupported-format errors and non-inheritance of project/other-collection reviews.
The transition requires semantic format dispatch and validation; accepting extra
JSON keys in the old verifier alone would be insufficient.

## Implementation sequence after the reserved decision

1. Additive backed-up schema and event-store lifecycle with provenance invariants.
2. Search-context snapshot and transactional source-to-membership registration.
3. Arc A collection selection, counts, details and explicit assignment/removal.
4. Observation reuse, scoped policy evaluation and scoped overrides.
5. Approved scoped review boundary plus versioned freeze/materialization support,
   or the explicitly approved organizational-only limitation.
6. End-to-end synthetic/public fixtures, read-only scale projection, native GUI,
   historical compatibility and milestone packaged verification if warranted.

Keep #12 and #13 open until the approved end-to-end scope is demonstrated. No
Arc C/D/E implementation, new RC build, signing work, tagging or publication is
part of this architecture checkpoint.

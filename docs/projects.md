# Durable projects and lineage — Checkpoint 2

A project is a folder containing `project.json`, `review/state.db`, the configuration
snapshot, original images, source evidence, and review history. No registry is needed.
Both frontends can use `services.create_project`, `open_project`, `project_status`,
`candidate_evidence`, `run_stage`, and `review_application`. The CLI uses these services.

## Identity and storage

- **Discovery/source:** one provider/query occurrence with source-specific license,
  creator, metadata, retrieval timestamp, and raw-response relationship. A source's
  original JSON is retained. Rank/page/order are nullable; legacy gaps are not invented.
- **Asset:** exact bytes identified by `sha256:DIGEST`; associated dimensions and local
  locations. Multiple discoveries and candidate histories may refer to one asset.
  Assets have no license/creator columns: those claims belong to sources.
- **Candidate:** stable `candidate_UUID` with its primary legacy image ID and aliases.
  Same-URL discoveries share a working candidate. Different-URL identical bytes share
  an asset but retain distinct candidate/review histories. This preserves potentially
  conflicting investigator decisions. Near duplicates remain different assets.
- **Project:** stable `project_UUID`, created timestamp, and schema version. The DB is
  authoritative; `project.json` mirrors it and can be regenerated if missing. A mismatch
  fails visibly. Copies retain identity; project forking is not implemented.

These UUIDs are Harvester-local, provisional identities, not a suite-wide stimulus
standard. Other applications' semantics have not been inspected. External identity
mappings and derivative relations can be added later without reusing legacy IDs.

The legacy `images`, `reviews`, and `review_events` tables remain intact as the current
working projection. `candidates`, `legacy_ids`, `assets`, `asset_locations`, `discoveries`,
and `acquisitions` provide explicit lineage relationships. Source claims remain visible
through browser detail/evidence endpoints and `lineage_LABEL.jsonl` export sidecars.
Flat CSV/JSONL remain compatible first-source projections. The existing license export
policy still uses that first source. [Frozen exports](freezing.md) preserve all retained
source-specific claims with an explicit no-filter policy.

## Migration and relocation

`project open --project PATH` recognizes old run folders containing the config snapshot
and SQLite DB. No discovery is rerun. Schema 0 is migrated to schema 1 on a separate
database copy. Integrity/foreign keys and available original hashes are checked before
atomic replacement. `review/state.pre-v1.MIGRATION_ID.db` keeps a recoverable old DB.
Malformed search JSONL or changed content fails migration and leaves the original DB
untouched. Missing originals are recorded as migration notes; their existing hashes
remain explicitly historical metadata. Future unknown schema versions are rejected.

All image/review rows, legacy IDs, review events, and original downloaded bytes are
preserved. Source records are recovered from both database rows and search JSONL with
explicit evidence origins, so duplicates in this evidence are intentional. Historical
stage manifests and raw responses are never rewritten. Operational image paths are
normalized to project-relative paths using known artifact-directory suffixes, allowing
recovery from obsolete Windows/Linux run roots. Available original bytes must match
their historical hash. External paths without recognizable owned suffixes stay external.

Stop older application versions and external database writers before migrating. SQLite
backup captures committed DB state; cooperative project locks serialize current service
writers. This does not attempt to coordinate unrelated direct SQLite writers.
Nonstandard legacy databases using WAL mode are rejected: create an offline backup in
DELETE journal mode first, so old WAL sidecars cannot be replayed onto a replacement DB.

Move/copy the **whole folder** to relocate a project. Owned paths resolve relative to
the new root, independent of cwd. Spaces and Unicode are supported. Historical raw JSON,
logs, external references, and backup DB paths may still name old locations; those files
are evidence and remain unchanged. Status reports missing asset locations. Legacy export
HTML should be regenerated after migration; it may contain pre-migration relative paths.

## Operations and recovery

Shared services allocate `operation_UUID`, persist request/config snapshots, append
start/completion/error events, and write structured results. Stage-manifest snapshots
and export snapshots live under `operations/OPERATION_ID/`. Search raw responses use
per-operation directories. Normal reruns do not overwrite earlier operation evidence.
Legacy stage projections and manifest files remain mutable; this is deliberately not
a full event-sourcing rewrite. The separate [freeze system](freezing.md) captures these
records in immutable scientific snapshots.
Requests capture software/Python/platform versions, schema version, git commit when
available, and the configuration hash. Failure to copy operation evidence is reported
as a failed operation rather than silently claiming complete success.

Progress is currently stage-level. Cancellation is checked before a stage, not inside
network calls/model inference. A crash can leave an operation marked started and an
`.harvester.lock` file. Confirm all project writers have stopped before removing a stale
lock manually; locks are never automatically stolen. Existing stages retain their
current resume behavior, including skipping terminal failed-download rows.

CLI and browser edits use the shared locking convention. Direct DB edits and low-level
stage calls are compatibility APIs and do not offer the same operation-level guarantees.

## Legacy accepted output

`images/accepted/` is **non-authoritative convenience output**. It may retain copies
from previously accepted decisions. Browser review no longer adds copies automatically;
export writes a conspicuous README, and browser/CLI messages explain the limitation.
Current review state/manifests are the working membership record. Authoritative research
membership uses frozen decisions followed by deterministic versioned assembly; see
[the freeze workflow](freezing.md).

The HTML contact sheet now uses the same selected rows as CSV/JSONL. Summary JSON still
describes the whole project. These are working reports, not self-contained frozen exports.

## Verification

Run `python -m pip install -e ".[dev]"` followed by `python -m pytest -q`.
The packaging test builds a wheel offline with installed setuptools/wheel, inspects its
resources, installs it into a temporary target, and serves browser pages/static resources
from that target in a separate isolated Python process outside the checkout.
Install build tools with `python -m pip install setuptools wheel` when necessary.

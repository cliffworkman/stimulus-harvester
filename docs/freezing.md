# Immutable freezes, assembly, and research export

The project remains editable. A freeze captures an explicit selection and its evidence;
an assembly copies original bytes using only that freeze. A frozen export packages the
assembly and its evidence for inspection without the project database. No detector is
enabled, selected, or rerun by any of these operations. No image transformation occurs.

## CLI workflow

Write a UTF-8 JSON request with the investigator identity and an explicit scope:

```json
{
  "reviewer": "investigator@example.org",
  "scope": {
    "mode": "all_current",
    "selection_rule": "All candidates currently registered in this project"
  },
  "deduplicate_accepted": false,
  "duplicate_resolutions": []
}
```

Replace the example investigator identity with the actual reviewer. For a subset use
`"mode": "candidates"`, a nonempty `"candidate_ids": ["candidate_UUID", ...]`, and a
selection rule explaining that subset. IDs are canonical candidate IDs, not filenames or
legacy local IDs. Validation records the exact resolved scope, outside-scope IDs, and the
current candidate inventory. Outside-scope candidates sharing scoped assets have their
full decision/source histories retained as related evidence.

```bash
stimulus-harvester freeze validate --project "my research project" --request freeze-request.json
stimulus-harvester freeze create --project "my research project" --request freeze-request.json
stimulus-harvester freeze list --project "my research project"
stimulus-harvester freeze show --project "my research project" --id FREEZE_ID
stimulus-harvester assemble --project "my research project" --freeze FREEZE_ID
stimulus-harvester export --project "my research project" --assembly ASSEMBLY_ID
stimulus-harvester verify-frozen --package "path/to/frozen_export_ID" --sha256 MANIFEST_SHA256
```

`freeze list` lists all three artifact kinds. `freeze show` verifies and displays any
registered artifact. `export --freeze FREEZE_ID` creates an assembly and exports it.
Freeze/export results include the manifest SHA-256; retain it separately as a trust anchor.
The validation command exits with status 1 and reports all discovered semantic blockers;
malformed requests, invalid project configuration, or unreadable databases are input errors.

To revise a selection, edit the live project and create a new freeze with
`"parent_freeze_id": "FREEZE_ID"` in its request. To demonstrate reproduction:

```bash
stimulus-harvester assemble --project "my research project" --freeze FREEZE_ID --parent-assembly ASSEMBLY_ID
```

The parent must be an intact assembly of that same freeze. Its files and membership are
verified before agreement is recorded. Every invocation produces a new artifact ID;
membership, output names, and image hashes reproduce, while creation times/IDs differ.

## Preconditions and scientific semantics

Every in-scope candidate needs a final accepted/rejected decision, a named investigator,
and a matching latest review event. Maybe and unrated block freezing. A legacy label
without matching review evidence is insufficient: record an explicit investigator decision.

| Effective screening state | Explicit review | Freeze behavior |
| --- | --- | --- |
| eligible | unrated/maybe | Blocked; eligibility does not imply acceptance |
| review_required | accepted | May proceed, with human decision/evidence preserved |
| excluded | accepted | Blocked until an explicit effective screening override removes exclusion |
| any | rejected | Preserved as rejected evidence; not included in membership |

Screening disabled is supported. Strict zero-human rules remain explicit project choices:
detected may map to excluded, and uncertain/not_detected/error/not_run to review_required.
Not detected never establishes absence. Observations, rule evaluations, scoped screening
overrides, investigator review events, and duplicate adjudications remain distinct records.
A new policy/evaluation or asset may invalidate a previous override under existing rules.

The current YAML and last activated screening evidence policy are recorded separately.
If they differ, validation warns and freezes both; it does not reinterpret existing evidence
using unexecuted configuration. Detector identity, model/weight metadata, thresholds,
observation IDs, rule versions, and overrides are copied from the actual screening records.

## Exact-asset duplicates

Identity is SHA-256, not appearance or a filename. Distinct hashes remain distinct assets.
Validation lists each exact-asset group and all associated candidate decisions.

* Multiple accepted candidates sharing bytes require `"deduplicate_accepted": true`;
  one image is materialized and every accepted candidate relationship is retained.
* An in-scope accepted/rejected conflict also needs an explicit include/omit adjudication:

```json
{
  "asset_id": "sha256:FULL_ASSET_HASH",
  "membership": "include",
  "reviewer": "investigator@example.org",
  "reason": "Document the scientific basis for retaining these exact bytes once"
}
```

Place that object in `duplicate_resolutions`. `omit` excludes the asset, while preserving
every original decision. Adjudications for assets without an in-scope membership conflict
are rejected. Multiple accepts still require the deduplication flag. Duplicate adjudication
does not bypass unresolved reviews or screening exclusions. Outside-scope decisions are
shown and preserved for shared assets but do not determine this explicitly scoped selection.
All retained source claims for included shared assets survive, including claims belonging
to rejected or outside-scope candidates.

## Evidence model and layout

The additive `frozen_schema` version 1 registers immutable artifacts in `frozen_artifacts`.
Each has a project ID, artifact ID/kind, freeze ID, optional parent ID, timestamp, operation
ID, relative path, canonical manifest text, and manifest SHA-256. UPDATE/DELETE triggers
reject normal database changes. Existing core and screening schemas are unchanged.

Canonical JSON uses sorted keys, compact separators, UTF-8, a final newline, and no NaN.
The freeze manifest includes request/scope, scoped and related candidate histories, reviews
and review events, assets and hashes, acquisition/discovery/source relationships, screening
histories, policies and overrides, configuration text/hash/parsed values, operations/events,
duplicate adjudications, exact membership, validation, semantics, and software versions.
Software evidence includes package version, available Git commit, Python/platform/schema
versions, and hashes of every installed package Python source file (including uncommitted
code when working from a checkout).

```text
freezes/freeze_UUID/
  manifest.json
  config_snapshot.yaml
  evidence/<SHA256>.bin       # raw source and available operation request/config evidence
  checksums.sha256
assemblies/assembly_UUID/
  manifest.json
  stimuli/<SHA256>.<ext>      # exact original acquired bytes
  checksums.sha256
frozen_exports/frozen_export_UUID/
  manifest.json
  assembly-manifest.json
  freeze/                    # complete freeze, including packaged evidence
  stimuli/<SHA256>.<ext>
  stimuli.csv
  stimuli.jsonl
  source-claims.json
  summary.json
  index.html
  README.txt
  checksums.sha256
```

Checksums cover every packaged file except the checksum file itself. Each manifest inventories
its contents except itself/checksums; its own hash is registered and reported separately.
Raw evidence is copied by content hash once per artifact. Original paths remain historical
provenance, with mappings to packaged evidence; inspection of the export needs no SQLite DB.
Non-shared outside-scope candidates remain in the live project and frozen scope inventory,
but their full review/source histories are not exported as part of this selection.

## Assembly and integrity

Assembly reads frozen membership, decisions, effective screening, canonical asset hashes,
and frozen asset locations. It ignores live reviews, live configuration, new sources,
new screening evidence, and the legacy accepted folder. Referenced historical assets retain
registered hash metadata; only member assets require original bytes at freeze/assembly time.

All existing frozen locations must still match the asset hash. A missing location can be
satisfied by another already-frozen valid location; no remaining valid location fails.
Changed bytes at any existing frozen location fail, even if another copy is intact.
No new live asset location or accepted-folder copy is silently substituted. Paths within
the project resolve relative to its current root, supporting whole-project relocation.

Every member is checked before staging; source and output bytes are checked again during
copy. A failed copy or integrity check publishes no assembly. Names use the frozen full
SHA-256 and frozen extension; order is asset-ID order. Manifests map each output to its
asset, accepted candidates, all relevant sources, and original locations. Image content is
never resized, recompressed, or normalized.

After materialization, exporting an existing assembly uses its copied bytes, so it can
succeed after originals become unavailable. Integrity checks still require the assembly
and its freeze evidence to be intact. Assembly/export reopening does not require a valid
current YAML configuration.

## Source-specific licensing and export membership

The explicit policy is `preserve_all_source_claims_no_filter`. Frozen export retains
conflicting, missing, and uncertain license/creator/attribution claims from all retained
sources; it makes no legal eligibility inference. Membership is identical across images,
CSV, JSONL, HTML, manifest, and summary. HTML image links are package-relative and metadata
is escaped. CSV encodes multiple source records as JSON in a cell. `source-claims.json`
also preserves claims for rejected scoped evidence.

License filtering, alternate labels, and partial output-format requests are rejected for
frozen export. Legacy `export` without `--freeze`/`--assembly` remains a mutable report
with its older first-source license filtering; it is not a frozen research package.

## Immutability limits and recovery

Immutability is enforced by the application, append-only registry, unique directories,
and hash verification. Files are not OS write-protected or cryptographically signed.
Accidental/manual file changes are detected on read; an attacker controlling both the
database and all bytes can rewrite evidence. Portable verification without an external
expected manifest hash establishes internal consistency only, not authenticity.

Publication stages files on the same filesystem and renames a complete directory before
committing its registry row. Copy/validation failures remove only the owned staging directory.
A process/power failure between rename and database commit can leave a complete but
unregistered orphan; services will not use it as an artifact. A crash can also leave a
pending directory or cooperative lock. Automated crash recovery and orphan adoption are
not implemented. Inspect the operation log, preserve evidence, and confirm writers are
stopped before manual recovery. Do not edit a registered manifest to repair it.

SQLite IMMEDIATE transactions provide a coherent decision/evidence snapshot. Project locks
coordinate shared services. External filesystem edits are not locked; hash checks establish
the bytes copied at materialization, and later verification detects subsequent changes.
Maintain project/package backups for archival preservation.

## Shared APIs, demo, and verification

The [desktop views](desktop.md) call `services.freeze_validate`, `services.run_stage` with
`freeze-create`/`assemble`/`export-frozen`, `services.frozen_artifact`,
`services.list_frozen_artifacts`, and `services.verify_frozen_package`. CLI callbacks only
parse options and present service results. Scientific transitions remain in the backend.

```bash
python -m stimulus_harvester.frozen.demo --output "new synthetic demo directory"
python -m pytest -q
```

The demo creates synthetic original images and explicitly mocked detector outputs. It
shows exclusion/override, duplicate include adjudication and licensing disagreement, then
freezes two assets. Later reviews, observations, rules, configuration and sources change.
Reassembly from the old freeze reproduces two assets and a new child freeze yields one.
Old freeze/assembly files are asserted unchanged and the portable export is hash-verified.
Fixture bytes/actions are deterministic; artifact IDs and timestamps are unique per run.
This is a software invariant demonstration, not additional detector benchmark evidence.

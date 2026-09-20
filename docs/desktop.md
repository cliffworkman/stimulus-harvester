# Stimulus Harvester desktop

The optional PySide6 desktop is a thin client of the same shared services as the CLI
and browser review. No model or threshold default is selected. The desktop opens,
reviews and exports projects without PyTorch, torchvision, OpenCLIP or Ollama.

## Start and reopen

```bash
python -m pip install -e ".[gui]"
stimulus-harvester-gui
stimulus-harvester-gui --project "path/to/my research project"
# Equivalent module entry point:
python -m stimulus_harvester.gui.app --project "path/to/my research project"
```

Project identity comes from the explicit folder and backend database/manifest, not an
application registry. Whole-folder relocation and spaces/Unicode paths are supported.
Open/migrate uses the existing backend migration safeguards. New projects start with no
seed queries and no screening policy. A name and empty/new destination are sufficient;
ordinary discovery and screening settings are forms rather than YAML editing.

## Six stages

1. **Project.** Browse/create/open, inspect identity/counts/software, check optional
   capabilities and recovery guidance. The synthetic demo creates six safe fixture
   candidates at the unresolved review boundary, using the CP5 fixture helpers and
   saved mock detector outputs. Advanced resolved JSON configuration is available;
   backend validation and audited operations remain authoritative.
2. **Discover.** Enter one seed per line, select Openverse/SearXNG/Commons, set provider
   page/result limits, configure SearXNG endpoint and Commons operator contact. Preview
   deterministic expansion and setup checks. Save/discover records configuration before
   searching. Partial success remains distinct, with provider/query failures exposed.
   Download originals explicitly. Candidate/source views retain all claims, URLs,
   queries, acquisition state, sizes and exact-asset relationships. Coverage reports
   show provider order, incremental URLs/assets and pairwise overlap; these are descriptive.
3. **Screen.** Explicitly choose model, thresholds and rule; no model or 0.10/0.50 is
   preselected. The common form supports a user-selected strict zero-human mapping or
   review-all mapping. Arbitrary existing multi-detector policies remain inspectable
   and editable through advanced backend-validated configuration. Run inference only
   when configured and dependencies are present. Disable screening explicitly saves
   and activates an empty policy without inference, preserving prior evidence.
   Saved observations and effective dispositions are separate. Counts describe the
   latest saved observation per candidate/current asset/source; multiple sources may
   contribute. Missing records are not invented as detections or proof of absence.
4. **Review freeze.** Select a candidate, enter investigator identity/notes, and record
   Accept, Maybe/defer or Reject. Alt+A/M/R apply within the review widget only. Source
   claims, saved observations, exact-asset relationships and full review history are
   inspectable. Screening overrides require an explicit disposition and reason and
   use the observed screening scope to reject stale adjudication. Accept never silently
   overrides an exclusion. Prepare freeze selects all current candidates or an explicit
   subset plus selection rule. Validation lists candidate/asset blockers and warnings;
   double-click candidate issues to navigate to review. Conflicting exact duplicates
   show original decisions/sources and require backend-supported include/omit resolution
   and reason. Multiple accepts also require explicit deduplication. The confirmation
   summarizes counts, exclusions, overrides, adjudications, investigator and parent.
   A snapshot fingerprint rejects concurrent edits after confirmation. Creation always
   revalidates in the backend; frozen evidence has no Edit action.
5. **Assemble.** Select an immutable freeze, inspect its verified membership, and optionally
   select a prior assembly for reproduction comparison. Assembly never uses current
   review labels or the accepted folder. Copied original bytes and immutable IDs/paths
   are verified by the existing backend. Missing/changed bytes are explicit failures.
6. **Export.** Select a freeze and existing assembly, or explicitly create one from the
   freeze. The package preserves all retained source-specific license/attribution claims;
   no legal permission is inferred or authoritative license selected. After publication,
   the GUI loads/verifies the artifact and shows its destination, lineage, membership,
   manifest/hash and source claims. The verified output folder can be opened. Frozen
   history views are read-only; advanced manifests remain available. Legacy mutable
   export stays in the CLI rather than the ordinary desktop flow.

## Images and saved boxes

Previews are decoded/resized in a worker into in-memory display copies. Canonical image
files are not rewritten. Saved detection boxes are optional painted overlays; detector
identity, thresholds, normalized status and raw evidence remain inspectable. Historical
observations for a different asset can be inspected as evidence, but cannot be overlaid
on the current image. `not_detected` is not confirmed absence. A detection becomes an
exclusion only under the configured rule; investigator rejection is a separate decision.

## Progress, cancellation and errors

Every service call, network operation, model run, migration, preview load, large copy,
export and hash verification executes in a worker. The window owns one task at a time;
its explicit project path is captured at submission. Queued Qt signals update views.
Progress is stage-level with an indeterminate indicator, not an invented percentage of
network or inference completion. The final 100% applies to the completed operation,
not a clicked workflow tab. No tab is marked complete merely from visiting it.

Cancellation is supported **before backend invocation only**. Once a stage starts, the
button disables and the message says the stage must finish. Closing while work runs is
refused; no thread is killed and no running operation is hidden. Finer in-flight
cancellation remains a future backend capability.

Errors distinguish input/configuration, setup/provider, partial discovery, dependencies,
screening/model, freeze blockers, integrity, recovery and unexpected errors. A practical
next step appears with the message. Expandable details retain traceback text and the
operation evidence path. Optional dependency failures disable inference, not project
inspection/review/export. Capability checks are explicit and bounded; installed modules
are distinguished from successfully tested runtimes. Ollama probes the configured local
service only when requested. CUDA availability is checked with the optional runtime.

Locks, pending directories and complete unregistered orphan directories are detected and
listed with guidance. The app never steals locks, deletes research evidence or adopts
orphans automatically. Stop all writers and inspect operation logs/backups before manual
recovery. Transient Windows handle contention during artifact rename is retried a bounded
number of times with the same non-overwriting destination; publication guarantees remain
as described in [freezing.md](freezing.md).

## Browser/CLI compatibility and architecture

The FastAPI/Jinja browser remains supported. Native, browser and CLI review share
`review.service.record_review`; review events remain append-only. Native writes use
`services.run_stage` with operation evidence. New CLI counterparts:

```bash
stimulus-harvester set-review --project PATH --local-id ID --label accepted --reviewer NAME --notes "Reason"
stimulus-harvester project configure --project PATH --config SETTINGS.yaml
```

`workspace.py` supplies frontend-neutral projections, config recipes/validation, dependency
and recovery diagnostics, freeze confirmation summaries and synthetic demo preparation.
`gui/adapter.py` wraps loose evidence mappings in typed Workspace/Reply/Issue contracts.
`gui/tasks.py` owns worker/session lifecycle; `gui/window.py` owns persistent context;
`gui/pages.py` and `gui/components.py` present inputs and results. Scientific state
transitions continue in the existing backend. See [the boundary audit](desktop-boundary.md).

The local Stimulus Upscaler interface informed typography, native controls, numbered
left-to-right tabs and persistent progress/context. System appearance is the initial
choice; Project also offers explicit Light and Dark palettes with readable live switches.
Warnings and scientific states use text, not color alone. Forms have labels, core review
has scoped keyboard shortcuts, views resize/scroll, and tables retain full values in
tooltips or advanced evidence. No pixel-perfect screenshot matching is required.

## Verification and limits

```bash
python -m pip install -e ".[dev,gui-test]"
python -m pytest -q
python -m stimulus_harvester.gui.demo --output "new desktop demo folder" --visible
```

The scripted demo drives real widgets/buttons; confirmations are automated for synthetic
fixtures only. It captures screenshots, resolves an exclusion and duplicate conflict,
creates a freeze, changes live decisions, assembles the old membership, and exports a
verified package. Original image/manifest hashes must remain unchanged. Without
`--visible`, the demo uses Qt's offscreen platform. The installed-wheel test exercises
browser, frozen backend and (when Qt is installed) desktop reopening outside the checkout.

Before a distributable release: native installer/signing and dependency licensing review,
fresh-machine tests, macOS/Linux validation, assistive-technology/user usability testing,
large-project paging/performance, richer thumbnail/contact-sheet browsing, and finer
progress/cancellation are still needed. There is no automatic update system, application
registry, destructive recovery wizard, release publication or selected production detector.

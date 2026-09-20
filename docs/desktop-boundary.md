# Checkpoint 6 service boundary audit

Audited before Qt implementation, from Checkpoint 5 commit `6d4cacd`.

| API | Result contract | Progress / cancellation | UI execution |
| --- | --- | --- | --- |
| create/open | frozen Project dataclass, explicit path/identity | none | worker (migration/filesystem) |
| project_status | mapping of counts, reviews, operations, missing assets | none | worker |
| run_stage | frozen OperationResult; payload varies by stage | stage start/end; cancellation before invocation only | worker |
| candidate_evidence, screening_report | evidence mappings, stable scientific record IDs | none | worker |
| discovery_diagnostics | coverage and provider/request evidence mappings | none | worker |
| freeze_validate | blockers/warnings/scope/duplicates/membership mapping | none | worker (hashing) |
| frozen_artifact | verified manifest and path | none | worker (hashing) |
| list_frozen_artifacts | ID/kind/parent/time/path/hash rows | none | worker |
| verify_frozen_package | verified ID/kind/count/hash-anchor result | none | worker (hashing) |

Existing review mutation is shared database machinery but browser-specific locking and
validation need a small reusable entry point. Config currently loads validated YAML but
has no audited mutable-settings operation. Add these through services; retain low-level
review compatibility. Add a frontend-neutral workspace projection, provider setup checks,
recovery diagnostics and freeze confirmation summary. Do not move selection rules to Qt.

The GUI adapter will normalize responses into typed session/task/error envelopes while
leaving scientific evidence mappings intact. Classify input/setup, partial discovery,
optional dependencies, model errors, freeze blockers, integrity failures, locks/pending
artifacts and unexpected errors; preserve technical details and operation paths.

One background task at a time per window, explicit project captured per request, queued
Qt signals for presentation. Cancellation is offered only before the operation starts;
running network/inference/copy operations finish normally. Closing during work must not
terminate a worker or disguise an operation as cancelled. No automatic recovery deletion.

Family reference inspected: sibling `stimulus-upscaler/src/stimulus_upscaler/gui.py`:
native PySide6, numbered tabs, persistent context/progress, Browse controls, palette-safe
typography/spacing. Harvester adds clear current-state versus read-only artifact views.
Qt threading reference: https://doc.qt.io/qtforpython-6/examples/example_widgets_thread_signals.html

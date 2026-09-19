# Observable-property screening

Screening keeps three independent records: what an annotator observed, what a project
rule concluded, and what an investigator overrode. It never changes investigator
Accept/Reject labels or deletes candidates, originals or source histories.

There is **no default production person detector**. Projects without an evaluated
policy report candidates as `review_required`, with `error_or_not_run` flagged.
Two optional local COCO person detectors are implemented for evaluation:
`fasterrcnn_resnet50_fpn_v2` and `fasterrcnn_mobilenet_v3_large_320_fpn`. COCO person
detection is a proxy for human presence; neither is a dedicated body-part, reflection,
face or statue classifier. A detector can confidently miss or misclassify such cases.

## Install and operate

Core installation and deterministic screening tests do not require torch. Install a
matched optional pair in an isolated environment. An explicit CPU installation is:

```console
python -m pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu
python -m pip install -e ".[screening]"
```

Choose compatible CUDA wheels separately if needed; see
[PyTorch's version matrix](https://pytorch.org/get-started/previous-versions/).
The first inference downloads the selected official weights to PyTorch's cache,
checks the published filename hash prefix and records the complete artifact SHA-256.
Set `TORCH_HOME` to control the cache location. Later cached inference is local.
Requested CUDA failure is explicit; there is no silent fallback to CPU.

```console
stimulus-harvester screen --project "my project" --screening-config configs/screening_human_presence.example.yaml
stimulus-harvester screening-report --project "my project" --no-history
stimulus-harvester screening-report --project "my project" --disposition review_required
stimulus-harvester screening-override --project "my project" --candidate LOCAL_ID --disposition eligible --reviewer "Investigator name" --note "Inspected original at full resolution"
```

The supplied example is a pilot choice with **illustrative, unvalidated thresholds**,
not a selected default. Its strict policy sends even `not_detected` cases for human
review. A project may explicitly map that state to `eligible` instead; the software
does not interpret it as proof of absence. For stricter-than-COCO requirements,
retain human review of negatives until an appropriate benchmark supports a change.

`--screening-config` is screening-only YAML. Without it, `screen` uses the project's
saved `screening` configuration. The CLI does not silently reuse a prior command's
policy override. Each run captures its fully resolved policy in SQLite and its
operation directory; reports use the most recently activated screening policy.
Malformed rules fail before activation. Disabled detectors create `not_run` records.

## Observation schema

`Observation` is a generic model with a property, source ID, annotator family, model
identity, parameters, thresholds, runtime/device context, project/candidate/asset
references, timestamp, operation ID, raw output, status and optional score/error.
Numerical scores require an explicit interpretation. Model identity includes the
exact weight enum, URL, full artifact hash, torch/torchvision versions and adapter
version when loading succeeds. Failed setup preserves requested identity and an
error rather than inventing a loaded artifact hash.

Five states are distinct:

| State | Meaning |
|---|---|
| `detected` | This source's positive criterion was met |
| `not_detected` | This source's criterion was not met; absence is not established |
| `uncertain` | Score lies in the specified band, or the output cap compromises a negative result |
| `error` | Setup, input, inference or normalization failed; error details are retained |
| `not_run` | Disabled, no acquired asset, or no compatible raw evidence for a requested replay |

For these two adapters, the score is the maximum retained COCO `person` score. It is
uncalibrated, is not an absence probability, and must not be pooled across detectors.
At or above `high_threshold` means detected. At or above `low_threshold` but below
high means uncertain. Below low means not detected. If the configured output count
cap is reached without a high-score person, the result is uncertain. Empty retained
output uses zero only within these adapters' explicitly documented score convention.

All returned classes, scores and xyxy boxes are retained, together with output count,
image dimensions, inference duration, score floor, NMS and detection cap. Coordinates
refer to EXIF-oriented RGB pixels; originals are not rewritten. These are post-NMS
detector outputs, not the full network proposal tensor. No field claims that weak
detections below the recorded output floor can be recovered later.

The generic observation and rule models can represent other properties and sources;
the shipped detector configurations deliberately support only the two human-presence
adapters. Adding a new executable detector requires an explicit adapter/config extension.
Existing VLM annotations continue through the original VLM pathway and are preserved
as separate evidence. They are not imported into a dedicated detector record or
automatically ensembled with it.

## Rule and override semantics

A rule has an ID, version, property, one explicit source ID, and a complete mapping
from all five observation states to `eligible`, `excluded` or `review_required`.
Unknown states/outcomes, missing mappings, duplicate IDs, mismatched source/property
and malformed thresholds are errors. Outcome mappings are entirely project configured.

Each immutable evaluation records the exact rule snapshot, its observation ID,
candidate/asset, policy, resulting disposition, explanation, timestamp and operation.
Multiple rule dispositions aggregate with explicit policy
`excluded_then_review_required`: any exclusion wins; otherwise incomplete/review-needed
rules require review; only a complete set of eligible rules yields eligibility.
This combines dispositions, not detector confidences or observations.

Overrides are append-only investigator events: reviewer identity, timestamp, prior
effective disposition, new disposition, optional note and the exact evaluation/asset/
policy scope. They never rewrite observations or evaluations. A subsequent override
references its predecessor. A new screening run, rule evaluation or acquired asset
invalidates the old override's applicability while preserving its history. Services
accept an optional `expected_scope` for clients to reject stale review submissions.
Reviewer names are asserted identity, not authentication; the local application has
no multi-user access-control system.

The legacy browser remains able to inspect excluded candidates. Its existing
Accept/Reject controls are not screening overrides. Use the CLI/shared service for
screening overrides in this checkpoint.

## Persistence, replay and interruption

The additive screening schema is version 1, independent of project schema 1. Tables
are `screening_policies`, `observations`, `screening_evaluations` and
`screening_overrides`. SQLite triggers reject UPDATE/DELETE on these evidence tables.
Migration is transactional and unknown extension versions are refused. This prevents
ordinary application edits, not deliberate database tampering by its owner.

Each observation commits independently. Per-candidate rule evaluations commit together.
A new policy activates before inference; if interrupted, candidates lacking all its
evaluations require review instead of inheriting stale eligible results. Previously
recorded observations remain auditable. There is no automatic mid-run resume. Setup
errors become per-candidate error observations and do not stop other configured
sources. One candidate's inference failure does not stop the other candidates.
Missing or hash-mismatched acquired bytes produce errors, never reassuring negatives.

```console
stimulus-harvester screen --project "my project" --screening-config revised-thresholds.yaml --reuse-raw
```

Replay appends new observations and evaluations with `derived_from` links. It requires
the same candidate/asset, source/property, model/weights and inference parameters.
Only enabled state and low/high thresholds may differ. It retains the original model
artifact/runtime identity and marks that no new inference occurred. It does not claim
to run an installed newer model version. The low threshold cannot go below the stored
output floor. Incompatible/missing evidence becomes `not_run`; the CLI does not
silently infer. Rerun without `--reuse-raw` to obtain new model evidence.

## Shared services and queues

`services.run_stage(project, "screen", screening_config=..., reuse_raw=False)` runs
screening; `run_stage(project, "screen-override", ...)` records adjudication.
`services.screening_report(project, disposition=..., include_history=...)` returns
effective queues, explicit observation states and histories. `candidate_evidence`
also includes that candidate's screening evidence. `error_or_not_run` is a diagnostic
flag overlapping the three disposition queues; it is not a fourth disposition.

No GUI, freeze, assembly or installer is added. **Legacy exports do not automatically
enforce screening dispositions**, and `images/accepted` remains a non-authoritative
convenience folder. Use the screening report to select and audit eligible candidates;
do not treat a legacy accepted export as a guaranteed no-human-presence set. A later
approved assembly workflow must enforce its explicit screening/review requirements.

## Real benchmark procedure

```console
python -m pip install -e ".[benchmark]"
python -m stimulus_harvester.screening.benchmark --manifest human-labels.json --output new-benchmark-folder --model fasterrcnn_resnet50_fpn_v2 --device cpu --threads 2 --low 0.1 --high 0.5
```

The manifest has `items` with image ID, path, SHA-256, `human_presence`
(`present`, `absent`, `uncertain`, or null), reviewer and review timestamp. Ground truth
is copied separately before inference; it never comes from model outputs. Human
selection/building identification is valuable metadata but must not be silently
converted into person-present/person-absent labels. Human annotations must address
the target property and the exact image bytes.

Blind reviewers to outputs and inspect original resolution. Include small distant
people, partial bodies, crowds, silhouettes, reflections, glass, empty architecture,
clutter, statues/mannequins and human-shaped artwork. Define how representations of
people count in advance. Record uncertainty and adjudicate disagreements; retain
original labels. Separate calibration from a held-out evaluation before choosing
thresholds or a default. A small selected pilot cannot justify an absence guarantee.

The runner saves per-image post-NMS outputs and exact identities, outcome/uncertainty
counts, CPU/CUDA inference timing, setup time, process RSS sampled every 50 ms and
CUDA allocation peaks where applicable. Human-labeled data enable detected-only and
detected-or-uncertain recall, eligible false-negative rate, false-positive detection
rate and negative review burden. Missing labels yield null accuracy metrics, never
zeros. Label/prediction image-hash mismatches are errors. Runtime samples are local
observations, not hardware-independent performance promises.

The adapters follow the official
[ResNet50-FPN-v2](https://docs.pytorch.org/vision/main/models/generated/torchvision.models.detection.fasterrcnn_resnet50_fpn_v2.html)
and [MobileNetV3 320-FPN](https://docs.pytorch.org/vision/main/models/generated/torchvision.models.detection.fasterrcnn_mobilenet_v3_large_320_fpn.html)
interfaces. The checkpoint exit report distinguishes mocked plumbing, real inference,
human truth coverage and remaining validation gaps.

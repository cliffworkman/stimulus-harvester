# Screening criteria and recorded evidence

Screening now starts with **Exclude images containing…** in either All results or
the selected collection. Add a criterion, inspect the proposed targets and confirm
the interpretation. No model or numeric boundary is selected by entering intent.
Automatic settings remain separate and require an explicit model and low/high
thresholds. No production default or accuracy recommendation is supplied.

The capability registry `coco-torchvision-0.23-v1` records the exact 91-entry
published metadata array (including background and unused N/A slots) from both
supported torchvision 0.23 COCO_V1 weight enums. Eighty foreground labels are
selectable. The registry includes model URLs and full SHA-256 hashes of the
checked weight files; policy snapshots preserve a copy. Source:
[torchvision v0.23 metadata](https://github.com/pytorch/vision/blob/v0.23.0/torchvision/models/_meta.py).

Deterministic aliases propose People → person, Cars → car only, Televisions → tv,
and Non-human animals → bird, cat, dog, horse, sheep, cow, elephant, bear, zebra,
giraffe. The animal mapping is explicitly partial; it does not cover every animal.
Other exact labels can be chosen from the published vocabulary. Edited/custom
interpretations are visibly confirmed and retain uncovered intent as partial
coverage. Unsupported text can remain a manual-only criterion; it never generates
machine evidence. No language model is used to infer mappings.

Criteria have stable IDs, original text, mapping and registry versions, explicit
targets, coverage/limitations and confirmation. Each immutable policy revision
captures the complete criterion definitions and its investigator or run attribution.
Editing a criterion preserves its ID but creates new policy/evaluation identities;
old overrides cannot silently satisfy the new policy.

## Observations and evaluation

Object observation format 2 uses property `coco_objects` and status `recorded`,
`error` or `not_run`. It records all retained post-NMS class IDs/labels, scores,
boxes, image dimensions/orientation, candidate/asset, operation/time, mechanism,
weight digest, runtime and inference parameters. It does not assert suitability,
exclusion or absence. The model retains detections above the documented output
floor, up to its cap; these are not all raw network proposals.

Criterion evaluation maps compatible evidence to Match detected, No match detected,
Uncertain, Error, Not run or Manual review required. A maximum target score at or
above the chosen high boundary yields exclusion. A score in the low/high band or
an output cap reached without a high match is uncertain. Below low means no match
recorded, never proven absence. Every other result needs human review; machine
no-match never automatically Accepts an image. Several criteria combine with OR
for exclusion, retaining every contributing reason. No confidence calibration is
claimed. Output-floor, NMS and output-cap settings retain their established adapter
values and are recorded; low/high boundaries remain mandatory user choices.

Evaluate saved observations does no inference and creates no new observation.
It reuses only object-format evidence for the same candidate and registered asset,
supported adapter and torchvision version, full registered weight hash, exact
vocabulary and matching inference parameters. Criterion targets and low/high
decision boundaries may change because they do not alter the saved detections;
the low boundary must still be at or above the recorded output floor. Different
weights/model/device/output floor/NMS/cap settings do not reuse evidence.
Cross-candidate reuse solely from matching bytes is not implemented.

Legacy person observations keep their original property, states and policy rules.
They remain inspectable with their original raw detections but are never silently
reinterpreted as object-format evidence. The legacy configuration path is retained
for existing projects. Explicitly adopting generalized criteria replaces the live
policy with confirmation and retains earlier evidence. Run screening is always a
separate explicit action; missing optional dependencies remain visible and errors
are recorded, not converted into no-match.

## Human review and overrides

Screening override remains distinct from Accept/Maybe/Reject. An accepted candidate
with a manual-only criterion, partial coverage, missing evidence or an automatic
error requires an attributed screening override with a reason and exact current
evaluation scope before reviewed freezing. This uses the existing override type:
no new human rating type and no fabricated observation. A rejection needs no
automatic proof to omit that candidate. Ordinary uncertainty/no-match remains
resolvable through the existing explicit suitability review contract. Policies
and overrides are independent across project and collection scopes.

## Persistence and immutable artifacts

The existing append-only policy JSON stores versioned criterion definitions and
evaluation records, including null observation references for manual/not-run
criteria. No generalized-screening DDL or historical data migration is needed.
Collection first-use migration retains Arc B's backup, validation and rollback.
No observation or artifact is updated in place.

Any freeze containing generalized policies/object evidence uses explicit format 3,
with screening contract 2 and an explicit project/collection review scope. It
captures the capability registry, criteria, mechanisms, exact observations,
evaluations, uncertainty/manual obligations, overrides and existing reproducibility
anchors. Assembly/export preserve that format and review scope. Existing v1/v2
readers/writers remain available for legacy evidence; old releases reject v3.
Later live edits never recompute immutable decisions. No unreviewed snapshot or
multi-collection composed package is added.

## Scope and limits

The initial generalized contract supports one automatic mechanism per scope and
multiple criteria. It does not add new models, validate clinical/scientific accuracy,
recommend thresholds, implement arbitrary natural-language classifiers or redesign
Basic/Advanced navigation. Manual interpretation remains the researcher's task.
Generalized inference requires the existing optional torch/torchvision install;
the ordinary Windows build remains no-ML. Detected-object outlines are display
overlays and never modify source bytes.

File availability metadata is cached only within one workspace projection, reducing
repeated filesystem calls for shared assets. Each new refresh checks again; freezes
still hash originals. This is not a persistent integrity or inference cache.

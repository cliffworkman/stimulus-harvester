# Human-presence empirical acceptance

This is a bounded offline benchmark workflow, not a production default or an
absence certificate. Keep human annotations, model outputs, rule decisions, and
investigator overrides separate. Never substitute image selection or building
identification metadata for target-property ground truth.

## Label before examining predictions

**Present:** any visibly discernible actual human, including distant people,
partial bodies/body parts, occlusion, real silhouettes/reflections, glass, crowds.
**Absent:** no actual human is visibly discernible. **Uncertain:** full available
resolution does not support a confident decision.

Statues/sculptures, mannequins, painted/drawn figures, photographs/posters/displays
depicting people, religious/figurative representations, and ambiguous human-shaped
objects are separate tags, not automatic actual-person positives. A separate
scientific rule may exclude these representations later.

The blinded HTML packet contains only image bytes, IDs, hashes, definitions, and
blank rating controls. It includes full-resolution viewing, local browser saving,
and downloadable JSON. Download a backup before closing. A reviewer must choose
their own identity and report prior exposure to model outputs or image-specific
discussion. Packet blindness cannot undo earlier exposure; record it honestly.
Keep every submitted original JSON export, including superseded versions. For
analysis, import one completed version per reviewer; edits within an unsubmitted
draft are not independent ratings. Do not import synthetic UI-test exports.

The annotation schema in `screening.acceptance.Annotation` records identity,
SHA-256, label, reviewer, timezone-aware timestamp, tags, note, exposure, and
rating/adjudication kind. All expected images must have a rating. A second reviewer
uses an independent packet/session. Preserve both initial ratings. Conflicting
labels require a separate human adjudication record referencing all original
annotation IDs for that image. Never silently overwrite or majority-vote a
disagreement. Single-reviewer labels do not establish inter-rater reliability.

For a mixed human/AI annotation file, explicitly designate the human rater and
authorized adjudicator identities. AI ratings and tags remain archived provenance;
they are not used as human ground truth, even when the human and AI agree. Start
with the designated human's label/tags and apply only explicit authorized
adjudications. Preserve their references to both conflicting original records.
`post_review_discussion` records adjudication exposure without describing it as blind.
The resolved artifact retains every input annotation verbatim and separately lists
the annotation IDs used in the human-ground-truth pathway. Preserve a byte-exact
copy of the complete original file as well, including its metadata and hash.

```console
python -m stimulus_harvester.screening.acceptance import-labels --manifest manifest.json --annotations checkpoint-4b-unified-annotations.json --human-reviewer Cliff --ai-reviewer "Lucien (GPT-5.6 Sol)" --adjudication-reviewer "Cliff + Lucien adjudication" --output resolved-human-target.json
```

## Reproducible analysis

Use a new output filename for every artifact; commands refuse to overwrite an
existing file. The examples below use paths relative to a benchmark directory.
No torch import or network connection is needed for label analysis or replay.

```console
python -m stimulus_harvester.screening.acceptance import-labels --manifest manifest.json --annotations reviewer-a.json --output resolved-labels.json
python -m stimulus_harvester.screening.acceptance split --labels resolved-labels.json --output split.json
python -m stimulus_harvester.screening.acceptance develop --labels resolved-labels.json --split split.json --run model-run --output model-development.json
python -m stimulus_harvester.screening.acceptance choose --development model-development.json --low 0.03 --high 0.50 --rationale "Explain the development recall, miss types, and workload tradeoff" --output model-choice.json
python -m stimulus_harvester.screening.acceptance evaluate --labels resolved-labels.json --split split.json --run model-run --choice model-choice.json --output model-heldout.json
```

The numerical choice above is an example, not a recommendation. The development
grid is low = .01/.03/.05/.10/.20 crossed with high = .25/.50/.75 (15 policies).
Replay changes only thresholds and validates saved score floor, NMS, and output
cap. Thresholds below the retained score floor are invalid. Error/not-run results
remain errors/not-run. Preserve exact model/weights/runtime identity and original
raw outputs. Generate new inference only for images without compatible outputs.

Real challenge inference exposed float32 resize roundoff at image edges (maximum
observed overshoot 0.0001220703125 pixels). Coordinate validation allows at most
0.001 pixel at the boundary, without modifying raw boxes; larger excursions still
fail. The benchmark retains raw output even if normalization fails. Preserve the
original error and explicitly record diagnostic/replay lineage when recovering it;
do not silently replace the source run or count an error as a negative.

A `model-run` has `report.json` with the original detector config and model identity,
and `predictions.jsonl`. If assembling pilot and challenge runs, first verify model
identity and inference configuration match, reject duplicate image IDs or hash
mismatches, and preserve each source run and its runtime report. Do not alter the
original CP4 outputs. Missing predictions cause analysis to fail rather than
silently reducing the evaluation population.

Splits use only human labels, cohort membership, image hashes, and a fixed seed.
Identical bytes stay together. Within each cohort/label stratum, deterministic
hash ordering assigns alternating assets to development and evaluation. A minimum
of 20 distinct present and 20 absent assets, with at least 10 of each held out, is
an operational floor for attempting a split, **not statistical evidence of
production safety**. If this floor is unmet, all analysis is exploratory and
held-out evaluation is disabled. Human-uncertain images remain in development;
their operational outcomes are counted but they are excluded from binary truth
denominators. Exact hash grouping does not discover near-duplicates or correlated
views of the same building: inspect these before claiming generalization, and use
a fresh independent validation set if dependencies or prior exposure compromise it.

Labels are bound to the split by digest. Changed labels require a new version and
an explicit reassessment of holdout validity. Record the chosen threshold and
rationale **before** evaluation. Evaluation accepts one preselected, tested policy
per model, not a held-out threshold sweep. Files and checks enforce consistency,
not access control: the investigator must not repeatedly choose policies after
seeing held-out outcomes. A retuned policy needs a new independent holdout. The
original CP4 pilot outputs were previously inspected for execution diagnostics;
do not describe these as never-seen model outputs or all reviewers as unexposed.

## Interpret counts before selecting a default

Reports keep random pilot and enriched challenge cohorts separate; challenge
frequency is not natural prevalence. Every policy includes the complete human
label by detector status table, detected true positives, actual misses
(`present`/`not_detected`), and all positive images not automatically detected.

- Detected recall = detected / all human-present images.
- Triage recall = (detected + uncertain) / all human-present images.
- Safe routing includes errors sent to human review; it is not successful detection.
- Explicit-negative specificity = not_detected / all human-absent images.
- False-positive rate = detected / all human-absent images. With abstentions this
  and explicit-negative specificity need not sum to one.
- Precision uses known human truth only; detections on human-uncertain images are
  reported separately. A missing denominator yields null, not zero.
- Triage workload counts uncertain/error/not-run. Strict workload counts every
  state except detected, assuming detected images are excluded automatically.
  Human adjudication of exclusions would add further work.
- False-negative and false-positive image lists preserve difficult-case tags.
  Tags overlap and their counts must not be added as independent observations.

Prioritize recall and the scientific consequences of each missed person, then
review burden and runtime/RAM/GPU/install cost. Neither size nor speed establishes
accuracy. No automatic model selection or score pooling occurs. For strict
zero-human studies, recommend detected -> excluded and every other status ->
review_required until a study-specific policy is justified. `not_detected` always
means absence is unproven. Do not begin freeze/assembly as part of this benchmark.

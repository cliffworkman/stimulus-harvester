# 0.2.0rc5 curation/navigation development milestone

This unsigned review candidate incorporates the approved comprehension slice
(`0eba82b`) and the curation/navigation implementation milestones (`4964edc`,
`e3a0d0c`). Cliff supplied the motivating workflow evidence; the adopted Cliff/Lucien
direction authorized autonomous arc-based implementation and internal review builds.
Codex designed, implemented and verified these changes. Agent-driven native checks
are not human usability or accessibility acceptance.

Review now has paged thumbnails, stable filtered navigation, explicit temporary
selection, previewed attributed Maybe/Reject batches, optional decision advance,
synchronized sorting and separate observation/disposition filters. Provenance remains
adjacent and raw evidence remains accessible. See docs/curation-navigation-arc.md.

No data schema, scientific inclusion rule, default detector or threshold changes.
The rc3 blank-detector fix and provider/recovery behavior remain. Prior release
artifacts are preserved. No tag, upload, publication or release announcement.

Known limitations: metadata tables still hold the complete project; initial opening
still verifies acquired-file readability; filters and selections are session-local.
The compact view was exercised at 850 × 690 logical pixels. A 1920 × 1080 screen at
150–200% scaling may not physically accommodate that window plus Windows decoration.
Qt content geometry is distinct from fitting on the available physical display;
high-scaling/small-display and assistive-technology acceptance remain open under #22.
Synthetic timing and scripted actions do not establish human accidental-decision
rates, suitability judgments or an improvement in provider relevance.

Collections, generalized screening, Basic mode and search continuation remain future
arcs. Candidate-versus-asset collection membership needs explicit discussion before
implementation. This is a curation milestone, not closure of all Checkpoint 8 gates.

Stimulus Harvester's current Windows builds are unsigned. Windows may display an
Unknown Publisher or SmartScreen warning. Code signing is intentionally deferred
while distribution infrastructure is being reconciled across the Stimulus Modernization
Suite. Hashes establish byte identity, not publisher identity. Final public release
authorization still applies to exact unsigned bytes, hashes, documentation and destination.

## Short human calibration

Use a test copy of a project containing downloaded prison/church images. In Review,
scan thumbnails, filter a provider or phrase, and inspect a few source/license records.
Mark a handful of clear rejects or Maybe candidates; inspect the exact batch preview
before recording a decision. Move through a few images with the arrow controls or
Alt+Left/Right. Stop where browsing or deciding becomes annoying or unclear, and
describe that bottleneck. There is no need to repeat the six-stage scientific workflow.

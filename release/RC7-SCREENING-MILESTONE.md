# 0.2.0rc7 generalized screening milestone

Arc C adds researcher-confirmed screening criteria, an inspectable versioned
capability registry, reusable multi-class object observations, scoped deterministic
evaluation, manual completion through attributed screening overrides and explicit
version-3 immutable provenance. See `docs/generalized-screening.md` for the exact
contracts and limitations. Criteria, observations, evaluations, overrides and
Accept/Reject remain distinct. No detector or threshold default is introduced.

People resolves to `person`, cars to `car` only, televisions to `tv`, and non-human
animals to ten explicitly listed supported animal labels with partial coverage.
Other supported labels can be selected explicitly. Unsupported intent can be
recorded as manual-only. Arbitrary text never becomes an automatic classifier.
The ordinary Windows build contains no ML libraries or weights; recording criteria,
manual review and evaluating compatible saved evidence do not require ML.

Legacy person evidence and version-1/version-2 artifacts retain their semantics.
Generalized screening uses existing append-only storage without new DDL. Older
applications reject version-3 artifacts; do not use an older application to edit a
project after adopting the generalized contract. Keep whole-project backups.
Existing Arc B collection migration retains its backup/rollback checks.

This is an unsigned internal milestone candidate, not completion of every final
distribution gate. Scripted native checks are not human usability/accessibility
acceptance or genuine fresh-Windows validation. Broader layout/accessibility,
Basic/Advanced navigation and other open UX issues remain outside Arc C.
Previous rc2–rc6 release artifacts are preserved. No tag, publication, release
upload or announcement is authorized.

Stimulus Harvester's current Windows builds are unsigned. Windows may display an
Unknown Publisher or SmartScreen warning. Code signing is intentionally deferred
while distribution infrastructure is being reconciled across the Stimulus
Modernization Suite. Hashes establish byte identity, not publisher identity.
Final public release authorization applies to exact unsigned bytes, hashes,
documentation and destination.

## Optional short calibration

In a test copy, open Screen and add People, Non-human animals and an unsupported
criterion such as Fire exits. Inspect each interpretation before confirming it.
Switch between All results and a collection and inspect the independent criteria.
Without ML configured, confirm that the interface makes the missing automatic
mechanism and manual obligations understandable. If existing compatible object
evidence is available, evaluate it and inspect image-level reasons and outlines.
Stop when terminology, criterion editing or understanding the next action becomes
confusing. This is optional milestone calibration, not a required six-stage test.

# 0.2.0rc4 provider/recovery workflow handoff

Cliff approved the bounded provider/recovery implementation after the Cliff/Lucien
(ChatGPT) scope discussion and Codex implementation/verification. Approved source
commit: `020ceb3fb35a225852f7bf300e2d2e3930833e33`. The precise model version of Lucien
is not inferred. The preceding implementation report and evidence remain unchanged.

This handoff advances Python/package versions to 0.2.0rc4 and Windows PE/installer
versions to 0.2.0.4. The pinned runtime, dependency wheel hashes, native library
selection, source-license archives and unsigned signing posture remain unchanged.
No provider/recovery or screening behavior is redesigned during packaging.

The approved changes address #2 metadata-driven Openverse exhaustion; #4 configured
Commons contact and bounded acquisition evidence; #5 explicit failed-download retry
with preserved history; #6 readable accounting; and only the necessary #21 messages.
No detector, model or threshold default exists. The rc3 blank-detector fix remains.

Use HUMAN-RC4-WORKFLOW-TEST.md for the short exploratory human test. The build report
records exact source commit, artifacts, hashes and packaged checks separately from
pytest counts. No fresh-Windows or human accessibility acceptance is implied by local
scripted checks. Remaining Checkpoint 8 gates still apply to the exact candidate.

Explicitly open and outside this handoff: #3 durable search continuation; #7 readable
image-adjacent provenance; #8 sorting/filtering; #9 reviewable-only queue; #10 visual
relevance triage; #11 license UX; #12 collections; #13 collection-scoped screening;
#14 Basic/Advanced IA; #15 generalized screening; #16 unreviewed snapshot; #17 composed
package creation; #18 collaborator-readable package; #19 contextual controls;
#20 SearXNG advanced placement; #21 broader operation UX; #22 image/detail layout.
None of those implementations begins here.

Current Windows builds are unsigned. Windows may display Unknown Publisher or
SmartScreen warnings. Code signing is intentionally deferred while distribution
infrastructure is reconciled across the Stimulus Modernization Suite. Unsigned
development/portfolio distribution is authorized; hashes establish byte identity,
not publisher identity. Final public authorization still applies to exact unsigned
bytes, hashes, documentation and destination. No tag, upload, publication or announcement.

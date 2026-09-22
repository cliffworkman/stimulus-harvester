# Bounded provider/recovery slice (development after rc3)

Baseline: `3bcf1e1b423b393c2851aaa621f5c47d427bc35d`, clean working tree.
Scope: issues #2, #4, #5, #6; narrow readable operation status from #21.
No Basic/Advanced redesign, search resume, collections, new screening semantics,
snapshot changes, new release artifact, tag or publication.

## Contribution lineage

- Cliff's real rc2 acceptance supplied the repeated 401s, missing downloaded files,
  Commons zero-acquisition run and ordinary-workflow recovery problem.
- Subsequent Cliff/Lucien (ChatGPT) discussion specified bounded investigation and
  later this implementation slice. The precise individual origin of every elaboration
  is not reliably reconstructed; Lucien's model version is not inferred.
- Codex's 2026-09-22 audit established the specific anonymous-limit response bodies,
  null-only pending selector, 743/438/305 reconciliation and Commons diagnostic/contact
  gaps. The historical Commons 403 cause remained unresolved.
- Codex implemented metadata-driven exhaustion, acquisition evidence/contact flow,
  explicit failed-download retry, shared count projections and focused regression
  tests under the user's adopted scope. A current bounded live success is new evidence,
  not a retrospective explanation for the historical denials.

## Semantics and boundaries

See [discovery and acquisition](discovery.md) for exact scope/count definitions.
Automatic screening remains unconfigured unless explicitly configured. No model or
threshold default was introduced; observation, policy evaluation and adjudication
remain separate. Existing reviewed-freeze rules are unchanged.

The next packaged candidate should be **0.2.0rc4** if approved after review. Current
source version markers remain at the rc3 baseline during this development slice;
working-tree changes are identified by the final diff receipt. No rc2/rc3 bytes are
rebuilt or replaced. Checkpoint 8 remains open. Signing remains intentionally deferred
suite-level work; unchanged non-signing gates and exact-byte final authorization apply.

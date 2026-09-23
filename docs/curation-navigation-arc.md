# Curation and navigation arc A

## Scope and lineage

Cliff's real workflow and the Cliff/Lucien discussion established the heterogeneous-results and overloaded-interface problems. The milestone direction supplied on 2026-09-22 replaces the prior per-slice human gate. Codex committed the reviewed comprehension slice as `0eba82b`, then designed and implemented this curation arc. Native/scripted verification is agent-driven evidence, not human usability acceptance. Lucien is ChatGPT; its exact model is not established.

This arc addresses the curation portions of #10, #8 and #22, with intersections in #7, #9 and #21. It does not implement collections, generalized screening, Basic/Advanced information architecture, search continuation or new freeze contracts.

## Behavior and architecture

- Review opens with available images in a thumbnail view; a table remains available. Both share the same typed sort, same-source provider/query/license filters, stable candidate identity and exact-byte-history filter. The initial order is first discovery, not inferred suitability or a merged provider relevance score. Original provider records remain intact.
- A thumbnail page contains at most 48 candidates. One background decoder runs at a time, obsolete queued pages are dropped, and an in-memory cache retains at most 192 display copies. No thumbnail files, network requests or scientific evidence are generated. Paging bounds image work; the metadata table still holds the complete project.
- Previous/next and Alt+Left/Alt+Right follow the current filtered order. Table/grid changes retain the inspected candidate. Selecting an image never marks it for a batch or records a review. Rapid movement coalesces to the latest requested preview; stale replies cannot replace the selected image. Other operations still serialize through the existing session worker.
- Checkbox/Select marks are temporary candidate selections, not collections. They persist across pages and sort changes. Filtering drops hidden selections. Project changes clear them. Batch preview lists every selected candidate and its previous decision, requires investigator identity and an explicit Maybe or Reject choice, and can be cancelled without a write.
- Maybe means further inspection under the existing review contract. It is not acceptance. Batch Accept is intentionally absent. Each batch uses one database transaction and appends normal per-candidate review events. A changed candidate asset or review label rejects the entire stale request. Storage errors roll back all its review changes. Later explicit decisions supersede records without deleting history.
- Individual decisions can optionally advance to the next candidate in the prior filtered order. Advance is initially off. Viewing, selecting and navigating have no decision side effects.
- Inspector screening evidence uses a candidate-scoped query through the same reporting logic as the full report; scientific aggregation rules are unchanged.
- The sort selector and table headers stay synchronized, including ascending/descending direction. Saved observation status and effective screening disposition have separate facets. Active filter values appear in the count line and full tooltip; zero-match filters survive a refresh until explicitly cleared.

## Scientific and compatibility boundaries

No schema migration, detector/model/threshold default, legal-clearance inference, latent-construct ranking, asset-group review propagation or freeze-membership change is introduced. Near-duplicate similarity and exact-byte identity remain separate. Original media, all source claims, immutable artifacts, observations, rule evaluations and investigator overrides remain authoritative and separate.

Available-image queues are based on readable acquired files. Unavailable results remain inspectable through the availability filter. Display filters do not govern freeze membership. A batch changes only the explicitly listed current candidate reviews; an earlier freeze never changes.

## Remaining acceptance limits

The full metadata table still scales with project size, and initial project opening still verifies available image readability. Thumbnail pages are bounded but not a fully virtualized infinite grid. Source metadata can be long. Page/filter/sort settings and temporary selections are session-local. Screen-reader and physical-display testing remain distinct from native Qt geometry checks.

Synthetic navigation measurements cannot establish investigator suitability, human accidental-decision rates or improved provider relevance. These parts of #10 remain open. Cross-stage provenance, broader license UX and operation recovery remain outside this curation milestone. No issue is closed solely because its curation subset improved.

The next arc is collections, but candidate-versus-asset membership has materially different scientific consequences and must be discussed before a migration or implementation. This arc does not choose that contract.

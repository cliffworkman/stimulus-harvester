"""Deterministic, non-LLM query expansion.

Combines a hand-authored seed list with programmatic synonym-group x phrase-template
combinations. No LLM is used here by default -- this stays simple, reproducible, and
fully offline. The actual seed list, synonym groups, and templates are supplied by the
task's YAML config (see `configs/prison_inside_cell.yaml`); this module has no
knowledge of what task it's being used for.
"""

from __future__ import annotations

import re
from itertools import product

from stimulus_harvester.config import QueryExpansionConfig

GENERATED_LABEL = "(generated)"

_PLACEHOLDER_RE = re.compile(r"\{(\d+)\}")


def _referenced_indices(template: str) -> list[int]:
    return sorted({int(m) for m in _PLACEHOLDER_RE.findall(template)})


def expand_queries(
    seed_queries: list[str],
    synonym_groups: list[list[str]],
    phrase_templates: list[str],
    max_expanded_queries: int,
) -> list[tuple[str, str]]:
    """Return a deterministic, deduplicated `(seed_label, query)` list.

    Hand-authored seeds come first, with `seed_label == query`. Programmatically
    generated queries follow, each labeled `GENERATED_LABEL` since they arise from a
    synonym-group combination rather than one specific seed. Deduplication is
    case-insensitive. The result never exceeds `max_expanded_queries` entries.

    A phrase template's `{N}` placeholders select which synonym group(s) it draws
    from (by position), so a template referencing only `{0}` iterates group 0 alone,
    while one referencing both `{0}` and `{1}` takes their full Cartesian product.
    """
    seen: set[str] = set()
    out: list[tuple[str, str]] = []

    def add(label: str, query: str) -> None:
        key = query.strip().lower()
        if key and key not in seen and len(out) < max_expanded_queries:
            seen.add(key)
            out.append((label, query.strip()))

    for seed in seed_queries:
        add(seed, seed)

    for template in phrase_templates:
        indices = _referenced_indices(template)
        if not indices:
            continue
        if any(i >= len(synonym_groups) for i in indices):
            continue  # template references a synonym group that doesn't exist
        groups_for_template = [synonym_groups[i] for i in indices]
        for combo in product(*groups_for_template):
            positional = dict(zip(indices, combo))
            args = [positional.get(i, "") for i in range(max(indices) + 1)]
            query = template.format(*args)
            add(GENERATED_LABEL, query)

    return out


def expand_queries_from_config(
    config: QueryExpansionConfig, seed_queries: list[str]
) -> list[tuple[str, str]]:
    if not config.enabled:
        return [(seed, seed) for seed in seed_queries]
    return expand_queries(
        seed_queries, config.synonym_groups, config.phrase_templates, config.max_expanded_queries
    )

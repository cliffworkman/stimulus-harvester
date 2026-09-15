from __future__ import annotations

from stimulus_harvester.search.query_expansion import GENERATED_LABEL, expand_queries


def test_deterministic_across_repeated_calls():
    seeds, groups, templates = ["a", "b"], [["x", "y"]], ["{0} z"]
    first = expand_queries(seeds, groups, templates, 100)
    second = expand_queries(seeds, groups, templates, 100)
    assert first == second


def test_no_duplicates_case_insensitive():
    result = expand_queries(["Hello World", "hello world"], [], [], 100)
    assert len(result) == 1


def test_includes_all_seeds_verbatim():
    seeds = ["a", "b", "c"]
    result = expand_queries(seeds, [], [], 100)
    assert [query for _, query in result] == seeds


def test_respects_max_expanded_queries_cap():
    seeds = ["a", "b", "c", "d", "e"]
    result = expand_queries(seeds, [], [], 3)
    assert len(result) == 3


def test_empty_groups_and_templates_returns_seeds_only():
    seeds = ["only", "these"]
    result = expand_queries(seeds, [], [], 100)
    assert result == [(seed, seed) for seed in seeds]


def test_single_group_template_iterates_that_group_only():
    groups = [["prison cell", "jail cell"]]
    templates = ["empty {0} interior"]
    result = expand_queries([], groups, templates, 100)
    queries = {query for _, query in result}
    assert queries == {"empty prison cell interior", "empty jail cell interior"}
    assert all(label == GENERATED_LABEL for label, _ in result)


def test_multi_group_template_uses_cartesian_product():
    groups = [["a1", "a2"], ["b1", "b2"]]
    templates = ["{0}-{1}"]
    result = expand_queries([], groups, templates, 100)
    queries = {query for _, query in result}
    assert queries == {"a1-b1", "a1-b2", "a2-b1", "a2-b2"}


def test_template_referencing_missing_group_is_skipped_safely():
    groups = [["only-group"]]
    templates = ["{0} and {1}"]  # references group 1, which doesn't exist
    result = expand_queries([], groups, templates, 100)
    assert result == []


def test_shipped_config_expansion_count_matches_hand_computation():
    groups = [
        ["prison cell", "jail cell", "cellblock cell", "detention cell"],
        [
            "looking outward",
            "looking out through the bars",
            "point of view from inside",
            "seen from within",
        ],
    ]
    templates = ["{0} interior {1}", "inside a {0}, {1}", "empty {0} interior", "{0} door from inside"]
    seeds = ["single seed for this test"]

    result = expand_queries(seeds, groups, templates, max_expanded_queries=1000)
    generated = [q for label, q in result if label == GENERATED_LABEL]

    # 4x4 + 4x4 + 4 + 4 = 40 generated, per the shipped config's synonym groups/templates
    assert len(generated) == 40
    assert len(result) == len(seeds) + 40

"""Synthetic cases only: label integrity, abstention metrics, and replay/split safety."""

import copy
import json

import pytest

from stimulus_harvester.screening.acceptance import (
    analyze,
    choose_policy,
    digest,
    main,
    make_split,
    metric_table,
    replay,
    resolve_labels,
    split_items,
    write_new,
)


def fixture(count=4):
    items, records = [], []
    for n in range(count):
        item = {"image_id": str(n), "sha256": f"{n:064x}", "cohort": "pilot"}
        items.append(item)
        records.append(
            {
                "annotation_id": f"a-{n}",
                **{k: item[k] for k in ("image_id", "sha256")},
                "human_presence": "present" if n % 2 else "absent",
                "reviewer": "Synthetic A",
                "reviewed_at": "2026-09-19T10:00:00+00:00",
                "tags": ["tiny_distant"],
                "note": "Synthetic unit test",
                "exposure": "none",
            }
        )
    return items, records


def config():
    return {
        "id": "test",
        "property": "human_presence",
        "model": "fasterrcnn_resnet50_fpn_v2",
        "low_threshold": 0.1,
        "high_threshold": 0.5,
    }


def prediction(item, score, status="not_detected"):
    return {
        "image_id": item["image_id"],
        "sha256": item["sha256"],
        "status": status,
        "raw": {
            "width": 10,
            "height": 10,
            "output_floor": 0.01,
            "max_detections": 300,
            "nms_threshold": 0.5,
            "detections": [{"class": "person", "score": score, "box_xyxy": [0, 0, 5, 5]}],
        },
    }


@pytest.mark.parametrize(
    "field,value",
    [
        ("reviewer", " "),
        ("reviewed_at", "2026-09-19T10:00:00"),
        ("human_presence", None),
        ("sha256", "a" * 64),
        ("image_id", "unknown"),
        ("tags", ["", "statue_sculpture"]),
        ("exposure", "assumed_blind"),
    ],
)
def test_invalid_human_labels_rejected(field, value):
    items, records = fixture()
    records[0][field] = value
    with pytest.raises(ValueError):
        resolve_labels(items, records)


def test_complete_and_unique_ratings_required():
    items, records = fixture()
    for bad in (
        records[:-1],
        records + [records[0]],
        records + [{**records[0], "annotation_id": "new"}],
    ):
        with pytest.raises(ValueError):
            resolve_labels(items, bad)


def test_edge_tags_do_not_relabel_and_metadata_is_untouched():
    items, records = fixture()
    items[0]["source_metadata"] = {"building": "Human supplied", "notes": "Original"}
    records[0]["tags"] = ["statue_sculpture", "religious_figurative", "custom tag"]
    before = copy.deepcopy((items, records))
    labels = resolve_labels(items, records)
    assert labels["items"][0]["human_presence"] == "absent"
    assert set(labels["items"][0]["tags"]) == set(records[0]["tags"])
    assert labels["items"][0]["adjudication_status"] == "single_reviewer"
    assert (items, records) == before


def test_independent_ratings_and_adjudication_are_preserved():
    items, records = fixture(1)
    second = {
        **records[0],
        "annotation_id": "b",
        "reviewer": "Synthetic B",
        "human_presence": "present",
    }
    with pytest.raises(ValueError, match="disagreement"):
        resolve_labels(items, records + [second])
    decision = {
        **records[0],
        "annotation_id": "decision",
        "reviewer": "Synthetic C",
        "human_presence": "uncertain",
        "kind": "adjudication",
        "adjudicates": ["a-0", "b"],
        "tags": ["ambiguous_human_shape"],
    }
    resolved = resolve_labels(items, records + [second, decision])
    assert len(resolved["annotations"]) == 3
    assert resolved["items"][0]["human_presence"] == "uncertain"
    assert resolved["items"][0]["adjudication_status"] == "adjudicated"
    decision["adjudicates"] = ["a-0", "foreign-image"]
    with pytest.raises(ValueError, match="reference all"):
        resolve_labels(items, records + [second, decision])


def test_small_positive_count_cannot_claim_a_holdout():
    labels = resolve_labels(*fixture())
    split = make_split(labels)
    assert split["mode"] == "exploratory_only"
    assert not split_items(labels, split, "heldout")
    assert len(split_items(labels, split, "development")) == 4


def test_split_is_label_only_deterministic_and_groups_identical_bytes():
    items, records = fixture(44)
    items.append({**items[1], "image_id": "duplicate", "cohort": "challenge"})
    records.append({**records[1], "image_id": "duplicate", "annotation_id": "duplicate"})
    labels = resolve_labels(items, records)
    split = make_split(labels)
    assert split == make_split(labels)
    assert split["mode"] == "heldout_available"
    dev, held = (split_items(labels, split, s) for s in ("development", "heldout"))
    assert not {i["sha256"] for i in dev} & {i["sha256"] for i in held}
    assert len(dev) + len(held) == 45
    assert sum(i["image_id"] in {"1", "duplicate"} for i in dev) in {0, 2}
    records[-1]["human_presence"] = "absent"
    with pytest.raises(ValueError, match="identical bytes"):
        make_split(resolve_labels(items, records))


def test_changed_labels_and_tampered_assignment_are_rejected():
    labels = resolve_labels(*fixture(44))
    split = make_split(labels)
    altered = copy.deepcopy(labels)
    altered["items"][0]["tags"] = ["changed"]
    with pytest.raises(ValueError, match="Labels changed"):
        split_items(altered, split, "development")
    sha = next(iter(split["assignment_by_sha256"]))
    split["assignment_by_sha256"][sha] = (
        "heldout" if split["assignment_by_sha256"][sha] == "development" else "development"
    )
    with pytest.raises(ValueError, match="deterministic"):
        split_items(labels, split, "heldout")


def test_abstention_metrics_do_not_silently_become_binary_negatives():
    items, records = fixture(11)
    truths = ["present"] * 5 + ["absent"] * 5 + ["uncertain"]
    states = ["detected", "uncertain", "not_detected", "error", "not_run"] * 2 + ["detected"]
    for row, truth in zip(records, truths):
        row["human_presence"] = truth
        row["tags"] = ["tiny_distant"] if truth == "present" else ["statue_sculpture"]
    labels = resolve_labels(items, records)
    predictions = [prediction(i, 0.2, s) for i, s in zip(items, states)]
    m = metric_table(predictions, labels["items"])
    assert m["human_present"] == 5 and m["human_uncertain"] == 1
    assert m["true_positives_detected"] == 1 and m["false_negatives_not_detected"] == 1
    assert m["detected_recall"] == 0.2 and m["detected_or_uncertain_recall"] == 0.4
    assert m["safe_routing_rate"] == 0.8
    assert m["specificity_explicit_not_detected"] == 0.2
    assert m["false_positive_detected_rate"] == 0.2
    assert m["precision_detected_known_truth"] == 0.5
    assert m["detected_with_uncertain_truth"] == 1
    assert m["review_workload_triage"] == 6 and m["review_workload_strict"] == 8
    assert m["false_negatives_by_tag"] == {"tiny_distant": 1}
    assert m["false_positives_by_tag"] == {"statue_sculpture": 1}
    assert m["positives_not_detected_automatically_by_tag"] == {"tiny_distant": 4}


def test_metrics_null_denominators_and_image_identity():
    items, records = fixture(1)
    labels = resolve_labels(items, records)
    predictions = [prediction(items[0], 0.2)]
    m = metric_table(predictions, labels["items"])
    assert m["detected_recall"] is None and m["precision_detected_known_truth"] is None
    for bad in ([], predictions * 2, [{**predictions[0], "sha256": "a" * 64}]):
        with pytest.raises(ValueError):
            metric_table(bad, labels["items"])


def test_replay_boundaries_floor_cap_and_failure_preservation():
    items, _ = fixture(4)
    predictions = [prediction(i, score) for i, score in zip(items, [0.01, 0.1, 0.5, 0.9])]
    before = copy.deepcopy(predictions)
    assert [p["status"] for p in replay(predictions, config(), 0.1, 0.5)] == [
        "not_detected",
        "uncertain",
        "detected",
        "detected",
    ]
    assert predictions == before
    with pytest.raises(ValueError):
        replay(predictions, config(), 0.001, 0.5)
    predictions[0]["raw"]["detections"] *= 300
    assert replay(predictions, config(), 0.1, 0.5)[0]["status"] == "uncertain"
    predictions[1].update(status="error", raw=None)
    assert replay(predictions, config(), 0.1, 0.5)[1]["status"] == "error"
    predictions[2]["raw"]["output_floor"] = 0.2
    with pytest.raises(ValueError, match="mismatch"):
        replay(predictions, config(), 0.1, 0.5)


def test_cohorts_stay_separate_and_heldout_cannot_be_swept():
    items, records = fixture(48)
    for i in items[24:]:
        i["cohort"] = "challenge"
    labels = resolve_labels(items, records)
    split = make_split(labels)
    predictions = [prediction(i, 0.2) for i in items]
    result = analyze(labels, split, predictions, config(), [(0.1, 0.5)])
    assert set(result[0]["by_cohort"]) == {"pilot", "challenge"}
    assert sum(r["images"] for r in result[0]["by_cohort"].values()) == 24
    with pytest.raises(ValueError, match="preselected"):
        analyze(labels, split, predictions, config(), [(0.1, 0.5), (0.2, 0.5)], subset="heldout")


def test_policy_must_be_tested_on_development_and_recorded_with_rationale():
    development = {
        "subset": "development",
        "mode": "heldout_available",
        "split_digest": "split",
        "model_identity": {"model": "test"},
        "results": [{"low": 0.1, "high": 0.5}],
    }
    choice = choose_policy(development, 0.1, 0.5, "Prefer high recall; inspect miss types")
    assert choice["development_digest"] == digest(development)
    for low, high, why in [(0.2, 0.5, "untested"), (0.1, 0.5, " ")]:
        with pytest.raises(ValueError):
            choose_policy(development, low, high, why)
    development["mode"] = "exploratory_only"
    with pytest.raises(ValueError):
        choose_policy(development, 0.1, 0.5, "insufficient")


def test_cli_import_and_exclusive_evidence_writes(tmp_path, monkeypatch):
    items, records = fixture()
    manifest, annotations, output = [
        tmp_path / name for name in ("manifest.json", "human.json", "labels.json")
    ]
    write_new(manifest, {"items": items})
    write_new(annotations, {"annotations": records})
    monkeypatch.setattr(
        "sys.argv",
        [
            "acceptance",
            "import-labels",
            "--manifest",
            str(manifest),
            "--annotations",
            str(annotations),
            "--output",
            str(output),
        ],
    )
    main()
    result = json.loads(output.read_text())
    assert len(result["annotations"]) == 4
    with pytest.raises(FileExistsError):
        main()


def test_cli_development_choice_and_heldout_bind_same_evidence(tmp_path, monkeypatch):
    items, records = fixture(48)
    labels_path, split_path, development, choice, evaluation = [
        tmp_path / name
        for name in (
            "labels.json",
            "split.json",
            "development.json",
            "choice.json",
            "evaluation.json",
        )
    ]
    write_new(labels_path, resolve_labels(items, records))
    run = tmp_path / "run"
    run.mkdir()
    write_new(run / "report.json", {"config": config(), "model_identity": {"test": "synthetic"}})
    predictions = [prediction(i, 0.2) for i in items]
    raw_path = run / "predictions.jsonl"
    raw_path.write_text("\n".join(json.dumps(p) for p in predictions))

    def invoke(*args):
        monkeypatch.setattr("sys.argv", ["acceptance", *map(str, args)])
        main()

    invoke("split", "--labels", labels_path, "--output", split_path)
    common = ["--labels", labels_path, "--split", split_path, "--run", run]
    invoke("develop", *common, "--output", development)
    invoke(
        "choose",
        "--development",
        development,
        "--low",
        0.1,
        "--high",
        0.5,
        "--rationale",
        "Synthetic test only",
        "--output",
        choice,
    )
    invoke("evaluate", *common, "--choice", choice, "--output", evaluation)
    result = json.loads(evaluation.read_text())
    assert result["subset"] == "heldout" and len(result["results"]) == 1
    assert result["results"][0]["by_cohort"]["pilot"]["images"] == 24
    predictions[0]["raw"]["detections"][0]["score"] = 0.9
    raw_path.write_text("\n".join(json.dumps(p) for p in predictions))
    with pytest.raises(ValueError, match="locked split"):
        invoke("evaluate", *common, "--choice", choice, "--output", tmp_path / "changed.json")


@pytest.mark.parametrize("overshoot,allowed", [(0.0001220703125, True), (0.01, False)])
def test_real_float32_boundary_roundoff_without_relaxing_malformed_boxes(overshoot, allowed):
    from stimulus_harvester.screening.config import DetectorConfig
    from stimulus_harvester.screening.detectors import normalize

    raw = prediction(fixture(1)[0][0], 0.7)["raw"]
    raw["detections"][0]["box_xyxy"] = [-overshoot, 0, 10, 10 + overshoot]
    before = copy.deepcopy(raw)
    if allowed:
        assert normalize(raw, DetectorConfig.model_validate(config()))[0] == "detected"
    else:
        with pytest.raises(ValueError, match="outside"):
            normalize(raw, DetectorConfig.model_validate(config()))
    assert raw == before


def test_benchmark_preserves_raw_when_normalization_fails(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from stimulus_harvester.screening import benchmark
    from stimulus_harvester.screening.config import DetectorConfig
    from stimulus_harvester.screening.detectors import file_hash

    image_path = tmp_path / "synthetic.bin"
    image_path.write_bytes(b"synthetic test; fake detector does not decode images")
    item = {"image_id": "synthetic", "sha256": file_hash(image_path), "path": str(image_path)}
    raw = prediction(item, 0.7)["raw"]
    raw["detections"][0]["box_xyxy"] = [0, 0, 100, 100]
    raw["inference_seconds"] = 0.001
    monkeypatch.setattr(
        benchmark,
        "TorchvisionDetector",
        lambda config: SimpleNamespace(
            identity={"test": "synthetic"}, runtime={}, predict=lambda path: raw, close=lambda: None
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "psutil",
        SimpleNamespace(
            Process=lambda: SimpleNamespace(memory_info=lambda: SimpleNamespace(rss=0))
        ),
    )
    manifest = tmp_path / "manifest.json"
    write_new(manifest, {"items": [item]})
    output = tmp_path / "output"
    report = benchmark.run(manifest, output, DetectorConfig.model_validate(config()))
    result = json.loads((output / "predictions.jsonl").read_text())
    assert result["status"] == "error" and result["raw"] == raw
    assert report["metrics"]["error_rate"] == 1


def test_ai_labels_and_tags_cannot_enter_explicit_human_pathway():
    items, records = fixture(1)
    records[0]["reviewed_at"] = "2026-09-19T10:00:00Z"
    records[0]["tags"] = ["human tag"]
    ai = {
        **records[0],
        "annotation_id": "ai",
        "reviewer": "AI",
        "human_presence": "present",
        "tags": ["AI-only tag"],
    }
    source = records + [ai]
    result = resolve_labels(items, source, human_reviewer="Synthetic A", ai_reviewers=["AI"])
    assert result["annotations"] == source
    row = result["items"][0]
    assert row["human_presence"] == "absent" and row["tags"] == ["human tag"]
    assert row["ground_truth_annotation_ids"] == ["a-0"]
    assert row["annotation_ids"] == ["a-0", "ai"]
    assert row["adjudication_status"] == "single_reviewer"
    assert result["ground_truth_pathway"]["ai_reviewers_excluded_from_ground_truth"] == ["AI"]
    result["annotations"][0]["tags"].append("output edit")
    assert records[0]["tags"] == ["human tag"]


def test_authorized_post_review_adjudication_supersedes_human_without_promoting_ai():
    items, records = fixture(1)
    ai = {**records[0], "annotation_id": "ai", "reviewer": "AI", "human_presence": "present"}
    decision = {
        **records[0],
        "annotation_id": "decision",
        "reviewer": "Human + AI",
        "human_presence": "present",
        "kind": "adjudication",
        "adjudicates": ["a-0", "ai"],
        "exposure": "post_review_discussion",
        "note": "Human-authorized final decision",
        "tags": [],
    }
    source = records + [ai, decision]
    result = resolve_labels(
        items,
        source,
        human_reviewer="Synthetic A",
        ai_reviewers=["AI"],
        adjudication_reviewers=["Human + AI"],
    )
    row = result["items"][0]
    assert row["human_presence"] == "present" and row["selected_annotation_id"] == "decision"
    assert row["ground_truth_annotation_ids"] == ["a-0", "decision"]
    assert row["tags"] == [] and row["note"] == decision["note"]
    assert result["annotations"] == source
    with pytest.raises(ValueError, match="provenance role"):
        resolve_labels(items, source, human_reviewer="Synthetic A", ai_reviewers=["AI"])


def test_mixed_rater_import_requires_human_coverage_and_explicit_disjoint_roles():
    items, records = fixture(1)
    with pytest.raises(ValueError, match="explicit human"):
        resolve_labels(items, records, ai_reviewers=["Synthetic A"])
    with pytest.raises(ValueError, match="disjoint"):
        resolve_labels(items, records, human_reviewer="Synthetic A", ai_reviewers=["Synthetic A"])
    with pytest.raises(ValueError, match="Missing human"):
        resolve_labels(items, records, human_reviewer="Human", ai_reviewers=["Synthetic A"])
    with pytest.raises(ValueError, match="provenance role"):
        resolve_labels(items, records, human_reviewer="Human")

"""Real inference runner with separate human truth; never scores unlabeled images as absent."""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from collections import Counter
from pathlib import Path

from stimulus_harvester.screening.config import DetectorConfig
from stimulus_harvester.screening.detectors import TorchvisionDetector, file_hash, normalize
from stimulus_harvester.storage import now


def metrics(predictions, items):
    by_id = {r["image_id"]: r for r in predictions}
    if len(by_id) != len(predictions) or len({i["image_id"] for i in items}) != len(items):
        raise ValueError("Duplicate benchmark image IDs")
    reviewed = [
        i
        for i in items
        if i.get("human_presence") in {"present", "absent"}
        and i.get("reviewer")
        and i.get("reviewed_at")
    ]
    for item in reviewed:
        if item["image_id"] not in by_id or by_id[item["image_id"]]["sha256"] != item["sha256"]:
            raise ValueError("Human labels and predictions must refer to the same image bytes")
    positives = [by_id[i["image_id"]] for i in reviewed if i["human_presence"] == "present"]
    negatives = [by_id[i["image_id"]] for i in reviewed if i["human_presence"] == "absent"]

    def fraction(rows, statuses):
        return sum(r["status"] in statuses for r in rows) / len(rows) if rows else None

    times = [r["raw"]["inference_seconds"] for r in predictions if r.get("raw")]
    return {
        "images": len(predictions),
        "statuses": dict(Counter(p["status"] for p in predictions)),
        "human_reviewed": len(reviewed),
        "positive_labels": len(positives),
        "negative_labels": len(negatives),
        "detected_recall": fraction(positives, {"detected"}),
        "detected_or_uncertain_recall": fraction(positives, {"detected", "uncertain"}),
        "false_negative_eligible_rate": fraction(positives, {"not_detected"}),
        "false_positive_detected_rate": fraction(negatives, {"detected"}),
        "negative_review_burden": fraction(negatives, {"uncertain", "error", "not_run"}),
        "uncertain_rate": fraction(predictions, {"uncertain"}),
        "error_rate": fraction(predictions, {"error"}),
        "median_inference_seconds": statistics.median(times) if times else None,
        "max_inference_seconds": max(times) if times else None,
        "interpretation": "Null rates mean missing human ground truth, not zero errors. Scores are uncalibrated. Uncertain/errors require separate review; no absence guarantee.",
    }


def run(manifest, output, config):
    import psutil
    import threading

    manifest = Path(manifest)
    source = json.loads(manifest.read_text(encoding="utf-8"))
    items = source["items"]
    if len({r["image_id"] for r in items}) != len(items):
        raise ValueError("Duplicate benchmark image IDs")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / "ground_truth_snapshot.json").write_text(
        json.dumps(source, indent=2), encoding="utf-8"
    )
    stop = threading.Event()
    memory = {"peak_sampled_rss_bytes": 0}
    process = psutil.Process()

    def sample():
        while not stop.wait(0.05):
            memory["peak_sampled_rss_bytes"] = max(
                memory["peak_sampled_rss_bytes"], process.memory_info().rss
            )

    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    started = time.perf_counter()
    detector = None
    predictions = []
    try:
        detector = TorchvisionDetector(config)
        setup_seconds = time.perf_counter() - started
        for item in items:
            path = Path(item["path"])
            if not path.is_absolute():
                path = manifest.parent / path
            result = {"image_id": item["image_id"], "sha256": item["sha256"], "timestamp": now()}
            try:
                if file_hash(path) != item["sha256"]:
                    raise ValueError("Benchmark image hash mismatch")
                raw = detector.predict(path)
                status, score, reason = normalize(raw, config)
                result.update(status=status, score=score, reason=reason, raw=raw)
            except Exception as exc:
                result.update(
                    status="error",
                    score=None,
                    raw=None,
                    error={"type": type(exc).__name__, "message": str(exc)},
                )
            predictions.append(result)
            with (output / "predictions.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(result, allow_nan=False) + "\n")
            print(
                f"{config.model}: {len(predictions)}/{len(items)} {item['image_id']} {result['status']}",
                flush=True,
            )
        report = {
            "config": config.model_dump(mode="json"),
            "model_identity": detector.identity,
            "runtime": detector.runtime,
            "platform": platform.platform(),
            "timestamp": now(),
            "setup_seconds_including_weight_download": setup_seconds,
            "total_seconds": time.perf_counter() - started,
            **memory,
            "memory_method": "process RSS sampled every 50 ms; includes Python/model, not just weights",
            "manifest_sha256": file_hash(manifest),
            "metrics": metrics(predictions, items),
        }
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        return report
    finally:
        stop.set()
        thread.join(timeout=1)
        if detector:
            detector.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--model",
        required=True,
        choices=["fasterrcnn_resnet50_fpn_v2", "fasterrcnn_mobilenet_v3_large_320_fpn"],
    )
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--low", type=float, required=True)
    parser.add_argument("--high", type=float, required=True)
    args = parser.parse_args()
    config = DetectorConfig(
        id="human-benchmark",
        property="human_presence",
        model=args.model,
        device=args.device,
        threads=args.threads,
        low_threshold=args.low,
        high_threshold=args.high,
    )
    run(args.manifest, args.output, config)


if __name__ == "__main__":
    main()

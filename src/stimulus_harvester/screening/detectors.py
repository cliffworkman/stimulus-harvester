"""Lazy optional local detectors. Installing core Harvester never imports torch."""

from __future__ import annotations

import hashlib
import math
import platform
import time
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageOps


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalize(raw, config):
    """Scores are detector-specific ranking scores, not absence probabilities."""
    width, height = raw["width"], raw["height"]
    if not isinstance(width, int) or not isinstance(height, int) or min(width, height) <= 0:
        raise ValueError("Invalid detector image dimensions")
    people = []
    for item in raw["detections"]:
        score, box = item["score"], item["box_xyxy"]
        if (
            not math.isfinite(score)
            or not 0 <= score <= 1
            or len(box) != 4
            or not all(math.isfinite(x) for x in box)
        ):
            raise ValueError("Malformed detection score/box")
        # Float32 resizing can put an edge fractionally outside the integer image
        # (observed: 1630.000122 for height 1630). Keep raw coordinates untouched;
        # accept at most one thousandth of a pixel, not genuinely out-of-frame boxes.
        tolerance = 0.001
        if not (
            -tolerance <= box[0] <= box[2] <= width + tolerance
            and -tolerance <= box[1] <= box[3] <= height + tolerance
        ):
            raise ValueError("Detection box outside oriented image")
        if item["class"] == "person":
            people.append(score)
    score = max(people, default=0.0)
    if score >= config.high_threshold:
        return "detected", score, "Person score meets high threshold"
    if len(raw["detections"]) >= config.max_detections:
        return "uncertain", score, "Output cap reached; low-scoring people may have been suppressed"
    if score >= config.low_threshold:
        return "uncertain", score, "Person score is within the configured uncertainty band"
    return (
        "not_detected",
        score,
        "No retained person detection meets low threshold; absence is not established",
    )


class TorchvisionDetector:
    family = "torchvision_object_detection"

    def __init__(self, config):
        import torch
        import torchvision
        from torchvision.models import detection

        self.config, self.torch = config, torch
        torch.set_num_threads(config.threads)
        if config.device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError(
                "CUDA was requested but is unavailable; no silent device substitution"
            )
        enums = {
            "fasterrcnn_resnet50_fpn_v2": detection.FasterRCNN_ResNet50_FPN_V2_Weights,
            "fasterrcnn_mobilenet_v3_large_320_fpn": detection.FasterRCNN_MobileNet_V3_Large_320_FPN_Weights,
        }
        weights = enums[config.model][config.weights]
        state = weights.get_state_dict(progress=False, check_hash=True)
        checkpoint = (
            Path(torch.hub.get_dir()) / "checkpoints" / Path(urlparse(weights.url).path).name
        )
        self.identity = {
            "model": config.model,
            "weights": f"{type(weights).__name__}.{weights.name}",
            "weights_url": weights.url,
            "artifact_sha256": file_hash(checkpoint),
            "artifact_bytes": checkpoint.stat().st_size,
            "torch": torch.__version__,
            "torchvision": torchvision.__version__,
            "adapter_version": "1",
        }
        self.model = getattr(detection, config.model)(
            weights=None,
            weights_backbone=None,
            box_score_thresh=config.output_floor,
            box_nms_thresh=config.nms_threshold,
            box_detections_per_img=config.max_detections,
        )
        self.model.load_state_dict(state)
        del state
        self.model.eval().to(config.device)
        self.transform, self.categories = weights.transforms(), weights.meta["categories"]
        if config.property == 'coco_objects':
            from .capabilities import registry
            if self.categories != registry()['categories']:
                raise ValueError('Model vocabulary differs from the supported capability registry')
            self.identity.update(adapter_version='2', categories=list(self.categories), registry_version=registry()['registry_version'])
        self.runtime = {
            "device": config.device,
            "threads": torch.get_num_threads(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "cuda_runtime": torch.version.cuda,
            "device_name": torch.cuda.get_device_name()
            if config.device == "cuda"
            else platform.processor(),
            "resize_min": self.model.transform.min_size,
            "resize_max": self.model.transform.max_size,
            "input_orientation": "PIL EXIF transpose, RGB",
            "batch_size": 1,
        }

    def predict(self, path):
        with Image.open(path) as original:
            image = ImageOps.exif_transpose(original).convert("RGB")
        width, height = image.size
        tensor = self.transform(image).to(self.config.device)
        if self.config.device == "cuda":
            self.torch.cuda.synchronize()
            self.torch.cuda.reset_peak_memory_stats()
        start = time.perf_counter()
        with self.torch.inference_mode():
            result = self.model([tensor])[0]
        if self.config.device == "cuda":
            self.torch.cuda.synchronize()
        elapsed = time.perf_counter() - start
        boxes = result["boxes"].cpu().tolist()
        labels = result["labels"].cpu().tolist()
        scores = result["scores"].cpu().tolist()
        return {
            "width": width,
            "height": height,
            "coordinate_system": "EXIF-oriented RGB pixels, xyxy",
            "detections": [
                {
                    "class_id": label,
                    "class": self.categories[label],
                    "score": score,
                    "box_xyxy": box,
                }
                for box, label, score in zip(boxes, labels, scores)
            ],
            "count": len(scores),
            "inference_seconds": elapsed,
            "cuda_peak_allocated_bytes": self.torch.cuda.max_memory_allocated()
            if self.config.device == "cuda"
            else None,
            "output_floor": self.config.output_floor,
            "nms_threshold": self.config.nms_threshold,
            "max_detections": self.config.max_detections,
            "representation": "post-NMS detections above output floor, not all network proposals",
        }

    def close(self):
        del self.model
        if self.config.device == "cuda":
            self.torch.cuda.empty_cache()


def build_detector(config):
    return TorchvisionDetector(config)

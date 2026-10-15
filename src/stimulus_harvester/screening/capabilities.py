"""Versioned published vocabulary; deterministic proposals, never an intent classifier."""

import copy
import json
from functools import lru_cache
from importlib.resources import files
from importlib.util import find_spec


@lru_cache
def registry():
    return json.loads(
        files("stimulus_harvester.resources")
        .joinpath("screening-capabilities.json")
        .read_text(encoding="utf-8")
    )


def labels():
    return {
        name: i
        for i, name in enumerate(registry()["categories"])
        if name not in {"N/A", "__background__"}
    }


ANIMALS = ("bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe")
ALIASES = {
    "people": ["person"],
    "no people": ["person"],
    "persons": ["person"],
    "cars": ["car"],
    "television": ["tv"],
    "televisions": ["tv"],
    "non-human animals": list(ANIMALS),
    "animals": list(ANIMALS),
}


def propose(text):
    original = text.strip()
    key = original.casefold()
    for prefix in ("exclude images containing ", "exclude ", "no "):
        if key.startswith(prefix):
            key = key[len(prefix) :].strip()
            break
    targets = ALIASES.get(key, [key] if key in labels() else [])
    partial = key in {"animals", "non-human animals"}
    return dict(
        text=original,
        targets=targets,
        mapping_id=f"alias:{key}" if targets else "manual",
        support="partial" if partial else "automatic" if targets else "unsupported",
        explanation="Only these ten COCO animal classes; other animals are not covered. Human inspection is required for the uncovered intent."
        if partial
        else "Cars means only car, not other vehicles."
        if key in {"car", "cars"}
        else "Exact published label: tv."
        if key in {"television", "televisions", "tv"}
        else "Harvester cannot automatically check this criterion with the currently available mechanisms."
        if not targets
        else "Proposed observable targets; confirm before use.",
        registry_version=registry()["registry_version"],
    )


def snapshot():
    return copy.deepcopy(registry())


def capability(criterion, detectors, *, installed=None):
    if criterion.mode == "manual":
        return "Manual review required"
    if installed is None:
        installed = find_spec("torch") is not None and find_spec("torchvision") is not None
    if not installed:
        return "Automatic mechanism unavailable; manual review required"
    if not any(d.enabled and d.property == "coco_objects" for d in detectors):
        return "Automatic screening not configured"
    return "Automatic check available" + (
        " for the confirmed subset; manual coverage remains"
        if criterion.coverage == "partial"
        else ""
    )

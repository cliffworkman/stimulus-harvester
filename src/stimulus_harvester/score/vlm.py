"""Pluggable VLM adjudication.

The default `NoOpVLM` backend means the rest of the pipeline works fully without any
VLM configured. The optional `OllamaVLM` backend calls a local Ollama server.

The *taxonomy* of observable properties asked about (e.g. "bars_visible" for a prison
task) is entirely config-driven via `VlmFieldSpec` -- this module has no knowledge of
any specific research task. What IS fixed here, deliberately, is the ethical
invariant: the model is only ever asked about directly observable scene properties,
never about psychological constructs such as "self relevance," emotion, or identity.
That constraint is baked into `OBSERVABLE_ONLY_PREAMBLE` so it cannot be configured
away by a future task's YAML file.
"""

from __future__ import annotations

import base64
import json
from abc import ABC, abstractmethod
from pathlib import Path

import httpx

from stimulus_harvester.config import VlmConfig, VlmFieldSpec
from stimulus_harvester.logging_utils import get_logger
from stimulus_harvester.models import VLMResult, VlmFieldValue

logger = get_logger("vlm")

OBSERVABLE_ONLY_PREAMBLE = """\
You are an image annotation assistant for an academic research project. You will be shown a
single photograph and must report ONLY directly observable visual properties of the scene. Do
not infer emotions, psychological states, intentions, self-relevance, or anything about who is
viewing the image or how they might feel. Do not guess at identity, criminal history, or any
information not visually evident.

Respond with a single JSON object and nothing else (no markdown, no commentary, no code
fences). Use exactly these keys:"""

_JSON_ONLY_SUFFIX = "Every key is required. Use true/false for booleans, not strings. Respond with JSON only."

_TYPE_HINT = {
    "bool": "boolean",
    "float": "number between 0 and 1",
    "str": "short string",
    "list_str": "array of short strings (use an empty array if none apply)",
}

_RETRY_SUFFIX = "\n\nYour previous reply was not valid JSON. Reply with valid JSON only."


def build_prompt(fields: list[VlmFieldSpec]) -> str:
    lines = [OBSERVABLE_ONLY_PREAMBLE, ""]
    for field in fields:
        lines.append(f'- "{field.name}" ({_TYPE_HINT[field.type]}): {field.description}')
    lines.append("")
    lines.append(_JSON_ONLY_SUFFIX)
    return "\n".join(lines)


def _validate_and_coerce(raw: dict, fields: list[VlmFieldSpec]) -> dict[str, VlmFieldValue]:
    out: dict[str, VlmFieldValue] = {}
    for field in fields:
        if field.name not in raw:
            if field.required:
                raise ValueError(f"missing required field: {field.name}")
            out[field.name] = None
            continue
        value = raw[field.name]
        if field.type == "bool":
            if not isinstance(value, bool):
                raise ValueError(f"field {field.name!r} expected bool, got {type(value).__name__}")
            out[field.name] = value
        elif field.type == "float":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"field {field.name!r} expected number, got {type(value).__name__}")
            out[field.name] = float(value)
        elif field.type == "str":
            if not isinstance(value, str):
                raise ValueError(f"field {field.name!r} expected string, got {type(value).__name__}")
            out[field.name] = value
        elif field.type == "list_str":
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                raise ValueError(f"field {field.name!r} expected list[str], got {type(value).__name__}")
            out[field.name] = value
    return out


class VLMBackend(ABC):
    name: str

    @abstractmethod
    def evaluate(self, image_path: Path, local_id: str, fields: list[VlmFieldSpec]) -> VLMResult:
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - trivial default
        pass


class NoOpVLM(VLMBackend):
    name = "noop"

    def evaluate(self, image_path: Path, local_id: str, fields: list[VlmFieldSpec]) -> VLMResult:
        return VLMResult(local_id=local_id, backend_name="noop", status="skipped")


class OllamaVLM(VLMBackend):
    """Calls a local Ollama server's /api/generate with structured JSON output.

    Never raises: any HTTP/timeout/connection error, or JSON that fails validation
    after retrying, is returned as a `status="error"` VLMResult so the pipeline
    continues to the next image.
    """

    name = "ollama"

    def __init__(
        self,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        client: httpx.Client | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def evaluate(self, image_path: Path, local_id: str, fields: list[VlmFieldSpec]) -> VLMResult:
        prompt = build_prompt(fields)
        image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        last_error: str | None = None

        for attempt in range(self.max_retries + 1):
            current_prompt = prompt if attempt == 0 else prompt + _RETRY_SUFFIX
            try:
                response = self._client.post(
                    f"{self.base_url}/api/generate",
                    json={
                        "model": self.model,
                        "prompt": current_prompt,
                        "images": [image_b64],
                        "format": "json",
                        "stream": False,
                        "options": {"temperature": 0.0},
                    },
                    timeout=self.timeout_seconds,
                )
                response.raise_for_status()
                envelope = response.json()
                raw = json.loads(envelope["response"])
                coerced = _validate_and_coerce(raw, fields)
                return VLMResult(
                    local_id=local_id,
                    backend_name="ollama",
                    model_name=self.model,
                    status="ok",
                    fields=coerced,
                )
            except (httpx.HTTPError, OSError) as exc:
                # Network/HTTP failures won't self-correct with a "reply valid JSON"
                # follow-up -- fail immediately rather than retrying pointlessly.
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("Ollama request failed for %s: %s", local_id, last_error)
                break
            except (KeyError, json.JSONDecodeError, ValueError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning(
                    "Malformed VLM response for %s (attempt %d/%d): %s",
                    local_id,
                    attempt + 1,
                    self.max_retries + 1,
                    last_error,
                )
                continue

        return VLMResult(
            local_id=local_id,
            backend_name="ollama",
            model_name=self.model,
            status="error",
            error_message=last_error,
        )


def build_backend(config: VlmConfig) -> VLMBackend:
    if config.backend == "ollama":
        return OllamaVLM(config.ollama_base_url, config.ollama_model, config.timeout_seconds, config.max_retries)
    return NoOpVLM()

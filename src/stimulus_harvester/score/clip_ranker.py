"""OpenCLIP-based image/text embedding.

All torch/open_clip imports are lazy (inside methods, not at module import time) so
that `import stimulus_harvester.score.clip_ranker` does not require the optional
`[clip]` extra to be installed -- only actually calling a method does.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    import numpy as np


class ClipRanker:
    def __init__(
        self,
        model_name: str,
        pretrained: str,
        device: Literal["auto", "cuda", "cpu"] = "auto",
    ):
        self.model_name = model_name
        self.pretrained = pretrained
        self._device_setting = device
        self._model = None
        self._preprocess = None
        self._tokenizer = None
        self._resolved_device: str | None = None

    def resolve_device(self) -> str:
        if self._resolved_device is None:
            if self._device_setting != "auto":
                self._resolved_device = self._device_setting
            else:
                import torch

                self._resolved_device = "cuda" if torch.cuda.is_available() else "cpu"
        return self._resolved_device

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        import open_clip

        device = self.resolve_device()
        model, _, preprocess = open_clip.create_model_and_transforms(
            self.model_name, pretrained=self.pretrained, device=device
        )
        model.eval()
        self._model = model
        self._preprocess = preprocess
        self._tokenizer = open_clip.get_tokenizer(self.model_name)

    def embed_images(self, image_paths: list[Path], batch_size: int = 16) -> "np.ndarray":
        import numpy as np
        import torch
        from PIL import Image

        self._ensure_loaded()
        device = self.resolve_device()
        chunks: list[np.ndarray] = []

        for start in range(0, len(image_paths), batch_size):
            batch_paths = image_paths[start : start + batch_size]
            tensors = []
            for path in batch_paths:
                with Image.open(path) as img:
                    tensors.append(self._preprocess(img.convert("RGB")))
            batch = torch.stack(tensors).to(device)
            with torch.no_grad():
                features = self._model.encode_image(batch)
                features = features / features.norm(dim=-1, keepdim=True)
            chunks.append(features.cpu().numpy())

        if not chunks:
            return np.zeros((0, 0), dtype="float32")
        return np.concatenate(chunks, axis=0)

    def embed_texts(self, texts: list[str]) -> "np.ndarray":
        import torch

        self._ensure_loaded()
        device = self.resolve_device()
        tokens = self._tokenizer(texts).to(device)
        with torch.no_grad():
            features = self._model.encode_text(tokens)
            features = features / features.norm(dim=-1, keepdim=True)
        return features.cpu().numpy()

"""MobileCLIP-S1 image encoder for dashboard reference library uploads."""

from __future__ import annotations

import base64
import io
import logging
import threading
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from PIL import Image

log = logging.getLogger(__name__)

MODEL_NAME = "MobileCLIP-S1"
MODEL_PRETRAINED = "datacompdr"
DEFAULT_TARGET_DIMS = 512
DEFAULT_EMBED_BYTES = 2048
BYTES_TO_DIMS = {512: 128, 1024: 256, 2048: 512}


class ImageEncoderError(Exception):
    pass


class ImageEncoder:
    """Lazy-loaded MobileCLIP image encoder (same pipeline as VIT.py)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._model = None
        self._preprocess = None
        self._device = "cpu"
        self._error: str | None = None

    @property
    def ready(self) -> bool:
        return self._model is not None and self._error is None

    @property
    def error(self) -> str | None:
        return self._error

    def _ensure_loaded(self) -> None:
        if self._model is not None or self._error is not None:
            return
        try:
            import torch  # type: ignore
            import open_clip  # type: ignore
            from PIL import Image  # noqa: F401
        except Exception as exc:
            self._error = (
                f"image encoder unavailable ({exc.__class__.__name__}: {exc}). "
                "Install torch and open_clip_torch in the backend environment."
            )
            log.warning(self._error)
            return

        try:
            device = "cuda" if torch.cuda.is_available() else "cpu"
            model, _, preprocess = open_clip.create_model_and_transforms(
                MODEL_NAME,
                pretrained=MODEL_PRETRAINED,
                device=device,
            )
            model.eval()
            self._model = model
            self._preprocess = preprocess
            self._device = device
            log.info("Image encoder ready (%s on %s)", MODEL_NAME, device)
        except Exception as exc:
            self._error = f"image encoder load failed: {exc}"
            log.warning(self._error)

    def encode_image_bytes(
        self,
        image_bytes: bytes,
        *,
        embedding_size_bytes: int = DEFAULT_EMBED_BYTES,
    ) -> tuple[str, int, int]:
        """
        Encode image bytes to a base64 float32 blob.

        Returns (data_b64, embedding_dim, embedding_size_bytes).
        """
        with self._lock:
            self._ensure_loaded()
            if self._error:
                raise ImageEncoderError(self._error)
            if self._model is None or self._preprocess is None:
                raise ImageEncoderError("image encoder not loaded")

            import torch  # type: ignore
            from PIL import Image

            size = embedding_size_bytes
            if size not in BYTES_TO_DIMS:
                size = min(BYTES_TO_DIMS, key=lambda n: abs(n - int(embedding_size_bytes)))
            target_dims = BYTES_TO_DIMS[size]

            try:
                img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
            except Exception as exc:
                raise ImageEncoderError(f"invalid image file: {exc}") from exc

            tensor = self._preprocess(img).unsqueeze(0).to(self._device)
            with torch.no_grad():
                emb = self._model.encode_image(tensor.float())
            emb = emb / emb.norm(dim=-1, keepdim=True)
            emb = emb[:, :target_dims]
            emb = emb / emb.norm(dim=-1, keepdim=True)
            vec = emb.cpu().numpy().astype(np.float32).reshape(-1)
            data_b64 = base64.b64encode(vec.tobytes()).decode("ascii")
            return data_b64, target_dims, size


image_encoder = ImageEncoder()

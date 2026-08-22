"""Embedding interfaces with a local FastEmbed implementation."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import numpy as np

from plotline_rag.config import CACHE_DIR, MODEL_NAME


TOKEN_RE = re.compile(r"[a-z0-9]+")


def normalize_rows(values: np.ndarray) -> np.ndarray:
    matrix = np.asarray(values, dtype=np.float32)
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32, copy=False)


class Embedder(Protocol):
    model_name: str

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray: ...

    def embed_query(self, text: str) -> np.ndarray: ...


class FastEmbedder:
    """Lazy wrapper around the ONNX-backed FastEmbed model."""

    def __init__(
        self,
        model_name: str = MODEL_NAME,
        cache_dir: Path | None = None,
        batch_size: int = 64,
    ) -> None:
        from fastembed import TextEmbedding

        self.model_name = model_name
        self.batch_size = batch_size
        model_cache = cache_dir or (CACHE_DIR / "models")
        model_cache.mkdir(parents=True, exist_ok=True)
        self._model = TextEmbedding(model_name=model_name, cache_dir=str(model_cache))

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        values = list(self._model.embed(list(texts), batch_size=self.batch_size))
        if not values:
            raise ValueError("cannot embed an empty document collection")
        return normalize_rows(np.asarray(values, dtype=np.float32))

    def embed_query(self, text: str) -> np.ndarray:
        query_embed = getattr(self._model, "query_embed", None)
        if query_embed is not None:
            values = list(query_embed(text))
        else:
            values = list(self._model.embed([text], batch_size=1))
        return normalize_rows(np.asarray(values, dtype=np.float32))[0]


class HashingEmbedder:
    """Small deterministic embedder used only by zero-network unit tests."""

    model_name = "test/hash-v1"

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim

    def _embed(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        for token in TOKEN_RE.findall(text.lower()):
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
            slot = int.from_bytes(digest, "big") % self.dim
            vector[slot] += 1.0
        return normalize_rows(vector)[0]

    def embed_documents(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            raise ValueError("cannot embed an empty document collection")
        return np.stack([self._embed(text) for text in texts]).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed(text)


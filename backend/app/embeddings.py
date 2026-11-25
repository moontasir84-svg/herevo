"""Text embeddings. Voyage AI (multilingual) in production; a deterministic hashing embedder for
tests and offline development, so the whole system runs without any API key."""
from __future__ import annotations

import hashlib
import math
import re
from functools import lru_cache

import numpy as np

from .config import get_settings

_WORD = re.compile(r"\w+", re.UNICODE)


class HashEmbedder:
    """Bag of words and character trigrams, hashed into a fixed-size unit vector. Not semantic, but
    stable, free and language-agnostic (works on Bangla script)."""

    def __init__(self, dim: int):
        self.dim = dim

    def _features(self, text: str):
        for w in _WORD.findall(text.lower()):
            yield "w:" + w
            padded = f"#{w}#"
            for i in range(len(padded) - 2):
                yield "c:" + padded[i : i + 3]

    def embed(self, texts: list[str], input_type: str = "document") -> list[np.ndarray]:
        out = []
        for t in texts:
            v = np.zeros(self.dim, dtype=np.float32)
            for f in self._features(t):
                h = int.from_bytes(hashlib.blake2b(f.encode(), digest_size=8).digest(), "little")
                v[h % self.dim] += 1.0 if (h >> 63) & 1 else -1.0
            n = math.sqrt(float((v * v).sum())) or 1.0
            out.append(v / n)
        return out


class VoyageEmbedder:
    def __init__(self, api_key: str, model: str, dim: int):
        import voyageai

        self.client = voyageai.Client(api_key=api_key)
        self.model = model
        self.dim = dim

    def embed(self, texts: list[str], input_type: str = "document") -> list[np.ndarray]:
        res = self.client.embed(texts, model=self.model, input_type=input_type, output_dimension=self.dim)
        return [np.asarray(e, dtype=np.float32) for e in res.embeddings]


@lru_cache
def get_embedder():
    s = get_settings()
    if s.embeddings_provider == "voyage" and s.voyage_api_key:
        return VoyageEmbedder(s.voyage_api_key, s.voyage_model, s.embedding_dim)
    return HashEmbedder(s.embedding_dim)

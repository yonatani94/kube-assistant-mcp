"""Ollama embedding adapter, separate from the chat provider."""

import math

import requests

from ..security import redact


class OllamaEmbedder:
    def __init__(self, model="embeddinggemma", base_url="http://localhost:11434", timeout=30):
        self.model, self.base_url, self.timeout = model, base_url.rstrip("/"), timeout
        response = requests.get(f"{self.base_url}/api/tags", timeout=timeout)
        response.raise_for_status()
        name = model if ":" in model else model + ":latest"
        models = response.json()["models"]
        matched = next((m for m in models if m["name"] in (model, name)), None)
        if not matched or not matched.get("digest"):
            raise ValueError("Embedding model is not installed or has no digest")
        self.identity = f"{name}@{matched['digest']}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = []
        for offset in range(0, len(texts), 16):
            batch = texts[offset : offset + 16]
            response = requests.post(
                f"{self.base_url}/api/embed",
                timeout=self.timeout,
                json={"model": self.model, "input": [redact(t) for t in batch], "truncate": False},
            )
            response.raise_for_status()
            result = response.json()["embeddings"]
            validate_vectors(result, len(batch))
            vectors.extend(result)
        validate_vectors(vectors, len(texts))
        return vectors


def validate_vectors(vectors, expected):
    if not isinstance(vectors, list) or len(vectors) != expected or not vectors:
        raise ValueError("Embedding count mismatch")
    dimension = len(vectors[0])
    if not dimension:
        raise ValueError("Empty embedding")
    for vector in vectors:
        if (
            len(vector) != dimension
            or not all(
                isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in vector
            )
            or not any(vector)
        ):
            raise ValueError("Invalid embedding vector")
    return dimension

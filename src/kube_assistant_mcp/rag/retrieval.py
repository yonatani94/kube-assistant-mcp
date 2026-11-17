"""Configuration and bounded retrieval. No optional imports until explicitly enabled."""

import os
import re
from dataclasses import dataclass

from ..security import redact


@dataclass
class RAGConfig:
    url: str = "http://localhost:6333"
    collection: str = "kube_assistant_knowledge"
    model: str = "embeddinggemma"
    ollama_url: str = "http://localhost:11434"
    timeout: int = 30
    top_k: int = 3
    max_context_chars: int = 5000
    chunk_chars: int = 1600
    overlap: int = 160

    def __post_init__(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", self.collection):
            raise ValueError("Invalid collection alias")
        if not 1 <= self.top_k <= 10 or not 200 <= self.max_context_chars <= 20000:
            raise ValueError("Invalid retrieval budget")
        if not 1 <= self.timeout <= 120:
            raise ValueError("Invalid RAG timeout")

    @classmethod
    def from_env(cls):
        return cls(
            url=os.getenv("KUBE_ASSISTANT_QDRANT_URL", cls.url),
            collection=os.getenv("KUBE_ASSISTANT_RAG_COLLECTION", cls.collection),
            model=os.getenv("KUBE_ASSISTANT_EMBED_MODEL", cls.model),
            ollama_url=os.getenv("KUBE_ASSISTANT_EMBED_URL", cls.ollama_url),
            timeout=int(os.getenv("KUBE_ASSISTANT_RAG_TIMEOUT", cls.timeout)),
            top_k=int(os.getenv("KUBE_ASSISTANT_RAG_TOP_K", cls.top_k)),
            max_context_chars=int(os.getenv("KUBE_ASSISTANT_RAG_CONTEXT_CHARS", cls.max_context_chars)),
            chunk_chars=int(os.getenv("KUBE_ASSISTANT_RAG_CHUNK_CHARS", cls.chunk_chars)),
            overlap=int(os.getenv("KUBE_ASSISTANT_RAG_OVERLAP", cls.overlap)),
        )


def rag_enabled():
    return os.getenv("KUBE_ASSISTANT_RAG_ENABLED", "").lower() in {"true", "1", "yes"}


class Retriever:
    def __init__(self, store, embedder, config):
        self.store, self.embedder, self.config = store, embedder, config

    def retrieve(self, query):
        vector = self.embedder.embed([redact(query[:2000])])[0]
        passages = self.store.search(vector, self.embedder.identity, self.config.top_k)
        result, remaining = [], self.config.max_context_chars
        for passage in passages:
            text = redact(passage["text"])
            # Keep complete chunks; do not cut a command or a qualification mid-sentence.
            cost = len(text) + len(passage["heading"]) + len(passage["source"]) + 100
            if cost > remaining:
                continue
            result.append(
                {
                    k: passage[k]
                    for k in (
                        "chunk_id",
                        "document_id",
                        "source",
                        "heading",
                        "content_hash",
                        "reviewed",
                        "score",
                    )
                }
            )
            result[-1]["text"] = text
            remaining -= cost
        return result

    def close(self):
        self.store.client.close()


def create_retriever(config=None):
    from qdrant_client import QdrantClient

    from .embeddings import OllamaEmbedder
    from .store import QdrantStore

    config = config or RAGConfig.from_env()
    embedder = OllamaEmbedder(config.model, config.ollama_url, config.timeout)
    client = QdrantClient(url=config.url, timeout=config.timeout)
    return Retriever(QdrantStore(client, config.collection), embedder, config)

"""Reproducible local retrieval pilot; no Kubernetes access or chat inference.

Run from the source checkout after starting Qdrant and pulling embeddinggemma.
"""
import json
import platform
import time
from importlib.metadata import version
from pathlib import Path

from kube_assistant_mcp.rag.documents import load_chunks
from kube_assistant_mcp.rag.retrieval import create_retriever

CASES = [
    ('Container exits repeatedly with a Python traceback after startup', 'runbooks/crashloopbackoff.md'),
    ('Container was OOMKilled and memory usage grew near its limit', 'runbooks/oomkilled.md'),
    ('ImagePullBackOff with manifest unknown when pulling a new image tag', 'runbooks/imagepullbackoff.md'),
    ('Pod is Pending with FailedScheduling insufficient cpu', 'runbooks/pending-pods.md'),
    ('Application lookup inventory no such host after namespace migration', 'runbooks/dns-failures.md'),
    ('Readiness probe failed with HTTP 503 but container is still running', 'runbooks/probe-failures.md'),
    ('Node disk pressure ephemeral storage eviction and exhausted capacity', 'runbooks/resource-exhaustion.md'),
]


def main():
    root = Path(__file__).resolve().parents[1]
    retriever = create_retriever()
    try:
        started = time.perf_counter()
        config = retriever.config
        chunks = load_chunks(root / 'knowledge', config.chunk_chars, config.overlap)
        indexed = retriever.store.ingest(chunks, retriever.embedder)
        ingest_seconds = time.perf_counter() - started
        rows = []
        for query, expected in CASES:
            started = time.perf_counter()
            passages = retriever.retrieve(query)
            rank = next((i for i, p in enumerate(passages, 1) if p['source'] == expected), None)
            rows.append({'query': query, 'expected_source': expected,
                         'sources': [p['source'] for p in passages], 'first_relevant_rank': rank,
                         'seconds': round(time.perf_counter() - started, 4)})
        report = {'purpose': 'Small authored retrieval pilot, not held-out diagnostic accuracy',
                  'python': platform.python_version(), 'platform': platform.platform(),
                  'qdrant_client': version('qdrant-client'), 'embedding_model': retriever.embedder.identity,
                  'index': indexed, 'chunk_chars': config.chunk_chars, 'overlap': config.overlap,
                  'top_k': config.top_k, 'context_chars': config.max_context_chars,
                  'ingestion_seconds': round(ingest_seconds, 4),
                  'hit_at_k': sum(r['first_relevant_rank'] is not None for r in rows) / len(rows),
                  'mrr_at_k': sum(1 / r['first_relevant_rank'] if r['first_relevant_rank'] else 0
                                  for r in rows) / len(rows), 'cases': rows}
        print(json.dumps(report, indent=2))
    finally:
        retriever.close()


if __name__ == '__main__':
    main()

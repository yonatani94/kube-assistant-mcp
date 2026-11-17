"""Opt-in real Ollama + Qdrant smoke test; isolated collection, no cluster access."""
import os
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(os.getenv('RUN_LIVE_RAG') != '1', reason='Set RUN_LIVE_RAG=1 for local services')


def test_real_ingestion_search_and_diagnostic_guidance():
    from kube_assistant_mcp.diagnostics import diagnose_pod
    from kube_assistant_mcp.models import IssueType, PodIssue, PodRef, Severity
    from kube_assistant_mcp.rag.documents import load_chunks
    from kube_assistant_mcp.rag.retrieval import RAGConfig, create_retriever

    class EvidenceClient:
        def find_failing_pods(self, namespace):
            return [PodIssue(PodRef('demo', namespace), IssueType.OOM_KILLED, Severity.HIGH)]

        def get_pod_logs(self, *args, **kwargs):
            return 'OOMKilled memory cgroup out of memory'

        def get_pod_events(self, *args):
            return ['Killing: memory limit exceeded']

    config = RAGConfig.from_env()
    config.collection = 'test_' + uuid4().hex
    retriever = create_retriever(config)
    try:
        chunks = load_chunks(Path(__file__).resolve().parents[2] / 'knowledge')
        retriever.store.ingest(chunks, retriever.embedder)
        assert retriever.store.ingest(chunks, retriever.embedder)['status'] == 'unchanged'
        result = diagnose_pod(EvidenceClient(), 'demo', use_llm=False, use_rag=True, retriever=retriever)
        assert result.rag_status == 'available'
        assert result.suggestions and result.evidence
        assert 'runbooks/oomkilled.md' in {p['source'] for p in result.retrieved_guidance}
        assert {p['chunk_id'] for p in result.retrieved_guidance} <= {c.chunk_id for c in chunks}
    finally:
        # Delete only snapshots with this freshly generated test prefix.
        for collection in retriever.store.client.get_collections().collections:
            if collection.name.startswith(config.collection + '__'):
                retriever.store.client.delete_collection(collection.name)
        retriever.close()

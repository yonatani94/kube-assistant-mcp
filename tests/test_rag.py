from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock

import pytest

from kube_assistant_mcp.rag.documents import load_chunks
from kube_assistant_mcp.rag.embeddings import OllamaEmbedder
from kube_assistant_mcp.rag.retrieval import RAGConfig, Retriever


class FakeEmbedder:
    identity = 'test@digest1'

    def embed(self, texts):
        return [[1.0, 0.0] if 'memory' in t.lower() else [0.0, 1.0] for t in texts]


@pytest.fixture
def store():
    qdrant = pytest.importorskip('qdrant_client')
    from kube_assistant_mcp.rag.store import QdrantStore
    client = qdrant.QdrantClient(':memory:')
    yield QdrantStore(client)
    client.close()


@pytest.fixture
def corpus(tmp_path):
    (tmp_path / 'memory.md').write_text('# Memory\n\nReviewed: 2026-11-17\n\nMemory limits require evidence.')
    (tmp_path / 'dns.md').write_text('# DNS\n\nCheck service names.')
    return tmp_path


def test_chunks_stable_bounded_and_code_aware(tmp_path):
    code = '```bash\nkubectl get pods\n```'
    (tmp_path / 'test.md').write_text('# Heading\n\n' + 'some words ' * 100 + '\n\n' + code)
    a = load_chunks(tmp_path, 200, 20)
    assert a == load_chunks(tmp_path, 200, 20)
    assert len({c.chunk_id for c in a}) == len(a)
    assert all(len(c.text) <= 200 for c in a)
    assert sum(code in c.text for c in a) == 1
    assert all(c.heading == 'Heading' and c.source == 'test.md' for c in a)


def test_oversized_fence_fails_instead_of_truncating(tmp_path):
    (tmp_path / 'a.md').write_text('# Test\n```text\n' + 'x'*300 + '\n```')
    with pytest.raises(ValueError, match='Code block'):
        load_chunks(tmp_path, 200, 20)


def test_document_escape_and_empty_corpus_rejected(tmp_path):
    root = tmp_path / 'root'
    root.mkdir()
    with pytest.raises(ValueError, match='no nonempty'):
        load_chunks(root)
    outside = tmp_path / 'outside.md'
    outside.write_text('private')
    (root / 'a.md').symlink_to(outside)
    with pytest.raises(ValueError, match='symlink'):
        load_chunks(root)


def test_real_qdrant_upsert_idempotence_and_reconciliation(store, corpus):
    chunks = load_chunks(corpus)
    assert store.ingest(chunks, FakeEmbedder())['status'] == 'indexed'
    aliases = store.client.get_aliases().aliases
    assert store.ingest(chunks, FakeEmbedder())['status'] == 'unchanged'
    assert store.client.get_aliases().aliases == aliases
    results = store.search([1., 0.], FakeEmbedder.identity)
    assert results[0]['source'] == 'memory.md'
    assert results[0]['chunk_id'] in {c.chunk_id for c in chunks}
    (corpus / 'dns.md').unlink()
    (corpus / 'memory.md').write_text('# Memory\n\nNew memory guidance.')
    updated = load_chunks(corpus)
    store.ingest(updated, FakeEmbedder())
    results = store.search([1., 0.], FakeEmbedder.identity, 10)
    assert {r['chunk_id'] for r in results} == {c.chunk_id for c in updated}
    assert not {r['chunk_id'] for r in results} & {c.chunk_id for c in chunks}


def test_failed_embedding_keeps_old_snapshot(store, corpus):
    store.ingest(load_chunks(corpus), FakeEmbedder())
    before = store.client.get_aliases().aliases
    (corpus / 'memory.md').write_text('# Memory\nNew version')
    broken = FakeEmbedder()
    broken.embed = Mock(side_effect=TimeoutError)
    with pytest.raises(TimeoutError):
        store.ingest(load_chunks(corpus), broken)
    assert store.client.get_aliases().aliases == before
    assert store.search([1., 0.], FakeEmbedder.identity)


def test_model_and_dimension_mismatch_rejected(store, corpus):
    chunks = load_chunks(corpus)
    store.ingest(chunks, FakeEmbedder())
    changed = FakeEmbedder()
    changed.identity = 'test@digest2'
    with pytest.raises(ValueError, match='model'):
        store.ingest(chunks, changed)
    with pytest.raises(ValueError, match='model'):
        store.search([1., 0.], changed.identity)
    with pytest.raises(ValueError, match='dimension'):
        store.search([1., 0., 0.], FakeEmbedder.identity)


def test_retrieval_budget_and_source_provenance(store, corpus):
    chunks = load_chunks(corpus)
    store.ingest(chunks, FakeEmbedder())
    retriever = Retriever(store, FakeEmbedder(), RAGConfig(top_k=1, max_context_chars=500))
    passages = retriever.retrieve('memory')
    assert len(passages) == 1
    assert passages[0]['source'] == 'memory.md'
    assert passages[0]['content_hash'] == chunks[1].content_hash
    retriever.config = replace(retriever.config, max_context_chars=200)
    assert sum(len(p['text']) + len(p['heading']) + len(p['source']) + 100
               for p in retriever.retrieve('memory')) <= 200


def test_ollama_adapter_batch_and_no_silent_truncation(mocker):
    tags = Mock()
    tags.json.return_value = {'models': [{'name': 'embeddinggemma:latest', 'digest': 'abc'}]}
    mocker.patch('requests.get', return_value=tags)
    response = Mock()
    response.json.return_value = {'embeddings': [[1., 0.]]}
    post = mocker.patch('requests.post', return_value=response)
    embedder = OllamaEmbedder()
    assert embedder.identity == 'embeddinggemma:latest@abc'
    assert embedder.embed(['password=secret']) == [[1., 0.]]
    assert post.call_args.kwargs['json']['truncate'] is False
    assert 'secret' not in post.call_args.kwargs['json']['input'][0]


def test_example_corpus_covers_categories():
    root = Path(__file__).resolve().parents[1] / 'knowledge'
    chunks = load_chunks(root)
    assert len({c.source for c in chunks}) == 9
    assert all(c.reviewed == '2026-11-17' for c in chunks)

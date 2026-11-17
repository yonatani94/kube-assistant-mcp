"""Qdrant snapshots: build completely, then atomically move the corpus alias.

One writer per alias is required. Old collections are retained for active readers/rollback.
"""

import json
from hashlib import sha256
from uuid import uuid4

from qdrant_client import QdrantClient, models

from .embeddings import validate_vectors

META_ID = "00000000-0000-0000-0000-000000000000"


class QdrantStore:
    def __init__(self, client: QdrantClient, collection="kube_assistant_knowledge"):
        self.client, self.collection = client, collection

    def metadata(self):
        if not self.client.collection_exists(self.collection):
            return None
        records = self.client.retrieve(self.collection, [META_ID], with_payload=True)
        if not records:
            raise ValueError("Collection has no compatible index metadata")
        return records[0].payload

    def ingest(self, chunks, embedder):
        fingerprint = sha256(json.dumps([c.payload() for c in chunks], sort_keys=True).encode()).hexdigest()
        old = self.metadata()
        if old and old["model"] != embedder.identity:
            raise ValueError("Embedding model changed; use a new collection alias and reindex")
        if old and old["fingerprint"] == fingerprint:
            return {"status": "unchanged", "chunks": len(chunks), "fingerprint": fingerprint}
        vectors = embedder.embed([f"{c.heading}\n{c.text}" for c in chunks])
        dimension = validate_vectors(vectors, len(chunks))
        if old and old["dimension"] != dimension:
            raise ValueError("Embedding dimension changed; use a new collection alias")
        name = f"{self.collection}__{uuid4().hex}"
        self.client.create_collection(
            name, vectors_config=models.VectorParams(size=dimension, distance=models.Distance.COSINE)
        )
        # Retain failed snapshots: an alias-switch timeout may have succeeded remotely.
        points = [
            models.PointStruct(id=c.chunk_id, vector=v, payload={"kind": "chunk", **c.payload()})
            for c, v in zip(chunks, vectors)
        ]
        metadata = {
            "kind": "metadata",
            "model": embedder.identity,
            "dimension": dimension,
            "fingerprint": fingerprint,
            "chunks": len(chunks),
        }
        points.append(
            models.PointStruct(id=META_ID, vector=[1.0] + [0.0] * (dimension - 1), payload=metadata)
        )
        for offset in range(0, len(points), 64):
            self.client.upsert(name, points[offset : offset + 64], wait=True)
        aliases = {a.alias_name for a in self.client.get_aliases().aliases}
        operations = []
        if self.collection in aliases:
            operations.append(
                models.DeleteAliasOperation(delete_alias=models.DeleteAlias(alias_name=self.collection))
            )
        operations.append(
            models.CreateAliasOperation(
                create_alias=models.CreateAlias(collection_name=name, alias_name=self.collection)
            )
        )
        self.client.update_collection_aliases(operations)
        return {"status": "indexed", **metadata}

    def search(self, vector, identity, limit=3):
        # Resolve once so metadata and search use the same immutable snapshot.
        aliases = {a.alias_name: a.collection_name for a in self.client.get_aliases().aliases}
        snapshot = aliases.get(self.collection, self.collection)
        records = self.client.retrieve(snapshot, [META_ID], with_payload=True)
        if not records or records[0].payload["model"] != identity:
            raise ValueError("Embedding model/index mismatch")
        dimension = validate_vectors([vector], 1)
        if records[0].payload["dimension"] != dimension:
            raise ValueError("Embedding dimension/index mismatch")
        result = self.client.query_points(
            snapshot,
            query=vector,
            limit=limit,
            with_payload=True,
            query_filter=models.Filter(
                must=[models.FieldCondition(key="kind", match=models.MatchValue(value="chunk"))]
            ),
        )
        return [{**p.payload, "score": p.score} for p in result.points]

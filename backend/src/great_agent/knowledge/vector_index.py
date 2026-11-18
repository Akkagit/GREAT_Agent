"""Rebuildable SQLite/Qdrant index, owned exclusively by one worker thread."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from great_agent.knowledge.embedding import EmbeddingUnavailable


class QdrantVectorIndex:
    def __init__(self, repository, embedding, path):
        self.repository, self.embedding, self.path = repository, embedding, Path(path)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="knowledge-vector")
        self._client = None
        self._builds = {}
        self._ready = {}
        self._failures = {}
        self._initialization_lock = asyncio.Lock()
        self._closed = False
        self.collection = "e5_" + embedding.fingerprint[:20]

    async def _run(self, callback, *args):
        return await asyncio.get_running_loop().run_in_executor(self._executor, callback, *args)

    def _open(self):
        if self._client is not None:
            return self._client
        from qdrant_client import QdrantClient, models
        self.path.mkdir(parents=True, exist_ok=True)
        client = QdrantClient(path=str(self.path))
        try:
            if not client.collection_exists(self.collection):
                client.create_collection(self.collection, vectors_config=models.VectorParams(
                    size=self.embedding.dimensions, distance=models.Distance.COSINE))
        except BaseException:
            client.close()
            raise
        self._client = client
        return client

    @staticmethod
    def _filter(**values):
        from qdrant_client import models
        return models.Filter(must=[models.FieldCondition(key=k, match=models.MatchValue(value=v)) for k, v in values.items()])

    def _generation(self, version_id):
        from great_agent.db.knowledge_chat_models import KnowledgeIndexGenerationModel as Generation
        with self.repository.sessions() as session:
            generations = session.scalars(select(Generation).where(Generation.version_id == version_id,
                Generation.embedding_fingerprint == self.embedding.fingerprint,
                Generation.tokenizer_fingerprint == self.embedding.tokenizer_fingerprint,
                Generation.config_fingerprint == self.embedding.config_fingerprint,
                Generation.status == "ready").order_by(Generation.created_at.desc())).all()
            client = self._open()
            for generation in generations:
                count = client.count(self.collection, count_filter=self._filter(generation_id=generation.id), exact=True).count
                if count == generation.point_count and count > 0:
                    return generation.id
        return None

    def _build(self, version_id, chunks):
        from great_agent.db.knowledge_chat_models import KnowledgeIndexGenerationModel as Generation, KnowledgeIndexSegmentModel as Segment
        from qdrant_client import models
        # Fail before staging or opening Qdrant if provisioning/RAM is unavailable.
        self.embedding.embed_query("index readiness")
        client = self._open()
        generation_id, count = str(uuid4()), 0
        with self.repository.sessions() as session:
            session.add(Generation(id=generation_id, version_id=version_id, embedding_fingerprint=self.embedding.fingerprint,
                tokenizer_fingerprint=self.embedding.tokenizer_fingerprint, config_fingerprint=self.embedding.config_fingerprint,
                status="staging", dimensions=self.embedding.dimensions, point_count=0))
            session.commit()
        try:
            for chunk in chunks:
                text = chunk["text"]
                for ordinal, span in enumerate(self.embedding.split_passage(text)):
                    if count >= 20000:
                        raise EmbeddingUnavailable("local semantic index point limit reached")
                    segment_id = str(uuid4())
                    source_spans = [{"chunk_id": chunk["id"], **span}]
                    passage = text[span["start"]:span["end"]]
                    payload = {"segment_id": segment_id, "generation_id": generation_id,
                        "parent_chunk_id": chunk["id"], "document_id": chunk["document_id"], "version_id": version_id}
                    vector = self.embedding.embed_passage(passage)
                    with self.repository.sessions() as session:
                        session.add(Segment(id=segment_id, generation_id=generation_id, parent_chunk_id=chunk["id"],
                            ordinal=ordinal, source_spans_json=json.dumps(source_spans), embedding_text_hash=sha256(("passage: " + passage).encode()).hexdigest(),
                            embedding_text=passage))
                        session.commit()
                    client.upsert(self.collection, points=[models.PointStruct(id=segment_id, vector=vector, payload=payload)], wait=True)
                    count += 1
            if not count or client.count(self.collection, count_filter=self._filter(generation_id=generation_id), exact=True).count != count:
                raise EmbeddingUnavailable("semantic index verification failed")
            with self.repository.sessions() as session:
                generation = session.get(Generation, generation_id)
                generation.status, generation.point_count = "ready", count
                session.commit()
            return generation_id
        except BaseException:
            with self.repository.sessions() as session:
                generation = session.get(Generation, generation_id)
                generation.status, generation.error_message = "failed", "semantic indexing failed"
                session.commit()
            raise

    async def _build_version(self, snapshot, version_id):
        try:
            self.repository.revalidate_snapshot(snapshot)
            chunks = [c for c in self.repository.list_snapshot_chunks(snapshot) if c["version_id"] == version_id]
            self._ready[version_id] = await self._run(self._build, version_id, chunks)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failures[version_id] = "semantic index is unavailable; lexical retrieval remains available"
        finally:
            self._builds.pop(version_id, None)

    def _search(self, generation_ids, query, limit):
        from great_agent.db.knowledge_chat_models import KnowledgeIndexGenerationModel as Generation, KnowledgeIndexSegmentModel as Segment
        from qdrant_client import models
        vector = self.embedding.embed_query(query)
        hits = self._open().query_points(self.collection, query=vector, query_filter=models.Filter(must=[
            models.FieldCondition(key="generation_id", match=models.MatchAny(any=generation_ids))]),
            limit=limit, with_payload=True, with_vectors=False).points
        result = []
        with self.repository.sessions() as session:
            for hit in hits:
                payload = hit.payload or {}
                segment = session.get(Segment, str(hit.id))
                generation = session.get(Generation, payload.get("generation_id", ""))
                if not segment or not generation or generation.status != "ready" or segment.generation_id not in generation_ids:
                    continue
                if generation.embedding_fingerprint != self.embedding.fingerprint or generation.version_id != payload.get("version_id"):
                    continue
                if segment.parent_chunk_id != payload.get("parent_chunk_id"):
                    continue
                result.append({**payload, "score": float(hit.score), "source_spans": json.loads(segment.source_spans_json),
                               "embedding_fingerprint": self.embedding.fingerprint})
        return result

    async def search(self, snapshot, query, limit=20):
        if self._closed:
            raise EmbeddingUnavailable("semantic index has closed")
        versions = [v["version_id"] for v in snapshot["document_versions"]]
        if not versions:
            return []
        pending = False
        # The readiness lookup awaits worker I/O. Serialize lookup+registration
        # so two cold requests cannot both schedule the same generation.
        async with self._initialization_lock:
            for version_id in versions:
                if version_id in self._failures:
                    raise EmbeddingUnavailable(self._failures[version_id])
                if version_id not in self._ready and version_id not in self._builds:
                    ready = await self._run(self._generation, version_id)
                    if ready:
                        self._ready[version_id] = ready
                    else:
                        self._builds[version_id] = asyncio.create_task(self._build_version(snapshot, version_id))
                pending |= version_id not in self._ready
        if pending:
            raise EmbeddingUnavailable("semantic index is building; lexical retrieval remains available")
        return await self._run(self._search, [self._ready[v] for v in versions], query, limit)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        if self._builds:
            await asyncio.gather(*list(self._builds.values()), return_exceptions=True)
        def close_owner():
            if self._client is not None:
                self._client.close()
                self._client = None
            self.embedding.close()
        await self._run(close_owner)
        self._executor.shutdown(wait=True)

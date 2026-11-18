from types import SimpleNamespace

import pytest


SNAPSHOT = {"conversation_id": "c", "base_id": "base", "binding_revision": 1,
            "document_versions": [{"document_id": "d", "version_id": "v-old"}]}


class Repository:
    def __init__(self):
        self.validated = 0
        self.chunks = {"a": {"id": "a", "version_id": "v-old", "document_id": "d", "text": "old immutable PPP"},
                       "b": {"id": "b", "version_id": "v-old", "document_id": "d", "text": "another PPP"}}

    def revalidate_snapshot(self, snapshot):
        self.validated += 1

    def search_snapshot(self, snapshot, query, limit=20):
        return {"items": [self.chunks["a"], {"id": "foreign", "document_id": "other", "version_id": "v-new"}]}

    def read_snapshot_chunk(self, snapshot, chunk_id):
        return self.chunks.get(chunk_id)


class Dense:
    async def search(self, snapshot, query, limit=20):
        return [{"parent_chunk_id": "b", "document_id": "d", "version_id": "v-old", "segment_id": "s1",
                 "score": 0.9, "embedding_fingerprint": "fixed-e5",
                 "source_spans": [{"start": 0, "end": 7, "offset_unit": "unicode_codepoint"}]},
                {"parent_chunk_id": "b", "document_id": "d", "version_id": "v-old", "segment_id": "s2",
                 "source_spans": [{"start": 8, "end": 11, "offset_unit": "unicode_codepoint"}]},
                {"parent_chunk_id": "a", "document_id": "d", "version_id": "v-old", "segment_id": "s3"},
                {"parent_chunk_id": "foreign", "document_id": "else", "version_id": "v-new"}]

    async def close(self):
        pass


async def test_lexical_filters_server_fixed_version_and_scope(tmp_path):
    from great_agent.knowledge.retrieval import KnowledgeRetriever
    repo = Repository()
    retriever = KnowledgeRetriever(repo, SimpleNamespace(embedding_enabled=False), tmp_path)
    result = await retriever.search(SNAPSHOT, "PPP")
    assert result["mode"] == "lexical"
    assert [c["id"] for c in result["items"]] == ["a"]
    assert result["queries"][0] == "PPP" and repo.validated >= 2
    await retriever.close()


async def test_rrf_deduplicates_parents_and_preserves_all_segment_spans(tmp_path):
    from great_agent.knowledge.retrieval import KnowledgeRetriever
    retriever = KnowledgeRetriever(Repository(), SimpleNamespace(embedding_enabled=False), tmp_path, dense=Dense())
    result = await retriever.search(SNAPSHOT, "PPP")
    assert result["mode"] == "hybrid"
    assert [c["id"] for c in result["items"]] == ["a", "b"]
    assert [span["start"] for span in result["items"][1]["matched_source_spans"]] == [0, 8]
    parent = result["items"][1]
    assert "".join(parent["text"][s["start"]:s["end"]] for s in parent["source_spans"]) == parent["text"]
    assert {x["channel"] for x in result["candidates"]} == {"lexical", "dense"}
    dense_candidate = next(x for x in result["candidates"] if x.get("segment_id") == "s1")
    assert dense_candidate["score"] == .9 and dense_candidate["embedding_fingerprint"] == "fixed-e5"
    assert dense_candidate["source_spans"][0]["start"] == 0
    assert "foreign" not in str(result)
    await retriever.close()


async def test_real_qdrant_generation_publication_sql_mapping_and_lifecycle(tmp_path):
    import asyncio
    from hashlib import sha256
    from sqlalchemy import select
    from great_agent.db.engine import create_engine_and_session_factory
    from great_agent.db.models import Base
    from great_agent.db.knowledge_repository import KnowledgeRepository
    from great_agent.db.knowledge_chat_repository import KnowledgeChatRepository
    from great_agent.db.knowledge_chat_models import KnowledgeIndexGenerationModel as Generation, KnowledgeIndexSegmentModel as Segment
    from great_agent.knowledge.vector_index import QdrantVectorIndex
    from great_agent.knowledge.embedding import EmbeddingUnavailable
    from test_knowledge_repository import upload, publish, parsed

    class TinyEmbedding:
        dimensions = 3
        fingerprint = "tiny-fixed-v1"
        tokenizer_fingerprint = "tiny-tokenizer-v1"
        config_fingerprint = "tiny-span-v1"
        def embed_query(self, text):
            return [1., 0., 0.]
        def embed_passage(self, text):
            return [1., 0., 0.] if "ambiguity" in text else [0., 1., 0.]
        def split_passage(self, text):
            return [{"start": 0, "end": len(text), "offset_unit": "unicode_codepoint"}]
        def close(self):
            pass

    engine, sessions = create_engine_and_session_factory(tmp_path / "index.sqlite")
    Base.metadata.create_all(engine)
    knowledge, repository = KnowledgeRepository(sessions), KnowledgeChatRepository(sessions)
    base = knowledge.create_base("GNSS")
    doc = publish(knowledge, upload(knowledge, base["id"]))
    conversation = repository.create_conversation(base_id=base["id"], document_ids=[doc["id"]])
    _, _, snapshot = repository.claim_turn(conversation["id"], "PPP", "r1", 60)
    index = QdrantVectorIndex(repository, TinyEmbedding(), tmp_path / "vectors")
    try:
        cold = await asyncio.gather(index.search(snapshot, "PPP"), index.search(snapshot, "PPP"), return_exceptions=True)
        assert all(isinstance(result, EmbeddingUnavailable) for result in cold)
        await asyncio.gather(*list(index._builds.values()))
        hits = await index.search(snapshot, "PPP")
        assert hits and all(h["version_id"] == snapshot["document_versions"][0]["version_id"] for h in hits)
        with sessions() as session:
            generation = session.scalars(select(Generation)).one()
            segments = session.scalars(select(Segment)).all()
            assert generation.status == "ready" and generation.point_count == len(segments)
            assert generation.embedding_fingerprint == "tiny-fixed-v1"
            assert segments[0].embedding_text_hash == sha256(("passage: " + segments[0].embedding_text).encode()).hexdigest()
        owner = index._client
        await index.search(snapshot, "PPP")
        assert index._client is owner
        job = knowledge.queue_reparse(doc["id"], "baseline", "again")
        publish(knowledge, {"document": doc, "job": job}, payload=parsed("new version troposphere"))
        assert (await index.search(snapshot, "PPP"))[0]["version_id"] == snapshot["document_versions"][0]["version_id"]
    finally:
        await index.close()
        engine.dispose()
    # The unique local client releases its persistent storage lock on close.
    from qdrant_client import QdrantClient
    reopened = QdrantClient(path=str(tmp_path / "vectors"))
    reopened.close()


async def test_dense_failure_reports_real_degraded_mode(tmp_path):
    from great_agent.knowledge.retrieval import KnowledgeRetriever
    class Missing(Dense):
        async def search(self, *args, **kw):
            raise RuntimeError("SECRET model missing")
    retriever = KnowledgeRetriever(Repository(), SimpleNamespace(embedding_enabled=False), tmp_path, dense=Missing())
    result = await retriever.search(SNAPSHOT, "PPP")
    assert result["mode"] == "lexical_degraded" and result["items"]
    assert "SECRET" not in result["warning"]
    await retriever.close()

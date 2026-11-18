"""Fixed-snapshot lexical/dense retrieval and rank fusion of immutable parents."""
from __future__ import annotations

import asyncio
from pathlib import Path
import time

from great_agent.knowledge.embedding import EmbeddingUnavailable, OnnxE5Embedding
from great_agent.knowledge.vector_index import QdrantVectorIndex


class KnowledgeRetriever:
    def __init__(self, repository, settings, root, *, dense=None):
        self.repository = repository
        self.settings = getattr(settings, "knowledge", settings)
        self.root = Path(root)
        self.dense = dense
        self._closed = False
        self._unavailable = False
        if dense is None and getattr(self.settings, "embedding_enabled", False):
            model = getattr(self.settings, "embedding_model_path", None)
            tokenizer = getattr(self.settings, "embedding_tokenizer_path", None)
            if model and tokenizer:
                embedding = OnnxE5Embedding(model, tokenizer, min_available_mb=getattr(self.settings, "embedding_min_available_mb", 384))
                self.dense = QdrantVectorIndex(repository, embedding, self.root / "vectors")
            else:
                self._unavailable = True

    def _parent(self, snapshot, candidate):
        chunk_id = candidate.get("parent_chunk_id", candidate.get("chunk_id", candidate.get("id")))
        allowed = {(v["document_id"], v["version_id"]) for v in snapshot["document_versions"]}
        if (candidate.get("document_id"), candidate.get("version_id")) not in allowed:
            return None
        try:
            chunk = self.repository.read_snapshot_chunk(snapshot, chunk_id)
        except (KeyError, ValueError):
            return None
        if not chunk or (chunk.get("document_id"), chunk.get("version_id")) not in allowed:
            return None
        return chunk

    async def search(self, snapshot: dict, query: str, limit=8):
        if self._closed:
            raise RuntimeError("knowledge retriever is closed")
        if not isinstance(query, str) or not query.strip() or len(query) > 2000:
            raise ValueError("search query must contain 1–2000 characters")
        if not 1 <= limit <= 20:
            raise ValueError("retrieval limit must be 1–20")
        started = time.monotonic()
        self.repository.revalidate_snapshot(snapshot)
        lexical = self.repository.search_snapshot(snapshot, query, limit=20)["items"]
        lexical_ms = (time.monotonic() - started) * 1000
        dense, mode, warning = [], "lexical", None
        dense_started = time.monotonic()
        if self.dense:
            try:
                dense = await self.dense.search(snapshot, query, limit=20)
                mode = "hybrid"
            except asyncio.CancelledError:
                raise
            except EmbeddingUnavailable as error:
                mode, warning = "lexical_degraded", str(error)
            except Exception:
                mode, warning = "lexical_degraded", "Semantic retrieval is unavailable; keyword retrieval was used."
        elif self._unavailable:
            mode, warning = "lexical_degraded", "Embedding resources are not configured; keyword retrieval was used."
        # Enabled/binding/version authorization is checked after asynchronous work,
        # then every candidate is re-read from authoritative SQLite.
        self.repository.revalidate_snapshot(snapshot)
        ranks, parents, candidates, spans = {}, {}, [], {}
        for channel, matches in (("lexical", lexical), ("dense", dense)):
            seen = set()
            parent_rank = 0
            for rank, candidate in enumerate(matches, 1):
                parent = self._parent(snapshot, candidate)
                if parent is None:
                    continue
                ident = parent["id"]
                parents[ident] = parent
                trace = {"channel": channel, "rank": rank, "chunk_id": ident,
                    "document_id": parent["document_id"], "version_id": parent["version_id"],
                    **({"segment_id": candidate["segment_id"]} if "segment_id" in candidate else {})}
                for field in ("score", "embedding_fingerprint"):
                    if field in candidate:
                        trace[field] = candidate[field]
                matched_spans = []
                if channel == "dense":
                    for span in candidate.get("source_spans", []):
                        if span.get("offset_unit") == "unicode_codepoint" and type(span.get("start")) is int and type(span.get("end")) is int:
                            if 0 <= span["start"] < span["end"] <= len(parent["text"]) and span.get("chunk_id", ident) == ident:
                                normalized = {"chunk_id": ident, **span}
                                matched_spans.append(normalized)
                                if normalized not in spans.setdefault(ident, []):
                                    spans[ident].append(normalized)
                    trace["source_spans"] = matched_spans
                candidates.append(trace)
                if ident not in seen:
                    seen.add(ident)
                    parent_rank += 1
                    ranks[ident] = ranks.get(ident, 0) + 1 / (60 + parent_rank)
        ordered = sorted(ranks, key=lambda ident: -ranks[ident])[:limit]
        items = []
        for ident in ordered:
            parent = parents[ident]
            # Evidence uses full immutable parents; hit spans remain separately
            # available for trace/highlight rather than claiming contiguous text.
            items.append({**parent, "rrf_score": ranks[ident],
                "source_spans": [{"chunk_id": ident, "start": 0, "end": len(parent["text"]), "offset_unit": "unicode_codepoint"}],
                "matched_source_spans": spans.get(ident, [])})
        result = {"mode": mode, "items": items, "candidates": candidates, "queries": [query],
                  "timings": {"lexical_ms": round(lexical_ms, 2), "dense_ms": round((time.monotonic() - dense_started) * 1000, 2),
                              "total_ms": round((time.monotonic() - started) * 1000, 2)}}
        if warning:
            result["warning"] = warning
        return result

    async def close(self):
        if not self._closed:
            self._closed = True
            if self.dense:
                await self.dense.close()

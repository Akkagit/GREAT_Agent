"""Explicitly provisioned E5 ONNX inference. No network or synthetic fallback."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path
import threading

import numpy as np


REVISION = "6a0d452a575215f80b8f66276dd4ee5d504942c6"
MODEL_SHA256 = "dd476dd0c2514e9b9be83aeb3853fac0763e0bdf4a71645407587d77c48a2d88"
TOKENIZER_SHA256 = "0b44a9d7b51c3c62626640cda0e2c2f70fdacdc25bbbd68038369d14ebdf4c39"


class EmbeddingUnavailable(RuntimeError):
    """A safe diagnostic; callers must retain lexical availability."""


def file_hash(path):
    digest = sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


class OnnxE5Embedding:
    dimensions = 384
    tokenizer_fingerprint = TOKENIZER_SHA256
    config_fingerprint = sha256(b"e5:query:/passage:;512;mask_mean;l2;split-contiguous-codepoints-v1").hexdigest()
    fingerprint = sha256(json.dumps({"revision": REVISION, "model": MODEL_SHA256,
        "tokenizer": TOKENIZER_SHA256, "dimensions": 384, "pool": "mask_mean", "norm": "l2",
        "prefixes": ["query: ", "passage: "], "max_tokens": 512}, sort_keys=True).encode()).hexdigest()

    def __init__(self, model_path, tokenizer_path, *, min_available_mb=384,
                 tokenizer_factory=None, session_factory=None, verify_resources=True):
        self.model_path, self.tokenizer_path = Path(model_path), Path(tokenizer_path)
        self.min_available_mb = min_available_mb
        self._tokenizer_factory, self._session_factory = tokenizer_factory, session_factory
        self._verify_resources = verify_resources
        self._session = self.tokenizer = None
        self._lock = threading.RLock()

    def _load(self):
        if self._session is not None:
            return
        try:
            if self._verify_resources:
                if not self.model_path.is_file() or not self.tokenizer_path.is_file():
                    raise EmbeddingUnavailable("embedding resources are not provisioned")
                if file_hash(self.model_path) != MODEL_SHA256 or file_hash(self.tokenizer_path) != TOKENIZER_SHA256:
                    raise EmbeddingUnavailable("embedding resource checksum mismatch")
            if self.min_available_mb:
                import psutil
                if psutil.virtual_memory().available < self.min_available_mb * 1024 * 1024:
                    raise EmbeddingUnavailable("insufficient available memory for semantic indexing")
            if self._tokenizer_factory:
                tokenizer = self._tokenizer_factory(str(self.tokenizer_path))
            else:
                from tokenizers import Tokenizer
                tokenizer = Tokenizer.from_file(str(self.tokenizer_path))
            pad_id = tokenizer.token_to_id("<pad>")
            if pad_id is None:
                raise EmbeddingUnavailable("embedding tokenizer has no padding token")
            tokenizer.enable_padding(pad_id=pad_id, pad_token="<pad>", direction="right")
            tokenizer.enable_truncation(max_length=512)
            if self._session_factory:
                session = self._session_factory(str(self.model_path))
            else:
                import onnxruntime as ort
                options = ort.SessionOptions()
                options.intra_op_num_threads = options.inter_op_num_threads = 1
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
                options.enable_cpu_mem_arena = False
                options.enable_mem_pattern = False
                options.add_session_config_entry("session.intra_op.allow_spinning", "0")
                options.add_session_config_entry("session.inter_op.allow_spinning", "0")
                session = ort.InferenceSession(str(self.model_path), sess_options=options, providers=["CPUExecutionProvider"])
            inputs = session.get_inputs()
            names = {x.name for x in inputs}
            if not {"input_ids", "attention_mask"}.issubset(names) or names - {"input_ids", "attention_mask", "token_type_ids"}:
                raise EmbeddingUnavailable("unsupported embedding model inputs")
            if any(x.type != "tensor(int64)" for x in inputs):
                raise EmbeddingUnavailable("unsupported embedding model input types")
            self.tokenizer, self._session = tokenizer, session
        except EmbeddingUnavailable:
            raise
        except Exception:
            raise EmbeddingUnavailable("embedding runtime could not initialize") from None

    def _embed(self, text, prefix):
        with self._lock:
            self._load()
            if prefix == "passage: ":
                self.tokenizer.no_truncation()
                try:
                    encoded = self.tokenizer.encode(prefix + text)
                finally:
                    self.tokenizer.enable_truncation(max_length=512)
                if len(encoded.ids) > 512:
                    raise EmbeddingUnavailable("long passage must be split before embedding")
            else:
                encoded = self.tokenizer.encode(prefix + text)
            values = {"input_ids": encoded.ids, "attention_mask": encoded.attention_mask,
                      "token_type_ids": encoded.type_ids}
            feeds = {x.name: np.asarray([values[x.name]], dtype=np.int64) for x in self._session.get_inputs()}
            try:
                outputs = self._session.run(None, feeds)
                hidden = next(x for x in outputs if getattr(x, "ndim", 0) == 3
                              and x.shape == (1, len(encoded.ids), self.dimensions))
                mask = feeds["attention_mask"].astype(np.float32)[..., None]
                vector = (hidden.astype(np.float32) * mask).sum(axis=1) / np.maximum(mask.sum(axis=1), 1)
                norm = np.linalg.norm(vector, axis=1, keepdims=True)
                if not np.isfinite(vector).all() or not np.isfinite(norm).all() or np.any(norm <= 0):
                    raise ValueError
                return (vector / norm)[0].astype(np.float32).tolist()
            except Exception:
                raise EmbeddingUnavailable("embedding inference failed") from None

    def embed_query(self, text):
        return self._embed(text, "query: ")

    def embed_passage(self, text):
        return self._embed(text, "passage: ")

    def split_passage(self, text):
        """Contiguous parent spans; verify complete prefixed inputs before encoding."""
        if not text:
            return []
        with self._lock:
            self._load()
            self.tokenizer.no_truncation()
            spans, start = [], 0
            try:
                while start < len(text):
                    # Binary search bounds cost while respecting real tokenizer
                    # special tokens, prefix, and Unicode source offsets.
                    low, high, best = start + 1, len(text), start
                    while low <= high:
                        end = (low + high) // 2
                        if len(self.tokenizer.encode("passage: " + text[start:end]).ids) <= 512:
                            best, low = end, end + 1
                        else:
                            high = end - 1
                    if best == start:
                        raise EmbeddingUnavailable("a source character exceeds embedding token capacity")
                    spans.append({"start": start, "end": best, "offset_unit": "unicode_codepoint"})
                    start = best
                return spans
            finally:
                self.tokenizer.enable_truncation(max_length=512)

    def close(self):
        with self._lock:
            self._session = self.tokenizer = None

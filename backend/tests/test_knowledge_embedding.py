from types import SimpleNamespace

import numpy as np
import pytest


class Tokenizer:
    def __init__(self):
        self.truncate = None
        self.pad = None

    def token_to_id(self, token):
        assert token == "<pad>"
        return 7

    def enable_truncation(self, max_length):
        self.truncate = max_length

    def no_truncation(self):
        self.truncate = None

    def enable_padding(self, **kwargs):
        self.pad = kwargs

    def encode(self, text, add_special_tokens=True):
        chars = list(text)
        ids = [1] + [10 + ord(x) % 100 for x in chars] + [2] if add_special_tokens else [10] * len(chars)
        offsets = [(0, 0)] + [(i, i + 1) for i in range(len(chars))] + [(0, 0)] if add_special_tokens else [(i, i + 1) for i in range(len(chars))]
        if self.truncate:
            ids, offsets = ids[:self.truncate], offsets[:self.truncate]
        return SimpleNamespace(ids=ids, attention_mask=[1] * len(ids), type_ids=[0] * len(ids), offsets=offsets)


class Session:
    def get_inputs(self):
        return [SimpleNamespace(name=x, type="tensor(int64)") for x in ["input_ids", "attention_mask", "token_type_ids"]]

    def run(self, outputs, feeds):
        assert feeds["input_ids"].dtype == np.int64
        length = feeds["input_ids"].shape[1]
        values = np.tile(np.arange(1, 385, dtype=np.float32), (1, length, 1))
        return [values]


def adapter(tmp_path):
    from great_agent.knowledge.embedding import OnnxE5Embedding
    tokenizer = Tokenizer()
    return OnnxE5Embedding(tmp_path / "model.onnx", tmp_path / "tokenizer.json", min_available_mb=0,
                           tokenizer_factory=lambda path: tokenizer, session_factory=lambda path: Session(),
                           verify_resources=False), tokenizer


def test_e5_real_inputs_pooling_normalization_and_tokenizer_pad(tmp_path):
    model, tokenizer = adapter(tmp_path)
    vector = model.embed_query("中文 PPP")
    assert len(vector) == 384 and np.linalg.norm(vector) == pytest.approx(1)
    assert tokenizer.pad["pad_id"] == 7 and tokenizer.truncate == 512
    assert model.fingerprint and model.dimensions == 384


def test_token_segments_cover_original_unicode_without_truncating_tail(tmp_path):
    model, _ = adapter(tmp_path)
    text = "中文😀PPP " * 160
    segments = model.split_passage(text)
    assert len(segments) > 1
    assert "".join(text[s["start"]:s["end"]] for s in segments) == text
    assert all(s["offset_unit"] == "unicode_codepoint" for s in segments)
    assert all(len(model.tokenizer.encode("passage: " + text[s["start"]:s["end"]]).ids) <= 512 for s in segments)


def test_missing_resources_never_produce_hash_vectors(tmp_path):
    from great_agent.knowledge.embedding import OnnxE5Embedding, EmbeddingUnavailable
    with pytest.raises(EmbeddingUnavailable):
        OnnxE5Embedding(tmp_path / "missing", tmp_path / "missing-tokenizer").embed_query("q")


def test_long_passage_must_be_split_instead_of_silently_truncated(tmp_path):
    from great_agent.knowledge.embedding import EmbeddingUnavailable
    model, _ = adapter(tmp_path)
    with pytest.raises(EmbeddingUnavailable, match="split"):
        model.embed_passage("x" * 600)

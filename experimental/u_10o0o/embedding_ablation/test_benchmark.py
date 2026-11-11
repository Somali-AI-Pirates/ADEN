"""Protect the ablation's numerical and evaluation-data boundaries."""

import numpy as np
import pytest
import torch

from experimental.u_10o0o.embedding_ablation import __main__ as benchmark


def test_bfloat_outputs_are_normalized_in_float32():
    class Model:
        def encode_document(self, texts, **kwargs):
            assert kwargs["normalize_embeddings"] is False
            return torch.tensor([[1, 2, 3], [4, 5, 6]], dtype=torch.bfloat16)

        def get_sentence_embedding_dimension(self):
            return 3

    result = benchmark.encode(
        Model(), ["first", "second"], kind="document", batch_size=2
    )
    assert result.dtype == np.float32
    np.testing.assert_allclose(np.linalg.norm(result, axis=1), [1, 1], atol=1e-6)


def test_query_cache_preserves_reordered_requests_and_rejects_unknown_text():
    rows = [{"id": "a", "question": "first"}, {"id": "b", "question": "second"}]
    cache = benchmark.SavedQueries(rows, np.eye(2, dtype=np.float32))
    np.testing.assert_array_equal(
        cache.encode_query(["second", "first"]), [[0, 1], [1, 0]]
    )
    with pytest.raises(KeyError):
        cache.encode_query(["not embedded"])
    with pytest.raises(ValueError, match="inconsistent"):
        benchmark.SavedQueries([rows[0], rows[0]], np.eye(2, dtype=np.float32))


@pytest.mark.parametrize(
    "array", [np.array([[np.nan, 0]]), np.zeros((1, 2)), np.ones((2, 2))]
)
def test_corrupted_vectors_are_rejected(array):
    with pytest.raises(ValueError):
        benchmark.validate_vectors(array, 1, 2)


def test_scoring_does_not_open_gold_when_any_condition_is_incomplete(
    monkeypatch, tmp_path
):
    from app.evaluation import dataset

    calls = []
    monkeypatch.setattr(benchmark, "validate_inputs", lambda run: {})

    def validate(run, name, frozen):
        if name == "qwen3":
            raise ValueError("incomplete")
        return [], []

    monkeypatch.setattr(benchmark, "validate_results", validate)
    monkeypatch.setattr(dataset, "load_references", lambda path: calls.append(path))
    with pytest.raises(ValueError, match="incomplete"):
        benchmark.score(tmp_path)
    assert calls == []


def test_selected_scoring_still_requires_every_selected_arm_before_gold(
    monkeypatch, tmp_path
):
    from app.evaluation import dataset
    from experimental.u_10o0o.embedding_ablation import score_selected

    gold_reads = []
    validated = []
    monkeypatch.setattr(score_selected, "validate_inputs", lambda run: {})

    def validate(run, name, frozen):
        validated.append(name)
        if name == "kure_v3":
            raise ValueError("selected arm is incomplete")
        return [], []

    monkeypatch.setattr(score_selected, "validate_results", validate)
    monkeypatch.setattr(
        dataset, "load_references", lambda path: gold_reads.append(path)
    )
    with pytest.raises(ValueError, match="incomplete"):
        score_selected.score(tmp_path, ["snowflake", "kure_v3"], tmp_path / "score")
    assert validated == ["snowflake", "kure_v3"]
    assert gold_reads == []

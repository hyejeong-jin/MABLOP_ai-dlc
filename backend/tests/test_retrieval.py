"""Cosine Top_K retrieval tests: brute-force correctness, cap, ordering, scale-invariance.

Validates: Requirements 2.3, 2.4, 7.2 (Design: cosine_top_k; Property 6).
"""
import io

import numpy as np
import pytest

import retrieval


def _brute_force(query, matrix, k):
    """Reference: full cosine, sort desc, take min(k, N) as [(idx, score)]."""
    qn = query / (np.linalg.norm(query) + 1e-12)
    mn = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)
    scores = mn @ qn
    order = np.argsort(-scores)[: min(k, scores.shape[0])]
    return [(int(i), float(scores[i])) for i in order]


def test_load_index_round_trip():
    m = np.arange(12, dtype=np.float32).reshape(4, 3)
    buf = io.BytesIO()
    np.save(buf, m)
    loaded = retrieval.load_index(buf.getvalue())
    assert loaded.dtype == np.float32
    np.testing.assert_array_equal(loaded, m)


def test_matches_brute_force_reference():
    rng = np.random.default_rng(0)
    matrix = rng.standard_normal((20, 8))
    query = rng.standard_normal(8)
    got = retrieval.cosine_top_k(query, matrix, 5)
    ref = _brute_force(query, matrix, 5)
    assert [i for i, _ in got] == [i for i, _ in ref]
    np.testing.assert_allclose([s for _, s in got], [s for _, s in ref], atol=1e-9)


def test_returns_min_k_n():
    rng = np.random.default_rng(1)
    matrix = rng.standard_normal((3, 4))
    query = rng.standard_normal(4)
    assert len(retrieval.cosine_top_k(query, matrix, 10)) == 3  # k > N
    assert len(retrieval.cosine_top_k(query, matrix, 2)) == 2   # k < N


def test_scores_descending():
    rng = np.random.default_rng(2)
    matrix = rng.standard_normal((15, 6))
    query = rng.standard_normal(6)
    scores = [s for _, s in retrieval.cosine_top_k(query, matrix, 7)]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("factor", [0.01, 3.0, 100.0])
def test_scale_invariant_query(factor):
    rng = np.random.default_rng(3)
    matrix = rng.standard_normal((12, 5))
    query = rng.standard_normal(5)
    base = [i for i, _ in retrieval.cosine_top_k(query, matrix, 5)]
    scaled = [i for i, _ in retrieval.cosine_top_k(query * factor, matrix, 5)]
    assert base == scaled


def test_scale_invariant_row():
    rng = np.random.default_rng(4)
    matrix = rng.standard_normal((12, 5))
    query = rng.standard_normal(5)
    base = retrieval.cosine_top_k(query, matrix, 12)
    scaled_matrix = matrix.copy()
    scaled_matrix[7] *= 50.0  # positive scaling of one row
    scaled = retrieval.cosine_top_k(query, scaled_matrix, 12)
    assert [i for i, _ in base] == [i for i, _ in scaled]
    np.testing.assert_allclose([s for _, s in base], [s for _, s in scaled], atol=1e-9)

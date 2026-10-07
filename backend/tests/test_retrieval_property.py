"""Property-based tests for cosine Top_K retrieval.

Feature: mablop-mvp, Property 6
Validates: Requirements 2.3, 2.4, 7.2 (Design: Property 6).
"""
import numpy as np
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

import retrieval

pytestmark = pytest.mark.property

# Finite, bounded, non-tiny floats; magnitude keeps rows well away from zero vectors.
_floats = st.floats(
    min_value=-1e3, max_value=1e3, allow_nan=False, allow_infinity=False, width=64
)


def _brute_force(query, matrix, k):
    """Reference: full cosine, argsort desc, take min(k, N) as [(idx, score)]."""
    qn = query / (np.linalg.norm(query) + 1e-12)
    mn = matrix / (np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-12)
    scores = mn @ qn
    order = np.argsort(-scores)[: min(k, scores.shape[0])]
    return [(int(i), float(scores[i])) for i in order]


@st.composite
def _case(draw):
    """Draw (query[D], matrix[N, D], k) with no zero/NaN/inf rows; k may exceed N."""
    n = draw(st.integers(min_value=1, max_value=30))
    d = draw(st.integers(min_value=1, max_value=10))
    matrix = draw(arrays(np.float64, (n, d), elements=_floats))
    query = draw(arrays(np.float64, (d,), elements=_floats))
    # Avoid (near) zero vectors: cosine direction is undefined/degenerate there.
    norms_ok = np.all(np.linalg.norm(matrix, axis=1) > 1e-3)
    if not norms_ok or np.linalg.norm(query) <= 1e-3:
        # Nudge to a stable non-zero direction instead of discarding the draw.
        matrix = matrix + 1.0
        query = query + 1.0
    k = draw(st.integers(min_value=1, max_value=n + 5))  # include k > N
    return query, matrix, k


@settings(max_examples=100, deadline=None)
@given(_case())
def test_cosine_top_k_matches_brute_force_and_cap(case):
    """Feature: mablop-mvp, Property 6

    cosine_top_k returns min(k, N) results in score-descending order,
    matching a brute-force reference. Robust to tie ambiguity.
    Validates: Requirements 2.3, 2.4, 7.2
    """
    query, matrix, k = case
    n = matrix.shape[0]
    got = retrieval.cosine_top_k(query, matrix, k)
    ref = _brute_force(query, matrix, k)

    # Returns min(k, N) results (2.4, 7.2).
    assert len(got) == min(k, n)

    got_scores = [s for _, s in got]
    ref_scores = [s for _, s in ref]

    # Scores are in descending order.
    assert all(a >= b - 1e-9 for a, b in zip(got_scores, got_scores[1:]))

    # Same score sequence as the reference (tolerant: tie order is ambiguous).
    np.testing.assert_allclose(got_scores, ref_scores, atol=1e-9)

    # Indices match the reference exactly when the selection boundary is unambiguous
    # (no tie straddling position k-1/k); otherwise only the score sequence is checked.
    all_scores = [s for _, s in _brute_force(query, matrix, n)]  # full ranking, desc
    boundary_clear = k >= n or abs(all_scores[k - 1] - all_scores[k]) > 1e-9
    if boundary_clear:
        assert set(i for i, _ in got) == set(i for i, _ in ref)


@settings(max_examples=100, deadline=None)
@given(_case(), st.floats(min_value=1e-3, max_value=1e3,
                          allow_nan=False, allow_infinity=False))
def test_cosine_top_k_scale_invariant(case, factor):
    """Feature: mablop-mvp, Property 6

    Positive scaling of the query or any indexed row leaves the result unchanged.
    Validates: Requirements 2.3, 2.4, 7.2
    """
    query, matrix, k = case
    base = retrieval.cosine_top_k(query, matrix, k)

    scaled_q = retrieval.cosine_top_k(query * factor, matrix, k)
    scaled_m = matrix.copy()
    scaled_m[0] *= factor  # positive scaling of one indexed row
    scaled_row = retrieval.cosine_top_k(query, scaled_m, k)

    base_scores = [s for _, s in base]
    np.testing.assert_allclose([s for _, s in scaled_q], base_scores, atol=1e-9)
    np.testing.assert_allclose([s for _, s in scaled_row], base_scores, atol=1e-9)

"""Property 5: re-learning overwrites the Vector_Index, never accumulates (Req 1.10)."""
import importlib

import boto3
import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

BUCKET = "mablop-test"


def _post(id_prefix, i, dim):
    """One chunk-meta dict + its vector, with a run-unique chunkId."""
    meta = {
        "chunkId": f"{id_prefix}-c{i}",
        "postId": f"{id_prefix}-p{i // 2}",
        "offset": i * 10,
        "text": f"{id_prefix} 본문 {i}",
    }
    return meta


_vec = lambda dim: st.lists(
    st.floats(min_value=-100, max_value=100, allow_nan=False, allow_infinity=False),
    min_size=dim, max_size=dim,
)


@st.composite
def _run(draw, id_prefix):
    """A non-empty run: fixed dim D, N posts, each a (meta, vector) pair."""
    dim = draw(st.integers(min_value=1, max_value=8))
    n = draw(st.integers(min_value=1, max_value=6))
    metas = [_post(id_prefix, i, dim) for i in range(n)]
    vectors = [draw(_vec(dim)) for _ in range(n)]
    return metas, vectors


@settings(max_examples=50, deadline=None)
@given(run_a=_run("A"), run_b=_run("B"))
def test_relearn_overwrites_not_accumulates(run_a, run_b):
    """Feature: mablop-mvp, Property 5

    Two successive learn runs over the same fixed index keys: the stored
    Vector_Index reflects ONLY run B -- no accumulation or duplication.
    Validates: Requirements 1.10
    """
    metas_a, vecs_a = run_a
    metas_b, vecs_b = run_b

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import os
        os.environ["MABLOP_BUCKET"] = BUCKET
        os.environ["MABLOP_EMBED_MODEL"] = "test-embed-model"
        import s3store
        import retrieval
        import index_store
        import checkpoint

        importlib.reload(s3store)
        importlib.reload(retrieval)
        importlib.reload(index_store)
        importlib.reload(checkpoint)

        # Run A: fresh runId, accumulate every post, finalize.
        state_a = checkpoint.resume_or_new("run-A")
        for meta, vec in zip(metas_a, vecs_a):
            checkpoint.record_post(state_a, meta["postId"] + meta["chunkId"], [meta], [vec])
        checkpoint.finalize(state_a)

        # Run B: fresh runId over the same fixed index keys, finalize.
        state_b = checkpoint.resume_or_new("run-B")
        for meta, vec in zip(metas_b, vecs_b):
            checkpoint.record_post(state_b, meta["postId"] + meta["chunkId"], [meta], [vec])
        checkpoint.finalize(state_b)

        matrix, row_map = index_store.load_index()

    expected = np.asarray(vecs_b, dtype=np.float32)
    assert matrix.shape == expected.shape, "stored matrix must match run B exactly, not accumulate"
    assert np.allclose(matrix, expected)

    stored_ids = [row_map[str(i)]["chunkId"] for i in range(len(row_map))]
    assert stored_ids == [m["chunkId"] for m in metas_b]
    # No run-A artifacts survive.
    a_ids = {m["chunkId"] for m in metas_a}
    assert not (set(stored_ids) & a_ids)
    assert len(row_map) == len(metas_b)

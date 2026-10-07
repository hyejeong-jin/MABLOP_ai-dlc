"""PBT: embedding index build+store then load round-trips the matrix + row-map (Req 1.5, 4.4; Property 2)."""
import importlib

import boto3
import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

BUCKET = "mablop-test"

# N>=1: empty-matrix (N=0) is a known separate edge case, out of scope here.
_floats = st.floats(min_value=-1e6, max_value=1e6, allow_nan=False, allow_infinity=False, width=32)


@st.composite
def _matrix_and_meta(draw):
    n = draw(st.integers(min_value=1, max_value=20))
    d = draw(st.integers(min_value=1, max_value=16))
    vectors = draw(st.lists(st.lists(_floats, min_size=d, max_size=d), min_size=n, max_size=n))
    meta = [
        {"chunkId": f"c{i}", "postId": f"p{i // 2}", "offset": i * 10, "text": f"본문 {i}"}
        for i in range(n)
    ]
    return vectors, meta


@settings(max_examples=100, deadline=None)
@given(_matrix_and_meta())
def test_property_index_round_trips_through_storage(data):
    """Feature: mablop-mvp, Property 2

    Validates: Requirements 1.5, 4.4
    """
    import os

    vectors, meta = data
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        os.environ["MABLOP_BUCKET"] = BUCKET
        os.environ["MABLOP_EMBED_MODEL"] = "test-embed-model"
        import s3store
        import retrieval
        import index_store

        importlib.reload(s3store)
        importlib.reload(retrieval)
        importlib.reload(index_store)

        index_store.build_and_store_index(vectors, meta)
        matrix, row_map = index_store.load_index()

        original = np.asarray(vectors, dtype=np.float32)
        assert matrix.shape == original.shape
        assert np.allclose(matrix, original)
        assert [row_map[str(i)]["chunkId"] for i in range(len(meta))] == [m["chunkId"] for m in meta]

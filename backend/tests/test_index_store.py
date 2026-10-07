"""Index store tests: build+store then load round-trips the float32 matrix + row-map (Req 1.5; Property 2)."""
import importlib

import boto3
import numpy as np
import pytest
from moto import mock_aws

BUCKET = "mablop-test"


@pytest.fixture
def mod(monkeypatch):
    """Fresh moto S3 with test bucket + embed-model env; yield reloaded index_store."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import s3store
        import retrieval
        import index_store

        importlib.reload(s3store)
        importlib.reload(retrieval)
        importlib.reload(index_store)
        yield index_store


def _meta(n):
    return [
        {"chunkId": f"c{i}", "postId": f"p{i // 2}", "offset": i * 10, "text": f"본문 {i}"}
        for i in range(n)
    ]


def test_round_trip_matrix_and_map(mod):
    vectors = [[0.1, -0.2, 0.3], [1.5, 0.0, -3.25], [0.0, 0.0, 0.0]]
    meta = _meta(3)
    mod.build_and_store_index(vectors, meta)

    matrix, row_map = mod.load_index()
    assert matrix.dtype == np.float32
    assert matrix.shape == (3, 3)
    assert np.allclose(matrix, np.asarray(vectors, dtype=np.float32))
    # identical chunk-id mapping, row -> chunkId
    assert [row_map[str(i)]["chunkId"] for i in range(3)] == ["c0", "c1", "c2"]
    assert row_map["1"] == {"chunkId": "c1", "postId": "p0", "offset": 10, "text": "본문 1"}


def test_manifest_dim_count(mod):
    vectors = [[1.0, 2.0, 3.0, 4.0], [5.0, 6.0, 7.0, 8.0]]
    manifest = mod.build_and_store_index(vectors, _meta(2))
    assert manifest["dim"] == 4
    assert manifest["count"] == 2
    assert manifest["model"] == "test-embed-model"
    assert manifest["builtAt"]
    assert len(manifest["checksum"]) == 64

    stored = mod.load_index()[0]
    # checksum stable across rebuild of identical bytes
    import s3store

    assert s3store.get_json(mod.MANIFEST_KEY)["checksum"] == manifest["checksum"]
    assert stored.shape == (2, 4)

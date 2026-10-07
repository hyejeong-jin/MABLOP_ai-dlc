"""Checkpoint/resume tests (Req 1.9, 1.10): save/load round-trip, resume skips done posts,
finalize builds index + clears, fresh runId starts clean (overwrite)."""
import importlib

import boto3
import numpy as np
import pytest
from moto import mock_aws

BUCKET = "mablop-test"


@pytest.fixture
def mod(monkeypatch):
    """Fresh moto S3 + bucket; yield reloaded checkpoint module."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import s3store
        import retrieval
        import index_store
        import checkpoint

        importlib.reload(s3store)
        importlib.reload(retrieval)
        importlib.reload(index_store)
        importlib.reload(checkpoint)
        yield checkpoint


def _chunks(post_id, n):
    return [
        {"chunkId": f"{post_id}-c{i}", "postId": post_id, "offset": i * 10, "text": f"{post_id} 본문 {i}"}
        for i in range(n)
    ]


def _vecs(n, d=3):
    return [[float(i), float(i) + 0.5, float(i) - 0.5] for i in range(n)][:n] or [[0.0] * d]


def test_save_load_round_trip(mod):
    state = mod.new_state("run-1")
    mod.record_post(state, "p0", _chunks("p0", 2), [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    mod.save(state)

    loaded = mod.load("run-1")
    assert loaded["runId"] == "run-1"
    assert loaded["processedPosts"] == ["p0"]
    assert loaded["chunks"][0]["chunkId"] == "p0-c0"
    assert loaded["vectors"] == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
    assert loaded["startedAt"] and loaded["updatedAt"]


def test_load_absent_is_none(mod):
    assert mod.load("nope") is None
    # resume_or_new falls back to a clean state
    fresh = mod.resume_or_new("nope")
    assert fresh["processedPosts"] == [] and fresh["chunks"] == []


def test_resume_skips_processed_posts(mod):
    # First pass processes p0, then "times out" and saves.
    s1 = mod.new_state("run-2")
    mod.record_post(s1, "p0", _chunks("p0", 1), [[1.0, 0.0, 0.0]])
    mod.save(s1)

    # Second invocation resumes; p0 must not be reprocessed.
    s2 = mod.resume_or_new("run-2")
    assert mod.is_processed(s2, "p0")
    mod.record_post(s2, "p0", _chunks("p0", 1), [[9.9, 9.9, 9.9]])  # ignored
    assert s2["vectors"] == [[1.0, 0.0, 0.0]]  # unchanged, no duplication

    # New post p1 appends normally.
    mod.record_post(s2, "p1", _chunks("p1", 1), [[0.0, 1.0, 0.0]])
    assert s2["processedPosts"] == ["p0", "p1"]
    assert s2["vectors"] == [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]


def test_resume_equals_uninterrupted(mod):
    import index_store

    # Interrupted run: p0 then resume for p1, then finalize.
    s1 = mod.new_state("run-3")
    mod.record_post(s1, "p0", _chunks("p0", 1), [[1.0, 2.0, 3.0]])
    mod.save(s1)
    s2 = mod.resume_or_new("run-3")
    mod.record_post(s2, "p1", _chunks("p1", 1), [[4.0, 5.0, 6.0]])
    mod.finalize(s2)
    interrupted = index_store.load_index()[0]

    # Uninterrupted run building the same accumulated data directly.
    expected = index_store.build_and_store_index(
        [[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]],
        _chunks("p0", 1) + _chunks("p1", 1),
    )
    assert expected["count"] == 2
    assert np.allclose(interrupted, np.asarray([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32))


def test_finalize_builds_index_and_clears(mod):
    state = mod.new_state("run-4")
    mod.record_post(state, "p0", _chunks("p0", 2), [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]])
    mod.save(state)
    assert mod.load("run-4") is not None

    manifest = mod.finalize(state)
    assert manifest["count"] == 2 and manifest["dim"] == 3
    # checkpoint cleared after finalize
    assert mod.load("run-4") is None

    import index_store

    matrix, row_map = index_store.load_index()
    assert matrix.shape == (2, 3)
    assert row_map["0"]["chunkId"] == "p0-c0"


def test_should_pause_budget(mod, monkeypatch):
    monkeypatch.setenv("MABLOP_LEARN_TIME_BUDGET", "100")
    assert mod.should_pause(100.0) is True
    assert mod.should_pause(150.0) is True
    assert mod.should_pause(99.9) is False


def test_fresh_runid_overwrites_not_inherits(mod):
    import index_store

    # Run A: completes with two posts.
    a = mod.new_state("run-A")
    mod.record_post(a, "p0", _chunks("p0", 1), [[1.0, 1.0, 1.0]])
    mod.record_post(a, "p1", _chunks("p1", 1), [[2.0, 2.0, 2.0]])
    mod.finalize(a)
    assert index_store.load_index()[0].shape == (2, 3)

    # Fresh run B does NOT inherit A's checkpoint state.
    b = mod.resume_or_new("run-B")
    assert b["processedPosts"] == [] and b["chunks"] == [] and b["vectors"] == []
    mod.record_post(b, "p9", _chunks("p9", 1), [[7.0, 8.0, 9.0]])
    mod.finalize(b)

    # Index reflects only run B (overwrite at fixed keys, Req 1.10).
    matrix, row_map = index_store.load_index()
    assert matrix.shape == (1, 3)
    assert np.allclose(matrix, np.asarray([[7.0, 8.0, 9.0]], dtype=np.float32))
    assert row_map["0"]["chunkId"] == "p9-c0"

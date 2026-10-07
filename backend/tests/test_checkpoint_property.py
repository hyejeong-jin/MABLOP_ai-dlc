"""Property test: checkpoint resume equals an uninterrupted learn run (Req 1.9; Property 4).

@given a random list of posts (unique ids, random chunk_meta + small vectors) and a random
pause point, we assert that finishing a run by resuming from a mid-run checkpoint yields the
same Vector_Index (matrix + row_map) and processedPosts as running straight through.

index_store writes fixed S3 keys, so each branch uses its OWN moto bucket + module reload to
avoid cross-branch overwrite. @given can't take pytest fixtures per example, so S3 is set up
inside a `with mock_aws():` block per example (same pattern as test_image_analyzer_caption.py).
"""
import importlib
import os

import boto3
import numpy as np
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

DIM = 3


def _reload_stack(bucket):
    """Fresh moto bucket + env, reload the S3-backed modules; return (checkpoint, index_store)."""
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=bucket)
    os.environ["MABLOP_BUCKET"] = bucket
    os.environ["MABLOP_EMBED_MODEL"] = "test-embed-model"
    import s3store
    import retrieval
    import index_store
    import checkpoint

    importlib.reload(s3store)
    importlib.reload(retrieval)
    importlib.reload(index_store)
    importlib.reload(checkpoint)
    return checkpoint, index_store


# A post: unique id (assigned later), 1..3 chunks, each with a DIM-length vector.
# min_size=1: an empty post set builds an empty index, which is an index_store concern
# (reshape(0, -1) is undefined), not a resume-equivalence question - out of scope here.
_posts = st.lists(
    st.integers(min_value=1, max_value=3),
    min_size=1,
    max_size=6,
)


def _build_posts(chunk_counts):
    """Turn a list of per-post chunk counts into (id, chunk_meta, vectors) tuples with unique ids."""
    posts = []
    seed = 0
    for pi, n in enumerate(chunk_counts):
        pid = f"p{pi}"
        chunks, vecs = [], []
        for ci in range(n):
            chunks.append(
                {
                    "chunkId": f"{pid}-c{ci}",
                    "postId": pid,
                    "offset": ci * 10,
                    "text": f"{pid} chunk {ci}",
                }
            )
            vecs.append([float(seed), float(seed) + 0.25, float(seed) - 0.5])
            seed += 1
        posts.append((pid, chunks, vecs))
    return posts


@settings(max_examples=100, deadline=None)
@given(chunk_counts=_posts, pause=st.integers(min_value=0, max_value=6))
def test_property_resume_equals_uninterrupted(chunk_counts, pause):
    """Feature: mablop-mvp, Property 4 - resuming from a checkpoint equals an uninterrupted run.

    Validates: Requirements 1.9
    """
    posts = _build_posts(chunk_counts)
    pause = min(pause, len(posts))

    # Branch A: uninterrupted - record every post into one state, then finalize.
    with mock_aws():
        cp, idx = _reload_stack("mablop-uninterrupted")
        s = cp.new_state("run-uninterrupted")
        for pid, chunks, vecs in posts:
            cp.record_post(s, pid, chunks, vecs)
        cp.finalize(s)
        uninterrupted_processed = list(s["processedPosts"])
        a_matrix, a_map = idx.load_index()
        a_matrix = np.array(a_matrix)

    # Branch B: interrupted - record up to pause, save, resume_or_new, record rest, finalize.
    with mock_aws():
        cp, idx = _reload_stack("mablop-interrupted")
        s1 = cp.new_state("run-interrupted")
        for pid, chunks, vecs in posts[:pause]:
            cp.record_post(s1, pid, chunks, vecs)
        cp.save(s1)
        s2 = cp.resume_or_new("run-interrupted")
        for pid, chunks, vecs in posts[pause:]:
            cp.record_post(s2, pid, chunks, vecs)
        cp.finalize(s2)
        interrupted_processed = list(s2["processedPosts"])
        b_matrix, b_map = idx.load_index()
        b_matrix = np.array(b_matrix)

    assert interrupted_processed == uninterrupted_processed
    assert a_matrix.shape == b_matrix.shape
    if a_matrix.size:
        assert np.allclose(a_matrix, b_matrix)
    a_ids = [a_map[str(i)]["chunkId"] for i in range(len(a_map))]
    b_ids = [b_map[str(i)]["chunkId"] for i in range(len(b_map))]
    assert a_ids == b_ids

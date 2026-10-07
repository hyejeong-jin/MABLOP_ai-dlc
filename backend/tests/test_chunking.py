"""Unit tests for post-body chunking (Req 1.4; Design Style_Learner step 4)."""
import pytest

import chunking


def test_empty_body_yields_no_chunks():
    assert chunking.chunk_body("") == []


def test_short_body_yields_single_chunk():
    body = "짧은 본문"
    assert chunking.chunk_body(body, chunk_size=100, overlap=10) == [body]


def test_body_equal_to_chunk_size_is_single_chunk():
    body = "a" * 50
    assert chunking.chunk_body(body, chunk_size=50, overlap=5) == [body]


def test_all_chunks_non_empty_and_bounded():
    body = "x" * 1000
    chunks = chunking.chunk_body(body, chunk_size=100, overlap=20)
    assert chunks  # non-empty list
    for c in chunks:
        assert c
        assert len(c) <= 100


def test_exact_multiple_coverage():
    # 300 chars, size 100, no overlap -> exactly 3 chunks, perfect reconstruction.
    body = "".join(str(i % 10) for i in range(300))
    chunks = chunking.chunk_body(body, chunk_size=100, overlap=0)
    assert len(chunks) == 3
    assert chunking.reconstruct(chunks, overlap=0) == body


def test_with_overlap_adjacent_chunks_share_prefix():
    body = "".join(str(i % 10) for i in range(250))
    size, overlap = 100, 20
    chunks = chunking.chunk_body(body, chunk_size=size, overlap=overlap)
    # Each subsequent chunk's first `overlap` chars equal prev chunk's last `overlap`.
    for prev, cur in zip(chunks, chunks[1:]):
        assert cur[:overlap] == prev[-overlap:]


def test_reconstruction_drops_no_content():
    body = "가나다라마바사아자차카타파하" * 100  # 1400 chars
    size, overlap = 300, 50
    chunks = chunking.chunk_body(body, chunk_size=size, overlap=overlap)
    assert chunking.reconstruct(chunks, overlap=overlap) == body


def test_reconstruction_covers_entire_body_various_params():
    body = "".join(chr(0xAC00 + (i % 100)) for i in range(1234))
    for size, overlap in [(50, 0), (50, 10), (128, 32), (1000, 100), (5, 1)]:
        chunks = chunking.chunk_body(body, chunk_size=size, overlap=overlap)
        assert chunks
        assert all(c for c in chunks)
        assert all(len(c) <= size for c in chunks)
        assert chunking.reconstruct(chunks, overlap=overlap) == body


@pytest.mark.parametrize(
    "size,overlap",
    [(0, 0), (-1, 0), (10, -1), (10, 10), (10, 20)],
)
def test_invalid_params_raise(size, overlap):
    with pytest.raises(ValueError):
        chunking.chunk_body("some body text", chunk_size=size, overlap=overlap)


def test_defaults_produce_valid_chunks():
    body = "한국어 블로그 본문 샘플 " * 200
    chunks = chunking.chunk_body(body)
    assert chunks
    assert all(len(c) <= chunking.DEFAULT_CHUNK_SIZE for c in chunks)
    assert chunking.reconstruct(chunks) == body

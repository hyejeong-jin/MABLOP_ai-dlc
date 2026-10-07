"""Property-based tests for post-body chunking (Req 1.4; Design Property 1)."""
from hypothesis import given, settings
from hypothesis import strategies as st

import chunking


# Random text: Korean, latin, unicode, whitespace; empty allowed (min_size=0).
_text = st.text(
    alphabet=st.characters(min_codepoint=0x20, max_codepoint=0xD7FF),
    min_size=0,
    max_size=2000,
)


@st.composite
def _size_overlap(draw):
    chunk_size = draw(st.integers(min_value=1, max_value=512))
    overlap = draw(st.integers(min_value=0, max_value=chunk_size - 1))
    return chunk_size, overlap


@given(body=_text, params=_size_overlap())
@settings(max_examples=100, deadline=None)
def test_chunking_preserves_content_and_bounds_size(body, params):
    """Feature: mablop-mvp, Property 1

    Validates: Requirements 1.4
    """
    chunk_size, overlap = params
    chunks = chunking.chunk_body(body, chunk_size=chunk_size, overlap=overlap)

    for c in chunks:
        assert c, "chunk must be non-empty"
        assert len(c) <= chunk_size, "chunk must not exceed chunk_size"

    assert chunking.reconstruct(chunks, overlap=overlap) == body

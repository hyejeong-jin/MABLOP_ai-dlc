"""Property-based test for image placeholder well-formedness.

Validates: Requirements 3.6, 3.7, 4.1, 7.4
Design: Property 9 (image placeholders are well-formed and bounded).
"""
import re

from hypothesis import given, settings
from hypothesis import strategies as st

import drafting_engine as de

_REF_RE = re.compile(r"!\[img-(\d+)\]")


def _placeholder_token(n):
    return "![img-{}]".format(n)


@st.composite
def _model_output(draw):
    """Random image_count (0..15) + random markdown interleaving valid,
    out-of-range, and duplicate ![img-N] placeholders with plain text."""
    image_count = draw(st.integers(min_value=0, max_value=15))
    k = min(image_count, de._MAX_IMAGES)

    # Candidate placeholder indices: in-range (1..k), out-of-range (>k, incl >10).
    in_range = list(range(1, k + 1))
    out_range = list(range(k + 1, k + 6)) + [11, 12, 20]
    pool = in_range + out_range

    tokens = []
    # random multiset of placeholders (may repeat -> exercises dedup)
    if pool:
        picks = draw(
            st.lists(st.sampled_from(pool), min_size=0, max_size=12)
        )
        tokens.extend(_placeholder_token(n) for n in picks)

    # plain text fragments (incl. possibility of none)
    texts = draw(
        st.lists(st.text(alphabet=st.characters(blacklist_characters="[]"),
                         max_size=8),
                 min_size=0, max_size=12)
    )
    tokens.extend(texts)

    draw(st.randoms()).shuffle(tokens)
    markdown = " ".join(tokens)
    return markdown, image_count


@settings(max_examples=100, deadline=None)
@given(_model_output())
def test_placeholders_well_formed_and_bounded(case):
    """Feature: mablop-mvp, Property 9"""
    markdown, image_count = case
    out = de.normalize_placeholders(markdown, image_count)

    k = min(image_count, de._MAX_IMAGES)

    # Each in-range index appears exactly once.
    for n in range(1, k + 1):
        assert out.count(_placeholder_token(n)) == 1

    # No out-of-range index (M > k) appears at all.
    referenced = [int(m) for m in _REF_RE.findall(out)]
    for n in referenced:
        assert 1 <= n <= k

    # Never more than 10 distinct referenced images.
    assert len(set(referenced)) <= de._MAX_IMAGES

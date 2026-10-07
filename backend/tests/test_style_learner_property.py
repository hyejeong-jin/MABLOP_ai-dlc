"""Property-based test for the MAX_POST_COUNT cap (Req 1.8, Design Property 3)."""
import os

from hypothesis import given, settings
from hypothesis import strategies as st

import style_learner


@settings(max_examples=100, deadline=None)
@given(
    n=st.integers(min_value=0, max_value=30),
    m=st.integers(min_value=1, max_value=20),
)
def test_discover_posts_caps_at_max_post_count(n, m):
    """Feature: mablop-mvp, Property 3

    Validates: Requirements 1.8

    For any list of discovered posts of any length, a single run processes
    exactly min(distinct_posts, MAX_POST_COUNT) posts.
    """
    # Distinct posts via unique logNo values -> unique postId (resolve dedup key).
    posts = [
        f"https://blog.naver.com/myblog/{1000000000 + i}" for i in range(n)
    ]
    distinct = n  # each logNo is unique, so no dedup reduction

    prev = os.environ.get(style_learner._MAX_POST_COUNT_ENV)
    os.environ[style_learner._MAX_POST_COUNT_ENV] = str(m)
    try:
        result = style_learner._discover_posts({"posts": posts})
    finally:
        if prev is None:
            os.environ.pop(style_learner._MAX_POST_COUNT_ENV, None)
        else:
            os.environ[style_learner._MAX_POST_COUNT_ENV] = prev

    assert len(result) == min(distinct, m)

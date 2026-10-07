"""Table-driven tests for Naver URL resolution (Req 1.1)."""
import pytest

import style_learner

_MOBILE = "https://m.blog.naver.com/myblog/221234567890"


@pytest.mark.parametrize(
    "shape,url",
    [
        ("desktop_path", "https://blog.naver.com/myblog/221234567890"),
        ("mobile_path", "https://m.blog.naver.com/myblog/221234567890"),
        (
            "desktop_query",
            "https://blog.naver.com/?blogId=myblog&logNo=221234567890",
        ),
        (
            "postview",
            "https://blog.naver.com/PostView.naver?blogId=myblog&logNo=221234567890",
        ),
    ],
)
def test_resolve_url_normalizes_to_mobile(shape, url):
    assert style_learner.resolve_url(url) == _MOBILE


def test_resolve_url_ignores_extra_query_and_fragment():
    url = (
        "https://blog.naver.com/myblog/221234567890"
        "?referrerCode=0&foo=bar#section"
    )
    assert style_learner.resolve_url(url) == _MOBILE


def test_resolve_url_handles_trailing_slash_and_whitespace():
    assert style_learner.resolve_url("  https://blog.naver.com/myblog/221234567890/  ") == _MOBILE


@pytest.mark.parametrize(
    "url",
    [
        "https://blog.naver.com/myblog",
        "https://blog.naver.com/PostView.naver?blogId=myblog",
        "https://blog.naver.com/",
        "not a url",
    ],
)
def test_resolve_url_rejects_unresolvable(url):
    with pytest.raises(ValueError):
        style_learner.resolve_url(url)

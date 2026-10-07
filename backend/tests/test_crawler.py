"""Unit tests for HTTP crawl + HTML->text extraction (Req 1.2, 1.7)."""
import re
import urllib.request
from urllib.error import URLError

import pytest

import crawler

_NAVER_HTML = """
<!doctype html>
<html><head><title>blog</title>
<style>.x{color:red}</style>
<script>var a = 1;</script>
</head>
<body>
  <div id="header">네비게이션 메뉴 무시해야 함</div>
  <div id="postViewArea">
    <p>안녕하세요.   오늘은   제주   카페   투어입니다.</p>
    <script>trackClick();</script>
    <p>라떼가 <b>정말</b> 맛있었어요!</p>
  </div>
  <div id="footer">댓글 영역</div>
</body></html>
"""

_PLAIN_HTML = """
<html><body><h1>제목</h1><p>본문   문단   하나</p>
<script>ignore()</script><p>문단 둘</p></body></html>
"""


class _FakeResponse:
    """Minimal urlopen() context-manager stand-in."""

    def __init__(self, body: bytes, charset: str = "utf-8"):
        self._body = body
        self._charset = charset
        self.headers = _FakeHeaders(charset)

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeHeaders:
    def __init__(self, charset):
        self._charset = charset

    def get_content_charset(self):
        return self._charset


def _patch_urlopen(monkeypatch, response):
    def fake_urlopen(req, timeout=None):
        return response
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


def test_crawl_returns_decoded_html(monkeypatch):
    _patch_urlopen(monkeypatch, _FakeResponse(_PLAIN_HTML.encode("utf-8")))
    html = crawler.crawl("https://m.blog.naver.com/x/1")
    assert "<h1>제목</h1>" in html


def test_extract_body_naver_container_only():
    text = crawler.extract_body(_NAVER_HTML)
    # Body text present.
    assert "제주 카페 투어입니다." in text
    assert "라떼가 정말 맛있었어요!" in text
    # Outside the postViewArea container is excluded.
    assert "네비게이션" not in text
    assert "댓글 영역" not in text


def test_extract_body_no_markup_leakage():
    text = crawler.extract_body(_NAVER_HTML)
    assert "<" not in text and ">" not in text
    # Script/style payloads never leak.
    assert "trackClick" not in text
    assert "color:red" not in text
    assert "var a" not in text


def test_extract_body_whitespace_collapsed():
    text = crawler.extract_body(_NAVER_HTML)
    assert "  " not in text  # no runs of 2+ spaces
    assert not re.search(r"[\t\n\r]", text)
    assert text == text.strip()


def test_extract_body_fallback_without_naver_container():
    text = crawler.extract_body(_PLAIN_HTML)
    assert "제목" in text
    assert "본문 문단 하나" in text
    assert "문단 둘" in text
    assert "ignore" not in text  # script stripped
    assert "<" not in text and ">" not in text


def test_crawl_surfaces_failure(monkeypatch):
    def boom(req, timeout=None):
        raise URLError("connection refused")
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(crawler.CrawlError):
        crawler.crawl("https://m.blog.naver.com/x/1")

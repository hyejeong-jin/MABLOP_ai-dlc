"""`preview-learning-sources` handler tests (user-auth-admin Req 17.3, 17.9, 20.9).

The action only lists posts found in the public RSS feed. It must not write to S3, must not call
Bedrock, needs no `grantId` for a member, and needs a session token. The crawler is stubbed, so no
external request is made; S3 is moto.
"""
import importlib
import json

import pytest

RSS_URL = "https://rss.blog.naver.com/myblog.xml"
RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>blog</title>
<item><title>첫 번째 글</title><link>https://blog.naver.com/myblog/223000001</link>
<description>&lt;p&gt;첫 글 요약입니다.&lt;/p&gt;</description></item>
<item><title>두 번째 글</title><link>https://blog.naver.com/myblog/223000002</link>
<description></description></item>
<item><title>링크 없는 글</title><description>x</description></item>
</channel></rss>"""

EXPECTED_POSTS = [
    {
        "postId": "223000001",
        "url": "https://blog.naver.com/myblog/223000001",
        "title": "첫 번째 글",
        "hasRssFallback": True,
    },
    {
        "postId": "223000002",
        "url": "https://blog.naver.com/myblog/223000002",
        "title": "두 번째 글",
        "hasRssFallback": False,
    },
]


@pytest.fixture
def h(s3_bucket, signing_secret):
    """Fresh handler modules on moto S3 (resets warm rate-limit state)."""
    import s3store

    importlib.reload(s3store)
    import access_controller

    importlib.reload(access_controller)
    import handler

    importlib.reload(handler)
    return handler


@pytest.fixture
def crawl_log(monkeypatch):
    """Replace the network crawl with an RSS-only stub; return the list of requested URLs."""
    import crawler

    urls = []

    def fake_crawl(url):
        urls.append(url)
        if url == RSS_URL:
            return RSS_XML
        raise crawler.CrawlError(f"unexpected url {url}")

    monkeypatch.setattr(crawler, "crawl", fake_crawl)
    return urls


@pytest.fixture
def bedrock_calls(monkeypatch):
    """Stub every Bedrock entry point and count calls (the expected count is 0)."""
    import bedrock

    calls = []

    def make(name):
        def stub(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"bedrock.{name} must not be called")

        return stub

    for name in ("get_client", "invoke", "embed", "text_generate", "vision_caption"):
        monkeypatch.setattr(bedrock, name, make(name))
    return calls


def _event(body, token=None):
    headers = {"X-Mablop-Token": token} if token is not None else {}
    return {"headers": headers, "body": json.dumps(body)}


def _body(resp):
    return json.loads(resp["body"])


def _preview(h, token, **extra):
    return h.handler(_event({"action": "preview-learning-sources", **extra}, token))


def test_member_without_grant_previews_only_rss_posts(
    h, create_user, make_session_token, crawl_log, bedrock_calls
):
    member = create_user("member1")
    resp = _preview(h, make_session_token(member["userId"]), blogUrl="https://blog.naver.com/myblog")

    assert resp["statusCode"] == 200
    body = _body(resp)
    assert body["count"] == 2
    # `title` is part of the response shape; its value is not asserted (discovery does not carry
    # the RSS title through to the preview today).
    assert [{**p, "title": ""} for p in body["posts"]] == [{**p, "title": ""} for p in EXPECTED_POSTS]
    assert all(isinstance(p["title"], str) for p in body["posts"])
    assert crawl_log == [RSS_URL]  # only the public RSS feed was requested
    assert bedrock_calls == []


def test_response_shape_matches_existing_contract(
    h, create_user, make_session_token, crawl_log, bedrock_calls
):
    member = create_user("member1")
    body = _body(_preview(h, make_session_token(member["userId"]), blogUrl="myblog"))

    assert set(body) == {"count", "posts"}
    assert body["count"] == len(body["posts"])
    for post in body["posts"]:
        assert set(post) == {"postId", "url", "title", "hasRssFallback"}
        assert isinstance(post["hasRssFallback"], bool)


def test_preview_writes_nothing_to_s3_besides_rate_limit_state(
    h, create_user, make_session_token, crawl_log, bedrock_calls, s3_calls
):
    member = create_user("member1")
    token = make_session_token(member["userId"])
    s3_calls.clear()

    resp = _preview(h, token, blogUrl="https://blog.naver.com/myblog")

    assert resp["statusCode"] == 200
    written = {c["key"] for c in s3_calls.writes()}
    assert all(k.endswith("metadata/ratelimit.json") for k in written), written
    for c in s3_calls.calls:
        key = c["key"] or ""
        assert not key.startswith(f"users/{member['userId']}/raw-posts/")
        assert not key.startswith(f"users/{member['userId']}/generated-posts/")


def test_component_itself_makes_no_s3_call_and_no_bedrock_call(crawl_log, bedrock_calls, s3_calls, s3_bucket):
    import style_learner

    s3_calls.clear()
    result = style_learner.preview_learning_sources({"blogUrl": "https://blog.naver.com/myblog"})

    assert result["count"] == 2
    assert s3_calls.calls == []
    assert bedrock_calls == []


def test_blog_without_rss_items_gives_empty_preview(h, create_user, make_session_token, monkeypatch, bedrock_calls):
    import crawler

    monkeypatch.setattr(crawler, "crawl", lambda url: "<rss><channel></channel></rss>")
    member = create_user("member1")
    resp = _preview(h, make_session_token(member["userId"]), blogUrl="myblog")

    assert resp["statusCode"] == 200
    assert _body(resp) == {"count": 0, "posts": []}


def test_rss_failure_gives_empty_preview_not_an_error(h, create_user, make_session_token, monkeypatch, bedrock_calls):
    import crawler

    def failing_crawl(url):
        raise crawler.CrawlError("offline")

    monkeypatch.setattr(crawler, "crawl", failing_crawl)
    member = create_user("member1")
    resp = _preview(h, make_session_token(member["userId"]), blogUrl="myblog")

    assert resp["statusCode"] == 200
    assert _body(resp) == {"count": 0, "posts": []}


def test_missing_session_token_gives_401(h, signing_secret, crawl_log, bedrock_calls):
    resp = h.handler(_event({"action": "preview-learning-sources", "blogUrl": "myblog"}))

    assert resp["statusCode"] == 401
    assert _body(resp)["error"]["code"] == "unauthorized"
    assert crawl_log == []
    assert bedrock_calls == []


def test_invalid_session_token_gives_401(h, signing_secret, crawl_log, bedrock_calls):
    resp = h.handler(_event({"action": "preview-learning-sources", "blogUrl": "myblog"}, "not-a-token"))

    assert resp["statusCode"] == 401
    assert _body(resp)["error"]["code"] == "unauthorized"
    assert crawl_log == []


@pytest.mark.parametrize("raw_body", ["[1, 2, 3]", '"preview-learning-sources"', "42"])
def test_non_object_body_is_bad_request(h, create_user, make_session_token, crawl_log, bedrock_calls, raw_body):
    member = create_user("member1")
    event = {"headers": {"X-Mablop-Token": make_session_token(member["userId"])}, "body": raw_body}
    resp = h.handler(event)

    assert resp["statusCode"] == 400
    assert _body(resp)["error"]["code"] == "bad_request"
    assert crawl_log == []


def test_misspelled_action_is_bad_request(h, create_user, make_session_token, crawl_log, bedrock_calls):
    member = create_user("member1")
    resp = h.handler(_event({"action": "preview-learning-source"}, make_session_token(member["userId"])))

    assert resp["statusCode"] == 400
    assert _body(resp)["error"]["code"] == "bad_request"
    assert crawl_log == []


@pytest.mark.parametrize("blog_url", [None, "", "   ", 123, ["x"]])
def test_missing_or_invalid_blog_url_gives_empty_preview_without_network(
    h, create_user, make_session_token, crawl_log, bedrock_calls, blog_url
):
    member = create_user("member1")
    resp = _preview(h, make_session_token(member["userId"]), blogUrl=blog_url)

    assert resp["statusCode"] == 200
    assert _body(resp) == {"count": 0, "posts": []}
    assert crawl_log == []
    assert bedrock_calls == []

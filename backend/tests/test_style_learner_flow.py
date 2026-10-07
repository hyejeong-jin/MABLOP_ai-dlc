"""End-to-end learn-style flow (Req 1.1-1.10; Design Style_Learner, learn-style contract).

Integration-style: moto S3 backs storage; bedrock.embed is monkeypatched to a
small fixed vector; style_profile.generate_profile is stubbed (no live Bedrock);
crawler.crawl/extract_body are monkeypatched with HTML fixtures. Drives the full
resolve->crawl/paste->chunk->embed->index/profile->checkpoint pipeline.
"""
import importlib

import boto3
import numpy as np
import pytest
from moto import mock_aws

BUCKET = "mablop-test"


@pytest.fixture
def sl(monkeypatch):
    """Fresh moto S3 + bucket + env; reload storage + pipeline modules; yield style_learner."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")
    monkeypatch.setenv("MABLOP_STYLE_MODEL", "test-style-model")
    monkeypatch.setenv("MABLOP_MAX_POST_COUNT", "20")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import s3store
        import retrieval
        import index_store
        import checkpoint
        import crawler
        import chunking
        import bedrock
        import style_profile
        import style_learner

        for m in (s3store, retrieval, index_store, checkpoint, crawler,
                  chunking, bedrock, style_profile, style_learner):
            importlib.reload(m)
        yield style_learner


def _url(log_no):
    """A resolvable desktop Naver post URL whose logNo becomes the postId."""
    return f"https://blog.naver.com/myblog/{log_no}"


def _patch(sl, monkeypatch, *, crawl_bodies=None, fail_urls=()):
    """Patch bedrock.embed (fixed vector), style_profile.generate_profile, crawler.

    crawl_bodies maps resolved mobile URL -> body text; fail_urls raise CrawlError.
    crawler.extract_body is identity (crawl already returns body text here).
    """
    crawl_bodies = crawl_bodies or {}
    captured = {"profile_bodies": None, "embed_calls": 0}

    def fake_embed(text):
        captured["embed_calls"] += 1
        return [0.1, 0.2, 0.3]

    def fake_generate_profile(bodies):
        captured["profile_bodies"] = list(bodies)
        return {"tone": "친근한 구어체", "builtAt": "x", "sourcePostCount": len(bodies)}

    def fake_crawl(url):
        if url in fail_urls:
            raise sl.crawler.CrawlError(f"boom {url}")
        return crawl_bodies.get(url, "")

    monkeypatch.setattr(sl.bedrock, "embed", fake_embed)
    monkeypatch.setattr(sl.style_profile, "generate_profile", fake_generate_profile)
    monkeypatch.setattr(sl.crawler, "crawl", fake_crawl)
    monkeypatch.setattr(sl.crawler, "extract_body", lambda html: html)
    return captured


def _s3():
    return boto3.client("s3", region_name="us-east-1")


def _exists(key):
    try:
        _s3().get_object(Bucket=BUCKET, Key=key)
        return True
    except Exception:
        return False


# --- happy path: 2 crawlable posts -> completed, index + profile + raw stored ---

def test_happy_path_two_crawlable_posts(sl, monkeypatch):
    body_a = "A " * 500  # > chunk_size so it chunks into multiple pieces
    body_b = "B 본문입니다. "
    captured = _patch(sl, monkeypatch, crawl_bodies={
        sl.resolve_url(_url("111")): body_a,
        sl.resolve_url(_url("222")): body_b,
    })

    out = sl.learn_style({
        "blogUrl": "https://blog.naver.com/myblog",
        "posts": [_url("111"), _url("222")],
    })

    assert out["status"] == "completed"
    assert out["processedPosts"] == 2
    assert out["chunks"] >= 2
    assert out["styleProfileKey"] == "style-profile/profile.json"
    assert out["indexKey"] == "embeddings/index.npy"
    assert out["checkpoint"] is None
    assert out["failedPosts"] == []

    # raw bodies stored under raw-posts/<postId>.txt (postId == logNo)
    assert _exists("raw-posts/111.txt")
    assert _exists("raw-posts/222.txt")
    # profile + index artifacts persisted
    assert _exists("style-profile/profile.json")
    assert _exists("embeddings/index.npy")
    assert _exists("embeddings/index-map.json")

    # profile built from both bodies; checkpoint cleared
    assert len(captured["profile_bodies"]) == 2
    import checkpoint
    assert checkpoint.load(out["runId"]) is None


# --- crawl failure falls back to pastedTexts ---

def test_crawl_failure_falls_back_to_pasted(sl, monkeypatch):
    good = sl.resolve_url(_url("111"))
    bad = sl.resolve_url(_url("222"))
    _patch(sl, monkeypatch,
           crawl_bodies={good: "crawled body"},
           fail_urls={bad})

    out = sl.learn_style({
        "posts": [_url("111"), _url("222")],
        "pastedTexts": [{"postId": "222", "text": "pasted fallback 본문"}],
    })

    assert out["status"] == "completed"
    assert out["processedPosts"] == 2
    assert out["failedPosts"] == []
    # the failed-crawl post used its pasted body
    stored = _s3().get_object(Bucket=BUCKET, Key="raw-posts/222.txt")["Body"].read()
    assert stored.decode("utf-8") == "pasted fallback 본문"


def test_crawl_failure_with_no_paste_is_skipped(sl, monkeypatch):
    good = sl.resolve_url(_url("111"))
    bad = sl.resolve_url(_url("222"))
    _patch(sl, monkeypatch, crawl_bodies={good: "crawled body"}, fail_urls={bad})

    out = sl.learn_style({"posts": [_url("111"), _url("222")]})

    assert out["status"] == "completed"
    assert out["processedPosts"] == 1
    assert out["failedPosts"] == ["222"]
    assert not _exists("raw-posts/222.txt")


# --- MAX_POST_COUNT cap respected ---

def test_max_post_count_cap(sl, monkeypatch):
    monkeypatch.setenv("MABLOP_MAX_POST_COUNT", "2")
    bodies = {sl.resolve_url(_url(str(i))): f"body {i}" for i in range(5)}
    _patch(sl, monkeypatch, crawl_bodies=bodies)

    out = sl.learn_style({"posts": [_url(str(i)) for i in range(5)]})

    assert out["status"] == "completed"
    assert out["processedPosts"] == 2  # capped


# --- resume: pause after first post, then complete without reprocessing ---

def test_resume_pauses_then_completes(sl, monkeypatch):
    b0 = sl.resolve_url(_url("111"))
    b1 = sl.resolve_url(_url("222"))
    captured = _patch(sl, monkeypatch, crawl_bodies={b0: "body0 본문", b1: "body1 본문"})

    # Force should_pause True so the run pauses right after the first post.
    monkeypatch.setattr(sl.checkpoint, "should_pause", lambda elapsed: True)

    payload = {"posts": [_url("111"), _url("222")]}
    first = sl.learn_style(payload)

    assert first["status"] == "in-progress"
    assert first["processedPosts"] == 1
    assert first["checkpoint"] == "metadata/learn-checkpoint-{}.json".format(first["runId"])
    assert first["styleProfileKey"] is None and first["indexKey"] is None
    embed_after_first = captured["embed_calls"]

    # Resume: let it finish this time.
    monkeypatch.setattr(sl.checkpoint, "should_pause", lambda elapsed: False)
    second = sl.learn_style({**payload, "resumeRunId": first["runId"]})

    assert second["status"] == "completed"
    assert second["runId"] == first["runId"]
    assert second["processedPosts"] == 2
    # post 111 was NOT re-embedded on resume (only post 222's chunks added)
    assert captured["embed_calls"] > embed_after_first
    # profile reflects both posts' raw bodies (loaded from S3, not just this call)
    assert len(captured["profile_bodies"]) == 2

    import index_store
    matrix, row_map = index_store.load_index()
    assert matrix.shape[0] == second["chunks"]
    # no duplicate processing of post 111
    post_ids = {v["postId"] for v in row_map.values()}
    assert post_ids == {"111", "222"}


# --- re-learn with a fresh run overwrites the index at fixed keys (1.10) ---

def test_relearn_fresh_run_overwrites_index(sl, monkeypatch):
    # Run 1: a single post.
    url1 = sl.resolve_url(_url("111"))
    _patch(sl, monkeypatch, crawl_bodies={url1: "first run 본문 하나"})
    first = sl.learn_style({"posts": [_url("111")]})
    assert first["status"] == "completed"

    import index_store
    m1, map1 = index_store.load_index()
    assert {v["postId"] for v in map1.values()} == {"111"}

    # Run 2: a different, fresh run with a different post -> overwrite, not append.
    url2 = sl.resolve_url(_url("999"))
    _patch(sl, monkeypatch, crawl_bodies={url2: "second run 본문 완전히 다름"})
    second = sl.learn_style({"posts": [_url("999")]})
    assert second["status"] == "completed"
    assert second["runId"] != first["runId"]

    m2, map2 = index_store.load_index()
    # index now reflects ONLY run 2 (fixed keys overwritten, no accumulation)
    assert {v["postId"] for v in map2.values()} == {"999"}
    assert m2.shape[0] == second["chunks"]

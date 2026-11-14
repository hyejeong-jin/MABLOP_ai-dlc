"""get-learning-post / rename-learning-post and the writing-activity feed of get-usage."""
import json

import pytest

import draft_history
import handler
import s3store
import style_learner
import usage_meter
from conftest import ACTING_USER_ID, OTHER_USER_ID
from errors import MablopError

NOW = 1_792_000_000  # 2026-10-14 (KST)


def _seed_post(post_id="post-1", text="본문입니다."):
    s3store.put_bytes(s3store.user_key(f"raw-posts/{post_id}.txt"), text.encode("utf-8"))


def test_get_learning_post_returns_the_stored_text(s3_bucket):
    _seed_post("post-1", "첫 글 본문")
    assert style_learner.get_learning_post({"postId": "post-1"}) == {"postId": "post-1", "text": "첫 글 본문"}


@pytest.mark.parametrize("bad", ["", "../x", "a/b", "a\\b", None, 5])
def test_get_learning_post_rejects_unsafe_ids(s3_bucket, bad):
    with pytest.raises(MablopError) as err:
        style_learner.get_learning_post({"postId": bad})
    assert err.value.code == "bad_request"


def test_get_learning_post_is_per_user_and_missing_is_404(s3_bucket):
    s3store.put_bytes(s3store.user_key("raw-posts/secret.txt", OTHER_USER_ID), b"other")
    with pytest.raises(MablopError) as err:
        style_learner.get_learning_post({"postId": "secret"})
    assert err.value.code == "not_found" and err.value.status == 404


def test_rename_moves_text_status_and_index_labels(s3_bucket):
    _seed_post("old-title", "본문")
    s3store.put_json(s3store.user_key("metadata/learning-status.json"), {"processedPosts": 1, "chunks": 1, "posts": [{"postId": "old-title", "chunkCount": 1}]})
    s3store.put_json(s3store.user_key("embeddings/index-map.json"), {"0": {"chunkId": "old-title-c0", "postId": "old-title", "offset": 0, "text": "본문"}})

    assert style_learner.rename_learning_post({"postId": "old-title", "newPostId": "새 제목"}) == {"postId": "새 제목", "renamed": True}

    assert not s3store.key_exists(s3store.user_key("raw-posts/old-title.txt"))
    assert s3store.get_bytes(s3store.user_key("raw-posts/새 제목.txt")).decode() == "본문"
    status = s3store.get_json(s3store.user_key("metadata/learning-status.json"))
    assert [p["postId"] for p in status["posts"]] == ["새 제목"]
    row = s3store.get_json(s3store.user_key("embeddings/index-map.json"))["0"]
    assert row["postId"] == "새 제목" and row["chunkId"] == "새 제목-c0"


def test_rename_refuses_to_overwrite_another_post_and_unsafe_names(s3_bucket):
    _seed_post("a", "A")
    _seed_post("b", "B")
    with pytest.raises(MablopError) as err:
        style_learner.rename_learning_post({"postId": "a", "newPostId": "b"})
    assert err.value.code == "bad_request"
    assert s3store.get_bytes(s3store.user_key("raw-posts/b.txt")) == b"B"
    for bad in ("", "x/y", "..", "x\\y"):
        with pytest.raises(MablopError):
            style_learner.rename_learning_post({"postId": "a", "newPostId": bad})
    with pytest.raises(MablopError) as missing:
        style_learner.rename_learning_post({"postId": "nope", "newPostId": "fine"})
    assert missing.value.code == "not_found"


def test_actions_need_login_but_no_grant(s3_bucket, signing_secret, create_user, make_session_token):
    member = create_user("member1")
    s3store.put_bytes(s3store.user_key("raw-posts/p1.txt", member["userId"]), "내용".encode())
    token = make_session_token(member["userId"])

    def call(body, tok=token):
        resp = handler.handler({"headers": {"X-Mablop-Token": tok} if tok else {}, "body": json.dumps(body)})
        return resp["statusCode"], json.loads(resp["body"])

    assert call({"action": "get-learning-post", "postId": "p1"}) == (200, {"postId": "p1", "text": "내용"})
    assert call({"action": "rename-learning-post", "postId": "p1", "newPostId": "p2"})[0] == 200
    assert call({"action": "get-learning-post", "postId": "p2"})[1]["text"] == "내용"
    assert call({"action": "get-learning-post", "postId": "p2"}, tok=None)[0] == 401


def test_activity_counts_drafts_and_learned_posts_per_kst_day(s3_bucket):
    # 2026-10-14 03:00 KST == 2026-10-13T18:00Z: the UTC date differs from the KST date.
    s3store.put_json(
        s3store.user_key("metadata/drafts.json"),
        {"v": 1, "drafts": [
            {"draftId": "d1", "title": "t", "createdAt": "2026-10-13T18:00:00.000Z"},
            {"draftId": "d2", "title": "t", "createdAt": "2026-10-14T05:00:00.000Z"},
        ]},
    )
    _seed_post("p1")
    out = usage_meter.get_usage({}, None, now=NOW)
    assert out["activity"]["2026-10-14"]["drafts"] == 2  # both drafts; the post is dated by its real upload time
    assert "2026-10-13" not in out["activity"]


def test_total_tokens_span_the_last_six_months(s3_bucket):
    s3store.put_json(s3store.user_key("metadata/usage-2026-08.json"), {"models": {"m": {"calls": 1, "inputTokens": 100, "outputTokens": 20}}})
    s3store.put_json(s3store.user_key("metadata/usage-2026-10.json"), {"models": {"m": {"calls": 1, "inputTokens": 5, "outputTokens": 5}}})
    out = usage_meter.get_usage({}, None, now=NOW)
    assert out["totalTokens"] == 130
    assert [m["calls"] for m in out["models"]] == [1]  # the table lists the current month only
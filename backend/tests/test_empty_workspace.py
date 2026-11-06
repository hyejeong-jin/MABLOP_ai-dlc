"""New member's empty workspace (Req 8.5, 8.8, 17.9).

A freshly approved member has no learning data. Through the handler with a real
Session_Token (moto S3, Bedrock stubbed and counted):

- ``get-learning-status`` returns 0 / 0 / an empty list, even when another user has data.
- ``generate-draft`` before any learning fails with the Korean ``bad_request`` and calls no Bedrock.
- ``get-result`` (kind ``draft``) for another user's draft id fails and never returns that content.
"""
import importlib
import json
from types import SimpleNamespace

import boto3
import pytest

from conftest import ACTING_USER_ID, OTHER_USER_ID

BUCKET = "mablop-test"
NO_LEARNED_MSG = "학습된 글이 없습니다. 먼저 스타일 학습을 진행하세요."
OTHER_DRAFT_ID = "draft-other-0001"
OTHER_DRAFT_TEXT = "다른 사용자의 비공개 초안"


@pytest.fixture
def h(monkeypatch, s3_bucket, signing_secret):
    """Fresh storage/engine/access/handler modules on top of moto S3."""
    monkeypatch.setenv("MABLOP_TEXT_MODEL", "test-quality-korean")
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")
    import s3store
    import retrieval
    import index_store
    import drafting_engine
    import style_learner
    import access_controller
    import handler

    for module in (s3store, retrieval, index_store, drafting_engine, style_learner, access_controller, handler):
        importlib.reload(module)
    return handler


@pytest.fixture
def bedrock_calls(h, monkeypatch):
    """Count Bedrock calls; any call returns a harmless value."""
    import drafting_engine

    calls = {"embed": 0, "invoke": 0}

    def fake_embed(text):
        calls["embed"] += 1
        return [1.0, 0.0, 0.0]

    def fake_invoke(model_id, body):
        calls["invoke"] += 1
        return {"outputText": "# 제목\n\n본문"}

    monkeypatch.setattr(drafting_engine.bedrock, "embed", fake_embed)
    monkeypatch.setattr(drafting_engine.bedrock, "invoke", fake_invoke)
    return calls


@pytest.fixture
def member(h, create_user, make_session_token):
    """A new member with an approved Grant and no learning data at all."""
    import grant_service

    user_id = create_user("newmember1")["userId"]
    grant_id = grant_service.request({}, SimpleNamespace(user_id=user_id))["grant"]["id"]
    grant_service.admin_approve(
        {"userId": user_id, "grantId": grant_id, "expiresInDays": 30},
        SimpleNamespace(user_id=ACTING_USER_ID),
    )
    return SimpleNamespace(user_id=user_id, grant_id=grant_id, token=make_session_token(user_id))


def _call(h, body, token):
    resp = h.handler({"headers": {"X-Mablop-Token": token}, "body": json.dumps(body)})
    return resp["statusCode"], json.loads(resp["body"])


def _put_other_users_data():
    """Another user's learning status and draft, stored under that user's own prefix."""
    s3 = boto3.client("s3")
    status = {"runId": "run-x", "processedPosts": 9, "chunks": 31, "posts": [{"postId": "p1", "chunkCount": 3}]}
    s3.put_object(
        Bucket=BUCKET,
        Key=f"users/{OTHER_USER_ID}/metadata/learning-status.json",
        Body=json.dumps(status).encode("utf-8"),
    )
    s3.put_object(
        Bucket=BUCKET,
        Key=f"users/{OTHER_USER_ID}/generated-posts/{OTHER_DRAFT_ID}.md",
        Body=OTHER_DRAFT_TEXT.encode("utf-8"),
    )


def test_new_member_learning_status_is_empty(h, member):
    status, body = _call(h, {"action": "get-learning-status"}, member.token)

    assert status == 200
    assert body == {"processedPosts": 0, "chunks": 0, "posts": []}


def test_new_member_learning_status_ignores_other_users_data(h, member):
    _put_other_users_data()

    status, body = _call(h, {"action": "get-learning-status"}, member.token)

    assert status == 200
    assert body == {"processedPosts": 0, "chunks": 0, "posts": []}


def test_generate_draft_before_learning_is_korean_bad_request(h, member, bedrock_calls):
    _put_other_users_data()

    status, body = _call(
        h,
        {"action": "generate-draft", "grantId": member.grant_id, "title": "제목", "imageKeys": []},
        member.token,
    )

    assert status == 400
    assert body["error"] == {"code": "bad_request", "message": NO_LEARNED_MSG}
    assert bedrock_calls["invoke"] == 0


def test_get_result_of_other_users_draft_id_fails_without_content(h, member):
    _put_other_users_data()

    status, body = _call(h, {"action": "get-result", "kind": "draft", "id": OTHER_DRAFT_ID}, member.token)

    assert status == 404
    assert body["error"] == {"code": "not_found", "message": "대상 초안을 찾을 수 없습니다."}
    assert "markdown" not in body
    assert OTHER_DRAFT_TEXT not in json.dumps(body, ensure_ascii=False)

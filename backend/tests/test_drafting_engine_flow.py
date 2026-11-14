"""End-to-end generate-draft + get-result flow (Req 2.1-2.9, 3.6-3.7, 4.1-4.2, 8.4, 8.5, 8.7, 22.1-22.4).

Integration-style: moto S3 backs storage; bedrock.embed + bedrock.invoke are
monkeypatched (no live Bedrock). Seeds a real index (index_store), a style
profile, and image caption JSON under the acting user's prefix, then drives
generate_draft/get_result and the handler end-to-end with session tokens.
"""
import importlib
import json
import re
from types import SimpleNamespace

import boto3
import numpy as np
import pytest

import user_scope
from conftest import ACTING_USER_ID, OTHER_USER_ID
from errors import MablopError

BUCKET = "mablop-test"
TEXT_MODEL = "test-quality-korean"
LITE_MODEL = "test-lite-korean"
_PH = re.compile(r"!\[img-(\d+)\]")
U = f"users/{ACTING_USER_ID}/"  # acting user's prefix
IMG = U + "images/"
NO_LEARNED_MSG = "학습된 글이 없습니다. 먼저 스타일 학습을 진행하세요."
BAD_IMAGE_MSG = "허용되지 않는 이미지 경로입니다."


@pytest.fixture
def env(monkeypatch, s3_bucket, signing_secret):
    """moto S3 + bucket + signing secret + env; reload storage/engine modules; yield drafting_engine."""
    monkeypatch.setenv("MABLOP_TEXT_MODEL", TEXT_MODEL)
    monkeypatch.setenv("MABLOP_LITE_MODEL", LITE_MODEL)
    monkeypatch.setenv("MABLOP_LITE_MONTHLY_LIMIT", "5")
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")
    monkeypatch.setenv("MABLOP_TOP_K", "2")
    import s3store
    import retrieval
    import index_store
    import drafting_engine

    importlib.reload(s3store)
    importlib.reload(retrieval)
    importlib.reload(index_store)
    importlib.reload(drafting_engine)
    return drafting_engine


def _seed_index():
    """Seed a 3-row float32 index with distinct rows + chunk texts for the bound user."""
    import index_store

    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    meta = [
        {"chunkId": "c0", "postId": "p0", "offset": 0, "text": "스타일 예시 하나"},
        {"chunkId": "c1", "postId": "p0", "offset": 50, "text": "스타일 예시 둘"},
        {"chunkId": "c2", "postId": "p1", "offset": 0, "text": "스타일 예시 셋"},
    ]
    index_store.build_and_store_index(vectors, meta)


def _seed_profile():
    import s3store

    s3store.put_json(s3store.user_key("style-profile/profile.json"), {"tone": "친근한 구어체"})


def _seed_caption(image_key, caption):
    import s3store

    s3store.put_json(image_key.rsplit(".", 1)[0] + ".json",
                     {"key": image_key, "caption": caption, "source": "user"})


def _seed_workspace(user_id):
    """Index + profile for another userId than the autouse-bound one."""
    with user_scope.bind(user_id):
        _seed_index()
        _seed_profile()


def _counts(markdown):
    out = {}
    for m in _PH.finditer(markdown):
        out[int(m.group(1))] = out.get(int(m.group(1)), 0) + 1
    return out


def _patch_bedrock(de, monkeypatch, markdown):
    """Patch embed -> query vector aligned to row 0; invoke -> canned markdown. Capture the prompt."""
    captured = {"invoked": 0}

    def fake_embed(text):
        captured["embed_input"] = text
        return [1.0, 0.0, 0.0]  # closest to seeded row 0

    def fake_invoke(model_id, body):
        captured["invoked"] += 1
        captured["model_id"] = model_id
        messages = body.get("messages", [])
        content = messages[0].get("content", []) if messages else []
        captured["prompt"] = content[0].get("text") if content else body.get("prompt")
        return {"outputText": markdown}

    monkeypatch.setattr(de.bedrock, "embed", fake_embed)
    monkeypatch.setattr(de.bedrock, "invoke", fake_invoke)
    return captured


def _index_draft_ids(user_id):
    """draftIds in users/{user_id}/metadata/drafts.json; [] when the index does not exist."""
    import s3store

    found = s3store.read_json_optional(f"users/{user_id}/metadata/drafts.json")
    return [] if found is None else [e["draftId"] for e in found[0]["drafts"]]


def test_generate_draft_full_flow_stores_and_returns(env, monkeypatch):
    de = env
    _seed_index()
    _seed_profile()
    _seed_caption(IMG + "img1.jpg", "카페 창가의 라떼")
    _seed_caption(IMG + "img2.jpg", "성산일출봉 전경")

    captured = _patch_bedrock(de, monkeypatch, "# 제목\n\n본문 ![img-1] 이어서 ![img-2]")

    out = de.generate_draft({
        "title": "제주 카페 투어",
        "outline": "1. 성산 2. 애월",
        "notes": "라떼가 인상적",
        "imageKeys": [IMG + "img1.jpg", IMG + "img2.jpg"],
    })

    # quality model invoked; input text assembled from title/outline/notes
    assert captured["model_id"] == TEXT_MODEL
    assert "제주 카페 투어" in captured["embed_input"]
    assert "라떼가 인상적" in captured["embed_input"]
    # retrieved chunk texts + captions + profile flow into the prompt
    assert "스타일 예시 하나" in captured["prompt"]
    assert "카페 창가의 라떼" in captured["prompt"]
    assert "친근한 구어체" in captured["prompt"]

    # placeholders well-formed: each supplied index exactly once (3.6, 3.7, 4.1)
    assert _counts(out["markdown"]) == {1: 1, 2: 1}
    # topK reflects retrieved chunk count (capped at MABLOP_TOP_K=2)
    assert out["topK"] == 2
    # draftKey is the full object key under the acting user's prefix (8.4)
    assert out["draftKey"] == f"users/{ACTING_USER_ID}/generated-posts/{out['draftId']}.md"

    # stored under the user's generated-posts/ matching the returned markdown (2.8, 4.2)
    stored = boto3.client("s3").get_object(Bucket=BUCKET, Key=out["draftKey"])["Body"].read()
    assert stored.decode("utf-8") == out["markdown"]
    # no object at a legacy (unprefixed) key
    legacy = boto3.client("s3").list_objects_v2(Bucket=BUCKET, Prefix="generated-posts/")
    assert legacy.get("KeyCount", 0) == 0

    # the new draftId is recorded in the user's Draft_Index with the request title (22.1)
    assert out["draftId"] in _index_draft_ids(ACTING_USER_ID)
    import s3store

    entries = s3store.get_json(f"{U}metadata/drafts.json")["drafts"]
    assert [e["title"] for e in entries if e["draftId"] == out["draftId"]] == ["제주 카페 투어"]


def test_get_result_returns_stored_draft(env, monkeypatch):
    de = env
    _seed_index()
    _patch_bedrock(de, monkeypatch, "# 제목\n\n본문")

    out = de.generate_draft({"title": "제목", "outline": "", "notes": "", "imageKeys": []})
    got = de.get_result({"kind": "draft", "id": out["draftId"]})

    assert got["status"] == "ready"
    assert got["markdown"] == out["markdown"]


def test_get_result_of_another_users_draft_behaves_like_missing(env):
    de = env
    boto3.client("s3").put_object(
        Bucket=BUCKET, Key=f"users/{OTHER_USER_ID}/generated-posts/draft-abc.md", Body="남의 초안".encode("utf-8")
    )
    boto3.client("s3").put_object(Bucket=BUCKET, Key="generated-posts/draft-legacy.md", Body=b"legacy")

    for draft_id in ("draft-abc", "draft-legacy"):
        with pytest.raises(MablopError) as exc:
            de.get_result({"kind": "draft", "id": draft_id})
        assert exc.value.code == "not_found"
        assert exc.value.status == 404
        assert exc.value.message == "대상 초안을 찾을 수 없습니다."


def test_generate_draft_tolerates_missing_profile_and_captions(env, monkeypatch):
    de = env
    _seed_index()  # no profile, no caption json
    _patch_bedrock(de, monkeypatch, "# 제목\n\n본문 ![img-1]")

    out = de.generate_draft({"title": "제목", "imageKeys": [IMG + "x.jpg"]})
    assert _counts(out["markdown"]) == {1: 1}
    # No valid Style_Profile => general-style fallback, no style chunks (Req 8.1).
    assert out["topK"] == 0
    assert out["generalStyle"] is True


def test_get_result_without_id_errors(env):
    de = env
    with pytest.raises(ValueError):
        de.get_result({"kind": "draft"})


def test_generate_draft_without_learned_index_uses_general_style(env, monkeypatch):
    de = env
    captured = _patch_bedrock(de, monkeypatch, "# 제목\n\n본문")

    out = de.generate_draft({"title": "제목", "imageKeys": []})

    assert out["generalStyle"] is True
    assert out["topK"] == 0
    assert "본문" in out["markdown"]
    assert captured["invoked"] == 1
    assert _index_draft_ids(ACTING_USER_ID) == [out["draftId"]]


@pytest.mark.parametrize(
    "key",
    ["images/img1.jpg", f"users/{OTHER_USER_ID}/images/img1.jpg", IMG + "../x.jpg", IMG],
)
def test_generate_draft_rejects_image_keys_outside_own_prefix(env, monkeypatch, key):
    from errors import MablopError

    de = env
    _seed_index()
    captured = _patch_bedrock(de, monkeypatch, "# 제목\n\n본문")

    with pytest.raises(MablopError) as exc:
        de.generate_draft({"title": "제목", "imageKeys": [IMG + "ok.jpg", key]})
    assert exc.value.code == "bad_request"
    assert str(exc.value.message) == BAD_IMAGE_MSG
    assert captured["invoked"] == 0  # nothing generated or read before the check
    assert _index_draft_ids(ACTING_USER_ID) == []


# --- handler path with session tokens ----------------------------------------------------


@pytest.fixture
def h(env):
    """Fresh access_controller / handler modules on top of the reloaded engine."""
    import access_controller

    importlib.reload(access_controller)
    import handler

    importlib.reload(handler)
    return handler


def _event(body, token):
    return {"headers": {"X-Mablop-Token": token}, "body": json.dumps(body)}


def test_handler_generate_draft_end_to_end(h, env, monkeypatch, create_user, make_session_token):
    """Admin session token: 200 + markdown; draft and Draft_Index land under the admin's prefix."""
    admin = create_user("admin1", role="admin")["userId"]
    _seed_workspace(admin)
    _patch_bedrock(env, monkeypatch, "# 제목\n\n본문 ![img-1]")

    resp = h.handler(_event({
        "action": "generate-draft",
        "title": "제주 카페 투어",
        "outline": "성산",
        "notes": "라떼",
        "imageKeys": [f"users/{admin}/images/img1.jpg"],
    }, make_session_token(admin)))

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "![img-1]" in body["markdown"]
    assert body["draftId"].startswith("draft-")
    assert body["topK"] == 2
    assert body["draftKey"] == f"users/{admin}/generated-posts/{body['draftId']}.md"
    assert body["draftId"] in _index_draft_ids(admin)
    assert _index_draft_ids(ACTING_USER_ID) == []


@pytest.fixture
def member(h, create_user, make_session_token):
    """A member with an approved Grant, an indexed workspace, and a session token."""
    import grant_service

    user_id = create_user("member1")["userId"]
    grant_id = grant_service.request({}, SimpleNamespace(user_id=user_id))["grant"]["id"]
    grant_service.admin_approve(
        {"userId": user_id, "grantId": grant_id, "expiresInDays": 30},
        SimpleNamespace(user_id=ACTING_USER_ID),
    )
    _seed_workspace(user_id)
    return SimpleNamespace(user_id=user_id, grant_id=grant_id, token=make_session_token(user_id))


def test_handler_member_without_grant_id_runs_as_lite_tier(h, env, monkeypatch, member):
    """Req 15.1: a member who names no grant is not rejected; the draft uses the lite model and quota."""
    captured = _patch_bedrock(env, monkeypatch, "# 제목\n\n본문")

    resp = h.handler(_event({"action": "generate-draft", "title": "제목", "imageKeys": []}, member.token))

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["tier"] == "lite"
    assert body["liteRemaining"] == 4  # monthly limit 5 in this test, one draft just used
    assert captured["model_id"] == LITE_MODEL
    assert _index_draft_ids(member.user_id) == [body["draftId"]]


def test_handler_member_with_grant_id_records_draft_in_own_index(h, env, monkeypatch, member):
    _patch_bedrock(env, monkeypatch, "# 본문 제목\n\n본문")

    resp = h.handler(_event(
        {"action": "generate-draft", "grantId": member.grant_id, "imageKeys": []}, member.token
    ))

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["tier"] == "quality"
    assert "liteRemaining" not in body
    assert body["draftKey"] == f"users/{member.user_id}/generated-posts/{body['draftId']}.md"
    assert _index_draft_ids(member.user_id) == [body["draftId"]]
    assert _index_draft_ids(ACTING_USER_ID) == []

    # get-result for kind=draft needs no Grant and reads the member's own object only
    got = h.handler(_event({"action": "get-result", "kind": "draft", "id": body["draftId"]}, member.token))
    assert got["statusCode"] == 200
    assert json.loads(got["body"])["markdown"] == body["markdown"]

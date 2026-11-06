"""Property test: user data isolation and namespace (Req 8.3-8.6, 8.8-8.10, 9.4, 22.12, 22.13; Property 15).

Two members, A and B, share one moto bucket. B's workspace is created through the handler (learned posts,
index, profile, image, draft) and legacy objects sit at unprefixed keys. Random sequences of A's actions then
run through the handler with A's Session_Token; the ``s3_calls`` recorder lists every S3 key each request
touches. Bedrock (embed, text generation, vision) is a deterministic stub tagged per user, so a leak of B's
chunks or profile into A's prompt is visible. Production code is not modified.

Feature: user-auth-admin, Property 15: User data isolation and namespace
"""
import hashlib
import json
from types import SimpleNamespace

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import bedrock
import grant_service
import handler
import s3store
import user_scope
from conftest import ACTING_USER_ID, TEST_BUCKET, TEST_REGION

RATELIMIT_KEY = "metadata/ratelimit.json"
STYLE_MODEL = "style-model-stub"
TEXT_MODEL = "text-model-stub"

BAD_IMAGE_MSG = "허용되지 않는 이미지 경로입니다."
NO_LEARNED_MSG = "학습된 글이 없습니다. 먼저 스타일 학습을 진행하세요."
NO_RAW_MSG = "저장된 raw-posts가 없습니다. 먼저 스타일 학습을 진행하세요."
DRAFT_NOT_FOUND_MSG = "대상 초안을 찾을 수 없습니다."
BAD_DRAFT_ID_MSG = "올바르지 않은 초안 번호입니다."

_SETTINGS = settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies --------------------------------------------------------------------------

_post_ids = st.sampled_from(["p1", "p2", "p3"])  # B learns p1 and p2 too: same ids, different owners
_word = st.text(alphabet="abcdef가나다", min_size=1, max_size=4)
_pasted = st.lists(
    st.tuples(_post_ids, _word, st.integers(min_value=1, max_value=150)),
    min_size=1,
    max_size=3,
    unique_by=lambda t: t[0],
)
_filenames = st.lists(
    st.sampled_from(["a.png", "../../x.png", "dir/b.jpg", "c[1].png", "한글.png", "noext"]),
    min_size=1,
    max_size=3,
)
_foreign = st.sampled_from([None, None, "legacy", "other_user", "traversal", "no_slash", "lookalike"])
_targets = st.sampled_from(["own", "own", "b", "unknown", "slash", "dots"])

_ops = st.one_of(
    st.tuples(st.just("learn"), _pasted),
    st.just(("rebuild",)),
    st.just(("status",)),
    st.tuples(st.just("presign"), _filenames),
    st.tuples(st.just("analyze"), st.booleans(), _foreign),
    st.tuples(
        st.just("generate"),
        st.integers(min_value=0, max_value=3),
        st.text(alphabet="abc가나 ", max_size=8),
        _foreign,
    ),
    st.tuples(st.just("get_result"), _targets, st.integers(min_value=0, max_value=9)),
    st.just(("list",)),
    st.tuples(st.just("delete"), _targets, st.integers(min_value=0, max_value=9)),
    st.tuples(st.just("grant_request"), st.sampled_from([None, "연구용", "x" * 100])),
    st.just(("grant_list",)),
)

# --- bedrock stub -------------------------------------------------------------------------

_world = {"a": None, "prompts": []}


def _tag() -> str:
    return "ALPHA" if user_scope.current_user_id() == _world["a"] else "BETA"


def _fake_embed(text):
    digest = hashlib.sha256(text.encode("utf-8")).digest()
    return [1.0 + digest[i] / 255.0 for i in range(4)]


def _fake_text_generate(prompt, model_id, max_tokens=None):
    tag = _tag()
    if model_id == STYLE_MODEL:
        return json.dumps({"tone": f"{tag}-profile"})
    _world["prompts"].append((user_scope.current_user_id(), prompt))
    return f"# {tag} 제목\n\n{tag} 본문"


def _fake_vision_caption(image_base64, media_type, model_id, max_tokens=None):
    return f"{_tag()} caption"


@pytest.fixture
def stubs(monkeypatch):
    monkeypatch.setattr(bedrock, "embed", _fake_embed)
    monkeypatch.setattr(bedrock, "text_generate", _fake_text_generate)
    monkeypatch.setattr(bedrock, "vision_caption", _fake_vision_caption)
    monkeypatch.setenv("MABLOP_STYLE_MODEL", STYLE_MODEL)
    monkeypatch.setenv("MABLOP_TEXT_MODEL", TEXT_MODEL)
    monkeypatch.setenv("MABLOP_TOP_K", "3")
    monkeypatch.setenv("MABLOP_MAX_RATE", "1000000")
    yield
    _world["a"] = None
    _world["prompts"] = []
    access_controller._window = None


# --- helpers -----------------------------------------------------------------------------


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _snapshot(exclude_prefix=None):
    """{key: bytes} of every object, skipping the ratelimit object and an optional prefix."""
    s3 = boto3.client("s3", region_name=TEST_REGION)
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if key == RATELIMIT_KEY or (exclude_prefix and key.startswith(exclude_prefix)):
                continue
            out[key] = s3.get_object(Bucket=TEST_BUCKET, Key=key)["Body"].read()
    return out


def _snapshot_prefix(prefix):
    return {k: v for k, v in _snapshot().items() if k.startswith(prefix)}


def _assert_within(calls, user_id):
    """Every key, copy source, and list prefix of the recorded calls lies in users/{user_id}/ (or is ratelimit)."""
    prefix = f"users/{user_id}/"
    for c in calls:
        assert not c["failed"]
        assert c["key"] or c["prefix"], c  # no bucket-wide call
        for k in (c["key"], c["source"]):
            if k:
                assert k.startswith(prefix) or k == RATELIMIT_KEY, (c["op"], k)
        if c["prefix"]:
            assert c["prefix"].startswith(prefix), (c["op"], c["prefix"])


class World:
    """Two members and the handler plumbing for one example."""

    def __init__(self, recorder, a, b):
        self.recorder = recorder
        self.a = a
        self.b = b
        self.a_posts = set()
        self.a_images = []
        self.a_drafts = {}  # draftId -> markdown
        self.b_draft_id = None
        self.b_draft_markdown = None
        self.b_image_key = None

    def call(self, who, body, grant=False):
        if grant:
            body = {**body, "grantId": who.grant_id}
        self.recorder.clear()
        resp = handler.handler({"headers": {"X-Mablop-Token": who.token}, "body": json.dumps(body)})
        calls = [dict(c) for c in self.recorder.calls]
        _assert_within(calls, who.user_id)
        return resp["statusCode"], json.loads(resp["body"]), resp["body"], calls


def _make_member(create_user, make_session_token, login_id):
    user_id = create_user(login_id)["userId"]
    grant_id = grant_service.request({}, SimpleNamespace(user_id=user_id))["grant"]["id"]
    grant_service.admin_approve(
        {"userId": user_id, "grantId": grant_id, "expiresInDays": 30},
        SimpleNamespace(user_id=ACTING_USER_ID),
    )
    return SimpleNamespace(user_id=user_id, grant_id=grant_id, token=make_session_token(user_id))


def _setup(recorder, create_user, make_session_token):
    """Fresh bucket with members A and B, B's workspace, and legacy objects."""
    _wipe_bucket()
    recorder.clear_failures()
    recorder.clear()
    access_controller._window = None
    _world["prompts"] = []

    a = _make_member(create_user, make_session_token, "alpha01")
    b = _make_member(create_user, make_session_token, "bravo01")
    _world["a"] = a.user_id
    w = World(recorder, a, b)

    for key, data in (
        ("raw-posts/legacy.txt", b"LEGACY post"),
        ("generated-posts/draft-legacy.md", b"# LEGACY"),
        ("embeddings/index.npy", b"LEGACY index"),
        ("images/legacy.png", b"LEGACY image"),
        ("metadata/learning-status.json", b'{"processedPosts": 9, "chunks": 31, "posts": []}'),
    ):
        s3store.put_bytes(key, data)

    # B's workspace, built through the handler like a real member would.
    pasted = [{"postId": pid, "text": f"BETA {pid} " * 120} for pid in ("p1", "p2")]
    status, *_ = w.call(b, {"action": "learn-style", "pastedTexts": pasted}, grant=True)
    assert status == 200
    status, body, _raw, _ = w.call(b, {"action": "issue-presigned-urls", "files": [{"filename": "b.png"}]})
    assert status == 200
    w.b_image_key = body["uploads"][0]["key"]
    status, *_ = w.call(
        b,
        {"action": "analyze-images", "images": [{"key": w.b_image_key, "description": "BETA desc"}]},
        grant=True,
    )
    assert status == 200
    status, body, _raw, _ = w.call(
        b,
        {"action": "generate-draft", "title": "BETA 제목", "imageKeys": [w.b_image_key]},
        grant=True,
    )
    assert status == 200
    w.b_draft_id, w.b_draft_markdown = body["draftId"], body["markdown"]
    _world["prompts"] = []
    return w


def _foreign_key(w, kind):
    a, b = w.a.user_id, w.b.user_id
    return {
        "legacy": "images/legacy.png",
        "other_user": w.b_image_key,
        "traversal": f"users/{a}/images/../../{b}/images/x.png",
        "no_slash": f"users/{a}/images",
        "lookalike": f"users/{a}0/images/a.png",
    }[kind]


def _draft_target(w, target, pick):
    """(draft id to send, expected category) for get_result / delete."""
    own = sorted(w.a_drafts)
    if target == "own" and own:
        return own[pick % len(own)], "own"
    if target == "b":
        return w.b_draft_id, "other"
    if target == "slash":
        return f"{w.b.user_id}/generated-posts/{w.b_draft_id}", "slash"
    if target == "dots":
        return f"../../{w.b.user_id}/generated-posts/{w.b_draft_id}", "dots"
    return "draft-does-not-exist", "other"


def _error(body):
    return body.get("error", {}) if isinstance(body, dict) else {}


# --- one step of A ----------------------------------------------------------------------


def _step(w, op):
    a = w.a
    kind = op[0]

    if kind == "learn":
        pasted = [{"postId": pid, "text": f"ALPHA {word} " * n} for pid, word, n in op[1]]
        status, body, _raw, _ = w.call(a, {"action": "learn-style", "pastedTexts": pasted}, grant=True)
        assert status == 200 and body["status"] == "completed"
        w.a_posts |= {pid for pid, _w, _n in op[1]}
        assert body["processedPosts"] == len(w.a_posts)
        assert body["styleProfileKey"].startswith(f"users/{a.user_id}/")
        assert body["indexKey"].startswith(f"users/{a.user_id}/")

    elif kind == "rebuild":
        status, body, _raw, _ = w.call(a, {"action": "rebuild-from-stored"}, grant=True)
        if w.a_posts:
            assert status == 200 and body["processedPosts"] == len(w.a_posts)
        else:  # B's raw-posts and the legacy raw post are invisible to A
            assert status == 400 and _error(body)["message"] == NO_RAW_MSG

    elif kind == "status":
        status, body, _raw, _ = w.call(a, {"action": "get-learning-status"})
        assert status == 200
        if not w.a_posts:
            assert body == {"processedPosts": 0, "chunks": 0, "posts": []}
        else:
            assert body["processedPosts"] == len(w.a_posts)
            assert {p["postId"] for p in body["posts"]} == w.a_posts
            assert body["chunks"] >= len(w.a_posts)

    elif kind == "presign":
        files = [{"filename": n, "contentType": "image/png"} for n in op[1]]
        status, body, _raw, _ = w.call(a, {"action": "issue-presigned-urls", "files": files})
        assert status == 200 and len(body["uploads"]) == len(files)
        for upload in body["uploads"]:
            assert upload["key"].startswith(f"users/{a.user_id}/images/")
            assert ".." not in upload["key"]
            w.a_images.append(upload["key"])

    elif kind == "analyze":
        _k, vision, foreign = op
        keys = w.a_images[:2]
        images = []
        for key in keys:
            if vision:
                s3store.put_bytes(key, b"\x89PNG stub")
                images.append({"key": key})
            else:
                images.append({"key": key, "description": "ALPHA desc"})
        if foreign:
            images.append({"key": _foreign_key(w, foreign), "description": "x"})
        status, body, _raw, calls = w.call(a, {"action": "analyze-images", "images": images}, grant=True)
        if foreign:
            assert status == 400 and _error(body)["message"] == BAD_IMAGE_MSG
            allowed = {f"users/{a.user_id}/account.json", f"users/{a.user_id}/metadata/grants.json", RATELIMIT_KEY}
            assert {c["key"] for c in calls if c["key"]} <= allowed  # no object read before the check
            assert all(c["op"] not in ("ListObjectsV2",) for c in calls)
            assert {c["key"] for c in w.recorder.writes()} <= {RATELIMIT_KEY}
        else:
            assert status == 200 and len(body["captions"]) == len(images)
            for cap in body["captions"]:
                assert cap["caption"].startswith("ALPHA")

    elif kind == "generate":
        _k, n_images, title, foreign = op
        keys = w.a_images[:n_images]
        if foreign:
            keys = keys + [_foreign_key(w, foreign)]
        before = len(_world["prompts"])
        status, body, _raw, calls = w.call(
            a, {"action": "generate-draft", "title": title, "imageKeys": keys}, grant=True
        )
        if foreign:
            assert status == 400 and _error(body)["message"] == BAD_IMAGE_MSG
            assert len(_world["prompts"]) == before
            assert {c["key"] for c in w.recorder.writes()} <= {RATELIMIT_KEY}
        elif not w.a_posts:
            assert status == 400 and _error(body)["message"] == NO_LEARNED_MSG
            assert len(_world["prompts"]) == before
        else:
            assert status == 200
            assert body["draftKey"] == f"users/{a.user_id}/generated-posts/{body['draftId']}.md"
            assert len(_world["prompts"]) == before + 1
            who, prompt = _world["prompts"][-1]
            assert who == a.user_id
            assert "BETA" not in prompt  # B's chunks and profile never reach A's prompt
            assert "ALPHA" in prompt
            w.a_drafts[body["draftId"]] = body["markdown"]

    elif kind == "get_result":
        draft_id, category = _draft_target(w, op[1], op[2])
        status, body, raw, _ = w.call(a, {"action": "get-result", "kind": "draft", "id": draft_id})
        if category == "own":
            assert status == 200 and body["markdown"] == w.a_drafts[draft_id]
        else:
            assert status != 200
            assert w.b_draft_markdown not in raw and "BETA" not in raw
            if category == "dots":
                assert status == 400
            else:
                assert status == 404

    elif kind == "list":
        status, body, _raw, _ = w.call(a, {"action": "list-drafts"})
        assert status == 200
        ids = [d["draftId"] for d in body["drafts"]]
        assert sorted(ids) == sorted(w.a_drafts)  # neither B's draft nor the legacy draft appears
        assert w.b_draft_id not in ids and "draft-legacy" not in ids

    elif kind == "delete":
        draft_id, category = _draft_target(w, op[1], op[2])
        status, body, _raw, _ = w.call(a, {"action": "delete-draft", "draftId": draft_id})
        if category == "own":
            assert status == 200 and body == {"draftId": draft_id, "deleted": True}
            del w.a_drafts[draft_id]
            assert not s3store.key_exists(f"users/{a.user_id}/generated-posts/{draft_id}.md")
        elif category in ("slash", "dots"):
            assert status == 400 and _error(body)["message"] == BAD_DRAFT_ID_MSG
        else:
            assert status == 404 and _error(body)["message"] == DRAFT_NOT_FOUND_MSG
        assert s3store.key_exists(f"users/{w.b.user_id}/generated-posts/{w.b_draft_id}.md")

    elif kind == "grant_request":
        status, body, _raw, _ = w.call(a, {"action": "grant-request", "purpose": op[1]})
        assert status in (200, 400)

    elif kind == "grant_list":
        status, body, _raw, _ = w.call(a, {"action": "grant-list-mine"})
        assert status == 200
        ids = [g["id"] for g in body["grants"]]
        assert a.grant_id in ids and len(ids) == len(set(ids))

    else:  # pragma: no cover
        raise AssertionError(kind)


def _b_attacks_a_drafts(w):
    """B asks for, lists, and deletes A's drafts; A's objects stay unchanged (Req 8.5, 22.12, 22.13)."""
    a_prefix = f"users/{w.a.user_id}/"
    before = _snapshot_prefix(a_prefix)
    index_key = f"{a_prefix}metadata/drafts.json"
    for draft_id, markdown in sorted(w.a_drafts.items()):
        status, _body, raw, _ = w.call(w.b, {"action": "get-result", "kind": "draft", "id": draft_id})
        assert status != 200 and markdown not in raw

        status, body, _raw, _ = w.call(w.b, {"action": "list-drafts"})
        assert status == 200
        assert draft_id not in [d["draftId"] for d in body["drafts"]]

        status, body, _raw, _ = w.call(w.b, {"action": "delete-draft", "draftId": draft_id})
        assert status == 404 and _error(body)["message"] == DRAFT_NOT_FOUND_MSG
        assert s3store.key_exists(f"{a_prefix}generated-posts/{draft_id}.md")
        found = s3store.read_json_optional(index_key)
        assert found is not None and draft_id in [e["draftId"] for e in found[0]["drafts"]]
    assert _snapshot_prefix(a_prefix) == before


# --- Property 15 ------------------------------------------------------------------------


@_SETTINGS
@given(ops=st.lists(_ops, min_size=1, max_size=7))
def test_user_actions_stay_in_own_namespace_and_leave_other_users_untouched(
    s3_bucket, s3_calls, signing_secret, stubs, create_user, make_session_token, ops
):
    """Validates: Requirements 8.3, 8.4, 8.5, 8.6, 8.8, 8.9, 8.10, 9.4, 22.12, 22.13"""
    w = _setup(s3_calls, create_user, make_session_token)
    a_prefix = f"users/{w.a.user_id}/"
    outside_before = _snapshot(exclude_prefix=a_prefix)

    for op in ops:
        _step(w, op)

    # everything outside users/{A}/ (B, legacy keys, login lookups) is byte-identical
    assert _snapshot(exclude_prefix=a_prefix) == outside_before

    # A's index holds only A's chunks
    found = s3store.read_json_optional(f"{a_prefix}embeddings/index-map.json")
    if found is not None:
        texts = [entry["text"] for entry in found[0].values()]
        assert texts and all("ALPHA" in t and "BETA" not in t for t in texts)
    profile = s3store.read_json_optional(f"{a_prefix}style-profile/profile.json")
    if profile is not None:
        assert profile[0]["tone"] == "ALPHA-profile"

    _b_attacks_a_drafts(w)


# --- examples ---------------------------------------------------------------------------


def test_new_member_reports_an_empty_workspace_despite_other_users_data(
    s3_bucket, s3_calls, signing_secret, stubs, create_user, make_session_token
):
    """Validates: Requirements 8.8, 8.10, 9.4"""
    w = _setup(s3_calls, create_user, make_session_token)
    c = _make_member(create_user, make_session_token, "carol01")

    status, body, _raw, _ = w.call(c, {"action": "get-learning-status"})
    assert status == 200
    assert body == {"processedPosts": 0, "chunks": 0, "posts": []}

    status, body, _raw, _ = w.call(c, {"action": "list-drafts"})
    assert status == 200 and body == {"drafts": []}

    status, body, _raw, _ = w.call(c, {"action": "generate-draft", "title": "t", "imageKeys": []}, grant=True)
    assert status == 400 and _error(body)["message"] == NO_LEARNED_MSG

    status, body, _raw, _ = w.call(c, {"action": "rebuild-from-stored"}, grant=True)
    assert status == 400 and _error(body)["message"] == NO_RAW_MSG


def test_other_users_draft_is_not_found_for_get_result_list_and_delete(
    s3_bucket, s3_calls, signing_secret, stubs, create_user, make_session_token
):
    """Validates: Requirements 8.5, 22.12, 22.13"""
    w = _setup(s3_calls, create_user, make_session_token)
    status, *_ = w.call(
        w.a,
        {"action": "learn-style", "pastedTexts": [{"postId": "p1", "text": "ALPHA hello " * 40}]},
        grant=True,
    )
    assert status == 200
    status, body, _raw, _ = w.call(w.a, {"action": "generate-draft", "title": "내 글", "imageKeys": []}, grant=True)
    assert status == 200
    w.a_drafts[body["draftId"]] = body["markdown"]
    assert "BETA" not in _world["prompts"][-1][1]

    _b_attacks_a_drafts(w)

    status, body, _raw, _ = w.call(w.a, {"action": "get-result", "kind": "draft", "id": w.b_draft_id})
    assert status == 404 and w.b_draft_markdown not in json.dumps(body, ensure_ascii=False)
    status, body, _raw, _ = w.call(w.a, {"action": "delete-draft", "draftId": w.b_draft_id})
    assert status == 404
    assert s3store.key_exists(f"users/{w.b.user_id}/generated-posts/{w.b_draft_id}.md")

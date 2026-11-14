"""Lite tier for generate-draft (Req 15.1, 15.2 of voice-box-content-research-branding).

A member who names no Grant may generate drafts on the lite model, up to a monthly quota
(MABLOP_LITE_MONTHLY_LIMIT, default 5). Grant holders and admins get the quality model and never touch
the quota. The tier is decided on the server from the verified request: a `tier` key in the body is
ignored, and a named grantId must be a real approved Grant of the acting user.

Bedrock is replaced by a recording fake; S3 is moto; sessions use the real token helpers.
"""
import json
import time
from types import SimpleNamespace

import pytest

import access_controller
import auth_messages
import bedrock
import drafting_engine
import errors
import grant_service
import s3store
import usage_meter
from access_controller import TIER_LITE, TIER_QUALITY, RequestContext
from conftest import ACTING_USER_ID
from errors import MablopError

QUALITY_MODEL = "test-quality-model"
LITE_MODEL = "test-lite-model"
LIMIT = 2
NOW_S = 1_800_000_000  # 2027-01-15 (KST), used to test month boundaries
GRANT_REQUIRED = "Bedrock 사용 권한이 필요합니다. 마이페이지에서 신청하세요."
QUOTA_MESSAGE = "이번 달 저품질 초안 한도를 모두 사용했습니다. 다음 달에 다시 이용하거나 Bedrock 사용 권한을 신청하세요."
HAIKU_PROFILE = "us.anthropic.claude-haiku-4-5-20251001-v1:0"


@pytest.fixture
def env(monkeypatch, s3_bucket):
    monkeypatch.setenv("MABLOP_TEXT_MODEL", QUALITY_MODEL)
    monkeypatch.setenv("MABLOP_LITE_MODEL", LITE_MODEL)
    monkeypatch.setenv("MABLOP_LITE_MONTHLY_LIMIT", str(LIMIT))
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")


@pytest.fixture
def fake_bedrock(monkeypatch):
    """Record the model id of every text call; fail on demand. No index is seeded, so style is general."""
    state = {"models": [], "fail": None}

    def fake_embed(text):
        return [1.0, 0.0, 0.0]

    def fake_invoke(model_id, body):
        if state["fail"] is not None:
            raise state["fail"]
        state["models"].append(model_id)
        return {"content": [{"type": "text", "text": "# 제목\n\n본문"}]}

    monkeypatch.setattr(bedrock, "embed", fake_embed)
    monkeypatch.setattr(bedrock, "invoke", fake_invoke)
    return state


@pytest.fixture
def h(env, signing_secret, monkeypatch):
    """The handler, with the warm rate-limit window reset so tests do not share request counts."""
    import handler

    monkeypatch.setattr(access_controller, "_window", None)
    return handler


@pytest.fixture
def member(create_user, make_session_token):
    user = create_user("lite-member")
    return SimpleNamespace(user_id=user["userId"], token=make_session_token(user["userId"]))


@pytest.fixture
def other_member(create_user, make_session_token):
    user = create_user("lite-other")
    return SimpleNamespace(user_id=user["userId"], token=make_session_token(user["userId"]))


def _approved_grant(member_id):
    """Request a Grant and approve it through the real service; return its id (valid now, 30 days)."""
    now = time.time()
    grant_id = grant_service.request({}, SimpleNamespace(user_id=member_id), now=now)["grant"]["id"]
    grant_service.admin_approve(
        {"userId": member_id, "grantId": grant_id, "expiresInDays": 30},
        SimpleNamespace(user_id=ACTING_USER_ID),
        now=now,
    )
    return grant_id


def _send(h, token, body):
    resp = h.handler({"headers": {"X-Mablop-Token": token}, "body": json.dumps(body)})
    return resp["statusCode"], json.loads(resp["body"])


def _draft_body(**extra):
    return {"action": "generate-draft", "title": "제목", "imageKeys": [], **extra}


def _draft_objects(user_id):
    return [o for o in s3store.list_keys(f"users/{user_id}/generated-posts/") if o["key"].endswith(".md")]


def _ctx(action, user_id, role="member"):
    return RequestContext(action, user_id, role, {"userId": user_id, "role": role})


# --- tier decision (access_controller.authorize) ----------------------------------------------


def test_member_draft_without_grant_id_is_lite_and_not_rejected(s3_bucket):
    ctx = access_controller.authorize(_ctx("generate-draft", ACTING_USER_ID), {"imageKeys": []})
    assert ctx.tier == TIER_LITE


def test_null_grant_id_is_lite(s3_bucket):
    """The browser sends grantId null when a member has no selected Grant."""
    ctx = access_controller.authorize(_ctx("generate-draft", ACTING_USER_ID), {"grantId": None})
    assert ctx.tier == TIER_LITE


def test_verified_grant_id_is_quality(create_user):
    member = create_user("grant-holder")
    grant_id = _approved_grant(member["userId"])
    ctx = access_controller.authorize(_ctx("generate-draft", member["userId"]), {"grantId": grant_id})
    assert ctx.tier == TIER_QUALITY


def test_unknown_grant_id_is_forbidden_not_downgraded_to_lite(s3_bucket):
    with pytest.raises(MablopError) as info:
        access_controller.authorize(_ctx("generate-draft", ACTING_USER_ID), {"grantId": "g_ffffffffffffffff"})
    assert (info.value.code, info.value.status, info.value.message) == ("forbidden", 403, GRANT_REQUIRED)


def test_another_users_grant_cannot_raise_the_tier(create_user):
    owner = create_user("grant-owner")
    attacker = create_user("grant-attacker")
    grant_id = _approved_grant(owner["userId"])
    with pytest.raises(MablopError) as info:
        access_controller.authorize(_ctx("generate-draft", attacker["userId"]), {"grantId": grant_id})
    assert info.value.code == "forbidden"


def test_body_tier_field_is_ignored_in_both_directions(create_user):
    member = create_user("tier-field")
    grant_id = _approved_grant(member["userId"])
    # a client asking for quality without a grant stays lite
    assert access_controller.authorize(
        _ctx("generate-draft", member["userId"]), {"tier": "quality"}
    ).tier == TIER_LITE
    # a client asking for lite with a valid grant is still quality, decided by the grant
    assert access_controller.authorize(
        _ctx("generate-draft", member["userId"]), {"tier": "lite", "grantId": grant_id}
    ).tier == TIER_QUALITY


def test_admin_draft_is_quality_without_any_grant(s3_bucket):
    ctx = access_controller.authorize(_ctx("generate-draft", ACTING_USER_ID, role="admin"), {})
    assert ctx.tier == TIER_QUALITY


@pytest.mark.parametrize(
    "action, body",
    [
        ("learn-style", {}),
        ("analyze-images", {}),
        ("rebuild-from-stored", {}),
        ("run-research", {}),
        ("get-result", {"kind": "learn"}),
    ],
)
def test_other_bedrock_actions_still_require_a_grant(create_user, action, body):
    member = create_user("still-gated")
    with pytest.raises(MablopError) as info:
        access_controller.authorize(_ctx(action, member["userId"]), body)
    assert (info.value.code, info.value.message) == ("forbidden", GRANT_REQUIRED)


def test_get_result_draft_still_needs_no_grant(s3_bucket):
    ctx = access_controller.authorize(_ctx("get-result", ACTING_USER_ID), {"kind": "draft", "id": "d1"})
    assert ctx.tier == TIER_QUALITY


# --- handler contract ---------------------------------------------------------------------------


def test_lite_draft_response_contract_and_model(h, fake_bedrock, member):
    status, body = _send(h, member.token, _draft_body())

    assert status == 200
    assert body["tier"] == "lite"
    assert body["liteRemaining"] == LIMIT - 1
    assert body["draftId"] and body["markdown"] and body["draftKey"]
    assert fake_bedrock["models"] == [LITE_MODEL]
    assert len(_draft_objects(member.user_id)) == 1


def test_quality_draft_uses_quality_model_and_omits_lite_remaining(h, fake_bedrock, member):
    grant_id = _approved_grant(member.user_id)

    status, body = _send(h, member.token, _draft_body(grantId=grant_id))

    assert status == 200
    assert body["tier"] == "quality"
    assert "liteRemaining" not in body
    assert fake_bedrock["models"] == [QUALITY_MODEL]


def test_lite_quota_blocks_the_draft_over_the_limit_before_any_model_call(h, fake_bedrock, member):
    for _ in range(LIMIT):
        assert _send(h, member.token, _draft_body())[0] == 200

    status, body = _send(h, member.token, _draft_body())

    assert status == 429
    assert body == {"error": {"code": "quota_exceeded", "message": QUOTA_MESSAGE}}
    assert fake_bedrock["models"] == [LITE_MODEL] * LIMIT
    assert len(_draft_objects(member.user_id)) == LIMIT


def test_quality_draft_is_not_limited_after_lite_quota_is_used_up(h, fake_bedrock, member):
    for _ in range(LIMIT):
        _send(h, member.token, _draft_body())
    assert _send(h, member.token, _draft_body())[0] == 429

    grant_id = _approved_grant(member.user_id)
    status, body = _send(h, member.token, _draft_body(grantId=grant_id))

    assert status == 200 and body["tier"] == "quality"
    assert fake_bedrock["models"][-1] == QUALITY_MODEL


def test_quality_draft_never_increments_the_lite_counter(h, fake_bedrock, member):
    grant_id = _approved_grant(member.user_id)
    for _ in range(LIMIT + 1):
        assert _send(h, member.token, _draft_body(grantId=grant_id))[0] == 200

    _, usage = _send(h, member.token, {"action": "get-usage"})
    assert usage["liteDrafts"] == {"used": 0, "limit": LIMIT}


def test_zero_limit_disables_lite_drafts(h, fake_bedrock, member, monkeypatch):
    monkeypatch.setenv("MABLOP_LITE_MONTHLY_LIMIT", "0")

    status, body = _send(h, member.token, _draft_body())

    assert status == 429 and body["error"]["code"] == "quota_exceeded"
    assert fake_bedrock["models"] == []


def test_lite_quota_is_per_user(h, fake_bedrock, member, other_member):
    for _ in range(LIMIT):
        _send(h, member.token, _draft_body())
    assert _send(h, member.token, _draft_body())[0] == 429

    status, body = _send(h, other_member.token, _draft_body())

    assert status == 200
    assert body["liteRemaining"] == LIMIT - 1


def test_client_cannot_inject_tier_or_remaining_count(h, fake_bedrock, member):
    status, body = _send(h, member.token, _draft_body(tier="quality", liteRemaining=99))

    assert status == 200
    assert body["tier"] == "lite"
    assert body["liteRemaining"] == LIMIT - 1
    assert fake_bedrock["models"] == [LITE_MODEL]


def test_client_cannot_name_quality_with_a_grant_it_does_not_hold(h, fake_bedrock, member):
    status, body = _send(h, member.token, _draft_body(tier="quality", grantId="g_ffffffffffffffff"))

    assert status == 403
    assert body["error"]["message"] == GRANT_REQUIRED
    assert fake_bedrock["models"] == []


# --- usage_meter: quota arithmetic, month boundary, refund, persistence --------------------------


def test_quota_resets_in_the_next_month(env):
    for _ in range(LIMIT):
        usage_meter.reserve_lite_draft(now=NOW_S)
    with pytest.raises(MablopError) as info:
        usage_meter.reserve_lite_draft(now=NOW_S)
    assert info.value.code == "quota_exceeded"
    assert info.value.status == 429
    assert usage_meter.reserve_lite_draft(now=NOW_S + 40 * 86400) == LIMIT - 1


def test_failed_lite_generation_returns_its_reserved_slot(env, fake_bedrock):
    fake_bedrock["fail"] = RuntimeError("bedrock throttled")
    with pytest.raises(RuntimeError):
        drafting_engine.generate_draft({"title": "제목", "imageKeys": []}, tier=TIER_LITE)
    fake_bedrock["fail"] = None

    usage = usage_meter.get_usage({}, None)
    assert usage["liteDrafts"] == {"used": 0, "limit": LIMIT}


def test_lite_counter_keeps_models_activity_and_research_fields(env, s3_bucket):
    usage_meter.record(QUALITY_MODEL, {"usage": {"input_tokens": 10, "output_tokens": 5}}, now=NOW_S)
    usage_meter.record_research({"requests": 2, "inputChars": 100, "outputTokens": 7, "searchCalls": 0}, False, now=NOW_S)

    usage_meter.reserve_lite_draft(now=NOW_S)
    usage_meter.record(LITE_MODEL, {"usage": {"input_tokens": 3, "output_tokens": 1}}, now=NOW_S)

    doc = s3store.get_json(f"users/{ACTING_USER_ID}/metadata/usage-2027-01.json")
    assert doc["liteDrafts"] == 1
    assert doc["models"][QUALITY_MODEL]["calls"] == 1
    assert doc["models"][LITE_MODEL]["calls"] == 1
    assert doc["research"]["runs"] == 1 and doc["research"]["requests"] == 2
    assert "activity" in doc


def test_get_usage_reports_lite_draft_counts(h, fake_bedrock, member):
    _send(h, member.token, _draft_body())

    status, usage = _send(h, member.token, {"action": "get-usage"})

    assert status == 200
    assert usage["liteDrafts"] == {"used": 1, "limit": LIMIT}
    assert {"models", "activity", "research", "totalUsd", "resetAt"} <= set(usage)


# --- configuration ----------------------------------------------------------------------------


def test_lite_limit_defaults_to_five_and_rejects_bad_values(monkeypatch):
    monkeypatch.delenv("MABLOP_LITE_MONTHLY_LIMIT", raising=False)
    assert usage_meter.lite_limit() == 5
    monkeypatch.setenv("MABLOP_LITE_MONTHLY_LIMIT", "7")
    assert usage_meter.lite_limit() == 7
    monkeypatch.setenv("MABLOP_LITE_MONTHLY_LIMIT", "abc")
    assert usage_meter.lite_limit() == 5
    monkeypatch.setenv("MABLOP_LITE_MONTHLY_LIMIT", "-1")
    assert usage_meter.lite_limit() == 5


def test_lite_model_defaults_to_haiku_and_can_be_overridden(monkeypatch):
    monkeypatch.delenv("MABLOP_LITE_MODEL", raising=False)
    assert drafting_engine._model_for_tier(TIER_LITE) == HAIKU_PROFILE
    monkeypatch.setenv("MABLOP_LITE_MODEL", "")
    assert drafting_engine._model_for_tier(TIER_LITE) == HAIKU_PROFILE
    monkeypatch.setenv("MABLOP_LITE_MODEL", "custom-lite")
    assert drafting_engine._model_for_tier(TIER_LITE) == "custom-lite"
    monkeypatch.setenv("MABLOP_TEXT_MODEL", "custom-quality")
    assert drafting_engine._model_for_tier(TIER_QUALITY) == "custom-quality"


def test_quota_exceeded_maps_to_http_429():
    assert errors.STATUS["quota_exceeded"] == 429
    err = MablopError("quota_exceeded", auth_messages.LITE_QUOTA_EXCEEDED)
    assert err.status == 429
    assert json.loads(errors.error_response(err)["body"]) == {
        "error": {"code": "quota_exceeded", "message": QUOTA_MESSAGE}
    }

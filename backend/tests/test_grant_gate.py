"""Example tests: Grant verification hook (Req 20.1, 20.5, 20.8, 20.9, 20.10, 17.9).

Two layers:
- `access_controller.requires_grant` / `check_grant` against a moto S3 bucket with real Grants made by
  `grant_service` (one S3 GetObject on the acting user's grants.json per Bedrock_Action).
- `handler.handler` with a Session_Token, where only the downstream actions are replaced by recording
  stubs, so the hook order (Grant check before the action runs) is visible.
The randomised matrix lives in test_grant_gate_property.py; these are fixed, readable examples.
"""
import importlib
import json
import time
from types import SimpleNamespace

import pytest

import access_controller
import auth_messages
import grant_service
from access_controller import RequestContext
from conftest import ACTING_USER_ID
from errors import MablopError

GRANT_REQUIRED_MESSAGE = "Bedrock 사용 권한이 필요합니다. 마이페이지에서 신청하세요."
BEDROCK_ACTIONS = ["learn-style", "analyze-images", "generate-draft", "rebuild-from-stored", "get-result"]
NON_BEDROCK_ACTIONS = [
    "issue-presigned-urls",
    "get-learning-status",
    "preview-learning-sources",
    "list-drafts",
    "delete-draft",
    "grant-request",
    "grant-list-mine",
    "admin-grant-list",
    "admin-grant-approve",
    "admin-grant-reject",
    "admin-grant-revoke",
]
NOW_S = 1_800_000_000
ADMIN_ACTOR = SimpleNamespace(user_id="u_aaaaaaaaaaaaaaaa")


def _grants_key(user_id):
    return f"users/{user_id}/metadata/grants.json"


@pytest.fixture
def acting_member(create_user):
    """A stored member record whose userId is ACTING_USER_ID (admin-grant-approve needs an existing user)."""
    return create_user("acting1", user_id=ACTING_USER_ID)


def _approved_grant(member_id, days=30, now=NOW_S):
    """Create a requested Grant and approve it through the real service; return its id."""
    grant_id = grant_service.request({}, SimpleNamespace(user_id=member_id), now=now)["grant"]["id"]
    grant_service.admin_approve(
        {"userId": member_id, "grantId": grant_id, "expiresInDays": days}, ADMIN_ACTOR, now=now
    )
    return grant_id


def _member_ctx(action, user_id=ACTING_USER_ID):
    return RequestContext(action, user_id, "member", {"userId": user_id, "role": "member"})


# --- message literal ---------------------------------------------------------------------


def test_grant_required_message_is_the_korean_literal():
    assert auth_messages.GRANT_REQUIRED == GRANT_REQUIRED_MESSAGE


# --- requires_grant (pure predicate) -----------------------------------------------------


def test_bedrock_action_set_is_the_five_actions():
    assert access_controller.BEDROCK_ACTIONS == frozenset(BEDROCK_ACTIONS)


@pytest.mark.parametrize("action", BEDROCK_ACTIONS)
def test_requires_grant_for_member_on_every_bedrock_action(action):
    assert access_controller.requires_grant(action, {"kind": "learn"}, "member") is True


@pytest.mark.parametrize("action", NON_BEDROCK_ACTIONS)
def test_requires_grant_false_for_non_bedrock_actions(action):
    assert access_controller.requires_grant(action, {}, "member") is False


def test_requires_grant_false_for_get_result_draft_only():
    assert access_controller.requires_grant("get-result", {"kind": "draft"}, "member") is False
    assert access_controller.requires_grant("get-result", {"kind": "learn"}, "member") is True
    assert access_controller.requires_grant("get-result", {}, "member") is True
    # kind=draft exempts only get-result
    assert access_controller.requires_grant("generate-draft", {"kind": "draft"}, "member") is True


@pytest.mark.parametrize("action", BEDROCK_ACTIONS)
def test_requires_grant_false_for_admin(action):
    assert access_controller.requires_grant(action, {}, "admin") is False


# --- check_grant: one grants.json GET per Bedrock_Action (Req 20.1, 20.10) ---------------


@pytest.mark.parametrize("action", BEDROCK_ACTIONS)
def test_bedrock_action_reads_the_grants_file_exactly_once(s3_bucket, s3_calls, acting_member, action):
    grant_id = _approved_grant(ACTING_USER_ID)
    s3_calls.clear()
    assert access_controller.check_grant(_member_ctx(action), {"grantId": grant_id, "kind": "learn"}, now=NOW_S + 1) is None
    assert s3_calls.count("GetObject", _grants_key(ACTING_USER_ID)) == 1
    assert len(s3_calls.calls) == 1  # nothing else is touched


def test_rejected_bedrock_action_also_reads_the_grants_file_once(s3_bucket, s3_calls, acting_member):
    _approved_grant(ACTING_USER_ID)
    s3_calls.clear()
    with pytest.raises(MablopError):
        access_controller.check_grant(
            _member_ctx("generate-draft"), {"grantId": "g_ffffffffffffffff"}, now=NOW_S + 1
        )
    assert s3_calls.count("GetObject", _grants_key(ACTING_USER_ID)) == 1
    assert len(s3_calls.calls) == 1


# --- no GET for get-result(kind=draft) and non-Bedrock actions (Req 20.8, 20.9) ----------


def test_get_result_draft_reads_no_grants_file_even_without_grant_id(s3_bucket, s3_calls):
    s3_calls.clear()
    assert access_controller.check_grant(_member_ctx("get-result"), {"kind": "draft", "id": "d1"}, now=NOW_S) is None
    assert s3_calls.calls == []


@pytest.mark.parametrize("action", NON_BEDROCK_ACTIONS)
def test_non_bedrock_action_reads_no_grants_file(s3_bucket, s3_calls, action):
    s3_calls.clear()
    assert access_controller.check_grant(_member_ctx(action), {}, now=NOW_S) is None
    assert s3_calls.calls == []


# --- admin is exempt (Req 20.6) -----------------------------------------------------------


@pytest.mark.parametrize("action", BEDROCK_ACTIONS)
def test_admin_skips_grant_verification(s3_bucket, s3_calls, action):
    ctx = RequestContext(action, ACTING_USER_ID, "admin", {"userId": ACTING_USER_ID, "role": "admin"})
    s3_calls.clear()
    assert access_controller.check_grant(ctx, {}, now=NOW_S) is None
    assert s3_calls.calls == []


# --- rejection: identical 403 and Korean message (Req 20.4) -------------------------------


def test_missing_grant_id_is_forbidden_with_korean_message_and_no_read(s3_bucket, s3_calls):
    s3_calls.clear()
    with pytest.raises(MablopError) as info:
        access_controller.check_grant(_member_ctx("learn-style"), {}, now=NOW_S)
    assert info.value.code == "forbidden"
    assert info.value.status == 403
    assert info.value.message == GRANT_REQUIRED_MESSAGE
    assert s3_calls.calls == []  # a missing id is never looked up


def test_rejection_message_is_identical_for_unknown_malformed_and_expired(s3_bucket, acting_member):
    expired = _approved_grant(ACTING_USER_ID, days=7)
    later = NOW_S + 8 * 86400
    messages = set()
    for grant_id, now in [("g_ffffffffffffffff", NOW_S + 1), ("not-a-grant", NOW_S + 1), (expired, later)]:
        with pytest.raises(MablopError) as info:
            access_controller.check_grant(_member_ctx("analyze-images"), {"grantId": grant_id}, now=now)
        assert (info.value.code, info.value.status) == ("forbidden", 403)
        messages.add(info.value.message)
    assert messages == {GRANT_REQUIRED_MESSAGE}


def test_blocked_call_is_audit_logged_without_the_grant_id(s3_bucket, capsys):
    capsys.readouterr()
    with pytest.raises(MablopError):
        access_controller.check_grant(_member_ctx("learn-style"), {"grantId": "g_ffffffffffffffff"}, now=NOW_S)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert lines == [{"event": "grant", "outcome": "blocked", "userId": ACTING_USER_ID}]


# --- revocation takes effect on the next call (Req 20.5) ----------------------------------


def test_call_right_after_revocation_is_rejected(s3_bucket, s3_calls, acting_member):
    grant_id = _approved_grant(ACTING_USER_ID)
    ctx = _member_ctx("generate-draft")
    body = {"grantId": grant_id}
    assert access_controller.check_grant(ctx, body, now=NOW_S + 1) is None

    grant_service.admin_revoke({"userId": ACTING_USER_ID, "grantId": grant_id}, ADMIN_ACTOR, now=NOW_S + 2)

    s3_calls.clear()
    with pytest.raises(MablopError) as info:
        access_controller.check_grant(ctx, body, now=NOW_S + 3)
    assert info.value.message == GRANT_REQUIRED_MESSAGE
    assert s3_calls.count("GetObject", _grants_key(ACTING_USER_ID)) == 1  # fresh read, no cache


# --- handler integration: the hook runs before the action (Req 20.1, 17.9) ----------------


@pytest.fixture
def gate(s3_bucket, signing_secret, create_user, make_session_token, s3_calls, monkeypatch):
    """Reloaded handler, a member and an admin with tokens, and recording stubs for every action."""
    import s3store

    importlib.reload(s3store)
    importlib.reload(access_controller)
    import handler

    importlib.reload(handler)
    member = create_user("member1")
    admin = create_user("admin1", role="admin")
    calls = []

    def make_stub(name):
        def stub(body, ctx):
            calls.append(name)
            return {"ran": name}

        return stub

    for name in BEDROCK_ACTIONS + NON_BEDROCK_ACTIONS:
        monkeypatch.setitem(handler.DISPATCH, name, make_stub(name))

    def send(token, body):
        event = {"headers": {"X-Mablop-Token": token}, "body": json.dumps(body)}
        resp = handler.handler(event)
        return resp["statusCode"], json.loads(resp["body"])

    return SimpleNamespace(
        send=send,
        calls=calls,
        member_id=member["userId"],
        member_token=make_session_token(member["userId"]),
        admin_token=make_session_token(admin["userId"]),
        s3_calls=s3_calls,
    )


@pytest.mark.parametrize("action", BEDROCK_ACTIONS)
def test_handler_member_with_valid_grant_runs_action_after_one_grants_read(gate, action):
    grant_id = _approved_grant(gate.member_id, now=int(time.time()))
    gate.s3_calls.clear()
    status, body = gate.send(gate.member_token, {"action": action, "grantId": grant_id, "kind": "learn"})
    assert status == 200
    assert body == {"ran": action}
    assert gate.calls == [action]
    assert gate.s3_calls.count("GetObject", _grants_key(gate.member_id)) == 1


@pytest.mark.parametrize("action", BEDROCK_ACTIONS)
def test_handler_member_without_grant_is_blocked_before_the_action_runs(gate, action):
    status, body = gate.send(gate.member_token, {"action": action, "kind": "learn"})
    assert status == 403
    assert body["error"]["code"] == "forbidden"
    assert body["error"]["message"] == GRANT_REQUIRED_MESSAGE
    assert gate.calls == []


def test_handler_get_result_draft_and_non_bedrock_actions_read_no_grants_file(gate):
    cases = [{"action": "get-result", "kind": "draft", "id": "d1"}] + [{"action": a} for a in NON_BEDROCK_ACTIONS if not a.startswith("admin-")]
    for body in cases:
        gate.s3_calls.clear()
        status, _ = gate.send(gate.member_token, body)
        assert status == 200, body
        assert gate.s3_calls.count("GetObject", _grants_key(gate.member_id)) == 0, body
    assert len(gate.calls) == len(cases)


@pytest.mark.parametrize("action", BEDROCK_ACTIONS)
def test_handler_admin_runs_bedrock_action_without_grant_id_or_grants_read(gate, action):
    gate.s3_calls.clear()
    status, body = gate.send(gate.admin_token, {"action": action, "kind": "learn"})
    assert (status, body) == (200, {"ran": action})
    assert gate.s3_calls.count("GetObject", _grants_key(gate.member_id)) == 0
    assert all("grants.json" not in (key or "") for key in gate.s3_calls.keys("GetObject"))


def test_handler_call_right_after_revocation_is_rejected_and_action_not_run(gate):
    grant_id = _approved_grant(gate.member_id, now=int(time.time()))
    request = {"action": "generate-draft", "grantId": grant_id}
    assert gate.send(gate.member_token, request)[0] == 200
    assert gate.calls == ["generate-draft"]

    grant_service.admin_revoke({"userId": gate.member_id, "grantId": grant_id}, ADMIN_ACTOR)

    status, body = gate.send(gate.member_token, request)
    assert status == 403
    assert body["error"]["message"] == GRANT_REQUIRED_MESSAGE
    assert gate.calls == ["generate-draft"]  # the second call never reached the action

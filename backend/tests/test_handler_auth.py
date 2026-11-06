"""Handler auth unit tests (user-auth-admin Req 17.4, 20.7, 16.7, 17.9).

Checks the DISPATCH / AUTH_SCOPE_ACTIONS / ADMIN_ONLY_ACTIONS / BEDROCK_ACTIONS tables against
literal action lists, that no refresh action exists, that a grantId in a header or query string
is ignored (403), and the forbidden / not_found status mapping. Messages are literals.
"""
import importlib
import json
from types import SimpleNamespace

import pytest

import errors

ACCOUNT_ACTIONS = {
    "login",
    "register",
    "bootstrap-admin",
    "get-me",
    "change-password",
    "migrate-legacy-data",
    "admin-list-users",
    "admin-approve-user",
    "admin-reject-user",
    "admin-disable-user",
    "admin-enable-user",
    "admin-reset-password",
    "admin-delete-user",
}
GRANT_ACTIONS = {
    "grant-request",
    "grant-list-mine",
    "admin-grant-list",
    "admin-grant-approve",
    "admin-grant-reject",
    "admin-grant-revoke",
}
HISTORY_ACTIONS = {"list-drafts", "delete-draft"}
NEW_ACTIONS = ACCOUNT_ACTIONS | GRANT_ACTIONS | HISTORY_ACTIONS
LEGACY_ACTIONS = {
    "issue-presigned-urls",
    "learn-style",
    "analyze-images",
    "generate-draft",
    "get-result",
    "get-learning-status",
    "preview-learning-sources",
    "rebuild-from-stored",
}
ADMIN_ONLY = {
    "migrate-legacy-data",
    "admin-list-users",
    "admin-approve-user",
    "admin-reject-user",
    "admin-disable-user",
    "admin-enable-user",
    "admin-reset-password",
    "admin-delete-user",
    "admin-grant-list",
    "admin-grant-approve",
    "admin-grant-reject",
    "admin-grant-revoke",
}
BEDROCK = {"learn-style", "analyze-images", "generate-draft", "rebuild-from-stored", "get-result"}

ADMIN_REQUIRED_MSG = "관리자 권한이 필요합니다."
GRANT_REQUIRED_MSG = "Bedrock 사용 권한이 필요합니다. 마이페이지에서 신청하세요."
NOT_FOUND_TARGET_MSG = "대상 사용자를 찾을 수 없습니다."


@pytest.fixture
def h(s3_bucket, signing_secret):
    """Fresh handler / access_controller modules (resets warm rate-limit state) on moto S3."""
    import s3store

    importlib.reload(s3store)
    import access_controller

    importlib.reload(access_controller)
    import handler

    importlib.reload(handler)
    return handler


def _event(body, token, headers=None, query=None):
    all_headers = dict(headers or {})
    if token is not None:
        all_headers["X-Mablop-Token"] = token
    evt = {"headers": all_headers, "body": json.dumps(body)}
    if query is not None:
        evt["queryStringParameters"] = query
        evt["rawQueryString"] = "&".join(f"{k}={v}" for k, v in query.items())
    return evt


def _body(resp):
    return json.loads(resp["body"])


# --- tables ------------------------------------------------------------------------------


def test_dispatch_contains_the_eight_legacy_and_twentyone_new_actions(h):
    assert len(NEW_ACTIONS) == 21
    assert set(h.DISPATCH) == LEGACY_ACTIONS | NEW_ACTIONS
    assert all(callable(fn) for fn in h.DISPATCH.values())


def test_auth_scope_actions_are_exactly_the_twentyone_new_actions(h):
    assert h.AUTH_SCOPE_ACTIONS == frozenset(NEW_ACTIONS)
    assert len(h.AUTH_SCOPE_ACTIONS) == 21
    assert h.AUTH_SCOPE_ACTIONS.isdisjoint(LEGACY_ACTIONS)


def test_admin_only_and_bedrock_action_sets(h):
    import access_controller

    assert access_controller.ADMIN_ONLY_ACTIONS == frozenset(ADMIN_ONLY)
    assert len(access_controller.ADMIN_ONLY_ACTIONS) == 12
    assert access_controller.BEDROCK_ACTIONS == frozenset(BEDROCK)
    assert len(access_controller.BEDROCK_ACTIONS) == 5
    assert access_controller.ADMIN_ONLY_ACTIONS <= set(h.DISPATCH)
    assert access_controller.BEDROCK_ACTIONS <= set(h.DISPATCH)


def test_no_refresh_action_exists(h):
    import access_controller

    for table in (h.DISPATCH, h.AUTH_SCOPE_ACTIONS, access_controller.ADMIN_ONLY_ACTIONS,
                  access_controller.BEDROCK_ACTIONS, access_controller.AUTH_EXEMPT_ACTIONS):
        assert not [a for a in table if "refresh" in a or "renew" in a]


@pytest.mark.parametrize("action", ["refresh", "refresh-token", "renew-token"])
def test_refresh_action_is_an_unknown_action(h, create_user, make_session_token, action):
    admin = create_user("admin1", role="admin")
    token = make_session_token(admin["userId"])
    resp = h.handler(_event({"action": action}, token))
    assert resp["statusCode"] == 400
    assert _body(resp)["error"]["code"] == "bad_request"


# --- grantId only from the body (Req 20.7) ----------------------------------------------


@pytest.fixture
def member_with_grant(h, create_user, make_session_token):
    import grant_service

    member = create_user("member1")["userId"]
    grant_id = grant_service.request({}, SimpleNamespace(user_id=member))["grant"]["id"]
    grant_service.admin_approve(
        {"userId": member, "grantId": grant_id, "expiresInDays": 30},
        SimpleNamespace(user_id="u_0123456789abcdef"),
    )
    return SimpleNamespace(token=make_session_token(member), grant_id=grant_id)


def test_grant_id_in_header_or_query_is_ignored_and_gives_403(h, monkeypatch, member_with_grant):
    import style_learner

    monkeypatch.setattr(style_learner, "learn_style", lambda *a, **k: {"ok": True})
    gid = member_with_grant.grant_id
    body = {"action": "learn-style"}

    for event in (
        _event(body, member_with_grant.token, headers={"X-Mablop-Grant-Id": gid, "grantId": gid}),
        _event(body, member_with_grant.token, query={"grantId": gid}),
        _event(body, member_with_grant.token, headers={"grantId": gid}, query={"grantId": gid}),
    ):
        resp = h.handler(event)
        assert resp["statusCode"] == 403
        assert _body(resp)["error"] == {"code": "forbidden", "message": GRANT_REQUIRED_MSG}

    # the same Grant in the body is accepted
    resp = h.handler(_event({**body, "grantId": gid}, member_with_grant.token))
    assert resp["statusCode"] == 200
    assert _body(resp) == {"ok": True}


# --- errors mapping (Req 16.7) ----------------------------------------------------------


def test_errors_map_forbidden_to_403_and_not_found_to_404():
    assert errors.STATUS["forbidden"] == 403
    assert errors.STATUS["not_found"] == 404
    assert errors.MablopError("forbidden").status == 403
    assert errors.MablopError("not_found").status == 404
    assert errors.error_response(errors.MablopError("not_found", "x"))["statusCode"] == 404


def test_member_calling_admin_action_gets_403_with_literal_message(h, create_user, make_session_token):
    member = create_user("member1")
    resp = h.handler(_event({"action": "admin-list-users"}, make_session_token(member["userId"])))
    assert resp["statusCode"] == 403
    assert _body(resp)["error"] == {"code": "forbidden", "message": ADMIN_REQUIRED_MSG}


def test_unknown_target_user_gives_404_with_literal_message(h, create_user, make_session_token):
    admin = create_user("admin1", role="admin")
    resp = h.handler(
        _event({"action": "admin-approve-user", "userId": "u_0000000000000000"},
               make_session_token(admin["userId"]))
    )
    assert resp["statusCode"] == 404
    assert _body(resp)["error"] == {"code": "not_found", "message": NOT_FOUND_TARGET_MSG}

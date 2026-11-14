"""Property-based tests for the Access_Controller administrator gate.

Property 10 (Req 4.8, 19.16). Hypothesis + moto + pytest. For every
administrator-only action and any authenticated, active User_Record,
`check_request` passes iff the record's role is `admin`; a member receives
`forbidden` (HTTP 403) with the ADMIN_REQUIRED message and no state changes
(the only S3 write is the global rate-limit window counter).

The function-scoped moto fixtures (`s3_bucket`, `signing_secret`, `s3_calls`)
are shared across examples, so the health check for function-scoped fixtures is
suppressed. Each example (re)writes its own User_Record. The global rate limit
is raised so it never interferes.
"""
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import auth_messages
import s3store
from errors import MablopError

ADMIN_REQUIRED_LITERAL = "관리자 권한이 필요합니다."

EXPECTED_ADMIN_ONLY = frozenset(
    {
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
        "admin-list-voc",
        "admin-update-voc-status",
    }
)

NOW_MS = 1_700_000_000_000  # whole seconds, so now * 1000 is exact

PBT_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

admin_actions = st.sampled_from(sorted(access_controller.ADMIN_ONLY_ACTIONS))
roles = st.sampled_from(["admin", "member"])
login_ids = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=4, max_size=32)
# Arbitrary extra request fields; they must not influence the gate.
extra_fields = st.dictionaries(
    st.sampled_from(["userId", "grantId", "reason", "kind"]),
    st.text(max_size=20),
    max_size=3,
)


@pytest.fixture(autouse=True)
def _gate_environment(monkeypatch):
    """Neutralize the global rate limit and the warm window state."""
    monkeypatch.setenv("MABLOP_MAX_RATE", str(10**9))
    monkeypatch.setattr(access_controller, "_window", None)


def test_admin_only_action_set_is_the_eleven_documented_actions():
    """Req 4.8, 19.16: the gate covers exactly the twelve administrator actions."""
    assert access_controller.ADMIN_ONLY_ACTIONS == EXPECTED_ADMIN_ONLY
    assert len(access_controller.ADMIN_ONLY_ACTIONS) == 14


@PBT_SETTINGS
@given(action=admin_actions, role=roles, login_id=login_ids, extra=extra_fields)
def test_property_admin_actions_require_admin_role(
    s3_bucket,
    s3_calls,
    signing_secret,
    create_user,
    make_session_token,
    action,
    role,
    login_id,
    extra,
):
    """Feature: user-auth-admin, Property 10 - an administrator action passes the gate iff the role is admin.

    Validates: Requirements 4.8, 19.16
    """
    record = create_user(login_id, role=role, status="active", now_ms=NOW_MS)
    token = make_session_token(record["userId"], NOW_MS)
    headers = {"X-Mablop-Token": token}
    body = json.dumps({**extra, "action": action})

    account_key = f"users/{record['userId']}/account.json"
    before = s3store.get_json(account_key)
    s3_calls.clear()

    if role == "admin":
        ctx = access_controller.check_request(headers, body, now=NOW_MS / 1000)
        assert ctx.action == action
        assert ctx.user_id == record["userId"]
        assert ctx.role == "admin"
    else:
        with pytest.raises(MablopError) as excinfo:
            access_controller.check_request(headers, body, now=NOW_MS / 1000)
        err = excinfo.value
        assert err.code == "forbidden"
        assert err.status == 403
        assert err.message == auth_messages.ADMIN_REQUIRED
        assert err.message == ADMIN_REQUIRED_LITERAL

    # No state changes: the only write is the global rate-limit window counter,
    # and the stored User_Record is untouched.
    written = {call["key"] for call in s3_calls.writes()}
    assert written <= {access_controller.RATELIMIT_KEY}
    assert s3store.get_json(account_key) == before

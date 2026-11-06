"""Property-based tests for access_controller.check_request: the authentication gate.

Property 8 (Req 4.1, 4.3, 4.4, 4.5, 10.1). Hypothesis + moto + pytest. A request to
any action outside {login, register, bootstrap-admin} is authenticated if and only
if its Session_Token has a valid signature, is unexpired (and not issued beyond the
clock skew), names an existing `active` User_Record, and has `iat` not earlier than
that record's `credentialsChangedAt`. Every other case (including unknown actions,
non-string actions, unparsable bodies, and a Bootstrap_Token in place of a Session_Token)
returns the one 401 with the Korean login message. The three exempt actions need no
Session_Token.

Time is passed to `check_request` in whole seconds so `int(now * 1000)` is exact.
The global rate limit is raised so that many examples do not hit 429.
"""
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import auth_crypto
from errors import MablopError

UNAUTHENTICATED_LITERAL = "로그인이 필요합니다. 다시 로그인하세요."
ADMIN_REQUIRED_LITERAL = "관리자 권한이 필요합니다."

PBT_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_TTL = auth_crypto.TOKEN_TTL_MS
_SKEW = auth_crypto.CLOCK_SKEW_MS
_BASE_MS = 1_700_000_000_000  # whole-second epoch milliseconds
_LOGIN_ID = "tester"

_KNOWN_ACTIONS = sorted(
    access_controller.ADMIN_ONLY_ACTIONS
    | access_controller.BEDROCK_ACTIONS
    | {"get-me", "change-password", "list-drafts", "delete-draft", "refresh", "health", "ping"}
)

# --- strategies ---------------------------------------------------------------------------

non_exempt_actions = st.one_of(
    st.sampled_from(_KNOWN_ACTIONS),
    st.text(max_size=40).filter(lambda a: a not in access_controller.AUTH_EXEMPT_ACTIONS),
)
# Bodies without a usable string action: they take the authenticated path as well.
odd_bodies = st.sampled_from(
    ["", "not json", "[]", "null", "{}", '{"action": 5}', '{"action": null}', '{"action": ["login"]}']
)
bodies = st.one_of(non_exempt_actions.map(lambda a: json.dumps({"action": a})), odd_bodies)

statuses = st.sampled_from(["active", "pending", "rejected", "disabled"])
roles = st.sampled_from(["member", "admin"])
variants = st.sampled_from(["valid", "unknown_user", "tampered", "bootstrap", "missing", "garbage"])
issue_ms_values = st.integers(min_value=0, max_value=10**6).map(lambda s: _BASE_MS + s * 1000)
# Seconds from issue to verification: boundaries of the skew window and the lifetime, plus a range.
ages_s = st.one_of(
    st.sampled_from(
        [
            -(_SKEW // 1000) - 1,
            -(_SKEW // 1000),
            -(_SKEW // 1000) + 1,
            -1,
            0,
            1,
            _TTL // 1000 - 1,
            _TTL // 1000,
            _TTL // 1000 + 1,
        ]
    ),
    st.integers(min_value=-300, max_value=_TTL // 1000 + 3600),
)
# credentialsChangedAt relative to iat, in milliseconds (0 means equal, which is accepted).
cca_deltas = st.one_of(st.sampled_from([-1, 0, 1, -1000, 1000]), st.integers(-10**7, 10**7))
header_names = st.sampled_from(["X-Mablop-Token", "x-mablop-token", "X-MABLOP-TOKEN"])
garbage_tokens = st.one_of(
    st.text(max_size=80),
    st.sampled_from(["a.b", "..", ".", "x" * 600, "eyJ2IjoxfQ.AAAA"]),
)
header_noise = st.dictionaries(
    st.sampled_from(["Content-Type", "User-Agent", "X-Other"]), st.text(max_size=10), max_size=3
)


# --- fixtures -----------------------------------------------------------------------------


@pytest.fixture
def gate_env(monkeypatch, s3_bucket, signing_secret, bootstrap_token):
    """Raise the global rate limit and reset the warm window state around the test."""
    monkeypatch.setenv("MABLOP_MAX_RATE", str(10**9))
    access_controller._window = None
    yield
    access_controller._window = None


def _reject(headers, body, now):
    """check_request must raise; return the error."""
    with pytest.raises(MablopError) as excinfo:
        access_controller.check_request(headers, body, now=now)
    return excinfo.value


def _assert_unauthenticated(err):
    assert err.code == "unauthorized"
    assert err.status == 401
    assert err.message == UNAUTHENTICATED_LITERAL


# --- Property 8 ---------------------------------------------------------------------------


@PBT_SETTINGS
@given(
    body=bodies,
    status=statuses,
    role=roles,
    variant=variants,
    issue_ms=issue_ms_values,
    age_s=ages_s,
    cca_delta=cca_deltas,
    header_name=header_names,
    garbage=garbage_tokens,
    noise=header_noise,
)
def test_property_authenticated_iff_valid_session(
    gate_env,
    create_user,
    make_session_token,
    s3_calls,
    acting_user_id,
    other_user_id,
    bootstrap_token,
    body,
    status,
    role,
    variant,
    issue_ms,
    age_s,
    cca_delta,
    header_name,
    garbage,
    noise,
):
    """Feature: user-auth-admin, Property 8 - authenticated iff signature, expiry, active status and credentialsChangedAt all hold.

    Validates: Requirements 4.1, 4.3, 4.4, 4.5, 10.1
    """
    record = create_user(
        _LOGIN_ID,
        status=status,
        role=role,
        now_ms=issue_ms,
        credentials_changed_at=auth_crypto.to_iso_ms(issue_ms + cca_delta),
    )
    uid = record["userId"]
    now_ms = issue_ms + age_s * 1000

    headers = dict(noise)
    if variant == "valid":
        headers[header_name] = make_session_token(uid, issue_ms)
    elif variant == "unknown_user":
        headers[header_name] = make_session_token(other_user_id, issue_ms)
    elif variant == "tampered":
        token = make_session_token(uid, issue_ms)
        payload_segment, signature = token.split(".")
        flipped = "A" if signature[0] != "A" else "B"
        headers[header_name] = f"{payload_segment}.{flipped}{signature[1:]}"
    elif variant == "bootstrap":
        headers[header_name] = bootstrap_token
    elif variant == "garbage":
        headers[header_name] = garbage
    # "missing": no token header at all

    time_ok = -_SKEW <= now_ms - issue_ms and now_ms - issue_ms < _TTL
    signature_ok = variant in ("valid", "unknown_user")
    expect_user = (
        variant == "valid" and status == "active" and time_ok and issue_ms >= issue_ms + cca_delta
    )
    action = access_controller._peek_action(body)
    expect_forbidden = (
        expect_user and action in access_controller.ADMIN_ONLY_ACTIONS and role != "admin"
    )

    s3_calls.clear()
    if expect_user and not expect_forbidden:
        ctx = access_controller.check_request(headers, body, now=now_ms / 1000)
        assert ctx.user_id == uid
        assert ctx.role == role
        assert ctx.action == action
        assert ctx.user["userId"] == uid
    elif expect_forbidden:
        err = _reject(headers, body, now_ms / 1000)
        assert err.code == "forbidden"
        assert err.status == 403
        assert err.message == ADMIN_REQUIRED_LITERAL
    else:
        _assert_unauthenticated(_reject(headers, body, now_ms / 1000))

    # The User_Record is read exactly once, and only after the token passed signature and lifetime checks.
    account_key = f"users/{uid}/account.json"
    other_key = f"users/{other_user_id}/account.json"
    reads = s3_calls.count("GetObject", account_key) + s3_calls.count("GetObject", other_key)
    assert reads == (1 if signature_ok and time_ok else 0)
    if not signature_ok:
        assert not [k for k in s3_calls.touched_keys() if k.startswith("users/")]


@PBT_SETTINGS
@given(
    action=st.sampled_from(["login", "register"]),
    variant=st.sampled_from(["missing", "garbage", "bootstrap", "valid_for_unknown"]),
    garbage=garbage_tokens,
    header_name=header_names,
    extra=st.dictionaries(
        st.text(alphabet="abcdefghij", min_size=1, max_size=6), st.text(max_size=10), max_size=3
    ),
    now_s=st.integers(min_value=1, max_value=2 * 10**9),
)
def test_property_login_and_register_need_no_session_token(
    gate_env, make_session_token, bootstrap_token, other_user_id, action, variant, garbage,
    header_name, extra, now_s,
):
    """Feature: user-auth-admin, Property 8 - login and register are served without a Session_Token.

    Validates: Requirements 4.1, 4.4
    """
    headers = {}
    if variant == "garbage":
        headers[header_name] = garbage
    elif variant == "bootstrap":
        headers[header_name] = bootstrap_token
    elif variant == "valid_for_unknown":
        headers[header_name] = make_session_token(other_user_id, now_s * 1000)
    body = json.dumps({"action": action, **{f"k_{k}": v for k, v in extra.items()}})

    ctx = access_controller.check_request(headers, body, now=float(now_s))

    assert ctx.action == action
    assert ctx.user_id is None
    assert ctx.role is None
    assert ctx.user is None


@PBT_SETTINGS
@given(
    now_s=st.integers(min_value=1, max_value=2 * 10**9),
    token_in=header_names,
)
def test_property_bootstrap_admin_needs_bootstrap_token_not_session_token(
    gate_env, bootstrap_token, now_s, token_in
):
    """Feature: user-auth-admin, Property 8 - bootstrap-admin passes with the Bootstrap_Token and no Session_Token.

    Validates: Requirements 4.1, 10.1
    """
    body = json.dumps({"action": "bootstrap-admin"})

    ctx = access_controller.check_request({token_in: bootstrap_token}, body, now=float(now_s))

    assert ctx.action == "bootstrap-admin"
    assert ctx.user_id is None
    assert ctx.role is None
    assert ctx.user is None

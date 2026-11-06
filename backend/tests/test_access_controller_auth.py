"""Unit tests for access_controller.check_request: the authentication pipeline.

Covers the exempt-action set, the check order (body length, rate, 401), the Bootstrap_Token
check running before any field validation, 401 for unknown actions without a token, the single
account.json read per authenticated request, an empty bucket without an administrator, and the
retirement of the shared token (Req 4.1, 4.2, 4.5, 9.1, 10.1, 10.5, 18.2, 17.9).
"""
import json

import pytest

import access_controller
from errors import MablopError

UNAUTHENTICATED_LITERAL = "로그인이 필요합니다. 다시 로그인하세요."

NOW_S = 1_700_000_000.0
NOW_MS = int(NOW_S * 1000)
LOGIN_ID = "tester"


def _body(action, **fields):
    return json.dumps({"action": action, **fields})


def _reject(headers, body, now=NOW_S):
    with pytest.raises(MablopError) as excinfo:
        access_controller.check_request(headers, body, now=now)
    return excinfo.value


def _assert_unauthenticated(err):
    assert err.code == "unauthorized"
    assert err.status == 401
    assert err.message == UNAUTHENTICATED_LITERAL


def _user_keys(recorder):
    return [k for k in recorder.touched_keys() if k.startswith("users/")]


@pytest.fixture(autouse=True)
def _fresh_window(monkeypatch):
    """Start every test with a clean warm rate window and the default (low-traffic) limit."""
    monkeypatch.delenv("MABLOP_MAX_RATE", raising=False)
    monkeypatch.delenv("MABLOP_MAX_BODY_BYTES", raising=False)
    access_controller._window = None
    yield
    access_controller._window = None


@pytest.fixture
def env(s3_bucket, signing_secret, bootstrap_token):
    """Empty bucket plus signing secret and bootstrap token in moto SSM."""
    return None


@pytest.fixture
def high_rate(monkeypatch):
    """Raise the global rate limit for tests that send many requests."""
    monkeypatch.setenv("MABLOP_MAX_RATE", str(10**9))


# --- Req 4.1: exempt set ------------------------------------------------------------------


def test_exempt_action_set_is_exactly_login_register_bootstrap_admin():
    assert access_controller.AUTH_EXEMPT_ACTIONS == frozenset(
        {"login", "register", "bootstrap-admin"}
    )
    assert len(access_controller.AUTH_EXEMPT_ACTIONS) == 3


@pytest.mark.parametrize("action", ["login", "register"])
def test_login_and_register_pass_without_any_token(env, action):
    ctx = access_controller.check_request({}, _body(action), now=NOW_S)

    assert (ctx.action, ctx.user_id, ctx.role, ctx.user) == (action, None, None, None)


# --- Req 4.2: order body length -> rate -> 401 --------------------------------------------


def test_check_order_is_body_length_then_rate_then_action_then_authentication(env, monkeypatch):
    calls = []
    real = {
        "len": access_controller.check_body_length,
        "rate": access_controller.check_rate,
        "peek": access_controller._peek_action,
        "auth": access_controller._authenticate,
    }

    def spy(name, fn):
        def wrapper(*args, **kwargs):
            calls.append(name)
            return fn(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(access_controller, "check_body_length", spy("len", real["len"]))
    monkeypatch.setattr(access_controller, "check_rate", spy("rate", real["rate"]))
    monkeypatch.setattr(access_controller, "_peek_action", spy("peek", real["peek"]))
    monkeypatch.setattr(access_controller, "_authenticate", spy("auth", real["auth"]))

    _assert_unauthenticated(_reject({}, _body("get-me")))

    assert calls == ["len", "rate", "peek", "auth"]


def test_oversized_body_is_rejected_before_rate_and_does_not_consume_rate(env, monkeypatch):
    monkeypatch.setenv("MABLOP_MAX_BODY_BYTES", "10")
    monkeypatch.setenv("MABLOP_MAX_RATE", "1")

    err = _reject({}, _body("get-me", pad="x" * 50))
    assert err.code == "body_too_large"
    assert err.status == 413

    # The rejected request did not use up the single allowed request in the window.
    monkeypatch.setenv("MABLOP_MAX_BODY_BYTES", str(1 << 20))
    _assert_unauthenticated(_reject({}, _body("get-me")))


def test_oversized_body_wins_over_exhausted_rate_limit(env, monkeypatch):
    monkeypatch.setenv("MABLOP_MAX_RATE", "1")
    _assert_unauthenticated(_reject({}, _body("get-me")))  # consumes the one allowed request
    monkeypatch.setenv("MABLOP_MAX_BODY_BYTES", "10")

    assert _reject({}, _body("get-me", pad="x" * 50)).code == "body_too_large"


def test_rate_limit_is_checked_before_authentication_and_applies_to_exempt_actions(env, monkeypatch):
    monkeypatch.setenv("MABLOP_MAX_RATE", "1")
    _assert_unauthenticated(_reject({}, _body("get-me")))

    for body in (_body("get-me"), _body("login"), _body("register"), _body("bootstrap-admin")):
        err = _reject({}, body)
        assert err.code == "rate_limited"
        assert err.status == 429


# --- Req 9.1: Bootstrap_Token is verified before any field validation ---------------------


@pytest.mark.parametrize(
    "fields",
    [{}, {"loginId": "A!", "name": "", "password": "short"}, {"loginId": 5, "name": None}],
)
def test_bootstrap_admin_with_wrong_token_gets_401_not_field_errors(env, create_user, s3_calls, fields):
    err = _reject({"X-Mablop-Token": "wrong-token"}, _body("bootstrap-admin", **fields))

    _assert_unauthenticated(err)
    assert not [k for k in _user_keys(s3_calls) if k.endswith("account.json") or "/login/" in k]


def test_bootstrap_admin_without_token_gets_401(env):
    _assert_unauthenticated(_reject({}, _body("bootstrap-admin", loginId="admin1")))


@pytest.mark.parametrize("fields", [{}, {"loginId": "A!", "name": "", "password": "short"}])
def test_bootstrap_admin_with_valid_token_passes_the_gate_without_reading_fields(
    env, bootstrap_token, fields
):
    ctx = access_controller.check_request(
        {"X-Mablop-Token": bootstrap_token}, _body("bootstrap-admin", **fields), now=NOW_S
    )

    assert (ctx.action, ctx.user_id, ctx.role, ctx.user) == ("bootstrap-admin", None, None, None)


def test_bootstrap_admin_does_not_accept_a_session_token(env, create_user, make_session_token):
    record = create_user(LOGIN_ID, role="admin", now_ms=NOW_MS)
    token = make_session_token(record["userId"], NOW_MS)

    _assert_unauthenticated(_reject({"X-Mablop-Token": token}, _body("bootstrap-admin")))


# --- Req 4.5: unknown action without a valid token ----------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        _body("no-such-action"),
        _body(""),
        json.dumps({"action": 5}),
        json.dumps({"action": None}),
        json.dumps({"action": ["login"]}),
        json.dumps({}),
        "[]",
        "null",
        "not json",
        "",
        None,
    ],
)
def test_unknown_or_unparsable_action_without_token_is_401_not_bad_request(env, body):
    _assert_unauthenticated(_reject({}, body))


def test_unknown_action_with_garbage_token_is_401(env):
    _assert_unauthenticated(_reject({"X-Mablop-Token": "a.b"}, _body("no-such-action")))


def test_unknown_action_with_valid_token_is_left_to_the_dispatcher(env, create_user, make_session_token):
    record = create_user(LOGIN_ID, now_ms=NOW_MS)
    token = make_session_token(record["userId"], NOW_MS)

    ctx = access_controller.check_request(
        {"X-Mablop-Token": token}, _body("no-such-action"), now=NOW_S
    )

    assert ctx.action == "no-such-action"
    assert ctx.user_id == record["userId"]


# --- Req 18.2: one account.json GET per authenticated request ------------------------------


def test_authenticated_request_reads_account_json_exactly_once(env, create_user, make_session_token, s3_calls):
    record = create_user(LOGIN_ID, now_ms=NOW_MS)
    token = make_session_token(record["userId"], NOW_MS)
    account_key = f"users/{record['userId']}/account.json"
    s3_calls.clear()

    ctx = access_controller.check_request({"X-Mablop-Token": token}, _body("get-me"), now=NOW_S)

    assert ctx.user_id == record["userId"]
    assert s3_calls.count("GetObject", account_key) == 1
    assert [k for k in _user_keys(s3_calls) if k != account_key] == []


def test_admin_action_by_admin_still_reads_account_json_once(env, create_user, make_session_token, s3_calls):
    record = create_user(LOGIN_ID, role="admin", now_ms=NOW_MS)
    token = make_session_token(record["userId"], NOW_MS)
    account_key = f"users/{record['userId']}/account.json"
    s3_calls.clear()

    ctx = access_controller.check_request(
        {"X-Mablop-Token": token}, _body("admin-list-users"), now=NOW_S
    )

    assert ctx.role == "admin"
    assert s3_calls.count("GetObject", account_key) == 1


def test_exempt_actions_read_no_user_record(env, bootstrap_token, s3_calls):
    s3_calls.clear()

    access_controller.check_request({}, _body("login"), now=NOW_S)
    access_controller.check_request({}, _body("register"), now=NOW_S)
    access_controller.check_request(
        {"X-Mablop-Token": bootstrap_token}, _body("bootstrap-admin"), now=NOW_S
    )

    assert not [k for k in _user_keys(s3_calls) if k.endswith("account.json")]


# --- Req 10.5: empty bucket, no administrator ---------------------------------------------


def test_empty_bucket_without_admin_accepts_only_exempt_actions(
    env, high_rate, bootstrap_token, make_session_token, other_user_id
):
    # Exempt actions succeed.
    for action, headers in (
        ("login", {}),
        ("register", {}),
        ("bootstrap-admin", {"X-Mablop-Token": bootstrap_token}),
    ):
        assert access_controller.check_request(headers, _body(action), now=NOW_S).action == action

    # Every other action fails with 401, with no token, a bootstrap token, or a signed token
    # whose user does not exist.
    unknown_user_token = make_session_token(other_user_id, NOW_MS)
    others = sorted(
        access_controller.ADMIN_ONLY_ACTIONS
        | access_controller.BEDROCK_ACTIONS
        | {"get-me", "change-password", "list-drafts", "delete-draft", "no-such-action"}
    )
    for action in others:
        for headers in (
            {},
            {"X-Mablop-Token": bootstrap_token},
            {"X-Mablop-Token": unknown_user_token},
        ):
            _assert_unauthenticated(_reject(headers, _body(action)))


# --- Req 10.1: Bootstrap_Token only for bootstrap-admin ------------------------------------


def test_bootstrap_token_is_rejected_for_every_other_action(env, high_rate, bootstrap_token, create_user):
    create_user(LOGIN_ID, role="admin", now_ms=NOW_MS)
    others = sorted(
        access_controller.ADMIN_ONLY_ACTIONS
        | access_controller.BEDROCK_ACTIONS
        | {"get-me", "change-password", "list-drafts", "delete-draft", "no-such-action"}
    )

    for action in others:
        _assert_unauthenticated(_reject({"X-Mablop-Token": bootstrap_token}, _body(action)))


def test_bootstrap_token_in_env_var_is_not_a_session_token_either(env, monkeypatch):
    monkeypatch.setenv("MABLOP_TOKEN", "shared-secret-from-env")

    _assert_unauthenticated(
        _reject({"X-Mablop-Token": "shared-secret-from-env"}, _body("generate-draft"))
    )

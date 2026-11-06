"""Property test: unexpected failures return a generic message (Req 6.6; Property 26).

An exception raised inside an authentication or administrator action (the component call, or
`check_request` itself) goes through the real Lambda `handler.handler`. Whatever its class and
message, the answer is HTTP 500 `internal_error` with the fixed Korean message, and neither the
exception text nor a stack trace appears in the response, in stdout (audit log) or in stderr.
The audit log line carries only `event`, `outcome` (`error:<ExceptionClass>`) and `userId`.

Feature: user-auth-admin, Property 26: Unexpected failures return a generic message
"""
import json
import re
from unittest import mock

import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import auth_crypto
import auth_messages
import handler
from conftest import BOOTSTRAP_TOKEN_VALUE, SIGNING_SECRET_VALUE

GENERIC_MESSAGE = "일시적인 오류가 발생했습니다. 잠시 후 다시 시도하세요."
ADMIN_LOGIN = "adminuser"
ADMIN_PASSWORD = "Pw#admin-initial-password"
_LOG_KEYS = {"event", "outcome", "userId"}
_USER_ID_RE = re.compile(r"^u_[0-9a-f]{16}$")

AUTH_ACTIONS = sorted(handler.AUTH_SCOPE_ACTIONS)


class _CustomError(Exception):
    pass


class _OddStrError(Exception):
    """Exception whose str() is the raw message and whose repr is something else."""

    def __init__(self, message):
        super().__init__("hidden")
        self._message = message

    def __str__(self):
        return self._message


def _make_client_error(message):
    return ClientError({"Error": {"Code": "SlowDown", "Message": message}}, "PutObject")


_EXCEPTION_FACTORIES = [
    RuntimeError,
    ValueError,
    KeyError,
    TypeError,
    OSError,
    ZeroDivisionError,
    AttributeError,
    _CustomError,
    _OddStrError,
    _make_client_error,
]

# --- strategies -------------------------------------------------------------------------

# "EXC#" never occurs in any fixed response text, so a hit is a real leak of the exception text.
_messages = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), min_size=1, max_size=80
).map(lambda s: "EXC#" + s)
_secret_messages = st.sampled_from(
    [
        "EXC# " + SIGNING_SECRET_VALUE,
        "EXC# " + BOOTSTRAP_TOKEN_VALUE,
        'EXC# Traceback (most recent call last): File "handler.py", line 1',
        "EXC# AccessDenied: arn:aws:s3:::mablop-test/users/u_0123456789abcdef/account.json",
    ]
)
_exceptions = st.builds(
    lambda factory, message: factory(message),
    st.sampled_from(_EXCEPTION_FACTORIES),
    st.one_of(_messages, _messages, _secret_messages),
)

_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# --- fixtures ---------------------------------------------------------------------------


@pytest.fixture
def env(s3_bucket, s3_calls, signing_secret, bootstrap_token, monkeypatch):
    """Bucket, S3 recorder, both secrets in SSM, fast hashing, and a rate limit that never triggers."""
    original = auth_crypto.hash_password

    def hashed(password, iterations=4):
        return original(password, iterations=4)

    monkeypatch.setattr(auth_crypto, "hash_password", hashed)
    monkeypatch.setattr(auth_crypto, "PBKDF2_ITERATIONS", 4)
    monkeypatch.setenv("MABLOP_MAX_RATE", "1000000")
    monkeypatch.setattr(access_controller, "_window", None)
    return s3_calls


def _send(capsys, body, token=None):
    headers = {} if token is None else {"X-Mablop-Token": token}
    capsys.readouterr()
    resp = handler.handler({"headers": headers, "body": json.dumps(body)}, None)
    return resp, capsys.readouterr()


@pytest.fixture
def admin_token(env, capsys):
    """Bootstrap the first administrator and log in; returns the Session_Token."""
    resp, _ = _send(
        capsys,
        {"action": "bootstrap-admin", "loginId": ADMIN_LOGIN, "name": "관리자", "password": ADMIN_PASSWORD},
        token=BOOTSTRAP_TOKEN_VALUE,
    )
    assert resp["statusCode"] == 200
    resp, _ = _send(capsys, {"action": "login", "loginId": ADMIN_LOGIN, "password": ADMIN_PASSWORD})
    assert resp["statusCode"] == 200
    return json.loads(resp["body"])["token"]


# --- checks -----------------------------------------------------------------------------


def _texts(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _texts(key)
            yield from _texts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _texts(item)


def _assert_generic(resp, captured, exc):
    assert resp["statusCode"] == 500
    raw = resp["body"]
    parsed = json.loads(raw)
    assert parsed == {"error": {"code": "internal_error", "message": GENERIC_MESSAGE}}
    assert auth_messages.INTERNAL == GENERIC_MESSAGE

    texts = [str(exc), repr(exc), *getattr(exc, "args", ())]
    marker_texts = [t for t in texts if isinstance(t, str) and "EXC#" in t]
    assert marker_texts
    for text in marker_texts:
        assert text not in raw
        assert not any(text in t for t in _texts(parsed))
        assert text not in captured.out
        assert text not in captured.err
    for forbidden in ("Traceback", 'File "', SIGNING_SECRET_VALUE, BOOTSTRAP_TOKEN_VALUE):
        assert forbidden not in raw
        assert forbidden not in captured.out
        assert forbidden not in captured.err

    # The audit log holds only the exception class name.
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert lines
    for line in lines:
        record = json.loads(line)
        assert set(record) <= _LOG_KEYS
        if "userId" in record:
            assert _USER_ID_RE.fullmatch(record["userId"])
    assert any(json.loads(line)["outcome"] == f"error:{type(exc).__name__}" for line in lines)


# --- properties -------------------------------------------------------------------------


@_SETTINGS
@given(action=st.sampled_from(AUTH_ACTIONS), exc=_exceptions)
def test_exception_in_auth_action_returns_generic_500(env, capsys, admin_token, action, exc):
    """Validates: Requirements 6.6"""

    def boom(body, ctx):
        raise exc

    token = BOOTSTRAP_TOKEN_VALUE if action == "bootstrap-admin" else admin_token
    with mock.patch.dict(handler.DISPATCH, {action: boom}):
        resp, captured = _send(capsys, {"action": action}, token=token)
    _assert_generic(resp, captured, exc)


@_SETTINGS
@given(
    action=st.one_of(st.sampled_from(AUTH_ACTIONS), st.just("generate-draft"), st.just("no-such-action")),
    exc=_exceptions,
)
def test_exception_in_check_request_returns_generic_500(env, capsys, action, exc):
    """Validates: Requirements 6.6"""

    def boom(headers, body, now=None):
        raise exc

    with mock.patch.object(access_controller, "check_request", boom):
        resp, captured = _send(capsys, {"action": action}, token="irrelevant")
    _assert_generic(resp, captured, exc)


# --- real storage outages ---------------------------------------------------------------


@pytest.mark.parametrize(
    "action, fail_op, key_startswith",
    [
        ("get-me", "GetObject", "users/u_"),  # user lookup inside authentication
        ("admin-list-users", "ListObjectsV2", None),  # failure inside an administrator action
    ],
)
def test_s3_outage_returns_generic_500(env, capsys, admin_token, action, fail_op, key_startswith):
    """Validates: Requirements 6.6"""
    rule = env.fail_on(fail_op, key_startswith=key_startswith, times=1)
    resp, captured = _send(capsys, {"action": action}, token=admin_token)
    env.clear_failures()

    assert rule["hits"] == 1
    assert resp["statusCode"] == 500
    assert json.loads(resp["body"]) == {"error": {"code": "internal_error", "message": GENERIC_MESSAGE}}
    for forbidden in ("injected failure", "InternalError", "Traceback", "ClientError"):
        assert forbidden not in resp["body"]
        assert forbidden not in captured.err
    # The audit log may name the exception class (outcome "error:ClientError") but nothing more.
    for forbidden in ("injected failure", "InternalError", "Traceback"):
        assert forbidden not in captured.out

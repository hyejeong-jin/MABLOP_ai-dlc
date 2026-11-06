"""Property-based test for the per-loginId login throttle (user-auth-admin Property 11).

**Property 11: Per-loginId throttle follows the reference model**
**Validates: Requirements 5.2, 5.3, 5.4, 5.5, 5.10**

A time-ordered sequence of attempts against one loginId (wrong and correct password logins, and
wrong and correct current-password ``change-password`` calls, all for an existing account, or only
failing logins for a loginId without a User_Record) is run through ``auth_service.login`` and
``auth_service.change_password`` on moto S3 and compared step by step with a small reference model:
5 failures within 10 minutes start a 10-minute block, a blocked loginId gets HTTP 429 even with the
correct password (also on change-password), a login success resets the counter, and an expired
window or block lets attempts through again.

``auth_service.login`` does not pass ``now`` to ``record_failure``, so ``auth_throttle.time`` is
replaced by a controllable clock; ``now`` is also passed explicitly to both service functions.
PBKDF2 calls above 10000 iterations run with one iteration so production-parameter hashes stay
verifiable and fast. Expected messages are literals from the requirements, not auth_messages.
"""
import json
from types import SimpleNamespace

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
import auth_store
import auth_throttle
import s3store
from errors import MablopError, error_response

PROP_SETTINGS = settings(
    max_examples=40, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)

BASE_TIME = 1_700_000_000
KNOWN_ID = "knownuser"
UNKNOWN_ID = "ghostuser"
PASSWORDS = ["correct-horse-battery", "another-horse-battery-2"]
WRONG_PASSWORD = "definitely-wrong-password"
NEW_PASSWORD_FOR_WRONG = "brand-new-password-1"

# Literals from Req 2.4 / 5.2 / 12.3.
LOGIN_FAILED_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."
LOGIN_BLOCKED_MESSAGE = "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
BAD_CURRENT_MESSAGE = "현재 비밀번호가 올바르지 않습니다."

FAIL_LIMIT, WINDOW_S, BLOCK_S = 5, 600, 600  # Req 5.2


class Model:
    """Reference model of one loginId's throttle (Req 5.2-5.5, 5.10)."""

    def __init__(self):
        self.fails = []
        self.until = 0

    def blocked(self, t):
        return t < self.until

    def fail(self, t):
        self.fails = [f for f in self.fails if t - f < WINDOW_S] + [t]
        if len(self.fails) >= FAIL_LIMIT:
            self.until = t + BLOCK_S

    def reset(self):
        self.fails = []


@pytest.fixture
def clock(monkeypatch):
    """A mutable epoch-seconds clock that replaces ``time`` inside auth_throttle."""
    now = [BASE_TIME]
    monkeypatch.setattr(auth_throttle, "time", SimpleNamespace(time=lambda: now[0]))
    return now


@pytest.fixture(autouse=True)
def _fast_pbkdf2(monkeypatch):
    real = auth_crypto.hashlib.pbkdf2_hmac

    def wrapped(hash_name, password, salt, iterations, dklen=None):
        return real(hash_name, password, salt, 1 if iterations > 10_000 else iterations, dklen)

    monkeypatch.setattr(auth_crypto.hashlib, "pbkdf2_hmac", wrapped)


@pytest.fixture
def world(s3_bucket, signing_secret, create_user, clock):
    """Returns reset(mode): a clean throttle and (for 'known') a fresh account with PASSWORDS[0]."""

    def reset(mode):
        s3store.delete(auth_throttle._key())
        clock[0] = BASE_TIME
        if mode == "known":
            return create_user(KNOWN_ID, password=PASSWORDS[0])["userId"]
        return None

    return reset


def _expect_error(call, status, message):
    with pytest.raises(MablopError) as err:
        call()
    response = error_response(err.value)
    assert response["statusCode"] == status
    assert json.loads(response["body"])["error"]["message"] == message


def _change_password(user_id, current, new, now):
    ctx = SimpleNamespace(user=auth_store.get_user(user_id), user_id=user_id)
    return auth_service.change_password({"currentPassword": current, "newPassword": new}, ctx, now)


KNOWN_KINDS = ["login_bad"] * 3 + ["cp_bad"] * 2 + ["login_good", "cp_good"]
DELTAS = [0, 0, 1, 5, 60, 299, 300, 599, 600, 601, 1200]


@st.composite
def scenarios(draw):
    mode = draw(st.sampled_from(["known", "unknown"]))
    kinds = KNOWN_KINDS if mode == "known" else ["login_bad"]
    steps = draw(
        st.lists(st.tuples(st.sampled_from(kinds), st.sampled_from(DELTAS)), min_size=1, max_size=14)
    )
    return mode, steps


@PROP_SETTINGS
@given(scenario=scenarios())
def test_login_and_change_password_follow_the_throttle_reference_model(world, clock, scenario):
    """**Validates: Requirements 5.2, 5.3, 5.4, 5.5, 5.10**"""
    mode, steps = scenario
    user_id = world(mode)
    login_id = KNOWN_ID if mode == "known" else UNKNOWN_ID
    model = Model()
    pw = 0  # index of the account's current password in PASSWORDS

    for kind, delta in steps:
        clock[0] += delta
        t = clock[0]
        blocked = model.blocked(t)

        if kind in ("login_bad", "login_good"):
            password = WRONG_PASSWORD if kind == "login_bad" else PASSWORDS[pw]
            body = {"loginId": login_id, "password": password}
            if blocked:  # Req 5.2, 5.3: even the correct password is refused
                _expect_error(lambda: auth_service.login(body, None, t), 429, LOGIN_BLOCKED_MESSAGE)
            elif kind == "login_bad":  # Req 5.4: same for unknown loginIds
                _expect_error(lambda: auth_service.login(body, None, t), 401, LOGIN_FAILED_MESSAGE)
                model.fail(t)
            else:
                assert "token" in auth_service.login(body, None, t)
                model.reset()  # Req 5.5
        else:
            current = WRONG_PASSWORD if kind == "cp_bad" else PASSWORDS[pw]
            new = NEW_PASSWORD_FOR_WRONG if kind == "cp_bad" else PASSWORDS[1 - pw]
            call = lambda: _change_password(user_id, current, new, t)  # noqa: E731
            if blocked:  # design deviation 6: change-password is refused while blocked
                _expect_error(call, 429, LOGIN_BLOCKED_MESSAGE)
            elif kind == "cp_bad":  # Req 5.10: shares the login failure counter
                _expect_error(call, 400, BAD_CURRENT_MESSAGE)
                model.fail(t)
            else:
                assert "token" in call()
                pw = 1 - pw  # a password change does not touch the counter

    # The stored state agrees with the model about whether the loginId is blocked right now.
    assert auth_throttle.is_login_blocked(auth_throttle._read_state(), login_id, clock[0]) == model.blocked(
        clock[0]
    )


def test_login_and_change_password_failures_share_one_counter_then_expire(world, clock):
    """**Validates: Requirements 5.2, 5.3, 5.10**"""
    user_id = world("known")
    body_bad = {"loginId": KNOWN_ID, "password": WRONG_PASSWORD}
    body_good = {"loginId": KNOWN_ID, "password": PASSWORDS[0]}
    for i in range(3):
        _expect_error(lambda: auth_service.login(body_bad, None, clock[0]), 401, LOGIN_FAILED_MESSAGE)
    for i in range(2):
        _expect_error(
            lambda: _change_password(user_id, WRONG_PASSWORD, NEW_PASSWORD_FOR_WRONG, clock[0]),
            400,
            BAD_CURRENT_MESSAGE,
        )
    _expect_error(lambda: auth_service.login(body_good, None, clock[0]), 429, LOGIN_BLOCKED_MESSAGE)
    _expect_error(
        lambda: _change_password(user_id, PASSWORDS[0], PASSWORDS[1], clock[0]), 429, LOGIN_BLOCKED_MESSAGE
    )
    clock[0] += BLOCK_S
    assert "token" in auth_service.login(body_good, None, clock[0])

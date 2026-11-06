"""Property-based test for indistinguishable login failures (user-auth-admin Property 6).

**Property 6: Failed logins are indistinguishable**
**Validates: Requirements 2.4, 2.5, 6.1**

``auth_service.login`` must answer every credential failure (unknown loginId, malformed loginId or
password, wrong password of an existing account in any status) with the same HTTP 401, code, literal
message and body, and must do the same hashing work: exactly one ``hashlib.pbkdf2_hmac`` call at the
production iteration count (600000). A blocked loginId is rejected with 429 before any PBKDF2 work.

``hashlib.pbkdf2_hmac`` is wrapped to record each call's iteration count. Calls above 10000
iterations run with one iteration instead, so records at the production count stay verifiable
without the 600000-iteration cost. The throttle object is deleted before each example because the
login failure counter is persisted in S3.
"""
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
import auth_throttle
import s3store
from errors import MablopError, error_response

PROP_SETTINGS = settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)

PRODUCTION_ITERATIONS = 600_000
CORRECT_PASSWORD = "correct-horse-battery"
# Literals from Req 2.4 / 5.2, deliberately not read from auth_messages (design D14).
LOGIN_FAILED_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."
LOGIN_BLOCKED_MESSAGE = "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
EXPECTED_FAILURE_BODY = {"error": {"code": "unauthorized", "message": LOGIN_FAILED_MESSAGE}}
EXPECTED_BLOCKED_BODY = {"error": {"code": "rate_limited", "message": LOGIN_BLOCKED_MESSAGE}}

EXISTING = {
    "active": "activeuser",
    "pending": "pendinguser",
    "disabled": "disableduser",
}
LOGIN_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_-"


class Pbkdf2Spy:
    """Records the iteration count of every hashlib.pbkdf2_hmac call."""

    def __init__(self):
        self.iterations = []

    def clear(self):
        self.iterations.clear()


@pytest.fixture
def pbkdf2_spy(monkeypatch):
    spy = Pbkdf2Spy()
    real = auth_crypto.hashlib.pbkdf2_hmac

    def wrapped(hash_name, password, salt, iterations, dklen=None):
        spy.iterations.append(iterations)
        fast = 1 if iterations > 10_000 else iterations
        return real(hash_name, password, salt, fast, dklen)

    monkeypatch.setattr(auth_crypto.hashlib, "pbkdf2_hmac", wrapped)
    return spy


@pytest.fixture
def accounts(pbkdf2_spy, create_user):
    """One account per status, stored with the production iteration count (spy applied first)."""
    for status, login_id in EXISTING.items():
        create_user(login_id, status=status, password=CORRECT_PASSWORD, iterations=PRODUCTION_ITERATIONS)
    pbkdf2_spy.clear()
    return EXISTING


@pytest.fixture
def attempt(pbkdf2_spy):
    """Run one login on a clean throttle; return (error response, PBKDF2 iteration list)."""

    def run(body, *, prepare=None):
        s3store.delete(auth_throttle._key())
        if prepare:
            prepare()
        pbkdf2_spy.clear()
        with pytest.raises(MablopError) as err:
            auth_service.login(body, None)
        return error_response(err.value), list(pbkdf2_spy.iterations)

    return run


def _assert_uniform_failure(response, iterations):
    assert response["statusCode"] == 401
    assert json.loads(response["body"]) == EXPECTED_FAILURE_BODY
    assert iterations == [PRODUCTION_ITERATIONS]


# --- strategies -------------------------------------------------------------------------------

valid_login_ids = st.text(alphabet=LOGIN_ID_ALPHABET, min_size=4, max_size=32).filter(
    lambda s: s not in EXISTING.values()
)
odd_values = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(),
    st.floats(),
    st.lists(st.integers(), max_size=3),
    st.dictionaries(st.text(max_size=3), st.integers(), max_size=2),
)
malformed_login_ids = st.one_of(
    odd_values,
    st.text(max_size=60).filter(lambda s: s not in EXISTING.values()),
    st.sampled_from(["", "abc", "UPPERCASE1", "has space", "a" * 33, "한글아이디입니다"]),
)
passwords = st.one_of(odd_values, st.text(max_size=200), st.sampled_from(["", "x", "a" * 129]))
wrong_passwords = passwords.filter(lambda p: p != CORRECT_PASSWORD)


@st.composite
def unknown_credentials(draw):
    """A body whose loginId matches no account: well-formed, malformed, or missing."""
    body = {}
    login_id = draw(st.one_of(valid_login_ids, malformed_login_ids, st.just(Ellipsis)))
    if login_id is not Ellipsis:
        body["loginId"] = login_id
    password = draw(st.one_of(passwords, st.just(Ellipsis)))
    if password is not Ellipsis:
        body["password"] = password
    return body


# --- Req 2.4, 2.5, 6.1: every credential failure looks and costs the same ----------------------


@PROP_SETTINGS
@given(body=unknown_credentials())
def test_unknown_or_malformed_login_is_uniform_with_one_production_pbkdf2(accounts, attempt, body):
    """**Validates: Requirements 2.4, 2.5, 6.1**"""
    response, iterations = attempt(body)
    _assert_uniform_failure(response, iterations)


@PROP_SETTINGS
@given(status=st.sampled_from(sorted(EXISTING)), password=wrong_passwords)
def test_wrong_password_of_existing_account_is_uniform_with_one_production_pbkdf2(
    accounts, attempt, status, password
):
    """**Validates: Requirements 2.4, 2.5, 6.1**"""
    response, iterations = attempt({"loginId": accounts[status], "password": password})
    _assert_uniform_failure(response, iterations)


@PROP_SETTINGS
@given(body=st.one_of(st.none(), st.integers(), st.text(max_size=20), st.lists(st.integers(), max_size=3)))
def test_non_dict_body_is_uniform_with_one_production_pbkdf2(accounts, attempt, body):
    """**Validates: Requirements 2.4, 2.5, 6.1**"""
    response, iterations = attempt(body)
    _assert_uniform_failure(response, iterations)


def test_all_failure_kinds_give_byte_identical_responses(accounts, attempt):
    """**Validates: Requirements 2.4, 2.5, 6.1**"""
    cases = [
        {"loginId": "nosuchuser", "password": CORRECT_PASSWORD},
        {"loginId": "BAD ID", "password": CORRECT_PASSWORD},
        {"loginId": 12345, "password": CORRECT_PASSWORD},
        {"loginId": "activeuser", "password": 12345},
        {"loginId": "activeuser", "password": "wrong-password-value"},
        {"loginId": "pendinguser", "password": "wrong-password-value"},
        {"loginId": "disableduser", "password": "wrong-password-value"},
        {},
    ]
    outcomes = [attempt(body) for body in cases]
    assert len({json.dumps(r, sort_keys=True) for r, _ in outcomes}) == 1
    assert all(iterations == [PRODUCTION_ITERATIONS] for _, iterations in outcomes)


# --- Req 5.2 / 6.1: a blocked loginId is rejected before any PBKDF2 work -----------------------


def _record_failures(login_id, count):
    for _ in range(count):
        auth_throttle.record_failure(login_id)


@PROP_SETTINGS
@given(
    login_id=st.one_of(st.sampled_from(sorted(EXISTING.values())), valid_login_ids),
    password=st.one_of(st.just(CORRECT_PASSWORD), passwords),
)
def test_blocked_login_id_returns_429_before_any_pbkdf2(accounts, attempt, login_id, password):
    """**Validates: Requirements 2.4, 2.5, 6.1**"""
    response, iterations = attempt(
        {"loginId": login_id, "password": password}, prepare=lambda: _record_failures(login_id, 5)
    )
    assert response["statusCode"] == 429
    assert json.loads(response["body"]) == EXPECTED_BLOCKED_BODY
    assert iterations == []


@PROP_SETTINGS
@given(body=unknown_credentials())
def test_globally_blocked_service_returns_429_before_any_pbkdf2(accounts, attempt, body):
    """**Validates: Requirements 2.4, 2.5, 6.1**"""
    response, iterations = attempt(body, prepare=lambda: _record_failures(None, 30))
    assert response["statusCode"] == 429
    assert json.loads(response["body"]) == EXPECTED_BLOCKED_BODY
    assert iterations == []

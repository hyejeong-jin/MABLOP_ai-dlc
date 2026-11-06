"""Property-based test for password change (user-auth-admin Property 24).

**Property 24: Password change**
**Validates: Requirements 5.10, 12.2, 12.3, 12.4, 12.5**

For a valid new password that differs from the current one, ``auth_service.change_password`` with
the correct current password stores a new hash, sets ``credentialsChangedAt`` to the same
millisecond value as the new token's ``iat`` (D4), and returns only ``{token, expiresAt}``. The new
token authenticates through the Access_Controller, an earlier token (``iat < credentialsChangedAt``)
does not, the new password logs in and the old one fails with 401. A wrong current password is a
400 ``bad_request`` (never 401), leaves the record unchanged, and counts toward the loginId's
failure counter. A policy-violating or unchanged new password is a 400 with the Korean rule
message and leaves the record unchanged.

``hashlib.pbkdf2_hmac`` is wrapped so calls above 10000 iterations run with one iteration; the
stored iteration count stays at the production value (600000). The throttle object is deleted
before each example because the failure counter is persisted in S3.
"""
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import auth_crypto
import auth_service
import auth_store
import auth_throttle
import s3store
from errors import MablopError, error_response

PROP_SETTINGS = settings(
    max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)

LOGIN_ID = "pwchanger"
PRODUCTION_ITERATIONS = 600_000
HOUR_MS = 3_600_000
# Literals from Req 5.10 / 12.3 / 12.4 / 3.4, deliberately not read from auth_messages (design D14).
BAD_CURRENT_MESSAGE = "현재 비밀번호가 올바르지 않습니다."
INVALID_PASSWORD_MESSAGE = "비밀번호는 10~128자여야 합니다."
SAME_PASSWORD_MESSAGE = "새 비밀번호는 현재 비밀번호와 달라야 합니다."
BLOCKED_MESSAGE = "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
LOGIN_FAILED_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."


@pytest.fixture
def pbkdf2_fast(monkeypatch):
    """Run PBKDF2 calls above 10000 iterations with one iteration (records keep their count)."""
    real = auth_crypto.hashlib.pbkdf2_hmac

    def wrapped(hash_name, password, salt, iterations, dklen=None):
        return real(hash_name, password, salt, 1 if iterations > 10_000 else iterations, dklen)

    monkeypatch.setattr(auth_crypto.hashlib, "pbkdf2_hmac", wrapped)


@pytest.fixture
def scenario(pbkdf2_fast, create_user, make_session_token):
    """Return start(...) -> (record, old_token, ctx) for an active account on a clean throttle."""

    def start(*, password, created_ms, old_iat_ms):
        s3store.delete(auth_throttle._key())
        record = create_user(
            LOGIN_ID,
            password=password,
            now_ms=created_ms,
            iterations=PRODUCTION_ITERATIONS,
        )
        old_token = make_session_token(record["userId"], old_iat_ms)
        ctx = access_controller._authenticate({"X-Mablop-Token": old_token}, old_iat_ms / 1000)
        return record, old_token, ctx

    return start


def _error(body, ctx, now):
    with pytest.raises(MablopError) as err:
        auth_service.change_password(body, ctx, now)
    response = error_response(err.value)
    return response["statusCode"], json.loads(response["body"])["error"]


def _stored(record):
    return auth_store.get_user(record["userId"])


# --- strategies -------------------------------------------------------------------------------

valid_passwords = st.one_of(
    st.text(
        alphabet=st.characters(blacklist_categories=("Cs",)), min_size=10, max_size=40
    ),
    st.integers(min_value=10, max_value=128).map(lambda n: "p" * n),
)
non_string_values = st.one_of(
    st.none(), st.booleans(), st.integers(), st.lists(st.integers(), max_size=2), st.just({})
)
invalid_new_passwords = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=("Cs",)), max_size=9),
    st.integers(min_value=129, max_value=200).map(lambda n: "q" * n),
    non_string_values,
    st.just(Ellipsis),  # field missing
)


@st.composite
def timeline(draw):
    """created <= old token iat < change time <= authentication time after the change (ms)."""
    created = draw(st.integers(min_value=1_600_000_000_000, max_value=2_000_000_000_000))
    old_iat = created + draw(st.integers(min_value=0, max_value=HOUR_MS))
    change = old_iat + draw(st.one_of(st.just(1), st.integers(min_value=1, max_value=HOUR_MS)))
    after = change + draw(st.integers(min_value=0, max_value=HOUR_MS))
    return created, old_iat, change, after


@st.composite
def password_pair(draw):
    """A current password and a valid, different new password."""
    current = draw(valid_passwords)
    new = draw(valid_passwords.filter(lambda p: p != current))
    return current, new


def _body(current, new):
    body = {}
    if current is not Ellipsis:
        body["currentPassword"] = current
    if new is not Ellipsis:
        body["newPassword"] = new
    return body


# --- Req 12.2: success -------------------------------------------------------------------------


@PROP_SETTINGS
@given(passwords=password_pair(), times=timeline())
def test_correct_current_password_changes_credentials_and_invalidates_old_token(
    scenario, passwords, times
):
    """**Validates: Requirements 12.2, 12.3**"""
    current, new = passwords
    created, old_iat, change, after = times
    record, old_token, ctx = scenario(password=current, created_ms=created, old_iat_ms=old_iat)

    result = auth_service.change_password(_body(current, new), ctx, change / 1000)

    assert set(result) == {"token", "expiresAt"}
    stored = _stored(record)
    assert stored["password"]["iterations"] == PRODUCTION_ITERATIONS
    assert stored["password"] != record["password"]
    assert auth_crypto.verify_password(new, stored["password"])
    assert not auth_crypto.verify_password(current, stored["password"])
    assert {k: v for k, v in stored.items() if k not in ("password", "credentialsChangedAt")} == {
        k: v for k, v in record.items() if k not in ("password", "credentialsChangedAt")
    }

    # D4: credentialsChangedAt and the new token's iat are the same millisecond value.
    payload = auth_crypto.verify_token(result["token"], after)
    assert payload.uid == record["userId"]
    assert payload.iat == change
    assert auth_crypto.from_iso_ms(stored["credentialsChangedAt"]) == change
    assert stored["credentialsChangedAt"] == auth_crypto.to_iso_ms(change)
    assert result["expiresAt"] == auth_crypto.to_iso_ms(payload.exp)

    # Access_Controller: the new token authenticates, the earlier one (iat < cutoff) does not.
    new_ctx = access_controller._authenticate({"X-Mablop-Token": result["token"]}, after / 1000)
    assert new_ctx.user_id == record["userId"]
    with pytest.raises(MablopError) as err:
        access_controller._authenticate({"X-Mablop-Token": old_token}, after / 1000)
    assert err.value.code == "unauthorized"
    assert error_response(err.value)["statusCode"] == 401

    # Login: the new password works, the old one fails with 401 (Req 12.2).
    login = auth_service.login({"loginId": LOGIN_ID, "password": new}, None, after / 1000)
    assert auth_crypto.verify_token(login["token"], after).uid == record["userId"]
    status, error = _error_login({"loginId": LOGIN_ID, "password": current}, after / 1000)
    assert status == 401
    assert error["message"] == LOGIN_FAILED_MESSAGE


def _error_login(body, now):
    with pytest.raises(MablopError) as err:
        auth_service.login(body, None, now)
    response = error_response(err.value)
    return response["statusCode"], json.loads(response["body"])["error"]


# --- Req 12.3, 12.5, 5.10: wrong current password ----------------------------------------------


@PROP_SETTINGS
@given(
    passwords=password_pair(),
    wrong=st.one_of(st.text(max_size=140), non_string_values),
    attempts=st.integers(min_value=1, max_value=4),
    times=timeline(),
)
def test_wrong_current_password_is_400_unchanged_and_counted(
    scenario, passwords, wrong, attempts, times
):
    """**Validates: Requirements 5.10, 12.3, 12.5**"""
    current, new = passwords
    if wrong == current or wrong == new:
        wrong = None
    created, old_iat, change, _after = times
    record, old_token, ctx = scenario(password=current, created_ms=created, old_iat_ms=old_iat)

    for attempt in range(1, attempts + 1):
        status, error = _error(_body(wrong, new), ctx, change / 1000)
        assert status == 400  # never 401 (Req 12.5)
        assert error == {"code": "bad_request", "message": BAD_CURRENT_MESSAGE}
        assert _stored(record) == record
        entry = auth_throttle._read_state()["login"][LOGIN_ID]
        assert len(entry["fails"]) == attempt  # Req 5.10

    # The earlier token is still valid because nothing changed.
    still = access_controller._authenticate({"X-Mablop-Token": old_token}, change / 1000)
    assert still.user_id == record["userId"]


def test_five_wrong_current_passwords_block_the_loginid(scenario):
    """**Validates: Requirements 5.10**"""
    record, _token, ctx = scenario(
        password="correct-horse-battery", created_ms=1_700_000_000_000, old_iat_ms=1_700_000_001_000
    )
    now = 1_700_000_002.0
    for _ in range(5):
        status, error = _error(
            {"currentPassword": "wrong-password-value", "newPassword": "another-valid-password"},
            ctx,
            now,
        )
        assert (status, error["message"]) == (400, BAD_CURRENT_MESSAGE)
    status, error = _error(
        {"currentPassword": "correct-horse-battery", "newPassword": "another-valid-password"},
        ctx,
        now,
    )
    assert (status, error) == (429, {"code": "rate_limited", "message": BLOCKED_MESSAGE})
    assert _stored(record) == record


# --- Req 12.4: policy violation or unchanged password -----------------------------------------


@PROP_SETTINGS
@given(current=valid_passwords, new=invalid_new_passwords, times=timeline())
def test_policy_violating_new_password_is_400_with_rule_message_and_unchanged(
    scenario, current, new, times
):
    """**Validates: Requirements 12.4**"""
    created, old_iat, change, _after = times
    record, _token, ctx = scenario(password=current, created_ms=created, old_iat_ms=old_iat)

    status, error = _error(_body(current, new), ctx, change / 1000)

    assert status == 400
    assert error == {"code": "bad_request", "message": INVALID_PASSWORD_MESSAGE}
    assert _stored(record) == record


@PROP_SETTINGS
@given(current=valid_passwords, times=timeline())
def test_new_password_equal_to_current_is_400_with_rule_message_and_unchanged(
    scenario, current, times
):
    """**Validates: Requirements 12.4**"""
    created, old_iat, change, _after = times
    record, _token, ctx = scenario(password=current, created_ms=created, old_iat_ms=old_iat)

    status, error = _error(_body(current, current), ctx, change / 1000)

    assert status == 400
    assert error == {"code": "bad_request", "message": SAME_PASSWORD_MESSAGE}
    assert _stored(record) == record

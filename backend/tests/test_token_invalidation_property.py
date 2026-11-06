"""Property-based test for token invalidation (user-auth-admin Property 22).

**Property 22: Credential changes invalidate earlier tokens**
**Validates: Requirements 11.10, 12.2**

For any user and any Session_Token issued at time T0, if the account is disabled, its password is
changed, or its password is reset at time T1 > T0, the Access_Controller rejects that token with
the uniform 401 from T1 onward, including after the user is re-enabled. A token issued at or after
T1 (the same millisecond counts) is accepted while the account is ``active``.

``hashlib.pbkdf2_hmac`` is wrapped so calls above 10000 iterations run with one iteration; the
throttle object is deleted before each example because change-password and admin-reset-password
read and write the failure counter persisted in S3.
"""
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

LOGIN_ID = "tokeninvalid"
PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "another-valid-password"
HOUR_MS = 3_600_000
KINDS = ("disable", "change_password", "reset_password")


@pytest.fixture
def pbkdf2_fast(monkeypatch):
    """Run PBKDF2 calls above 10000 iterations with one iteration (records keep their count)."""
    real = auth_crypto.hashlib.pbkdf2_hmac

    def wrapped(hash_name, password, salt, iterations, dklen=None):
        return real(hash_name, password, salt, 1 if iterations > 10_000 else iterations, dklen)

    monkeypatch.setattr(auth_crypto.hashlib, "pbkdf2_hmac", wrapped)


@pytest.fixture
def account(pbkdf2_fast, create_user, make_session_token):
    """Return start(created_ms) -> active member record on a clean throttle."""

    def start(created_ms):
        s3store.delete(auth_throttle._key())
        return create_user(LOGIN_ID, password=PASSWORD, now_ms=created_ms)

    return start


@st.composite
def timeline(draw):
    """created <= every old iat < change time T1 <= later checks, all within one token lifetime."""
    created = draw(st.integers(min_value=1_600_000_000_000, max_value=2_000_000_000_000))
    old_iats = draw(
        st.lists(st.integers(min_value=0, max_value=HOUR_MS), min_size=1, max_size=3)
    )
    old_iats = [created + offset for offset in old_iats]
    change = max(old_iats) + draw(
        st.one_of(st.just(1), st.integers(min_value=1, max_value=HOUR_MS))
    )
    later = draw(st.lists(st.integers(min_value=0, max_value=HOUR_MS), min_size=1, max_size=3))
    return created, old_iats, change, later


def _rejected(token, now_ms):
    """Assert the Access_Controller answers the uniform 401 for this token."""
    with pytest.raises(MablopError) as err:
        access_controller._authenticate({"X-Mablop-Token": token}, now_ms / 1000)
    assert err.value.code == "unauthorized"
    assert error_response(err.value)["statusCode"] == 401


def _accepted(token, now_ms, user_id):
    ctx = access_controller._authenticate({"X-Mablop-Token": token}, now_ms / 1000)
    assert ctx.user_id == user_id


def _change_credentials(kind, record, old_token, old_iat, change):
    """Apply one credential change at ``change`` (ms); return the token the call issued, if any."""
    now_s = change / 1000
    if kind == "disable":
        auth_service.admin_disable_user({"userId": record["userId"]}, None, now_s)
        return None
    if kind == "reset_password":
        auth_service.admin_reset_password({"userId": record["userId"]}, None, now_s)
        return None
    ctx = access_controller._authenticate({"X-Mablop-Token": old_token}, old_iat / 1000)
    result = auth_service.change_password(
        {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}, ctx, now_s
    )
    return result["token"]


@PROP_SETTINGS
@given(kind=st.sampled_from(KINDS), times=timeline())
def test_credential_change_invalidates_earlier_tokens_and_accepts_later_ones(
    account, make_session_token, kind, times
):
    """**Validates: Requirements 11.10, 12.2**"""
    created, old_iats, change, later = times
    record = account(created)
    user_id = record["userId"]
    old_tokens = [(make_session_token(user_id, iat), iat) for iat in old_iats]
    for token, iat in old_tokens:
        _accepted(token, iat, user_id)  # valid before the change

    issued = _change_credentials(kind, record, old_tokens[0][0], old_tokens[0][1], change)
    stored = auth_store.get_user(user_id)
    assert auth_crypto.from_iso_ms(stored["credentialsChangedAt"]) == change

    if kind == "disable":
        # Disabled: everything is rejected, including a token issued at T1 itself.
        assert stored["status"] == "disabled"
        _rejected(make_session_token(user_id, change), change)
        auth_service.admin_enable_user({"userId": user_id}, None, (change + 1) / 1000)
        assert auth_store.get_user(user_id)["status"] == "active"
        assert auth_crypto.from_iso_ms(
            auth_store.get_user(user_id)["credentialsChangedAt"]
        ) == change  # re-enabling does not move the cutoff

    check_times = [change + offset for offset in later]
    for now_ms in check_times:
        # Tokens issued before T1 are rejected from T1 onward, also after re-enabling.
        for token, _iat in old_tokens:
            _rejected(token, now_ms)
        # Tokens issued at or after T1 are accepted while the account is active.
        _accepted(make_session_token(user_id, change), now_ms, user_id)
        _accepted(make_session_token(user_id, now_ms), now_ms, user_id)
        if issued is not None:
            assert auth_crypto.verify_token(issued, now_ms).iat == change
            _accepted(issued, now_ms, user_id)


@PROP_SETTINGS
@given(kind=st.sampled_from(KINDS), created=st.integers(min_value=1_600_000_000_000, max_value=2_000_000_000_000))
def test_token_issued_one_millisecond_before_the_change_is_rejected(
    account, make_session_token, kind, created
):
    """**Validates: Requirements 11.10, 12.2**"""
    record = account(created)
    user_id = record["userId"]
    change = created + 1_000
    boundary_token = make_session_token(user_id, change - 1)
    _accepted(boundary_token, change - 1, user_id)

    _change_credentials(kind, record, boundary_token, change - 1, change)
    if kind == "disable":
        auth_service.admin_enable_user({"userId": user_id}, None, (change + 1) / 1000)

    _rejected(boundary_token, change + 1)
    _accepted(make_session_token(user_id, change), change + 1, user_id)

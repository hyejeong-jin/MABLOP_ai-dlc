"""Property-based test for the login outcome per account status (user-auth-admin Property 7).

**Property 7: Login outcome follows account status**
**Validates: Requirements 2.1, 2.6, 2.7**

For any User_Record and its correct password, ``auth_service.login`` returns a verifiable
Session_Token (8 hours), ``expiresAt`` and ``user{name, loginId, role}`` when the status is
``active`` (and nothing else: no hash or salt), HTTP 403 "승인 대기 중인 계정입니다." when
``pending``, and HTTP 403 "사용이 중지된 계정입니다." when ``disabled``. The 403 answers leave the
throttle state untouched. A wrong password gives the same generic 401 in every status.

Records use small PBKDF2 iteration counts. The injected ``now`` is only relied on for the token
(``iat``/``exp``/``expiresAt``); ``record_failure`` always uses the real clock, so no assertion
depends on ``now`` for the wrong-password path.
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
    max_examples=30, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)

# Literals from Req 2.1 / 2.4 / 2.6 / 2.7, deliberately not read from auth_messages (design D14).
PENDING_MESSAGE = "승인 대기 중인 계정입니다."
DISABLED_MESSAGE = "사용이 중지된 계정입니다."
LOGIN_FAILED_MESSAGE = "아이디 또는 비밀번호가 올바르지 않습니다."
EIGHT_HOURS_MS = 8 * 60 * 60 * 1000

LOGIN_ID_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_-"

login_ids = st.text(alphabet=LOGIN_ID_ALPHABET, min_size=4, max_size=32)
names = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs")), min_size=1, max_size=30
).filter(lambda s: s.strip() == s and s != "")
passwords = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)), min_size=10, max_size=40
)
roles = st.sampled_from(["member", "admin"])
iteration_counts = st.integers(min_value=1, max_value=20)
now_seconds = st.integers(min_value=1_600_000_000, max_value=2_000_000_000)


@pytest.fixture
def prepare(create_user, signing_secret):
    """Create one account with a clean throttle; return the stored record."""

    def run(login_id, *, status, password, name="테스터", role="member", iterations=5):
        s3store.delete(auth_throttle._key())
        return create_user(
            login_id, status=status, password=password, name=name, role=role, iterations=iterations
        )

    return run


def _throttle_state():
    return auth_throttle._read_state()


def _raised(body, **kwargs):
    with pytest.raises(MablopError) as err:
        auth_service.login(body, None, **kwargs)
    return error_response(err.value)


# --- Req 2.1: active ---------------------------------------------------------------------------


@PROP_SETTINGS
@given(
    login_id=login_ids,
    name=names,
    password=passwords,
    role=roles,
    iterations=iteration_counts,
    now_s=now_seconds,
)
def test_active_account_gets_verifiable_token_and_public_user_only(
    prepare, login_id, name, password, role, iterations, now_s
):
    """**Validates: Requirements 2.1**"""
    record = prepare(
        login_id, status="active", password=password, name=name, role=role, iterations=iterations
    )
    result = auth_service.login({"loginId": login_id, "password": password}, None, now=now_s)

    now_ms = now_s * 1000
    assert set(result) == {"token", "expiresAt", "user"}
    assert result["user"] == {"name": name, "loginId": login_id, "role": role}

    payload = auth_crypto.verify_token(result["token"], now_ms)
    assert payload.uid == record["userId"]
    assert payload.iat == now_ms
    assert payload.exp == now_ms + EIGHT_HOURS_MS
    assert result["expiresAt"] == auth_crypto.to_iso_ms(now_ms + EIGHT_HOURS_MS)

    serialized = json.dumps(result, ensure_ascii=False)
    for secret_value in (record["password"]["hash"], record["password"]["salt"]):
        assert secret_value not in serialized
    assert "password" not in result["user"]


# --- Req 2.6, 2.7: pending and disabled --------------------------------------------------------


@PROP_SETTINGS
@given(
    login_id=login_ids,
    password=passwords,
    status=st.sampled_from(["pending", "disabled"]),
    role=roles,
    iterations=iteration_counts,
)
def test_pending_and_disabled_correct_password_give_403_without_touching_throttle(
    prepare, login_id, password, status, role, iterations
):
    """**Validates: Requirements 2.6, 2.7**"""
    prepare(login_id, status=status, password=password, role=role, iterations=iterations)
    # Seed some earlier failures for this loginId so a (wrong) counter change would be visible.
    auth_throttle.record_failure(login_id)
    auth_throttle.record_failure(login_id)
    before = _throttle_state()

    response = _raised({"loginId": login_id, "password": password})

    expected = PENDING_MESSAGE if status == "pending" else DISABLED_MESSAGE
    assert response["statusCode"] == 403
    assert json.loads(response["body"]) == {"error": {"code": "forbidden", "message": expected}}
    assert _throttle_state() == before


def test_403_for_pending_and_disabled_never_counts_toward_blocking(prepare):
    """**Validates: Requirements 2.6, 2.7**"""
    password = "correct-horse-battery"
    prepare("pendinguser", status="pending", password=password)
    for _ in range(auth_throttle.LOGIN_FAIL_LIMIT + 2):
        assert _raised({"loginId": "pendinguser", "password": password})["statusCode"] == 403
    assert _throttle_state()["login"] == {}
    assert _throttle_state()["globalFails"] == []

    prepare("disableduser", status="disabled", password=password)
    for _ in range(auth_throttle.LOGIN_FAIL_LIMIT + 2):
        assert _raised({"loginId": "disableduser", "password": password})["statusCode"] == 403
    assert _throttle_state()["login"] == {}
    assert _throttle_state()["globalFails"] == []


# --- Req 2.4 (with Property 7): a wrong password is the generic 401 in every status -----------


@PROP_SETTINGS
@given(
    login_id=login_ids,
    password=passwords,
    wrong=passwords,
    status=st.sampled_from(["active", "pending", "disabled"]),
    role=roles,
    iterations=iteration_counts,
)
def test_wrong_password_gives_same_401_in_every_status(
    prepare, login_id, password, wrong, status, role, iterations
):
    """**Validates: Requirements 2.1, 2.6, 2.7**"""
    if wrong == password:
        wrong = password + "x"
    prepare(login_id, status=status, password=password, role=role, iterations=iterations)

    response = _raised({"loginId": login_id, "password": wrong})

    assert response["statusCode"] == 401
    assert json.loads(response["body"]) == {
        "error": {"code": "unauthorized", "message": LOGIN_FAILED_MESSAGE}
    }

"""Property test: registration is idempotent per loginId and does not reveal accounts
(Req 1.1, 1.3, 1.5, 6.2, 9.15; Property 5).

Runs auth_service.register against a moto S3 bucket. PBKDF2 is replaced by a low-iteration
wrapper (and counted) so the examples stay fast. Production code is not modified.

Feature: user-auth-admin, Property 5: Registration is idempotent per loginId and does not
reveal existing accounts
"""
import json
import re
from datetime import datetime, timezone

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
from conftest import TEST_BUCKET, TEST_REGION

EXPECTED_MESSAGE = "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."
EXPECTED_RESPONSE = {"message": EXPECTED_MESSAGE}

_USER_ID_RE = re.compile(r"^u_[0-9a-f]{16}$")
_CREATED_AT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
_THROTTLE_KEY = "users/_auth/throttle.json"

BASE_NOW = 1_800_000_000  # epoch seconds (integer, so the expected createdAt is exact)
STEP = 3601  # more than the 1 hour register window, so the register throttle never interferes

# --- strategies -------------------------------------------------------------------------

_login_ids = st.one_of(
    st.just("jhe8888"),
    st.from_regex(r"[a-z0-9_-]{4,32}", fullmatch=True),
)
_names = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=30
)
_passwords = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=10, max_size=128
)
_prior_states = st.sampled_from(["absent", "pending", "active", "disabled", "admin"])

_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- fixtures and helpers ---------------------------------------------------------------


@pytest.fixture
def pbkdf2_calls(monkeypatch):
    """Replace hash_password with a 4-iteration version and count how often it runs."""
    original = auth_crypto.hash_password
    calls = []

    def fast_hash(password, iterations=4):
        calls.append(1)
        return original(password, iterations=4)

    monkeypatch.setattr(auth_service.auth_crypto, "hash_password", fast_hash)
    return calls


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _snapshot() -> dict:
    """Every object in the bucket except the register throttle state: key -> bytes."""
    client = boto3.client("s3", region_name=TEST_REGION)
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            if obj["Key"] == _THROTTLE_KEY:
                continue
            body = client.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
            out[obj["Key"]] = body
    return out


def _accounts(snapshot: dict) -> list[dict]:
    return [
        json.loads(data.decode("utf-8"))
        for key, data in snapshot.items()
        if key.startswith("users/u_") and key.endswith("/account.json")
    ]


def _lookups(snapshot: dict) -> list[str]:
    return [key for key in snapshot if key.startswith("users/_auth/login/")]


def _expected_created_at(now_s: int) -> str:
    return datetime.fromtimestamp(now_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def _prior(create_user, login_id: str, state: str):
    if state == "absent":
        return None
    if state == "admin":
        return create_user(login_id, role="admin", status="active")
    return create_user(login_id, role="member", status=state)


# --- first registration -----------------------------------------------------------------


@_SETTINGS
@given(login_id=_login_ids, name=_names, password=_passwords)
def test_valid_register_creates_one_pending_member(
    s3_bucket, pbkdf2_calls, login_id, name, password
):
    """Validates: Requirements 1.1, 1.5, 9.15"""
    _wipe_bucket()
    pbkdf2_calls.clear()

    result = auth_service.register(
        {"loginId": login_id, "name": name, "password": password}, None, now=BASE_NOW
    )

    assert result == EXPECTED_RESPONSE
    assert len(pbkdf2_calls) == 1
    snap = _snapshot()
    accounts = _accounts(snap)
    assert len(accounts) == 1
    record = accounts[0]
    assert record["loginId"] == login_id
    assert record["name"] == name
    assert record["role"] == "member"
    assert record["status"] == "pending"
    assert _USER_ID_RE.fullmatch(record["userId"])
    assert _CREATED_AT_RE.fullmatch(record["createdAt"])
    assert record["createdAt"] == _expected_created_at(BASE_NOW)
    assert record["approvedAt"] is None
    assert record["credentialsChangedAt"] == record["createdAt"]
    assert auth_crypto.verify_password(password, record["password"]) is True
    assert snap[f"users/_auth/login/{login_id}.json"]
    assert json.loads(snap[f"users/_auth/login/{login_id}.json"])["userId"] == record["userId"]
    assert _lookups(snap) == [f"users/_auth/login/{login_id}.json"]


# --- existing accounts in every state ---------------------------------------------------


@_SETTINGS
@given(
    login_id=_login_ids,
    state=_prior_states,
    name=_names,
    password=_passwords,
)
def test_register_for_any_prior_state_returns_the_same_response_and_changes_nothing_when_taken(
    s3_bucket, create_user, pbkdf2_calls, login_id, state, name, password
):
    """Validates: Requirements 1.1, 1.3, 1.5, 6.2, 9.15"""
    _wipe_bucket()
    prior = _prior(create_user, login_id, state)
    before = _snapshot()
    pbkdf2_calls.clear()

    result = auth_service.register(
        {"loginId": login_id, "name": name, "password": password}, None, now=BASE_NOW
    )

    # same status/body whatever the prior state; no hint about the existing account
    assert result == EXPECTED_RESPONSE
    assert list(result) == ["message"]
    assert login_id not in json.dumps(result, ensure_ascii=False)
    # exactly one PBKDF2 computation in every case
    assert len(pbkdf2_calls) == 1

    after = _snapshot()
    accounts = [a for a in _accounts(after) if a["loginId"] == login_id]
    assert len(accounts) == 1  # never more than one record per loginId
    if prior is not None:
        assert after == before  # no record or lookup was created or changed
        assert accounts[0] == prior
    else:
        assert accounts[0]["role"] == "member" and accounts[0]["status"] == "pending"
        assert _USER_ID_RE.fullmatch(accounts[0]["userId"])


@_SETTINGS
@given(login_id=_login_ids, state=_prior_states, name=_names, password=_passwords)
def test_response_is_identical_for_new_and_existing_loginIds(
    s3_bucket, create_user, pbkdf2_calls, login_id, state, name, password
):
    """Validates: Requirements 1.3, 6.2"""
    _wipe_bucket()
    body = {"loginId": login_id, "name": name, "password": password}
    fresh = auth_service.register(body, None, now=BASE_NOW)

    _wipe_bucket()
    _prior(create_user, login_id, state if state != "absent" else "pending")
    existing = auth_service.register(body, None, now=BASE_NOW)

    assert fresh == existing == EXPECTED_RESPONSE


# --- repeated registration --------------------------------------------------------------


@_SETTINGS
@given(
    login_id=_login_ids,
    attempts=st.lists(st.tuples(_names, _passwords), min_size=2, max_size=4),
)
def test_repeated_register_keeps_the_first_record(
    s3_bucket, pbkdf2_calls, login_id, attempts
):
    """Validates: Requirements 1.1, 1.5, 6.2"""
    _wipe_bucket()
    pbkdf2_calls.clear()
    first_snapshot = None

    for index, (name, password) in enumerate(attempts):
        result = auth_service.register(
            {"loginId": login_id, "name": name, "password": password},
            None,
            now=BASE_NOW + index * STEP,
        )
        assert result == EXPECTED_RESPONSE
        snap = _snapshot()
        if first_snapshot is None:
            first_snapshot = snap
            assert len(_accounts(snap)) == 1
        else:
            assert snap == first_snapshot  # later attempts neither add nor change a record

    assert len(pbkdf2_calls) == len(attempts)  # one PBKDF2 per request, new or not
    record = _accounts(first_snapshot)[0]
    first_name, first_password = attempts[0]
    assert record["name"] == first_name
    assert auth_crypto.verify_password(first_password, record["password"]) is True


# --- distinct userIds -------------------------------------------------------------------


@_SETTINGS
@given(
    login_ids=st.lists(
        st.from_regex(r"[a-z0-9_-]{4,32}", fullmatch=True), min_size=2, max_size=4, unique=True
    ),
    password=_passwords,
)
def test_created_records_have_distinct_user_ids(s3_bucket, pbkdf2_calls, login_ids, password):
    """Validates: Requirements 1.1, 1.5"""
    _wipe_bucket()
    for index, login_id in enumerate(login_ids):
        result = auth_service.register(
            {"loginId": login_id, "name": "이름", "password": password},
            None,
            now=BASE_NOW + index * STEP,
        )
        assert result == EXPECTED_RESPONSE

    accounts = _accounts(_snapshot())
    user_ids = [a["userId"] for a in accounts]
    assert len(accounts) == len(login_ids)
    assert len(set(user_ids)) == len(user_ids)
    assert all(_USER_ID_RE.fullmatch(u) for u in user_ids)
    assert sorted(a["loginId"] for a in accounts) == sorted(login_ids)
    assert all(a["role"] == "member" and a["status"] == "pending" for a in accounts)


# --- jhe8888 has no special path ---------------------------------------------------------


@pytest.mark.parametrize("state", ["absent", "pending", "active", "disabled", "admin"])
def test_jhe8888_takes_the_ordinary_path(s3_bucket, create_user, pbkdf2_calls, state):
    """Validates: Requirements 9.15, 1.3"""
    _wipe_bucket()
    prior = _prior(create_user, "jhe8888", state)
    before = _snapshot()
    pbkdf2_calls.clear()

    result = auth_service.register(
        {"loginId": "jhe8888", "name": "제이", "password": "long-enough-password"},
        None,
        now=BASE_NOW,
    )

    assert result == EXPECTED_RESPONSE
    assert len(pbkdf2_calls) == 1
    after = _snapshot()
    accounts = [a for a in _accounts(after) if a["loginId"] == "jhe8888"]
    assert len(accounts) == 1
    if prior is None:
        # registered as an ordinary pending member; nothing legacy-related is written
        assert accounts[0]["role"] == "member" and accounts[0]["status"] == "pending"
        assert not any(key.startswith("users/_auth/legacy") for key in after)
    else:
        assert after == before

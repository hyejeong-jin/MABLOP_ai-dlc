"""Property test: administrator-issued temporary passwords (Req 3.5, 11.6; Property 23).

Part 1 checks auth_crypto.generate_temp_password directly: exactly 16 characters, all drawn from
TEMP_PASSWORD_ALPHABET (no look-alike characters), always inside the 10..128 password policy.
Part 2 runs auth_service.admin_reset_password on a moto S3 bucket: the plaintext appears only in
the response, never in any S3 object or captured log output; only a verifying hash is stored;
credentialsChangedAt is updated; the target loginId's failure counter is cleared (other loginIds
keep theirs); every call returns a different value. PBKDF2 is replaced by a low-iteration wrapper.
Production code is not modified.

Feature: user-auth-admin, Property 23: Temporary passwords
"""
import json

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
import auth_throttle
from conftest import TEST_BUCKET, TEST_REGION
from errors import MablopError

BASE_NOW = 1_800_000_000  # epoch seconds
THROTTLE_KEY = "users/_auth/throttle.json"

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# --- helpers ----------------------------------------------------------------------------


@pytest.fixture
def fast_pbkdf2(monkeypatch):
    original = auth_crypto.hash_password

    def fast_hash(password, iterations=4):
        return original(password, iterations=4)

    monkeypatch.setattr(auth_service.auth_crypto, "hash_password", fast_hash)


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _snapshot() -> dict:
    """Every object in the bucket: key -> bytes."""
    client = boto3.client("s3", region_name=TEST_REGION)
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = client.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
    return out


def _login_entries() -> dict:
    data = _snapshot().get(THROTTLE_KEY)
    return json.loads(data)["login"] if data else {}


def _is_blocked(login_id, now_s) -> bool:
    try:
        auth_throttle.check_login(login_id, now_s)
    except MablopError as exc:
        assert exc.code == "rate_limited"
        return True
    return False


# --- Part 1: the generator ---------------------------------------------------------------


@_SETTINGS
@given(rounds=st.integers(min_value=1, max_value=40))
def test_generated_temp_passwords_use_the_alphabet_and_meet_the_policy(rounds):
    """Validates: Requirements 3.5, 11.6"""
    alphabet = set(auth_crypto.TEMP_PASSWORD_ALPHABET)
    assert not alphabet & set("0O1lI")  # look-alike characters are excluded
    for _ in range(rounds):
        temporary = auth_crypto.generate_temp_password()
        assert isinstance(temporary, str)
        assert len(temporary) == 16
        assert set(temporary) <= alphabet
        assert 10 <= len(temporary) <= 128  # password policy (Req 3.5)


# --- Part 2: admin-reset-password -------------------------------------------------------


@_SETTINGS
@given(
    status=st.sampled_from(["pending", "active", "disabled"]),
    target_fails=st.integers(min_value=0, max_value=5),
    other_fails=st.integers(min_value=0, max_value=4),
    calls=st.integers(min_value=1, max_value=3),
    offset=st.integers(min_value=0, max_value=100_000),
)
def test_admin_reset_password_issues_a_one_time_hash_only_temporary_password(
    s3_bucket, create_user, fast_pbkdf2, capsys, status, target_fails, other_fails, calls, offset
):
    """Validates: Requirements 3.5, 11.6"""
    _wipe_bucket()
    target = create_user("member-target", status=status)
    other = create_user("member-other", status="active")
    start = BASE_NOW + offset
    for i in range(target_fails):
        auth_throttle.record_failure("member-target", start - 100 + i)
    for i in range(other_fails):
        auth_throttle.record_failure("member-other", start - 100 + i)
    if target_fails >= auth_throttle.LOGIN_FAIL_LIMIT:
        assert _is_blocked("member-target", start)
    other_entry_before = _login_entries().get("member-other")
    assert (other_entry_before is not None) == (other_fails > 0)
    capsys.readouterr()  # drop output of earlier steps

    issued = []
    previous_changed_at = target["credentialsChangedAt"]
    for call_index in range(calls):
        now_s = start + call_index * 10
        result = auth_service.admin_reset_password({"userId": target["userId"]}, None, now=now_s)

        # the plaintext leaves only through the response
        assert set(result) == {"userId", "temporaryPassword"}
        assert result["userId"] == target["userId"]
        temporary = result["temporaryPassword"]
        assert len(temporary) == 16
        assert set(temporary) <= set(auth_crypto.TEMP_PASSWORD_ALPHABET)
        assert 10 <= len(temporary) <= 128

        # the stored record keeps only a hash that verifies the temporary password
        snapshot = _snapshot()
        stored = json.loads(snapshot[f"users/{target['userId']}/account.json"])
        assert auth_crypto.verify_password(temporary, stored["password"]) is True
        assert set(stored["password"]) == {"alg", "iterations", "salt", "hash"}
        assert stored["credentialsChangedAt"] == auth_crypto.to_iso_ms(now_s * 1000)
        assert stored["credentialsChangedAt"] != previous_changed_at
        previous_changed_at = stored["credentialsChangedAt"]
        # nothing else of the record changes
        for field in ("v", "userId", "loginId", "name", "role", "status", "createdAt", "approvedAt"):
            assert stored[field] == target[field]

        # no S3 object (accounts, lookups, throttle state, ...) and no log line holds a plaintext
        for earlier in issued + [temporary]:
            for key, data in snapshot.items():
                assert earlier.encode("utf-8") not in data, key
        captured = capsys.readouterr()
        for earlier in issued + [temporary]:
            assert earlier not in captured.out
            assert earlier not in captured.err
        assert captured.out.strip() != ""  # the audit line was written
        for line in captured.out.strip().splitlines():
            entry = json.loads(line)
            assert entry == {
                "event": "admin-reset-password",
                "outcome": "success",
                "userId": target["userId"],
            }

        # the failure counter of the target loginId is gone; other loginIds keep theirs
        entries = _login_entries()
        assert "member-target" not in entries
        assert entries.get("member-other") == other_entry_before
        assert _is_blocked("member-target", now_s) is False

        # a different value on every call; older temporary passwords no longer verify
        assert temporary not in issued
        for earlier in issued:
            assert auth_crypto.verify_password(earlier, stored["password"]) is False
        issued.append(temporary)

    assert len(set(issued)) == len(issued)
    # the other member's account is untouched
    other_stored = json.loads(_snapshot()[f"users/{other['userId']}/account.json"])
    assert other_stored == other

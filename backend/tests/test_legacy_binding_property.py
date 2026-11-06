"""Property test: legacy binding happens once and is idempotent
(Req 9.12, 9.14, 9.16, 9.17, 9.18, 9.19; Property 33).

Random sequences of `admin-approve-user` and `migrate-legacy-data` calls run against
auth_service on a moto S3 bucket that holds legacy objects, and are compared with a small
reference model of the Legacy_Binding_Record. A spy around legacy_migrator.run counts the runs.
Messages are compared with literals. Production code is not modified.

Feature: user-auth-admin, Property 33: Legacy binding happens once and is idempotent
"""
import json

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_service
import legacy_migrator
from conftest import TEST_BUCKET, TEST_REGION
from errors import MablopError

BASE_NOW = 1_800_000_000  # epoch seconds
OWNER = "jhe8888"
BINDING_KEY = "users/_auth/legacy-owner.json"

ALREADY_BOUND = "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다."
SUCCEEDED = "기존 학습 데이터와 초안을 연결했습니다."
FAILED = (
    "기존 데이터 연결에 실패했습니다. 승인은 유지되었습니다. "
    "관리자 권한으로 데이터 연결을 다시 실행하세요."
)
NEED_PENDING = "승인 대기 상태의 계정만 승인할 수 있습니다."
BINDING_FIELDS = {"status", "copied", "skipped", "failedKeys", "divergedKeys", "message"}
NOT_APPLICABLE = {
    "status": "not_applicable",
    "copied": 0,
    "skipped": 0,
    "failedKeys": [],
    "divergedKeys": [],
    "message": None,
}

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies -------------------------------------------------------------------------

_steps = st.lists(
    st.one_of(
        st.tuples(st.just("approve"), st.integers(min_value=0, max_value=5)),
        st.tuples(st.just("migrate"), st.integers(min_value=0, max_value=5)),
        st.tuples(st.just("migrate_default"), st.just(0)),
    ),
    min_size=1,
    max_size=10,
)


# --- helpers ----------------------------------------------------------------------------


def _client():
    return boto3.client("s3", region_name=TEST_REGION)


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _snapshot() -> dict:
    """Every object in the bucket: key -> bytes."""
    client = _client()
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = client.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
    return out


def _seed_legacy(raw_count: int, draft_count: int) -> dict:
    """Put legacy objects at the bucket root; return {relative path: bytes}."""
    legacy = {}
    for i in range(raw_count):
        legacy[f"raw-posts/p{i}.txt"] = f"post-{i}".encode()
    for i in range(draft_count):
        legacy[f"generated-posts/d{i}.md"] = f"# draft {i}".encode()
    legacy["embeddings/index.npy"] = b"npy-bytes"
    legacy["metadata/learning-status.json"] = json.dumps(
        {"processedPosts": 9, "chunks": 31}
    ).encode()
    client = _client()
    for key, body in legacy.items():
        client.put_object(Bucket=TEST_BUCKET, Key=key, Body=body)
    client.put_object(Bucket=TEST_BUCKET, Key="images/legacy.png", Body=b"img")  # never copied
    return legacy


def _user_keys(snapshot: dict, user_id: str) -> set:
    prefix = f"users/{user_id}/"
    return {key[len(prefix):] for key in snapshot if key.startswith(prefix)}


@pytest.fixture
def run_spy(monkeypatch):
    """Record every legacy_migrator.run(target, now) call made through auth_service."""
    calls = []
    original = legacy_migrator.run

    def spy(target, now=None):
        calls.append(target)
        return original(target, now)

    monkeypatch.setattr(auth_service.legacy_migrator, "run", spy)
    return calls


# --- the property ------------------------------------------------------------------------


@_SETTINGS
@given(
    raw_count=st.integers(min_value=1, max_value=3),
    draft_count=st.integers(min_value=0, max_value=2),
    member_count=st.integers(min_value=1, max_value=3),
    owner_present=st.booleans(),
    steps=_steps,
)
def test_legacy_binding_happens_once_and_is_idempotent(
    s3_bucket, create_user, run_spy, raw_count, draft_count, member_count, owner_present, steps
):
    """Validates: Requirements 9.12, 9.14, 9.16, 9.17, 9.18, 9.19"""
    _wipe_bucket()
    run_spy.clear()
    legacy = _seed_legacy(raw_count, draft_count)
    total = len(legacy)
    original_root = {k: v for k, v in _snapshot().items() if not k.startswith("users/")}

    login_ids = ([OWNER] if owner_present else []) + [f"member{i}" for i in range(member_count)]
    users = []  # (userId, loginId)
    status = {}
    for login_id in login_ids:
        record = create_user(login_id, status="pending")
        users.append((record["userId"], login_id))
        status[record["userId"]] = "pending"
    owner_id = users[0][0] if owner_present else None

    bound = None  # userId named by the Legacy_Binding_Record
    binding_record = None  # raw bytes of the record, fixed at creation

    for step_index, (kind, index) in enumerate(steps):
        now_s = BASE_NOW + step_index * 10
        if kind == "migrate_default" and not owner_present:
            kind, index = "migrate", 0
        user_id, login_id = users[index % len(users)]
        runs_before = len(run_spy)

        if kind == "approve":
            body = {"userId": user_id}
            if status[user_id] != "pending":
                with pytest.raises(MablopError) as info:
                    auth_service.admin_approve_user(body, None, now=now_s)
                assert info.value.code == "bad_request" and info.value.message == NEED_PENDING
                assert len(run_spy) == runs_before  # no second run from a second click
                continue
            result = auth_service.admin_approve_user(body, None, now=now_s)
            status[user_id] = "active"
            assert result["userId"] == user_id and result["status"] == "active"
            binding = result["legacyBinding"]
            assert set(binding) == BINDING_FIELDS
            if login_id != OWNER:
                assert binding == NOT_APPLICABLE
                assert len(run_spy) == runs_before  # nothing copied for other loginIds
            else:
                assert run_spy[runs_before:] == [user_id]  # exactly one run, for this user
                outcome = "first" if bound is None else ("same" if bound == user_id else "other")
                if outcome == "other":
                    assert binding == {**NOT_APPLICABLE, "status": "already_bound", "message": ALREADY_BOUND}
                else:
                    assert binding["status"] == "succeeded" and binding["message"] == SUCCEEDED
                    assert binding["failedKeys"] == [] and binding["divergedKeys"] == []
                    expected = (total, 0) if outcome == "first" else (0, total)
                    assert (binding["copied"], binding["skipped"]) == expected
                    bound = user_id
        else:
            if kind == "migrate_default":
                body, target = {}, owner_id
            else:
                body, target = {"targetUserId": user_id}, user_id
            if bound is not None and bound != target:
                with pytest.raises(MablopError) as info:
                    auth_service.migrate_legacy_data(body, None, now=now_s)
                assert info.value.code == "forbidden" and info.value.status == 403
                assert info.value.message == ALREADY_BOUND
            else:
                first = bound is None
                result = auth_service.migrate_legacy_data(body, None, now=now_s)
                migration = result["migration"]
                assert migration["status"] == "succeeded"
                expected = (total, 0) if first else (0, total)
                assert (migration["copied"], migration["skipped"]) == expected
                assert migration["failedKeys"] == [] and migration["divergedKeys"] == []
                bound = target
            assert run_spy[runs_before:] == [target]

        # --- invariants after every step ---------------------------------------------------
        snapshot = _snapshot()
        for uid, _ in users:  # statuses never regress, including after a migrate
            account = json.loads(snapshot[f"users/{uid}/account.json"])
            assert account["status"] == status[uid]
        if bound is None:
            assert BINDING_KEY not in snapshot
        else:
            raw = snapshot[BINDING_KEY]
            binding_record = raw if binding_record is None else binding_record
            assert raw == binding_record  # never changes after creation
            assert json.loads(raw)["userId"] == bound
        for uid, _ in users:
            keys = _user_keys(snapshot, uid)
            if uid == bound:
                assert keys == {"account.json", "metadata/migration.json", *legacy}
                for rel, body_bytes in legacy.items():
                    assert snapshot[f"users/{uid}/{rel}"] == body_bytes
            else:
                assert keys == {"account.json"}  # other users received nothing
        assert {k: v for k, v in snapshot.items() if not k.startswith("users/")} == original_root


# --- failure examples ---------------------------------------------------------------------


def _prepare_owner(create_user, login_id=OWNER):
    legacy = _seed_legacy(2, 1)
    record = create_user(login_id, status="pending")
    return legacy, record["userId"]


def _account_status(user_id: str) -> str:
    data = _client().get_object(Bucket=TEST_BUCKET, Key=f"users/{user_id}/account.json")["Body"].read()
    return json.loads(data)["status"]


def test_approval_stays_active_when_migrator_raises(s3_bucket, create_user, monkeypatch):
    """Validates: Requirements 9.19"""
    _, owner = _prepare_owner(create_user)

    def boom(target, now=None):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(auth_service.legacy_migrator, "run", boom)
    result = auth_service.admin_approve_user({"userId": owner}, None, now=BASE_NOW)
    assert result["status"] == "active"
    assert result["legacyBinding"] == {**NOT_APPLICABLE, "status": "failed", "message": FAILED}
    assert "secret-internal-detail" not in json.dumps(result)
    assert _account_status(owner) == "active"


def test_failed_copy_keeps_approval_and_retry_for_same_user_succeeds(s3_bucket, s3_calls, create_user):
    """Validates: Requirements 9.14, 9.16, 9.17, 9.19"""
    legacy, owner = _prepare_owner(create_user)
    other = create_user("member0", status="pending")["userId"]
    failing = "raw-posts/p0.txt"
    s3_calls.fail_on("CopyObject", f"users/{owner}/{failing}")

    result = auth_service.admin_approve_user({"userId": owner}, None, now=BASE_NOW)
    binding = result["legacyBinding"]
    assert result["status"] == "active" and _account_status(owner) == "active"
    assert binding["status"] == "failed" and binding["message"] == FAILED
    assert failing in binding["failedKeys"]
    assert json.loads(_snapshot()[BINDING_KEY])["userId"] == owner  # the failed run still bound

    # another user is refused while the record names the owner
    with pytest.raises(MablopError) as info:
        auth_service.migrate_legacy_data({"targetUserId": other}, None, now=BASE_NOW + 1)
    assert info.value.status == 403 and info.value.message == ALREADY_BOUND
    assert _user_keys(_snapshot(), other) == {"account.json"}

    # the retry for the bound user completes the copy; a further retry changes nothing
    s3_calls.clear_failures()
    retry = auth_service.migrate_legacy_data({"targetUserId": owner}, None, now=BASE_NOW + 2)["migration"]
    assert retry["status"] == "succeeded" and retry["failedKeys"] == []
    assert retry["copied"] >= 1 and retry["copied"] + retry["skipped"] == len(legacy)
    state = _snapshot()
    again = auth_service.migrate_legacy_data({}, None, now=BASE_NOW + 3)["migration"]
    assert (again["status"], again["copied"], again["skipped"]) == ("succeeded", 0, len(legacy))
    after = _snapshot()
    assert {k: v for k, v in after.items() if not k.endswith("migration.json")} == {
        k: v for k, v in state.items() if not k.endswith("migration.json")
    }
    for rel, body_bytes in legacy.items():
        assert after[f"users/{owner}/{rel}"] == body_bytes

"""Property test: administrator actions follow the status state machine
(Req 11.2, 11.3, 11.4, 11.5, 11.7, 11.8, 11.9; Property 20).

Random action sequences run against auth_service.admin_* on a moto S3 bucket and are compared
with a small reference model of the state machine. Error messages are compared with literals.
PBKDF2 is replaced by a low-iteration wrapper. Production code is not modified.

Feature: user-auth-admin, Property 20: Administrator actions follow the status state machine
"""
import copy
import json
from datetime import datetime, timezone

import boto3
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
from conftest import TEST_BUCKET, TEST_REGION
from errors import MablopError

BASE_NOW = 1_800_000_000  # epoch seconds
STEP = 3601  # longer than the register throttle window

ADMIN_TARGET = "관리자 계정은 변경할 수 없습니다."
NOT_FOUND = "대상 사용자를 찾을 수 없습니다."
NEED = {
    "approve": "승인 대기 상태의 계정만 승인할 수 있습니다.",
    "reject": "승인 대기 상태의 계정만 거절할 수 있습니다.",
    "disable": "활성 상태의 계정만 비활성화할 수 있습니다.",
    "enable": "비활성 상태의 계정만 재활성화할 수 있습니다.",
}
REQUIRED_STATUS_WORD = {
    "approve": "승인 대기",
    "reject": "승인 대기",
    "disable": "활성",
    "enable": "비활성",
}
REQUIRED_STATUS = {
    "approve": "pending",
    "reject": "pending",
    "disable": "active",
    "enable": "disabled",
}
ACTIONS = ["approve", "reject", "disable", "enable", "reset"]
FUNCTIONS = {
    "approve": "admin_approve_user",
    "reject": "admin_reject_user",
    "disable": "admin_disable_user",
    "enable": "admin_enable_user",
    "reset": "admin_reset_password",
}
ABSENT = object()
MALFORMED = [
    ABSENT,
    None,
    123,
    "",
    "u_xyz",
    "U_0123456789ABCDEF",
    "u_0123456789abcdef0",
    "../account",
    ["u_0123456789abcdef"],
]

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies -------------------------------------------------------------------------

_initial_states = st.lists(
    st.sampled_from(["pending", "active", "disabled", "admin"]), min_size=1, max_size=5
)
_targets = st.one_of(
    st.tuples(st.just("user"), st.integers(min_value=0, max_value=4)),
    st.tuples(
        st.just("missing"), st.text(alphabet="0123456789abcdef", min_size=16, max_size=16)
    ),
    st.tuples(st.just("malformed"), st.sampled_from(MALFORMED)),
)
_steps = st.lists(st.tuples(st.sampled_from(ACTIONS), _targets), min_size=1, max_size=12)


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


def _accounts(snapshot: dict) -> dict:
    return {
        json.loads(data)["userId"]: json.loads(data)
        for key, data in snapshot.items()
        if key.startswith("users/u_") and key.endswith("/account.json")
    }


def _lookups(snapshot: dict) -> set:
    return {key for key in snapshot if key.startswith("users/_auth/login/")}


def _iso(now_s: int) -> str:
    return datetime.fromtimestamp(now_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def _body_for(target, user_id):
    kind, value = target
    if kind == "malformed" and value is ABSENT:
        return {}
    return {"userId": user_id}


def _expected(model: dict, action: str, user_id, now_s: int):
    """Reference state machine. Returns (outcome, model_changes) without mutating the model.

    outcome is ("ok", response) or ("err", code, status, message).
    """
    record = model.get(user_id) if isinstance(user_id, str) else None
    if record is None:
        return ("err", "not_found", 404, NOT_FOUND)
    if record["role"] == "admin":
        return ("err", "bad_request", 400, ADMIN_TARGET)
    if action == "reset":
        return ("ok", {"userId": user_id})
    if record["status"] != REQUIRED_STATUS[action]:
        return ("err", "bad_request", 400, NEED[action])
    response = {
        "approve": {"userId": user_id, "status": "active"},
        "reject": {"userId": user_id, "removed": True},
        "disable": {"userId": user_id, "status": "disabled"},
        "enable": {"userId": user_id, "status": "active"},
    }[action]
    return ("ok", response)


def _apply_to_model(model: dict, action: str, user_id: str, now_s: int) -> None:
    record = model[user_id]
    if action == "approve":
        record["status"] = "active"
        record["approvedAt"] = _iso(now_s)
    elif action == "reject":
        del model[user_id]
    elif action == "disable":
        record["status"] = "disabled"
        record["credentialsChangedAt"] = _iso(now_s)
    elif action == "enable":
        record["status"] = "active"
    elif action == "reset":
        record["credentialsChangedAt"] = _iso(now_s)


# --- the property ------------------------------------------------------------------------


@_SETTINGS
@given(states=_initial_states, steps=_steps)
def test_admin_actions_follow_the_status_state_machine(
    s3_bucket, create_user, fast_pbkdf2, states, steps
):
    """Validates: Requirements 11.2, 11.3, 11.4, 11.5, 11.7, 11.8, 11.9"""
    _wipe_bucket()
    pool = []  # original userIds by index
    model = {}
    login_ids = {}
    for index, state in enumerate(states):
        role, status = ("admin", "active") if state == "admin" else ("member", state)
        record = create_user(f"member{index}", role=role, status=status)
        pool.append(record["userId"])
        model[record["userId"]] = copy.deepcopy(record)
        login_ids[record["userId"]] = record["loginId"]
    rejected = []

    for step_index, (action, target) in enumerate(steps):
        kind, value = target
        if kind == "user":
            user_id = pool[value % len(pool)]
        elif kind == "missing":
            user_id = "u_" + value
            assume(user_id not in login_ids)
        else:
            user_id = None if value is ABSENT else value
        now_s = BASE_NOW + step_index * 10
        expected = _expected(model, action, user_id, now_s)
        before = _snapshot()
        func = getattr(auth_service, FUNCTIONS[action])

        if expected[0] == "err":
            _, code, status_code, message = expected
            with pytest.raises(MablopError) as info:
                func(_body_for(target, user_id), None, now=now_s)
            assert info.value.code == code
            assert info.value.status == status_code
            assert info.value.message == message  # literal comparison
            if action in NEED and code == "bad_request" and message == NEED[action]:
                assert REQUIRED_STATUS_WORD[action] in message
            assert _snapshot() == before  # a failed action changes nothing
            continue

        result = func(_body_for(target, user_id), None, now=now_s)
        after = _snapshot()
        accounts = _accounts(after)

        if action == "reset":
            temporary = result.pop("temporaryPassword")
            assert result == expected[1]
            assert isinstance(temporary, str) and len(temporary) == 16
            stored = accounts[user_id]
            assert auth_crypto.verify_password(temporary, stored["password"]) is True
            assert stored["password"] != model[user_id]["password"]
            assert temporary not in json.dumps(stored)  # only the hash is stored
            model[user_id]["password"] = stored["password"]
        elif action == "approve":
            binding = result.pop("legacyBinding")
            assert binding == {
                "status": "not_applicable",
                "copied": 0,
                "skipped": 0,
                "failedKeys": [],
                "divergedKeys": [],
                "message": None,
            }
            assert result == expected[1]
        else:
            assert result == expected[1]

        _apply_to_model(model, action, user_id, now_s)
        if action == "reject":
            rejected.append(login_ids[user_id])

        # stored accounts equal the model exactly; the lookups equal the surviving loginIds
        assert accounts == model
        assert _lookups(after) == {f"users/_auth/login/{login_ids[u]}.json" for u in model}
        assert not (before.keys() - after.keys() - {f"users/{user_id}/account.json"}
                    - {f"users/_auth/login/{login_ids[user_id]}.json"})

    # a rejected loginId can register again as a fresh pending member
    for order, login_id in enumerate(rejected):
        assert login_id not in {r["loginId"] for r in model.values()}
        result = auth_service.register(
            {"loginId": login_id, "name": "재가입", "password": "long-enough-password"},
            None,
            now=BASE_NOW + 100_000 + order * STEP,
        )
        assert result == {"message": "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."}
        again = [a for a in _accounts(_snapshot()).values() if a["loginId"] == login_id]
        assert len(again) == 1
        assert again[0]["status"] == "pending" and again[0]["role"] == "member"

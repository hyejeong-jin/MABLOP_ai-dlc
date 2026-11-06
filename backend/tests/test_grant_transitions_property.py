"""Property test: Grant transitions follow the status state machine
(Req 19.8, 19.9, 19.10, 19.11, 19.12, 19.13, 19.14, 19.15; Property 30).

Random action sequences (request / approve / reject / revoke, real and unknown grantIds, real and
unknown userIds) run against grant_service on a moto S3 bucket and are compared with a small
reference model of the state machine. Error messages are compared with literals. `now` is injected
as epoch seconds. Production code is not modified.

Feature: user-auth-admin, Property 30: Grant transitions follow the status state machine
"""
import copy
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import boto3
import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import grant_service
from conftest import TEST_BUCKET, TEST_REGION
from errors import MablopError

BASE_NOW = 1_800_000_000  # epoch seconds
STEP = 10
DAY = 86400

NOT_FOUND_USER = "대상 사용자를 찾을 수 없습니다."
NOT_FOUND_GRANT = "대상 권한을 찾을 수 없습니다."
ALREADY_REQUESTED = "이미 승인 대기 중인 신청이 있습니다."
NEED = {
    "approve": "승인 대기 상태의 권한만 승인할 수 있습니다.",
    "reject": "승인 대기 상태의 권한만 거절할 수 있습니다.",
    "revoke": "승인된 권한만 폐기할 수 있습니다.",
}
REQUIRED_STATUS = {"approve": "requested", "reject": "requested", "revoke": "approved"}
REQUIRED_STATUS_WORD = {"approve": "승인 대기", "reject": "승인 대기", "revoke": "승인된"}
NEW_STATUS = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}
FUNCTIONS = {
    "approve": "admin_approve",
    "reject": "admin_reject",
    "revoke": "admin_revoke",
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
ADMIN_CTX = SimpleNamespace(user_id="u_0123456789abcdef")

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies -------------------------------------------------------------------------

_user_targets = st.one_of(
    st.tuples(st.just("user"), st.integers(min_value=0, max_value=2)),
    st.tuples(st.just("user"), st.integers(min_value=0, max_value=2)),
    st.tuples(
        st.just("missing"), st.text(alphabet="0123456789abcdef", min_size=16, max_size=16)
    ),
    st.tuples(st.just("malformed"), st.sampled_from(MALFORMED)),
)
_grant_refs = st.one_of(
    st.tuples(st.just("real"), st.integers(min_value=0, max_value=30)),
    st.tuples(st.just("real"), st.integers(min_value=0, max_value=30)),
    st.tuples(st.just("unknown"), st.text(alphabet="0123456789abcdef", min_size=16, max_size=16)),
)
_purposes = st.one_of(st.none(), st.text(max_size=20))
_request_steps = st.tuples(
    st.just("request"), st.integers(min_value=0, max_value=2), _purposes
)
_decision_steps = st.tuples(
    st.sampled_from(["approve", "reject", "revoke"]),
    _user_targets,
    _grant_refs,
    st.sampled_from([7, 30, 90, None]),
)
_steps = st.lists(st.one_of(_request_steps, _decision_steps), min_size=1, max_size=15)


# --- helpers ----------------------------------------------------------------------------


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


def _grants_key(user_id: str) -> str:
    return f"users/{user_id}/metadata/grants.json"


def _iso(now_s: int) -> str:
    return datetime.fromtimestamp(now_s, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S") + ".000Z"


def _public(grant: dict) -> dict:
    out = {k: grant[k] for k in ("id", "status", "purpose", "requestedAt", "decidedAt", "expiresAt")}
    out["expired"] = False
    return out


def _find(model: dict, user_id, grant_id):
    if not isinstance(user_id, str) or user_id not in model:
        return None
    return next((g for g in model[user_id] if g["id"] == grant_id), None)


def _assert_error(info, code: str, status: int, message: str):
    assert info.value.code == code
    assert info.value.status == status
    assert info.value.message == message  # literal comparison


# --- the property ------------------------------------------------------------------------


@_SETTINGS
@given(user_count=st.integers(min_value=1, max_value=3), steps=_steps)
def test_grant_transitions_follow_the_status_state_machine(s3_bucket, create_user, user_count, steps):
    """Validates: Requirements 19.8, 19.9, 19.10, 19.11, 19.12, 19.13, 19.14, 19.15"""
    _wipe_bucket()
    pool = [create_user(f"member{i}")["userId"] for i in range(user_count)]
    model = {user_id: [] for user_id in pool}  # stored Grant dicts per user, in store order
    expires_s = {}  # grant id -> expiry in epoch seconds (approved grants)

    for step_index, step in enumerate(steps):
        now_s = BASE_NOW + step_index * STEP
        before = _snapshot()

        if step[0] == "request":
            _, user_index, purpose = step
            user_id = pool[user_index % len(pool)]
            if any(g["status"] == "requested" for g in model[user_id]):
                with pytest.raises(MablopError) as info:
                    grant_service.request({"purpose": purpose}, SimpleNamespace(user_id=user_id), now=now_s)
                _assert_error(info, "bad_request", 400, ALREADY_REQUESTED)
                assert _snapshot() == before
                continue
            result = grant_service.request(
                {"purpose": purpose}, SimpleNamespace(user_id=user_id), now=now_s
            )
            created = result["grant"]
            expected_grant = {
                "id": created["id"],
                "status": "requested",
                "purpose": purpose if purpose is not None and purpose.strip() else None,
                "requestedAt": _iso(now_s),
                "decidedAt": None,
                "expiresAt": None,
            }
            assert grant_service._GRANT_ID_RE.fullmatch(created["id"])
            assert result == {"grant": {**expected_grant, "expired": False}}
            model[user_id].append(expected_grant)
            after = _snapshot()
            assert {k for k in after if after.get(k) != before.get(k)} == {_grants_key(user_id)}
            assert json.loads(after[_grants_key(user_id)]) == {"v": 1, "grants": model[user_id]}
        else:
            action, target, ref, days = step
            kind, value = target
            if kind == "user":
                user_id = pool[value % len(pool)]
            elif kind == "missing":
                user_id = "u_" + value
                assume(user_id not in pool)
            else:
                user_id = None if value is ABSENT else value

            flat = [g["id"] for grants in model.values() for g in grants]
            if ref[0] == "real" and flat:
                grant_id = flat[ref[1] % len(flat)]
            else:
                grant_id = "g_" + (ref[1] if ref[0] == "unknown" else "0" * 16)
                assume(grant_id not in flat)

            body = {"grantId": grant_id}
            if not (kind == "malformed" and value is ABSENT):
                body["userId"] = user_id
            if days is not None and action == "approve":
                body["expiresInDays"] = days
            func = getattr(grant_service, FUNCTIONS[action])

            target_grant = _find(model, user_id, grant_id)
            if not isinstance(user_id, str) or user_id not in model:
                error = ("not_found", 404, NOT_FOUND_USER)
            elif target_grant is None:
                error = ("not_found", 404, NOT_FOUND_GRANT)
            elif target_grant["status"] != REQUIRED_STATUS[action]:
                error = ("bad_request", 400, NEED[action])
            else:
                error = None

            if error is not None:
                with pytest.raises(MablopError) as info:
                    func(copy.deepcopy(body), ADMIN_CTX, now=now_s)
                _assert_error(info, *error)
                if error[0] == "bad_request":
                    assert REQUIRED_STATUS_WORD[action] in error[2]
                assert _snapshot() == before  # a failed call changes nothing
                continue

            result = func(copy.deepcopy(body), ADMIN_CTX, now=now_s)
            target_grant["status"] = NEW_STATUS[action]
            target_grant["decidedAt"] = _iso(now_s)
            if action == "approve":
                n_days = 30 if days is None else days
                target_grant["expiresAt"] = _iso(now_s + n_days * DAY)
                expires_s[grant_id] = now_s + n_days * DAY
            elif action == "reject":
                assert target_grant["expiresAt"] is None  # reject keeps expiresAt null
            # revoke keeps expiresAt unchanged
            assert result == {"userId": user_id, "grant": {**_public(target_grant), "expired": False}}
            after = _snapshot()
            assert {k for k in after if after.get(k) != before.get(k)} == {_grants_key(user_id)}
            assert set(after) == set(before)
            assert json.loads(after[_grants_key(user_id)]) == {"v": 1, "grants": model[user_id]}

        # every stored Grant_Store equals the model; verify agrees with the status and expiry
        now_ms = now_s * 1000
        for user_id, grants in model.items():
            if grants:
                assert json.loads(_snapshot()[_grants_key(user_id)]) == {"v": 1, "grants": grants}
            for grant in grants:
                expected_ok = grant["status"] == "approved" and expires_s[grant["id"]] * 1000 > now_ms
                assert grant_service.verify(user_id, grant["id"], now_ms) is expected_ok
                if grant["status"] in ("rejected", "revoked", "requested"):
                    assert grant_service.verify(user_id, grant["id"], now_ms) is False

    # list_mine and admin_list reflect the model (requestedAt values are distinct per step)
    now_s = BASE_NOW + len(steps) * STEP
    everything = []
    for user_id, grants in model.items():
        newest_first = sorted(grants, key=lambda g: g["requestedAt"], reverse=True)
        listed = grant_service.list_mine({}, SimpleNamespace(user_id=user_id), now=now_s)["grants"]
        assert [g["id"] for g in listed] == [g["id"] for g in newest_first]
        assert [g["status"] for g in listed] == [g["status"] for g in newest_first]
        everything.extend((user_id, g) for g in grants)
    ordered = sorted(everything, key=lambda pair: pair[1]["requestedAt"], reverse=True)
    ordered.sort(key=lambda pair: pair[1]["status"] != "requested")
    admin_rows = grant_service.admin_list({}, ADMIN_CTX, now=now_s)["grants"]
    assert [(r["userId"], r["id"]) for r in admin_rows] == [(u, g["id"]) for u, g in ordered]

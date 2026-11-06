"""Property test: Grant listings are scoped, ordered, and flag expiry (Req 19.6, 19.7; Property 32).

Random users, Grant_Stores (missing, malformed, or well formed), and Grants are written straight to a
moto S3 bucket. `grant_service.list_mine` and `grant_service.admin_list` are compared with a small
reference model: scope, ordering (requestedAt descending, ties by id descending, `requested` first for
the administrator), the derived `expired` flag, and the documented field set. `now` is injected as
epoch seconds. Production code is not modified.

Feature: user-auth-admin, Property 32: Grant listings are scoped, ordered, and flag expiry
"""
import json
from types import SimpleNamespace

import boto3
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import grant_service
from conftest import TEST_BUCKET, TEST_REGION

BASE_MS = 1_700_000_000_000
PUBLIC_FIELDS = {"id", "status", "purpose", "requestedAt", "decidedAt", "expiresAt", "expired"}
ADMIN_FIELDS = PUBLIC_FIELDS | {"userId", "loginId", "name"}
STATUSES = ["requested", "approved", "rejected", "revoked"]
EXPIRY_OFFSETS_MS = [-86_400_000, -1000, -1, 0, 1, 1000, 86_400_000]
MALFORMED_STORES = [[], "text", 123, None, {"v": 1}, {"v": 1, "grants": "x"}, {"v": 1, "grants": {}}]
GRANT_COUNT_MAX = 40

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies ---------------------------------------------------------------------------

_grant_specs = st.fixed_dictionaries(
    {
        "status": st.sampled_from(STATUSES),
        "requested_k": st.integers(min_value=0, max_value=3),  # few distinct values: many ties
        "expires_offset": st.sampled_from(EXPIRY_OFFSETS_MS),
        "has_expires": st.booleans(),
        "has_decided": st.booleans(),
        "purpose": st.one_of(st.none(), st.text(max_size=15)),
        "extra": st.booleans(),
    }
)
_stores = st.one_of(
    st.just(("missing", None)),
    st.tuples(st.just("malformed"), st.sampled_from(MALFORMED_STORES)),
    st.tuples(st.just("ok"), st.lists(_grant_specs, max_size=6)),
)
_scenarios = st.tuples(
    st.lists(_stores, min_size=1, max_size=4),
    st.integers(min_value=1_700_000_000, max_value=1_900_000_000),  # now (epoch seconds)
    st.permutations(range(GRANT_COUNT_MAX)),  # unique grant ids with arbitrary order
    st.booleans(),  # add a non-dict entry to well formed stores
)


# --- helpers ------------------------------------------------------------------------------


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _put(key: str, obj):
    boto3.client("s3", region_name=TEST_REGION).put_object(
        Bucket=TEST_BUCKET, Key=key, Body=json.dumps(obj).encode("utf-8")
    )


def _snapshot() -> dict:
    client = boto3.client("s3", region_name=TEST_REGION)
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = client.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
    return out


def _grants_key(user_id: str) -> str:
    return f"users/{user_id}/metadata/grants.json"


def _stored_grant(spec: dict, grant_id: str, now_ms: int) -> dict:
    status = spec["status"]
    grant = {
        "id": grant_id,
        "status": status,
        "purpose": spec["purpose"],
        "requestedAt": auth_crypto.to_iso_ms(BASE_MS + spec["requested_k"] * 1000),
        "decidedAt": None,
        "expiresAt": None,
    }
    if status != "requested" and spec["has_decided"]:
        grant["decidedAt"] = auth_crypto.to_iso_ms(BASE_MS + 5000)
    if status == "approved" or (status in ("revoked", "rejected") and spec["has_expires"]):
        grant["expiresAt"] = auth_crypto.to_iso_ms(now_ms + spec["expires_offset"])
    if spec["extra"]:
        grant["password"] = "secret-value"  # fields outside the documented set must not be listed
        grant["internalNote"] = {"k": "v"}
    return grant


def _expected_public(stored: dict, now_ms: int) -> dict:
    out = {name: stored[name] for name in PUBLIC_FIELDS - {"expired"}}
    # ISO strings of one format compare like the instants they denote
    out["expired"] = (
        stored["status"] == "approved" and stored["expiresAt"] <= auth_crypto.to_iso_ms(now_ms)
    )
    return out


def _newest_first(items: list) -> list:
    return sorted(items, key=lambda g: (g["requestedAt"], g["id"]), reverse=True)


def _setup(scenario, make_user_record):
    """Write users and stores; return (users, per-user stored grants, now_s, now_ms)."""
    stores, now_s, id_order, with_junk = scenario
    now_ms = now_s * 1000
    users, stored_by_user, next_id = [], {}, 0
    for index, (kind, content) in enumerate(stores):
        record = make_user_record(f"member{index}", name=f"사용자{index}")
        _put(f"users/{record['userId']}/account.json", record)
        users.append(record)
        stored_by_user[record["userId"]] = []
        if kind == "malformed":
            _put(_grants_key(record["userId"]), content)
        elif kind == "ok":
            grants = []
            for spec in content:
                grants.append(_stored_grant(spec, f"g_{id_order[next_id]:016x}", now_ms))
                next_id += 1
            stored_by_user[record["userId"]] = list(grants)
            raw = list(grants)
            if with_junk:
                raw.append(7)  # a non-dict entry is skipped
            _put(_grants_key(record["userId"]), {"v": 1, "grants": raw})
    return users, stored_by_user, now_s, now_ms


# --- the property -------------------------------------------------------------------------


@_SETTINGS
@given(scenario=_scenarios)
def test_grant_listings_are_scoped_ordered_and_flag_expiry(
    s3_bucket, s3_calls, make_user_record, scenario
):
    """Validates: Requirements 19.6, 19.7"""
    _wipe_bucket()
    users, stored_by_user, now_s, now_ms = _setup(scenario, make_user_record)
    before = _snapshot()

    # grant-list-mine: only the caller's store is read, own Grants only, newest first
    for user in users:
        user_id = user["userId"]
        s3_calls.clear()
        result = grant_service.list_mine({}, SimpleNamespace(user_id=user_id), now=now_s)
        assert s3_calls.touched_keys() == [_grants_key(user_id)]
        assert s3_calls.ops() == ["GetObject"]
        expected = [
            _expected_public(g, now_ms) for g in _newest_first(stored_by_user[user_id])
        ]
        assert result == {"grants": expected}
        for item in result["grants"]:
            assert set(item) == PUBLIC_FIELDS
        mine_ids = {g["id"] for g in stored_by_user[user_id]}
        others = {g["id"] for uid, gs in stored_by_user.items() if uid != user_id for g in gs}
        assert not mine_ids & others
        assert {item["id"] for item in result["grants"]} == mine_ids

    # admin-grant-list: everyone's Grants with owner fields, `requested` first, then newest first
    result = grant_service.admin_list({}, SimpleNamespace(user_id=users[0]["userId"]), now=now_s)
    owners = {u["userId"]: u for u in users}
    rows = []
    for user_id, grants in stored_by_user.items():
        for stored in grants:
            row = _expected_public(stored, now_ms)
            row.update(
                {
                    "userId": user_id,
                    "loginId": owners[user_id]["loginId"],
                    "name": owners[user_id]["name"],
                }
            )
            rows.append(row)
    rows = _newest_first(rows)
    expected_rows = [r for r in rows if r["status"] == "requested"] + [
        r for r in rows if r["status"] != "requested"
    ]
    assert result == {"grants": expected_rows}
    for item in result["grants"]:
        assert set(item) == ADMIN_FIELDS

    # listing never changes stored data (no `expired` is persisted)
    after = _snapshot()
    assert after == before
    for user in users:
        raw = after.get(_grants_key(user["userId"]))
        if raw is not None:
            assert b'"expired"' not in raw


def test_expiry_boundary_is_inclusive(s3_bucket, make_user_record):
    """Validates: Requirements 19.6, 19.7 (expiresAt == now counts as expired)"""
    record = make_user_record("boundary")
    now_s = 1_800_000_000
    now_ms = now_s * 1000
    _put(f"users/{record['userId']}/account.json", record)
    base = {"purpose": None, "requestedAt": auth_crypto.to_iso_ms(BASE_MS), "decidedAt": None}
    grants = [
        {**base, "id": "g_" + "0" * 15 + "1", "status": "approved",
         "expiresAt": auth_crypto.to_iso_ms(now_ms - 1)},
        {**base, "id": "g_" + "0" * 15 + "2", "status": "approved",
         "expiresAt": auth_crypto.to_iso_ms(now_ms)},
        {**base, "id": "g_" + "0" * 15 + "3", "status": "approved",
         "expiresAt": auth_crypto.to_iso_ms(now_ms + 1)},
        {**base, "id": "g_" + "0" * 15 + "4", "status": "revoked",
         "expiresAt": auth_crypto.to_iso_ms(now_ms - 1)},
    ]
    _put(_grants_key(record["userId"]), {"v": 1, "grants": grants})
    ctx = SimpleNamespace(user_id=record["userId"])

    flags = {
        g["id"][-1]: g["expired"] for g in grant_service.list_mine({}, ctx, now=now_s)["grants"]
    }
    assert flags == {"1": True, "2": True, "3": False, "4": False}
    admin_flags = {
        g["id"][-1]: g["expired"] for g in grant_service.admin_list({}, ctx, now=now_s)["grants"]
    }
    assert admin_flags == flags

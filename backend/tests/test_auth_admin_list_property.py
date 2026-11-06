"""Property test: admin-list-users is complete, ordered, and free of secrets
(Req 6.3, 11.1; Property 21).

Users are written straight into a moto S3 bucket with make_user_record, then
auth_service.admin_list_users is called. Production code is not modified.

Feature: user-auth-admin, Property 21: User listing is complete, ordered, and free of secrets
"""
import json

import boto3
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_service
import s3store
from conftest import TEST_BUCKET, TEST_REGION

PUBLIC_FIELDS = {"userId", "loginId", "name", "role", "status", "createdAt", "approvedAt"}
SECRET_KEYS = {"password", "salt", "hash", "iterations", "alg", "credentialsChangedAt"}

_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_user_specs = st.lists(
    st.tuples(
        st.sampled_from(["pending", "active", "disabled"]),
        st.sampled_from(["member", "admin"]),
        # small range so equal createdAt values (ties) occur
        st.integers(min_value=1_700_000_000_000, max_value=1_700_000_000_000 + 50_000),
    ),
    min_size=0,
    max_size=8,
)
_name = st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=30)
_names = st.lists(_name, min_size=8, max_size=8)
_noise = st.booleans()


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _group(user: dict) -> int:
    return 0 if user["status"] == "pending" else 1


@_SETTINGS
@given(specs=_user_specs, names=_names, noise=_noise)
def test_listing_is_complete_ordered_and_secret_free(
    s3_bucket, make_user_record, specs, names, noise
):
    """Validates: Requirements 6.3, 11.1"""
    _wipe_bucket()

    stored = {}
    for index, (status, role, created_ms) in enumerate(specs):
        record = make_user_record(
            f"user{index:02d}",
            name=names[index],
            role=role,
            status=status,
            now_ms=created_ms,
            credentials_changed_at="2024-01-01T00:00:00.000Z",
        )
        s3store.put_json(f"users/{record['userId']}/account.json", record)
        stored[record["userId"]] = record

    if noise:
        # users/_auth/ objects and a user prefix without account.json must never be listed
        s3store.put_json("users/_auth/admin.json", {"userId": "u_" + "0" * 16})
        s3store.put_json("users/_auth/login/user00.json", {"userId": "u_" + "0" * 16})
        s3store.put_json("users/u_" + "f" * 16 + "/metadata/grants.json", {"grants": []})

    result = auth_service.admin_list_users({}, None)
    users = result["users"]

    # (a) every stored user exactly once, nothing else
    ids = [u["userId"] for u in users]
    assert len(ids) == len(set(ids))
    assert set(ids) == set(stored)

    # (c) exactly the seven public fields, values taken from the stored record
    for user in users:
        assert set(user) == PUBLIC_FIELDS
        record = stored[user["userId"]]
        for field in PUBLIC_FIELDS:
            assert user[field] == record[field]

    # (b) pending first, then createdAt ascending inside each group
    order = [(_group(u), u["createdAt"]) for u in users]
    assert order == sorted(order)

    # (d) no secret key names and no hash/salt values anywhere in the serialized response
    text = json.dumps(result, ensure_ascii=False)
    assert not (SECRET_KEYS & set(_all_keys(result)))
    for record in stored.values():
        assert record["password"]["hash"] not in text
        assert record["password"]["salt"] not in text


def _all_keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _all_keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _all_keys(inner)

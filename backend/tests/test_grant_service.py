"""Unit tests for grant_service (Req 19.3-19.5, 19.8-19.15, 19.17-19.19, 20.2, 20.3).

Concrete examples on a moto S3 bucket: Korean message literals (compared with literals, not with
auth_messages), blank purpose, the expiresAt == now boundary, both 404 messages, revoking an
expired Grant, response/stored field sets, one representative flow, and the verify read count.
The state machine and the creation limits are covered by test_grant_transitions_property.py and
test_grant_creation_property.py, so they are not repeated here. Production code is not modified.
"""
import json
from types import SimpleNamespace

import boto3
import pytest

import auth_crypto
import grant_service
from conftest import TEST_BUCKET, TEST_REGION
from errors import MablopError

NOW = 1_800_000_000  # epoch seconds
DAY = 86400
ADMIN_CTX = SimpleNamespace(user_id="u_0123456789abcdef")

PUBLIC_FIELDS = {"id", "status", "purpose", "requestedAt", "decidedAt", "expiresAt", "expired"}
STORED_FIELDS = {"id", "status", "purpose", "requestedAt", "decidedAt", "expiresAt"}

PURPOSE_TOO_LONG = "사용 목적은 100자 이하로 입력하세요."
ALREADY_REQUESTED = "이미 승인 대기 중인 신청이 있습니다."
GRANT_LIMIT = "더 이상 신청할 수 없습니다. 관리자에게 문의하세요."
EXPIRY_INVALID = "만료 기간은 7일, 30일, 90일 중에서 선택하세요."
NOT_FOUND_USER = "대상 사용자를 찾을 수 없습니다."
NOT_FOUND_GRANT = "대상 권한을 찾을 수 없습니다."
NEED_APPROVE = "승인 대기 상태의 권한만 승인할 수 있습니다."
NEED_REJECT = "승인 대기 상태의 권한만 거절할 수 있습니다."
NEED_REVOKE = "승인된 권한만 폐기할 수 있습니다."


# --- helpers ----------------------------------------------------------------------------


def _ctx(user_id):
    return SimpleNamespace(user_id=user_id)


def _grants_key(user_id):
    return f"users/{user_id}/metadata/grants.json"


def _raw(user_id):
    """The stored grants.json bytes of a user, or None."""
    client = boto3.client("s3", region_name=TEST_REGION)
    try:
        return client.get_object(Bucket=TEST_BUCKET, Key=_grants_key(user_id))["Body"].read()
    except client.exceptions.NoSuchKey:
        return None


def _stored(user_id):
    return json.loads(_raw(user_id).decode("utf-8"))


def _seed(user_id, grants):
    import s3store

    s3store.put_json(_grants_key(user_id), {"v": 1, "grants": grants})


def _grant(index, status, *, now_s=NOW, days=30):
    """A stored Grant dict with a valid id; approved/revoked ones carry an expiry."""
    stamp = auth_crypto.to_iso_ms((now_s + index) * 1000)
    has_expiry = status in ("approved", "revoked")
    return {
        "id": "g_" + format(index, "016x"),
        "status": status,
        "purpose": None,
        "requestedAt": stamp,
        "decidedAt": None if status == "requested" else stamp,
        "expiresAt": auth_crypto.to_iso_ms((now_s + days * DAY) * 1000) if has_expiry else None,
    }


def _expect(code, status, message, func, *args, **kwargs):
    with pytest.raises(MablopError) as info:
        func(*args, **kwargs)
    assert info.value.code == code
    assert info.value.status == status
    assert info.value.message == message  # literal comparison


# --- Korean messages --------------------------------------------------------------------


def test_request_while_requested_exists_gives_korean_message_and_keeps_store(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]
    grant_service.request({}, _ctx(user_id), now=NOW)
    before = _raw(user_id)

    _expect("bad_request", 400, ALREADY_REQUESTED, grant_service.request, {}, _ctx(user_id), now=NOW + 1)
    assert _raw(user_id) == before


def test_request_with_twenty_unremovable_grants_gives_limit_message(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]
    _seed(user_id, [_grant(i, "approved") for i in range(20)])  # all approved and unexpired
    before = _raw(user_id)

    _expect("bad_request", 400, GRANT_LIMIT, grant_service.request, {}, _ctx(user_id), now=NOW + 100)
    assert _raw(user_id) == before


@pytest.mark.parametrize("purpose", ["가" * 101, 123, ["목적"], {"a": 1}, True])
def test_request_with_invalid_purpose_gives_korean_message_and_creates_nothing(
    s3_bucket, create_user, purpose
):
    user_id = create_user("alice")["userId"]

    _expect("bad_request", 400, PURPOSE_TOO_LONG, grant_service.request, {"purpose": purpose}, _ctx(user_id), now=NOW)
    assert _raw(user_id) is None


def test_request_accepts_purpose_of_exactly_one_hundred_characters(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]
    purpose = "가" * 100

    result = grant_service.request({"purpose": purpose}, _ctx(user_id), now=NOW)

    assert result["grant"]["purpose"] == purpose
    assert _stored(user_id)["grants"][0]["purpose"] == purpose


@pytest.mark.parametrize("days", [0, 1, 14, 31, 365, -7, True, False, 30.0, 7.5, "30", None, [30]])
def test_approve_with_invalid_expires_in_days_gives_korean_message_and_keeps_grant(
    s3_bucket, create_user, days
):
    user_id = create_user("alice")["userId"]
    grant_id = grant_service.request({}, _ctx(user_id), now=NOW)["grant"]["id"]
    before = _raw(user_id)

    _expect(
        "bad_request", 400, EXPIRY_INVALID, grant_service.admin_approve,
        {"userId": user_id, "grantId": grant_id, "expiresInDays": days}, ADMIN_CTX, now=NOW + 10,
    )
    assert _raw(user_id) == before


@pytest.mark.parametrize(
    "func, status, message",
    [
        ("admin_approve", "approved", NEED_APPROVE),
        ("admin_approve", "rejected", NEED_APPROVE),
        ("admin_reject", "approved", NEED_REJECT),
        ("admin_reject", "revoked", NEED_REJECT),
        ("admin_revoke", "requested", NEED_REVOKE),
        ("admin_revoke", "rejected", NEED_REVOKE),
    ],
)
def test_wrong_status_gives_korean_message_naming_required_status_and_keeps_store(
    s3_bucket, create_user, func, status, message
):
    user_id = create_user("alice")["userId"]
    grant = _grant(1, status)
    _seed(user_id, [grant])
    before = _raw(user_id)

    _expect(
        "bad_request", 400, message, getattr(grant_service, func),
        {"userId": user_id, "grantId": grant["id"]}, ADMIN_CTX, now=NOW + 10,
    )
    assert _raw(user_id) == before


# --- purpose ----------------------------------------------------------------------------


@pytest.mark.parametrize("body", [{}, {"purpose": None}, {"purpose": ""}, {"purpose": "   "}, {"purpose": "\t\n"}])
def test_absent_null_or_blank_purpose_is_stored_as_null(s3_bucket, create_user, body):
    user_id = create_user("alice")["userId"]

    result = grant_service.request(body, _ctx(user_id), now=NOW)

    assert result["grant"]["purpose"] is None
    assert _stored(user_id)["grants"][0]["purpose"] is None


def test_non_blank_purpose_is_stored_as_submitted(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]

    grant_service.request({"purpose": "  블로그 초안  "}, _ctx(user_id), now=NOW)

    assert _stored(user_id)["grants"][0]["purpose"] == "  블로그 초안  "


# --- expiresAt == now boundary ----------------------------------------------------------


def test_grant_expiring_exactly_now_is_not_valid_and_listed_as_expired(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]
    grant_id = grant_service.request({}, _ctx(user_id), now=NOW)["grant"]["id"]
    approved = grant_service.admin_approve(
        {"userId": user_id, "grantId": grant_id, "expiresInDays": 7}, ADMIN_CTX, now=NOW
    )["grant"]
    expires_s = NOW + 7 * DAY
    assert approved["expiresAt"] == auth_crypto.to_iso_ms(expires_s * 1000)

    assert grant_service.verify(user_id, grant_id, expires_s * 1000) is False
    mine = grant_service.list_mine({}, _ctx(user_id), now=expires_s)["grants"]
    assert mine[0]["expired"] is True
    rows = grant_service.admin_list({}, ADMIN_CTX, now=expires_s)["grants"]
    assert rows[0]["expired"] is True


def test_grant_one_millisecond_before_expiry_is_valid_and_not_expired(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]
    grant_id = grant_service.request({}, _ctx(user_id), now=NOW)["grant"]["id"]
    grant_service.admin_approve(
        {"userId": user_id, "grantId": grant_id, "expiresInDays": 7}, ADMIN_CTX, now=NOW
    )
    expires_s = NOW + 7 * DAY

    assert grant_service.verify(user_id, grant_id, expires_s * 1000 - 1) is True
    mine = grant_service.list_mine({}, _ctx(user_id), now=expires_s - 1)["grants"]
    assert mine[0]["expired"] is False


# --- 404 --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "func", ["admin_approve", "admin_reject", "admin_revoke"]
)
def test_unknown_user_gives_404_with_user_message(s3_bucket, create_user, func):
    other = create_user("alice")["userId"]
    grant = _grant(1, "requested")
    _seed(other, [grant])

    _expect(
        "not_found", 404, NOT_FOUND_USER, getattr(grant_service, func),
        {"userId": "u_" + "0" * 16, "grantId": grant["id"]}, ADMIN_CTX, now=NOW,
    )


@pytest.mark.parametrize("user_id", [None, 7, "", "alice", "../account", "U_0123456789ABCDEF"])
def test_malformed_user_id_gives_404_with_user_message(s3_bucket, user_id):
    _expect(
        "not_found", 404, NOT_FOUND_USER, grant_service.admin_approve,
        {"userId": user_id, "grantId": "g_" + "0" * 16}, ADMIN_CTX, now=NOW,
    )


def test_missing_user_id_key_gives_404_with_user_message(s3_bucket):
    _expect("not_found", 404, NOT_FOUND_USER, grant_service.admin_reject, {"grantId": "g_" + "0" * 16}, ADMIN_CTX, now=NOW)


@pytest.mark.parametrize("func", ["admin_approve", "admin_reject", "admin_revoke"])
def test_unknown_grant_gives_404_with_grant_message(s3_bucket, create_user, func):
    user_id = create_user("alice")["userId"]
    _seed(user_id, [_grant(1, "requested")])
    before = _raw(user_id)

    _expect(
        "not_found", 404, NOT_FOUND_GRANT, getattr(grant_service, func),
        {"userId": user_id, "grantId": "g_" + "f" * 16}, ADMIN_CTX, now=NOW,
    )
    assert _raw(user_id) == before


def test_grant_of_another_user_is_not_found_for_the_target_user(s3_bucket, create_user):
    alice = create_user("alice")["userId"]
    bob = create_user("bobby")["userId"]
    grant = _grant(1, "requested")
    _seed(alice, [grant])

    _expect(
        "not_found", 404, NOT_FOUND_GRANT, grant_service.admin_approve,
        {"userId": bob, "grantId": grant["id"]}, ADMIN_CTX, now=NOW,
    )
    assert _stored(alice)["grants"][0]["status"] == "requested"


def test_user_with_no_grant_store_gives_grant_404_not_internal_error(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]

    _expect(
        "not_found", 404, NOT_FOUND_GRANT, grant_service.admin_revoke,
        {"userId": user_id, "grantId": "g_" + "1" * 16}, ADMIN_CTX, now=NOW,
    )
    assert _raw(user_id) is None  # a failed call must not create the store


# --- revoking an expired Grant ----------------------------------------------------------


def test_expired_grant_can_still_be_revoked_and_keeps_expires_at(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]
    grant_id = grant_service.request({}, _ctx(user_id), now=NOW)["grant"]["id"]
    approved = grant_service.admin_approve(
        {"userId": user_id, "grantId": grant_id, "expiresInDays": 7}, ADMIN_CTX, now=NOW
    )["grant"]
    later = NOW + 8 * DAY
    assert grant_service.list_mine({}, _ctx(user_id), now=later)["grants"][0]["expired"] is True

    result = grant_service.admin_revoke({"userId": user_id, "grantId": grant_id}, ADMIN_CTX, now=later)

    assert result["userId"] == user_id
    assert result["grant"]["status"] == "revoked"
    assert result["grant"]["decidedAt"] == auth_crypto.to_iso_ms(later * 1000)
    assert result["grant"]["expiresAt"] == approved["expiresAt"]
    assert result["grant"]["expired"] is False  # only approved Grants count as expired
    assert _stored(user_id)["grants"][0]["status"] == "revoked"


# --- no secrets or extra fields ---------------------------------------------------------


def test_responses_and_stored_objects_contain_only_documented_fields(s3_bucket, create_user):
    record = create_user("alice", name="앨리스")
    user_id = record["userId"]

    requested = grant_service.request({"purpose": "테스트"}, _ctx(user_id), now=NOW)
    assert set(requested) == {"grant"}
    assert set(requested["grant"]) == PUBLIC_FIELDS
    grant_id = requested["grant"]["id"]

    mine = grant_service.list_mine({}, _ctx(user_id), now=NOW)
    assert set(mine) == {"grants"}
    assert all(set(g) == PUBLIC_FIELDS for g in mine["grants"])

    admin_rows = grant_service.admin_list({}, ADMIN_CTX, now=NOW)
    assert set(admin_rows) == {"grants"}
    assert all(set(row) == PUBLIC_FIELDS | {"userId", "loginId", "name"} for row in admin_rows["grants"])
    assert admin_rows["grants"][0]["loginId"] == "alice"
    assert admin_rows["grants"][0]["name"] == "앨리스"

    approved = grant_service.admin_approve({"userId": user_id, "grantId": grant_id}, ADMIN_CTX, now=NOW + 1)
    assert set(approved) == {"userId", "grant"}
    assert set(approved["grant"]) == PUBLIC_FIELDS

    # the user's password hash and salt never appear in any Grant response
    secret_text = json.dumps([requested, mine, admin_rows, approved], ensure_ascii=False)
    for forbidden in ("password", "salt", "iterations", "hash", "token", "secret"):
        assert forbidden not in secret_text.lower()

    stored = _stored(user_id)
    assert set(stored) == {"v", "grants"}
    assert all(set(g) == STORED_FIELDS for g in stored["grants"])  # no `expired` is stored


def test_reject_leaves_expires_at_null_and_returns_documented_fields(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]
    grant_id = grant_service.request({}, _ctx(user_id), now=NOW)["grant"]["id"]

    result = grant_service.admin_reject({"userId": user_id, "grantId": grant_id}, ADMIN_CTX, now=NOW + 5)

    assert set(result["grant"]) == PUBLIC_FIELDS
    assert result["grant"]["status"] == "rejected"
    assert result["grant"]["expiresAt"] is None
    assert result["grant"]["decidedAt"] == auth_crypto.to_iso_ms((NOW + 5) * 1000)


# --- representative flow ----------------------------------------------------------------


def test_request_approve_verify_revoke_flow(s3_bucket, create_user):
    user_id = create_user("alice")["userId"]

    requested = grant_service.request({"purpose": "글 작성"}, _ctx(user_id), now=NOW)["grant"]
    grant_id = requested["id"]
    assert requested["status"] == "requested"
    assert requested["requestedAt"] == auth_crypto.to_iso_ms(NOW * 1000)
    assert requested["decidedAt"] is None and requested["expiresAt"] is None
    assert grant_service.verify(user_id, grant_id, (NOW + 1) * 1000) is False  # not approved yet

    approved = grant_service.admin_approve({"userId": user_id, "grantId": grant_id}, ADMIN_CTX, now=NOW + 60)
    assert approved["grant"]["status"] == "approved"
    assert approved["grant"]["decidedAt"] == auth_crypto.to_iso_ms((NOW + 60) * 1000)
    assert approved["grant"]["expiresAt"] == auth_crypto.to_iso_ms((NOW + 60 + 30 * DAY) * 1000)  # default 30 days
    assert grant_service.verify(user_id, grant_id, (NOW + 61) * 1000) is True
    assert grant_service.verify("u_" + "a" * 16, grant_id, (NOW + 61) * 1000) is False  # another user's store

    listed = grant_service.list_mine({}, _ctx(user_id), now=NOW + 61)["grants"]
    assert [(g["id"], g["status"], g["expired"]) for g in listed] == [(grant_id, "approved", False)]

    revoked = grant_service.admin_revoke({"userId": user_id, "grantId": grant_id}, ADMIN_CTX, now=NOW + 120)
    assert revoked["grant"]["status"] == "revoked"
    assert grant_service.verify(user_id, grant_id, (NOW + 121) * 1000) is False

    final = grant_service.list_mine({}, _ctx(user_id), now=NOW + 121)["grants"]
    assert [(g["id"], g["status"]) for g in final] == [(grant_id, "revoked")]


# --- verify reads -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "grant_id",
    [
        None,
        123,
        "",
        "g_",
        "g_0123456789abcde",  # 15 hex digits
        "g_0123456789abcdef0",  # 17 hex digits
        "g_0123456789ABCDEF",  # upper case
        "g_0123456789abcdeg",  # non-hex
        "G_0123456789abcdef",
        " g_0123456789abcdef",
        "g_0123456789abcdef\n",
        "0123456789abcdef",
        "../g_0123456789abcdef",
    ],
)
def test_verify_with_malformed_grant_id_returns_false_without_reading_s3(s3_bucket, s3_calls, create_user, grant_id):
    user_id = create_user("alice")["userId"]
    s3_calls.clear()

    assert grant_service.verify(user_id, grant_id, NOW * 1000) is False
    assert s3_calls.calls == []


def test_verify_reads_the_grant_store_exactly_once(s3_bucket, s3_calls, create_user):
    user_id = create_user("alice")["userId"]
    grant = _grant(1, "approved")
    _seed(user_id, [_grant(2, "approved"), grant, _grant(3, "requested")])
    s3_calls.clear()

    assert grant_service.verify(user_id, grant["id"], (NOW + 5) * 1000) is True

    assert s3_calls.count("GetObject") == 1
    assert s3_calls.count("GetObject", _grants_key(user_id)) == 1
    assert s3_calls.ops() == ["GetObject"]


@pytest.mark.parametrize("scenario", ["unknown-id", "not-approved", "expired", "no-store"])
def test_verify_false_cases_also_read_exactly_once(s3_bucket, s3_calls, create_user, scenario):
    user_id = create_user("alice")["userId"]
    approved = _grant(1, "approved", days=7)
    if scenario != "no-store":
        _seed(user_id, [approved, _grant(2, "revoked")])
    target = {
        "unknown-id": "g_" + "f" * 16,
        "not-approved": _grant(2, "revoked")["id"],
        "expired": approved["id"],
        "no-store": approved["id"],
    }[scenario]
    now_ms = (NOW + (8 * DAY if scenario == "expired" else 5)) * 1000
    s3_calls.clear()

    assert grant_service.verify(user_id, target, now_ms) is False

    assert s3_calls.ops() == ["GetObject"]
    assert s3_calls.count("GetObject", _grants_key(user_id)) == 1

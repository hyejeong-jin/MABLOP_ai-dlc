"""Property test: Grant creation respects the per-user limits
(Req 19.1, 19.2, 19.3, 19.4, 19.5, 19.17, 19.18; Property 31).

grant_service.request / admin_approve / admin_reject / admin_revoke run against a moto S3 bucket
with an injected `now`, and are compared with a small reference model. Messages are literals.
Production code is not modified.

Feature: user-auth-admin, Property 31: Grant creation respects the per-user limits
"""
import json
import re
from types import SimpleNamespace

import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import grant_service
from conftest import ACTING_USER_ID, TEST_BUCKET, TEST_REGION
from errors import MablopError

BASE_MS = 1_800_000_000_000  # epoch milliseconds
DAY_MS = 24 * 60 * 60 * 1000
ADMIN_ID = "u_aaaaaaaaaaaaaaaa"

PURPOSE_TOO_LONG = "사용 목적은 100자 이하로 입력하세요."
ALREADY_REQUESTED = "이미 승인 대기 중인 신청이 있습니다."
LIMIT_REACHED = "더 이상 신청할 수 없습니다. 관리자에게 문의하세요."
EXPIRY_INVALID = "만료 기간은 7일, 30일, 90일 중에서 선택하세요."
NEED = {
    "approve": "승인 대기 상태의 권한만 승인할 수 있습니다.",
    "reject": "승인 대기 상태의 권한만 거절할 수 있습니다.",
    "revoke": "승인된 권한만 폐기할 수 있습니다.",
}
REQUIRED_STATUS = {"approve": "requested", "reject": "requested", "revoke": "approved"}
STORED_FIELDS = {"id", "status", "purpose", "requestedAt", "decidedAt", "expiresAt"}
ID_RE = re.compile(r"^g_[0-9a-f]{16}$")
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")
ABSENT = object()

GRANTS_KEY = f"users/{ACTING_USER_ID}/metadata/grants.json"
ACTOR = SimpleNamespace(user_id=ACTING_USER_ID)
ADMIN = SimpleNamespace(user_id=ADMIN_ID)

_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies -------------------------------------------------------------------------

_chars = st.characters(blacklist_categories=("Cs",))
_blank = st.text(alphabet=" \t\r\n\u3000\u00a0", min_size=0, max_size=12)
_valid_purpose = st.one_of(
    st.just(ABSENT),
    st.none(),
    _blank,
    st.text(alphabet=_chars, max_size=100),
    st.text(alphabet=_chars, min_size=100, max_size=100),
    st.text(alphabet=st.characters(whitelist_categories=("Lo",)), min_size=100, max_size=100),
)
_too_long_purpose = st.one_of(
    st.text(alphabet=_chars, min_size=101, max_size=160),
    st.text(alphabet=" ", min_size=101, max_size=130),
)
_non_string_purpose = st.sampled_from(
    [0, 1, 123, 1.5, True, False, [], ["목적"], {}, {"a": 1}]
)
_any_purpose = st.one_of(_valid_purpose, _too_long_purpose, _non_string_purpose)

_STATUSES = ["requested", "approved", "rejected", "revoked"]
_invalid_days = st.one_of(
    st.sampled_from(
        [None, True, False, 7.0, 30.0, 90.0, "7", "30", "90", "", 0, 1, 6, 8, 29, 31, 89, 91,
         365, -7, -30, [7], [], {}, {"d": 7}, float("nan")]
    ),
    st.integers().filter(lambda n: n not in (7, 30, 90)),
    st.floats(allow_nan=False).filter(lambda f: f not in (7.0, 30.0, 90.0)),
    st.text(max_size=8),
)


@st.composite
def _seed_grants(draw):
    """Up to 20 Grants of random statuses and times, with distinct ids and requestedAt."""
    now_ms = BASE_MS
    offsets = draw(st.lists(st.integers(0, 400 * 24 * 3600), unique=True, max_size=20))
    ids = draw(
        st.lists(st.text(alphabet="0123456789abcdef", min_size=16, max_size=16),
                 unique=True, min_size=len(offsets), max_size=len(offsets))
    )
    grants = []
    for off, hex_id in zip(offsets, ids):
        status = draw(st.sampled_from(_STATUSES))
        requested_at = auth_crypto.to_iso_ms(now_ms - off * 1000)
        decided_at = expires_at = None
        if status != "requested":
            decided_at = auth_crypto.to_iso_ms(now_ms - off * 1000 + 1000)
        if status in ("approved", "revoked"):
            delta = draw(st.integers(-10 * DAY_MS, 10 * DAY_MS))
            expires_at = auth_crypto.to_iso_ms(now_ms + delta)
        purpose = draw(st.one_of(st.none(), st.text(alphabet=_chars, max_size=20)))
        grants.append(
            {"id": "g_" + hex_id, "status": status, "purpose": purpose,
             "requestedAt": requested_at, "decidedAt": decided_at, "expiresAt": expires_at}
        )
    return grants


@st.composite
def _full_store(draw):
    """Exactly 20 approved/requested Grants with future expiry: nothing is removable."""
    out = []
    for i in range(20):
        out.append(
            {"id": "g_%016x" % (i + 1), "status": draw(st.sampled_from(["approved", "approved", "requested"])),
             "purpose": None, "requestedAt": auth_crypto.to_iso_ms(BASE_MS - (i + 1) * 3600_000),
             "decidedAt": auth_crypto.to_iso_ms(BASE_MS - 1000),
             "expiresAt": auth_crypto.to_iso_ms(BASE_MS + (i + 1) * DAY_MS)}
        )
    for g in out:
        if g["status"] == "requested":
            g["decidedAt"] = None
            g["expiresAt"] = None
    return out


# --- helpers ----------------------------------------------------------------------------


def _s3():
    return boto3.client("s3", region_name=TEST_REGION)


def _seed(grants):
    """Write the Grant_Store of the acting user (None deletes the object)."""
    if grants is None:
        _s3().delete_object(Bucket=TEST_BUCKET, Key=GRANTS_KEY)
        return
    body = json.dumps({"v": 1, "grants": grants}, ensure_ascii=False).encode("utf-8")
    _s3().put_object(Bucket=TEST_BUCKET, Key=GRANTS_KEY, Body=body)


def _raw():
    """(bytes, etag) of the Grant_Store, or None when absent."""
    try:
        resp = _s3().get_object(Bucket=TEST_BUCKET, Key=GRANTS_KEY)
    except ClientError as err:
        if err.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return resp["Body"].read(), resp["ETag"]


def _stored_grants():
    raw = _raw()
    return None if raw is None else json.loads(raw[0].decode("utf-8"))["grants"]


def _body(purpose):
    return {} if purpose is ABSENT else {"purpose": purpose}


def _iso(ms):
    return auth_crypto.to_iso_ms(ms)


def _is_expired(grant, now_ms):
    # Both strings use the fixed-width ISO format, so string order equals time order.
    return grant["status"] == "approved" and grant["expiresAt"] <= _iso(now_ms)


def _expected_request(grants, purpose, now_ms):
    """Reference model: ("error", message) or ("ok", normalized purpose, grants after cleanup)."""
    if purpose is not ABSENT and purpose is not None:
        if not isinstance(purpose, str) or len(purpose) > 100:
            return ("error", PURPOSE_TOO_LONG)
    normalized = None
    if isinstance(purpose, str) and purpose.strip():
        normalized = purpose
    if any(g["status"] == "requested" for g in grants):
        return ("error", ALREADY_REQUESTED)
    remaining = list(grants)
    if len(grants) >= 20:
        removable = [
            g for g in grants if g["status"] in ("rejected", "revoked") or _is_expired(g, now_ms)
        ]
        if not removable:
            return ("error", LIMIT_REACHED)
        oldest = min(removable, key=lambda g: g["requestedAt"])
        remaining = [g for g in grants if g is not oldest]
    return ("ok", normalized, remaining)


def _assert_new_grant(stored_new, response, purpose, now_ms, other_ids):
    assert set(stored_new) == STORED_FIELDS  # no secret value, no `expired` field stored
    assert ID_RE.fullmatch(stored_new["id"])
    assert stored_new["id"] not in other_ids
    assert stored_new["status"] == "requested"
    assert stored_new["purpose"] == purpose
    assert stored_new["requestedAt"] == _iso(now_ms) and ISO_RE.fullmatch(stored_new["requestedAt"])
    assert stored_new["decidedAt"] is None and stored_new["expiresAt"] is None
    assert response == {"grant": {**stored_new, "expired": False}}


@pytest.fixture
def member(create_user):
    return create_user("member31", user_id=ACTING_USER_ID)


@pytest.fixture
def admin_user(create_user):
    return create_user("admin31", user_id=ADMIN_ID, role="admin")


# --- properties -------------------------------------------------------------------------


@_SETTINGS
@given(
    seed=st.one_of(st.none(), _seed_grants(), _full_store()),
    purpose=_any_purpose,
    now_offset=st.integers(0, 3600),
)
def test_request_appends_one_grant_iff_limits_allow(s3_bucket, seed, purpose, now_offset):
    """Req 19.3, 19.4, 19.5, 19.17, 19.18: exactly one Grant is added only when the limits allow."""
    now_ms = BASE_MS + now_offset * 1000
    _seed(seed)
    before = _raw()
    grants = seed or []
    verdict = _expected_request(grants, purpose, now_ms)

    if verdict[0] == "error":
        with pytest.raises(MablopError) as caught:
            grant_service.request(_body(purpose), ACTOR, now=now_ms / 1000)
        assert caught.value.code == "bad_request"
        assert caught.value.message == verdict[1]
        assert _raw() == before  # store is byte-identical (or still absent)
        return

    _, normalized, remaining = verdict
    result = grant_service.request(_body(purpose), ACTOR, now=now_ms / 1000)
    stored = _stored_grants()
    assert len(stored) <= 20
    assert len(stored) == len(remaining) + 1
    assert stored[:-1] == remaining  # only the cleanup candidate was removed, nothing else changed
    _assert_new_grant(stored[-1], result, normalized, now_ms, {g["id"] for g in remaining})


@_SETTINGS
@given(purpose=st.one_of(_too_long_purpose, _non_string_purpose), seed=st.one_of(st.none(), _seed_grants()))
def test_invalid_purpose_is_checked_before_store_state(s3_bucket, purpose, seed):
    """Req 19.4: the purpose is checked first, so it wins over an existing `requested` Grant."""
    _seed(seed)
    before = _raw()
    with pytest.raises(MablopError) as caught:
        grant_service.request(_body(purpose), ACTOR, now=BASE_MS / 1000)
    assert caught.value.code == "bad_request"
    assert caught.value.message == PURPOSE_TOO_LONG
    assert _raw() == before


_step = st.tuples(
    st.sampled_from(["request", "request", "request", "approve", "approve", "reject", "revoke"]),
    _any_purpose,
    st.sampled_from([7, 30, 90]),
    st.integers(0, 40),
    st.one_of(st.integers(1, 3600), st.integers(1, 3_500_000)),
)


@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)
@given(steps=st.lists(_step, min_size=1, max_size=70))
def test_request_sequences_keep_ids_fields_and_cap(s3_bucket, member, admin_user, steps):
    """Req 19.1, 19.2, 19.5, 19.17: ids stay unique, fields stay fixed, the store never exceeds 20."""
    _seed(None)
    model = []
    now_ms = BASE_MS
    for kind, purpose, days, index, advance in steps:
        now_ms += advance * 1000
        before = _raw()
        if kind == "request":
            verdict = _expected_request(model, purpose, now_ms)
            if verdict[0] == "error":
                with pytest.raises(MablopError) as caught:
                    grant_service.request(_body(purpose), ACTOR, now=now_ms / 1000)
                assert (caught.value.code, caught.value.message) == ("bad_request", verdict[1])
                assert _raw() == before
            else:
                _, normalized, remaining = verdict
                result = grant_service.request(_body(purpose), ACTOR, now=now_ms / 1000)
                new = _stored_grants()[-1]
                _assert_new_grant(new, result, normalized, now_ms, {g["id"] for g in remaining})
                model = remaining + [new]
        elif model:
            target = model[index % len(model)]
            body = {"userId": ACTING_USER_ID, "grantId": target["id"]}
            if kind == "approve":
                body["expiresInDays"] = days
            func = getattr(grant_service, "admin_" + kind)
            if target["status"] != REQUIRED_STATUS[kind]:
                with pytest.raises(MablopError) as caught:
                    func(body, ADMIN, now=now_ms / 1000)
                assert (caught.value.code, caught.value.message) == ("bad_request", NEED[kind])
                assert _raw() == before
            else:
                func(body, ADMIN, now=now_ms / 1000)
                updated = dict(target)
                updated["status"] = {"approve": "approved", "reject": "rejected", "revoke": "revoked"}[kind]
                updated["decidedAt"] = _iso(now_ms)
                if kind == "approve":
                    updated["expiresAt"] = _iso(now_ms + days * DAY_MS)
                model = [updated if g is target else g for g in model]
        stored = _stored_grants() or []  # an untouched store is still absent
        assert stored == model
        assert len(stored) <= 20
        assert len({g["id"] for g in stored}) == len(stored)
        assert all(set(g) == STORED_FIELDS and ID_RE.fullmatch(g["id"]) for g in stored)
        assert all(g["status"] in _STATUSES for g in stored)  # "expired" is never stored


@_SETTINGS
@given(days=_invalid_days, extra=_seed_grants())
def test_invalid_expires_in_days_is_rejected_before_store_read(
    s3_calls, s3_bucket, member, admin_user, days, extra
):
    """Req 19.9, 19.10: only the integers 7, 30, 90 pass, checked before the Grant_Store is read."""
    requested = {"id": "g_00000000000000aa", "status": "requested", "purpose": None,
                 "requestedAt": _iso(BASE_MS - 1000), "decidedAt": None, "expiresAt": None}
    seed = [g for g in extra if g["id"] != requested["id"] and g["status"] != "requested"][:19]
    _seed(seed + [requested])
    before = _raw()
    s3_calls.clear()
    body = {"userId": ACTING_USER_ID, "grantId": requested["id"], "expiresInDays": days}
    with pytest.raises(MablopError) as caught:
        grant_service.admin_approve(body, ADMIN, now=BASE_MS / 1000)
    assert (caught.value.code, caught.value.message) == ("bad_request", EXPIRY_INVALID)
    assert GRANTS_KEY not in s3_calls.touched_keys()
    assert _raw() == before


@_SETTINGS
@given(days=st.one_of(st.just(ABSENT), st.sampled_from([7, 30, 90])), now_offset=st.integers(0, 10**6))
def test_valid_expires_in_days_sets_expiry_and_default_is_30(
    s3_bucket, member, admin_user, days, now_offset
):
    """Req 19.8, 19.9: expiresAt is decidedAt + N days; an absent expiresInDays means 30."""
    now_ms = BASE_MS + now_offset * 1000
    _seed(None)
    created = grant_service.request({}, ACTOR, now=BASE_MS / 1000)["grant"]
    body = {"userId": ACTING_USER_ID, "grantId": created["id"]}
    if days is not ABSENT:
        body["expiresInDays"] = days
    grant_service.admin_approve(body, ADMIN, now=now_ms / 1000)
    stored = _stored_grants()[0]
    assert stored["status"] == "approved"
    assert stored["decidedAt"] == _iso(now_ms)
    assert stored["expiresAt"] == _iso(now_ms + (30 if days is ABSENT else days) * DAY_MS)


def test_second_request_is_rejected_and_store_is_unchanged(s3_bucket):
    """Req 19.5: one `requested` Grant at a time."""
    _seed(None)
    grant_service.request({"purpose": "첫 신청"}, ACTOR, now=BASE_MS / 1000)
    before = _raw()
    with pytest.raises(MablopError) as caught:
        grant_service.request({"purpose": "둘째 신청"}, ACTOR, now=BASE_MS / 1000 + 1)
    assert (caught.value.code, caught.value.message) == ("bad_request", ALREADY_REQUESTED)
    assert _raw() == before


def test_full_store_of_unexpired_approved_grants_blocks_until_one_expires(s3_bucket):
    """Req 19.17, 19.18: 20 live approved Grants block; once the oldest expires it is replaced."""
    grants = [
        {"id": "g_%016x" % (i + 1), "status": "approved", "purpose": None,
         "requestedAt": _iso(BASE_MS - (i + 1) * 3600_000), "decidedAt": _iso(BASE_MS - 1000),
         "expiresAt": _iso(BASE_MS + (i + 1) * DAY_MS)}
        for i in range(20)
    ]
    _seed(grants)
    before = _raw()
    with pytest.raises(MablopError) as caught:
        grant_service.request({}, ACTOR, now=BASE_MS / 1000)
    assert (caught.value.code, caught.value.message) == ("bad_request", LIMIT_REACHED)
    assert _raw() == before

    # Grant i=0 (expiresAt BASE+1 day) expires; the removable one with the oldest requestedAt is i=0.
    later = BASE_MS + DAY_MS
    grant_service.request({}, ACTOR, now=later / 1000)
    stored = _stored_grants()
    assert len(stored) == 20
    assert "g_%016x" % 1 not in {g["id"] for g in stored}
    assert stored[-1]["status"] == "requested"

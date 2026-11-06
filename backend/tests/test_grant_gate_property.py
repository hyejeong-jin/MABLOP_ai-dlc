"""Property test: Grant verification gate (Req 20.1, 20.2, 20.3, 20.4, 20.5, 20.6, 20.8, 20.9, 20.10; Property 29).

access_controller.check_grant runs against a moto S3 bucket whose Grant_Stores are written directly.
Random role / action / body / store contents / time are compared with a reference model of the
gate. The s3_calls recorder counts GetObject calls on grants.json: at most one for the acting
user's store, none for any other key. A second property revokes (or lets expire) a Grant between two
calls. Error messages are compared with literals. `now` is injected. Production code is not modified.

Feature: user-auth-admin, Property 29: Grant verification gate
"""
import json
from types import SimpleNamespace

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import auth_crypto
import grant_service
import s3store
from access_controller import RequestContext
from conftest import ACTING_USER_ID, OTHER_USER_ID, TEST_BUCKET, TEST_REGION
from errors import MablopError

GRANT_REQUIRED_MESSAGE = "Bedrock 사용 권한이 필요합니다. 마이페이지에서 신청하세요."
BEDROCK_ACTIONS = ["learn-style", "analyze-images", "generate-draft", "rebuild-from-stored", "get-result"]
NON_BEDROCK_ACTIONS = [
    "issue-presigned-urls",
    "get-learning-status",
    "preview-learning-sources",
    "list-drafts",
    "delete-draft",
    "grant-request",
    "grant-list-mine",
    "get-me",
    "change-password",
    "admin-grant-approve",
    "unknown-action",
]
NOW_S = 1_800_000_000  # epoch seconds, exact in milliseconds
NOW_MS = NOW_S * 1000
DAY_MS = 86_400_000
EXPIRY_OFFSETS_MS = [-DAY_MS, -1000, -1, 0, 1, 1000, DAY_MS]
STATUSES = ["requested", "approved", "rejected", "revoked"]
ABSENT = object()
MALFORMED_IDS = [
    None,
    123,
    "",
    "g_",
    "G_0123456789abcdef",
    "g_0123456789ABCDEF",
    "g_0123456789abcde",
    "g_0123456789abcdef0",
    "x_0123456789abcdef",
    " g_0123456789abcdef",
    "g_0123456789abcdef\n",
    ["g_0123456789abcdef"],
    {"id": "g_0123456789abcdef"},
]

_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies --------------------------------------------------------------------------

_grant_specs = st.lists(
    st.tuples(st.sampled_from(STATUSES), st.sampled_from(EXPIRY_OFFSETS_MS)), min_size=0, max_size=5
)
_store_modes = st.sampled_from(["missing", "present", "present", "present"])
_grant_refs = st.one_of(
    st.tuples(st.just("own"), st.integers(min_value=0, max_value=4)),
    st.tuples(st.just("own"), st.integers(min_value=0, max_value=4)),
    st.tuples(st.just("other"), st.integers(min_value=0, max_value=4)),
    st.tuples(st.just("unknown"), st.text(alphabet="0123456789abcdef", min_size=16, max_size=16)),
    st.tuples(st.just("malformed"), st.sampled_from(MALFORMED_IDS)),
    st.tuples(st.just("absent"), st.none()),
)
_kinds = st.sampled_from([ABSENT, "draft", "learn", "result", None, 5])
_actions = st.sampled_from(BEDROCK_ACTIONS + BEDROCK_ACTIONS + NON_BEDROCK_ACTIONS)
_roles = st.sampled_from(["member", "admin"])

# --- helpers -----------------------------------------------------------------------------


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _grant_id(owner: str, index: int) -> str:
    prefix = "a" if owner == ACTING_USER_ID else "b"
    return "g_" + prefix * 12 + f"{index:04x}"


def _grants_key(user_id: str) -> str:
    return f"users/{user_id}/metadata/grants.json"


def _stored_grants(owner: str, specs) -> list:
    out = []
    for index, (status, offset_ms) in enumerate(specs):
        approved_like = status in ("approved", "revoked")
        out.append(
            {
                "id": _grant_id(owner, index),
                "status": status,
                "purpose": None,
                "requestedAt": auth_crypto.to_iso_ms(NOW_MS - 10 * DAY_MS),
                "decidedAt": None if status == "requested" else auth_crypto.to_iso_ms(NOW_MS - 5 * DAY_MS),
                "expiresAt": auth_crypto.to_iso_ms(NOW_MS + offset_ms) if approved_like else None,
            }
        )
    return out


def _write_store(user_id: str, grants: list):
    s3store.put_json(_grants_key(user_id), {"v": 1, "grants": grants})


def _is_valid(grant: dict) -> bool:
    return (
        grant["status"] == "approved"
        and auth_crypto.from_iso_ms(grant["expiresAt"]) > NOW_MS
    )


def _needs_grant(role, action, kind, body_is_dict=True) -> bool:
    if role != "member" or action not in BEDROCK_ACTIONS:
        return False
    return not (action == "get-result" and body_is_dict and kind == "draft")


def _ctx(role: str, action: str) -> RequestContext:
    return RequestContext(action, ACTING_USER_ID, role, {"userId": ACTING_USER_ID, "role": role})


def _gets(recorder, key=None):
    return recorder.count("GetObject", key)


# --- Property 29: the gate matches the reference model --------------------------------


@_SETTINGS
@given(
    role=_roles,
    action=_actions,
    kind=_kinds,
    ref=_grant_refs,
    mode=_store_modes,
    own_specs=_grant_specs,
    other_specs=_grant_specs,
)
def test_grant_gate_passes_exactly_when_the_model_says_so(
    s3_bucket, s3_calls, capsys, role, action, kind, ref, mode, own_specs, other_specs
):
    """Validates: Requirements 20.1, 20.2, 20.3, 20.4, 20.6, 20.8, 20.9, 20.10"""
    _wipe_bucket()
    own = _stored_grants(ACTING_USER_ID, own_specs) if mode == "present" else []
    other = _stored_grants(OTHER_USER_ID, other_specs)
    if mode == "present":
        _write_store(ACTING_USER_ID, own)
    _write_store(OTHER_USER_ID, other)

    kind_part, value = ref
    if kind_part == "own":
        grant_id = _grant_id(ACTING_USER_ID, value)
    elif kind_part == "other":
        grant_id = _grant_id(OTHER_USER_ID, value)
    elif kind_part == "unknown":
        grant_id = "g_" + value
    else:
        grant_id = value
    body = {} if kind_part == "absent" else {"grantId": grant_id}
    if kind is not ABSENT:
        body["kind"] = kind

    needs = _needs_grant(role, action, kind)
    well_formed = isinstance(grant_id, str) and grant_service._GRANT_ID_RE.fullmatch(grant_id) is not None
    own_match = next((g for g in own if g["id"] == grant_id), None) if well_formed else None
    should_pass = (not needs) or (own_match is not None and _is_valid(own_match))

    s3_calls.clear()
    capsys.readouterr()
    ctx = _ctx(role, action)
    if should_pass:
        assert access_controller.check_grant(ctx, body, now=NOW_S) is None
        assert capsys.readouterr().out == ""  # nothing is logged on success
    else:
        with pytest.raises(MablopError) as info:
            access_controller.check_grant(ctx, body, now=NOW_S)
        assert info.value.code == "forbidden"
        assert info.value.status == 403
        assert info.value.message == GRANT_REQUIRED_MESSAGE  # identical for every cause (20.4)
        log_lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
        assert log_lines == [{"event": "grant", "outcome": "blocked", "userId": ACTING_USER_ID}]

    # reads: none unless the Grant check applies and the id is well formed; never another user's store
    assert _gets(s3_calls, _grants_key(OTHER_USER_ID)) == 0
    assert _gets(s3_calls, _grants_key(ACTING_USER_ID)) <= 1
    assert len(s3_calls.calls) == _gets(s3_calls, _grants_key(ACTING_USER_ID))
    if not needs or not well_formed:
        assert s3_calls.calls == []
    else:
        assert _gets(s3_calls, _grants_key(ACTING_USER_ID)) == 1
    assert s3_calls.writes() == []


@_SETTINGS
@given(
    action=_actions,
    own_specs=_grant_specs,
    ref=_grant_refs,
)
def test_non_dict_body_needs_a_grant_only_for_member_bedrock_actions(
    s3_bucket, s3_calls, action, own_specs, ref
):
    """Validates: Requirements 20.1, 20.4, 20.7, 20.9"""
    _wipe_bucket()
    _write_store(ACTING_USER_ID, _stored_grants(ACTING_USER_ID, own_specs))
    s3_calls.clear()
    body = [ref[1]]  # a body that is not an object carries no grantId

    for role in ("member", "admin"):
        ctx = _ctx(role, action)
        if _needs_grant(role, action, None, body_is_dict=False):
            with pytest.raises(MablopError) as info:
                access_controller.check_grant(ctx, body, now=NOW_S)
            assert (info.value.code, info.value.status) == ("forbidden", 403)
            assert info.value.message == GRANT_REQUIRED_MESSAGE
        else:
            assert access_controller.check_grant(ctx, body, now=NOW_S) is None
    assert s3_calls.calls == []  # without a grantId nothing is read


# --- a Grant is judged per call (revocation, expiry between calls) ---------------------


@_SETTINGS
@given(
    days=st.sampled_from([7, 30, 90]),
    action=st.sampled_from(BEDROCK_ACTIONS),
    gap_s=st.integers(min_value=0, max_value=100 * 86400),
)
def test_revocation_between_two_calls_rejects_the_second_call(
    s3_bucket, s3_calls, create_user, days, action, gap_s
):
    """Validates: Requirements 20.5, 20.10 (and 20.2 for the boundary of expiry)"""
    _wipe_bucket()
    member = create_user("member1")["userId"]
    admin_ctx = SimpleNamespace(user_id="u_0123456789abcdef")
    request = grant_service.request({}, SimpleNamespace(user_id=member), now=NOW_S)
    grant_id = request["grant"]["id"]
    grant_service.admin_approve(
        {"userId": member, "grantId": grant_id, "expiresInDays": days}, admin_ctx, now=NOW_S
    )
    ctx = RequestContext(action, member, "member", {"userId": member, "role": "member"})
    body = {"grantId": grant_id, "kind": "learn"}
    store_key = _grants_key(member)

    # first call: accepted with one read of the member's own store
    s3_calls.clear()
    assert access_controller.check_grant(ctx, body, now=NOW_S + 1) is None
    assert _gets(s3_calls, store_key) == 1
    assert len(s3_calls.calls) == 1

    # the same call later: accepted iff expiresAt is still later than the clock (no caching)
    later = NOW_S + gap_s
    s3_calls.clear()
    if later < NOW_S + days * 86400:
        assert access_controller.check_grant(ctx, body, now=later) is None
    else:
        with pytest.raises(MablopError) as info:
            access_controller.check_grant(ctx, body, now=later)
        assert info.value.message == GRANT_REQUIRED_MESSAGE
    assert _gets(s3_calls, store_key) == 1

    # revoke between calls: the next call is rejected, and an earlier acceptance was not cached
    grant_service.admin_revoke(
        {"userId": member, "grantId": grant_id}, admin_ctx, now=NOW_S + 2
    )
    s3_calls.clear()
    with pytest.raises(MablopError) as info:
        access_controller.check_grant(ctx, body, now=NOW_S + 3)
    assert (info.value.code, info.value.status) == ("forbidden", 403)
    assert info.value.message == GRANT_REQUIRED_MESSAGE
    assert _gets(s3_calls, store_key) == 1
    assert len(s3_calls.calls) == 1


def test_grant_that_exists_only_in_another_users_store_is_rejected(s3_bucket, s3_calls):
    """Validates: Requirements 20.3 (example: a valid Grant of another user does not count)"""
    _write_store(OTHER_USER_ID, _stored_grants(OTHER_USER_ID, [("approved", DAY_MS)]))
    ctx = _ctx("member", "generate-draft")
    body = {"grantId": _grant_id(OTHER_USER_ID, 0)}
    s3_calls.clear()
    with pytest.raises(MablopError) as info:
        access_controller.check_grant(ctx, body, now=NOW_S)
    assert info.value.message == GRANT_REQUIRED_MESSAGE
    assert _gets(s3_calls, _grants_key(OTHER_USER_ID)) == 0
    # the same id passes for its owner
    owner_ctx = RequestContext("generate-draft", OTHER_USER_ID, "member", {})
    assert access_controller.check_grant(owner_ctx, body, now=NOW_S) is None

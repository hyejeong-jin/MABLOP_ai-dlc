"""Property test: delete-draft validates, orders its steps, and recovers (Req 22.8-22.11; Property 37).

The Draft_Index and generated-posts objects are written straight into moto S3. The S3 call recorder
(``s3_calls``) shows the order of the writes and injects DeleteObject failures. Production code is not
modified.

Feature: user-auth-admin, Property 37: delete-draft validates, orders its steps, and recovers
"""
import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_messages
import draft_history
import s3store
from conftest import ACTING_USER_ID, OTHER_USER_ID, TEST_BUCKET, TEST_REGION
from errors import MablopError

_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

INDEX_KEY = f"users/{ACTING_USER_ID}/metadata/drafts.json"
OTHER_INDEX_KEY = f"users/{OTHER_USER_ID}/metadata/drafts.json"
_BASE_MS = 1_700_000_000_000

_id_suffix = st.text(alphabet="abcXYZ019-_가나다", min_size=1, max_size=6)
_title = st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=10)

# --- invalid draftId values (Req 22.9) -----------------------------------------------------

_plain = st.text(alphabet="abcXYZ019-_ ", max_size=5)
_control = st.one_of(st.characters(max_codepoint=31), st.just(chr(127)))
_invalid_strings = st.one_of(
    st.just(""),
    st.builds(lambda a, p, b: a + p + b, _plain, st.sampled_from(["/", "\\", ".."]), _plain),
    st.builds(lambda a, c, b: a + c + b, _plain, _control, _plain),
)
_non_strings = st.one_of(
    st.none(),
    st.integers(),
    st.booleans(),
    st.lists(st.text(max_size=3), max_size=2),
    st.dictionaries(st.text(max_size=3), st.text(max_size=3), max_size=2),
)
_invalid_bodies = st.one_of(
    _invalid_strings.map(lambda v: {"draftId": v}),
    _non_strings.map(lambda v: {"draftId": v}),
    st.just({}),
    st.just(None),
    st.just([]),
)


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _gen_key(draft_id, user_id=ACTING_USER_ID):
    return f"users/{user_id}/generated-posts/{draft_id}.md"


def _snapshot():
    """{key: bytes} of every object in the bucket."""
    s3 = boto3.client("s3", region_name=TEST_REGION)
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = s3.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
    return out


def _entry(draft_id, title, n):
    return {"draftId": draft_id, "title": title, "createdAt": auth_crypto.to_iso_ms(_BASE_MS + n * 1000)}


def _index_entries():
    """Entries of the acting user's stored index (None when the index object is missing)."""
    found = s3store.read_json_optional(INDEX_KEY)
    return None if found is None else found[0]["drafts"]


# (suffix, indexed, has_object) for drafts other than the target; at least one of the two is set.
_others = st.lists(
    st.tuples(_id_suffix, st.booleans(), st.booleans()).filter(lambda t: t[1] or t[2]),
    max_size=4,
    unique_by=lambda t: t[0],
)


def _arrange(target_id, mode, others, other_user_has_same, force_index):
    """Write the acting user's index and objects plus another user's data; return the index entries."""
    entries = []
    for n, (suffix, indexed, has_object) in enumerate(others):
        draft_id = "o" + suffix
        title = f"title-{suffix}"
        if indexed:
            entries.append(_entry(draft_id, title, n))
        if has_object:
            s3store.put_bytes(_gen_key(draft_id), f"# {title}\n".encode("utf-8"))
    if mode in ("both", "index_only"):
        entries.append(_entry(target_id, "target-title", 90))
    if mode in ("both", "object_only"):
        s3store.put_bytes(_gen_key(target_id), b"# target-title\n")
    if entries or force_index:
        s3store.put_json(INDEX_KEY, {"v": 1, "drafts": entries})
    if other_user_has_same:  # Req 22.13: another user's draft with the same id stays untouched
        s3store.put_bytes(_gen_key(target_id, OTHER_USER_ID), b"# other user\n")
        s3store.put_json(OTHER_INDEX_KEY, {"v": 1, "drafts": [_entry(target_id, "other", 1)]})
    return entries


def _begin(s3_calls):
    _wipe_bucket()
    s3_calls.clear_failures()
    s3_calls.clear()


# --- (a) invalid draftId ---------------------------------------------------------------------


@_SETTINGS
@given(body=_invalid_bodies, with_data=st.booleans())
def test_invalid_draft_id_is_rejected_without_any_s3_call(s3_bucket, s3_calls, body, with_data):
    """Validates: Requirements 22.9"""
    _begin(s3_calls)
    if with_data:
        _arrange("tx", "both", [("a", True, True)], True, False)
    before = _snapshot()
    s3_calls.clear()

    with pytest.raises(MablopError) as exc:
        draft_history.delete_draft(body, None)

    assert exc.value.code == "bad_request"
    assert exc.value.message == "올바르지 않은 초안 번호입니다."
    assert exc.value.message == auth_messages.BAD_DRAFT_ID
    assert s3_calls.calls == []  # no S3 read and no S3 write
    assert _snapshot() == before


@_SETTINGS
@given(draft_id=_invalid_strings)
def test_valid_draft_id_rejects_unsafe_strings(draft_id):
    """Validates: Requirements 22.9"""
    assert draft_history.valid_draft_id(draft_id) is False


@_SETTINGS
@given(draft_id=_id_suffix)
def test_valid_draft_id_accepts_plain_strings(draft_id):
    """Validates: Requirements 22.9"""
    assert draft_history.valid_draft_id(draft_id) is True


# --- (b) unknown draft -----------------------------------------------------------------------


@_SETTINGS
@given(
    suffix=_id_suffix,
    others=_others,
    other_user_has_same=st.booleans(),
    force_index=st.booleans(),
)
def test_unknown_draft_is_not_found_and_storage_is_unchanged(
    s3_bucket, s3_calls, suffix, others, other_user_has_same, force_index
):
    """Validates: Requirements 22.9, 22.10"""
    _begin(s3_calls)
    target_id = "t" + suffix  # the other drafts all start with "o"
    _arrange(target_id, "none", others, other_user_has_same, force_index)
    before = _snapshot()
    s3_calls.clear()

    with pytest.raises(MablopError) as exc:
        draft_history.delete_draft({"draftId": target_id}, None)

    assert exc.value.code == "not_found"
    assert exc.value.status == 404
    assert exc.value.message == "대상 초안을 찾을 수 없습니다."
    assert exc.value.message == auth_messages.DRAFT_NOT_FOUND
    # The implementation checks existence before any write: no empty index is created.
    assert s3_calls.writes() == []
    assert _snapshot() == before


# --- (c) normal deletion ---------------------------------------------------------------------


@_SETTINGS
@given(
    suffix=_id_suffix,
    mode=st.sampled_from(["both", "index_only", "object_only"]),
    others=_others,
    other_user_has_same=st.booleans(),
    force_index=st.booleans(),
)
def test_delete_removes_index_entry_before_object_and_leaves_the_rest(
    s3_bucket, s3_calls, suffix, mode, others, other_user_has_same, force_index
):
    """Validates: Requirements 22.8, 22.13"""
    _begin(s3_calls)
    target_id = "t" + suffix
    entries = _arrange(target_id, mode, others, other_user_has_same, force_index)
    before = _snapshot()
    object_key = _gen_key(target_id)
    s3_calls.clear()

    result = draft_history.delete_draft({"draftId": target_id}, None)

    assert result == {"draftId": target_id, "deleted": True}
    # Order of S3 writes: the index PutObject, then the object DeleteObject (Req 22.8).
    writes = [(c["op"], c["key"]) for c in s3_calls.writes()]
    assert writes == [("PutObject", INDEX_KEY), ("DeleteObject", object_key)]
    assert all(not c["failed"] for c in s3_calls.calls)

    after = _snapshot()
    assert object_key not in after
    assert s3_calls.count("DeleteObject") == 1
    stored = _index_entries()
    assert stored is not None
    assert [e["draftId"] for e in stored] == [e["draftId"] for e in entries if e["draftId"] != target_id]
    assert stored == [e for e in entries if e["draftId"] != target_id]  # other entries unchanged

    # Everything else (other drafts, the other user's data) is byte-identical.
    for key, data in before.items():
        if key in (INDEX_KEY, object_key):
            continue
        assert after.get(key) == data
    assert set(after) == (set(before) - {object_key}) | {INDEX_KEY}


# --- (d) object deletion fails, then recovers ---------------------------------------------------


@_SETTINGS
@given(
    suffix=_id_suffix,
    mode=st.sampled_from(["both", "object_only"]),
    others=_others,
    other_user_has_same=st.booleans(),
    list_between=st.booleans(),
)
def test_failed_object_delete_leaves_entry_removed_and_a_repeat_succeeds(
    s3_bucket, s3_calls, suffix, mode, others, other_user_has_same, list_between
):
    """Validates: Requirements 22.10, 22.11"""
    _begin(s3_calls)
    target_id = "t" + suffix
    entries = _arrange(target_id, mode, others, other_user_has_same, False)
    object_key = _gen_key(target_id)
    s3_calls.fail_on("DeleteObject", key=object_key)
    s3_calls.clear()

    # the first call: the entry is removed, then the object delete fails and the error propagates
    with pytest.raises(ClientError):
        draft_history.delete_draft({"draftId": target_id}, None)
    writes = s3_calls.writes()
    assert [(c["op"], c["key"], c["failed"]) for c in writes] == [
        ("PutObject", INDEX_KEY, False),
        ("DeleteObject", object_key, True),
    ]
    stored = _index_entries()
    assert stored is not None
    assert target_id not in [e["draftId"] for e in stored]  # entry already gone (Req 22.10)
    assert stored == [e for e in entries if e["draftId"] != target_id]
    assert s3store.key_exists(object_key)  # the object remains

    if list_between:
        # list-drafts supplements the remaining object, so the draft shows up again (Req 22.6, 22.10)
        listed = draft_history.list_drafts({}, None)["drafts"]
        found = [d for d in listed if d["draftId"] == target_id]
        assert len(found) == 1
        assert found[0]["title"] == "target-title"
        assert target_id in [e["draftId"] for e in _index_entries()]

    # the failure clears; repeating the call deletes the object and succeeds (Req 22.11)
    s3_calls.clear_failures()
    s3_calls.clear()
    result = draft_history.delete_draft({"draftId": target_id}, None)

    assert result == {"draftId": target_id, "deleted": True}
    assert s3_calls.count("DeleteObject", object_key) == 1
    assert not s3store.key_exists(object_key)
    final = _index_entries()
    assert final is not None
    assert target_id not in [e["draftId"] for e in final]
    expected_ids = {e["draftId"] for e in entries if e["draftId"] != target_id}
    if list_between:  # list-drafts back-filled the other objects the index lacked
        expected_ids |= {"o" + s for s, _indexed, has_object in others if has_object}
    assert {e["draftId"] for e in final} == expected_ids
    assert len(final) == len(expected_ids)
    if other_user_has_same:
        assert s3store.read_json_optional(OTHER_INDEX_KEY) is not None
        snap = _snapshot()
        assert _gen_key(target_id, OTHER_USER_ID) in snap

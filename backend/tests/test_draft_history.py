"""Unit tests for draft_history: concrete examples (Req 22.5, 22.6, 22.10, 17.9).

The property tests (test_draft_title_property.py, test_draft_list_property.py,
test_draft_index_property.py) cover the general laws. These tests pin down S3 call counts,
Korean message literals, the exact entry shape, and per-user scoping with moto S3.
Message strings are compared with literals, not with the auth_messages constants.
"""
import json

import boto3
import pytest
from botocore.exceptions import ClientError

import draft_history
from conftest import ACTING_USER_ID, OTHER_USER_ID, TEST_BUCKET, TEST_REGION
from errors import MablopError

INDEX_KEY = f"users/{ACTING_USER_ID}/metadata/drafts.json"
OTHER_INDEX_KEY = f"users/{OTHER_USER_ID}/metadata/drafts.json"
BASE_S = 1_800_000_000


def _key(draft_id, user_id=ACTING_USER_ID):
    return f"users/{user_id}/generated-posts/{draft_id}.md"


def _s3():
    return boto3.client("s3", region_name=TEST_REGION)


def _put(key, body):
    if isinstance(body, (dict, list)):
        body = json.dumps(body, ensure_ascii=False)
    _s3().put_object(Bucket=TEST_BUCKET, Key=key, Body=body.encode("utf-8"))


def _exists(key):
    resp = _s3().list_objects_v2(Bucket=TEST_BUCKET, Prefix=key)
    return any(o["Key"] == key for o in resp.get("Contents", []))


def _raw_index(key=INDEX_KEY):
    return json.loads(_s3().get_object(Bucket=TEST_BUCKET, Key=key)["Body"].read().decode("utf-8"))


# --- list-drafts: S3 call counts ---------------------------------------------------------


def test_list_drafts_reads_index_once_when_nothing_to_backfill(s3_calls, s3_bucket):
    """Req 22.5: with a complete index, list-drafts reads drafts.json exactly once and writes nothing."""
    for i, draft_id in enumerate(("d1", "d2")):
        _put(_key(draft_id), f"# 제목 {draft_id}\n본문")
        draft_history.record(draft_id, None, f"# 제목 {draft_id}\n본문", now=BASE_S + i + 0.5)
    s3_calls.clear()

    result = draft_history.list_drafts({}, None)

    assert [e["draftId"] for e in result["drafts"]] == ["d2", "d1"]
    assert s3_calls.count("GetObject", INDEX_KEY) == 1
    assert s3_calls.writes() == []


def test_second_list_after_backfill_makes_no_writes(s3_calls, s3_bucket):
    """Req 22.6: objects missing from the index are added once; the next call has 0 PutObject."""
    _put(_key("old1"), "# 옛 글\n본문")
    _put(_key("old2"), "제목 줄이 없는 본문")

    first = draft_history.list_drafts({}, None)

    assert {e["draftId"]: e["title"] for e in first["drafts"]} == {"old1": "옛 글", "old2": "제목 없음"}
    assert s3_calls.count("PutObject", INDEX_KEY) == 1
    assert {e["draftId"] for e in _raw_index()["drafts"]} == {"old1", "old2"}

    s3_calls.clear()
    second = draft_history.list_drafts({}, None)

    assert second == first
    assert s3_calls.count("PutObject") == 0
    assert s3_calls.writes() == []


# --- interrupted delete ------------------------------------------------------------------


def test_interrupted_delete_leaves_object_and_next_list_backfills(s3_calls, s3_bucket):
    """Req 22.10: index entry removed first; a failed object delete is repaired by the next list."""
    _put(_key("d1"), "# 남는 글\n본문")
    draft_history.record("d1", None, "# 남는 글\n본문", now=BASE_S + 0.5)
    rule = s3_calls.fail_on("DeleteObject", key=_key("d1"))

    with pytest.raises(ClientError):
        draft_history.delete_draft({"draftId": "d1"}, None)

    assert rule["hits"] == 1
    assert _raw_index()["drafts"] == []  # entry removed before the object delete
    assert _exists(_key("d1"))  # object remains

    s3_calls.clear_failures()
    result = draft_history.list_drafts({}, None)

    assert [(e["draftId"], e["title"]) for e in result["drafts"]] == [("d1", "남는 글")]
    assert [e["draftId"] for e in _raw_index()["drafts"]] == ["d1"]

    s3_calls.clear()
    assert draft_history.list_drafts({}, None) == result
    assert s3_calls.writes() == []


def test_delete_draft_removes_entry_then_object(s3_calls, s3_bucket):
    """Req 22.8: the index PutObject precedes the DeleteObject, and the response shape is fixed."""
    _put(_key("d1"), "# 지울 글\n본문")
    draft_history.record("d1", None, "# 지울 글\n본문", now=BASE_S + 0.5)
    s3_calls.clear()

    result = draft_history.delete_draft({"draftId": "d1"}, None)

    assert result == {"draftId": "d1", "deleted": True}
    assert [(c["op"], c["key"]) for c in s3_calls.writes()] == [
        ("PutObject", INDEX_KEY),
        ("DeleteObject", _key("d1")),
    ]
    assert not _exists(_key("d1"))


# --- Korean message literals -------------------------------------------------------------


@pytest.mark.parametrize("bad_id", [None, "", "a/b", "a\\b", "..", "x..y", "a\nb", 123])
def test_delete_draft_rejects_invalid_id_with_korean_message(s3_bucket, bad_id):
    """Req 22.9: an invalid draftId is bad_request with the literal message."""
    with pytest.raises(MablopError) as err:
        draft_history.delete_draft({"draftId": bad_id}, None)
    assert err.value.code == "bad_request"
    assert err.value.message == "올바르지 않은 초안 번호입니다."


def test_delete_draft_without_draft_id_field_is_bad_request(s3_bucket):
    with pytest.raises(MablopError) as err:
        draft_history.delete_draft({}, None)
    assert err.value.code == "bad_request"
    assert err.value.message == "올바르지 않은 초안 번호입니다."


def test_delete_draft_unknown_id_is_not_found_with_korean_message(s3_calls, s3_bucket):
    """Req 22.9: neither an index entry nor an object -> not_found, nothing written."""
    s3_calls.clear()
    with pytest.raises(MablopError) as err:
        draft_history.delete_draft({"draftId": "nope"}, None)
    assert err.value.code == "not_found"
    assert err.value.message == "대상 초안을 찾을 수 없습니다."
    assert s3_calls.writes() == []


def test_untitled_default_is_the_korean_literal(s3_bucket):
    """Req 22.2: no request title and no '#' line gives the default title."""
    assert draft_history.derive_title(None, "제목 줄 없음") == "제목 없음"
    assert draft_history.derive_title("   ", "") == "제목 없음"
    assert draft_history.derive_title(None, "#\n본문") == "제목 없음"
    draft_history.record("d1", None, "본문만 있다", now=BASE_S + 0.5)
    assert _raw_index()["drafts"][0]["title"] == "제목 없음"


# --- entry shape, empty state, JSON ------------------------------------------------------


def test_index_entry_has_exactly_draft_id_title_created_at(s3_bucket):
    """Req 22.1: stored and listed entries carry exactly draftId, title, createdAt."""
    draft_history.record("d1", "요청 제목", "# 본문 제목\n내용", now=BASE_S + 0.5)
    _put(_key("old"), "# 보충 글\n내용")

    listed = draft_history.list_drafts({}, None)["drafts"]

    assert {e["draftId"] for e in listed} == {"d1", "old"}
    for entry in listed + _raw_index()["drafts"]:
        assert set(entry) == {"draftId", "title", "createdAt"}
    by_id = {e["draftId"]: e for e in listed}
    assert by_id["d1"]["title"] == "요청 제목"
    assert by_id["d1"]["createdAt"] == "2027-01-15T08:00:00.500Z"


def test_list_drafts_on_empty_workspace_returns_empty_list(s3_calls, s3_bucket):
    """Req 22.5: no index and no objects -> {'drafts': []} without writing an index."""
    assert draft_history.list_drafts({}, None) == {"drafts": []}
    assert s3_calls.writes() == []


def test_responses_are_json_serializable(s3_bucket):
    """The actions return plain JSON data (the handler serializes them)."""
    draft_history.record("d1", "한글 제목", "본문", now=BASE_S + 0.5)
    listed = draft_history.list_drafts({}, None)
    deleted = draft_history.delete_draft({"draftId": "d1"}, None)

    for payload in (listed, deleted, {"drafts": []}):
        assert json.loads(json.dumps(payload, ensure_ascii=False)) == payload
    assert json.loads(json.dumps(listed))["drafts"][0]["title"] == "한글 제목"
    assert deleted == {"draftId": "d1", "deleted": True}


# --- per-user scoping --------------------------------------------------------------------


def test_other_users_drafts_are_not_listed_or_deletable(s3_calls, s3_bucket):
    """Req 22.13: another user's objects and index entries never show up, and delete cannot reach them."""
    _put(_key("theirs", OTHER_USER_ID), "# 남의 글\n본문")
    _put(
        OTHER_INDEX_KEY,
        {"v": 1, "drafts": [{"draftId": "theirs", "title": "남의 글", "createdAt": "2027-01-15T08:00:00.000Z"}]},
    )
    _put(_key("mine"), "# 내 글\n본문")
    s3_calls.clear()  # forget the test setup writes

    result = draft_history.list_drafts({}, None)

    assert [e["draftId"] for e in result["drafts"]] == ["mine"]
    assert [e["draftId"] for e in _raw_index(OTHER_INDEX_KEY)["drafts"]] == ["theirs"]

    with pytest.raises(MablopError) as err:
        draft_history.delete_draft({"draftId": "theirs"}, None)
    assert err.value.code == "not_found"
    assert err.value.message == "대상 초안을 찾을 수 없습니다."
    assert _exists(_key("theirs", OTHER_USER_ID))
    assert [e["draftId"] for e in _raw_index(OTHER_INDEX_KEY)["drafts"]] == ["theirs"]
    assert all(k.startswith(f"users/{ACTING_USER_ID}/") for k in s3_calls.keys("PutObject", "DeleteObject"))


# --- valid_draft_id examples -------------------------------------------------------------


@pytest.mark.parametrize(
    "draft_id",
    ["d1", "20270115-080000-abc123", "draft_5", "a.b", "가나다", "x-y"],
)
def test_valid_draft_id_accepts_ordinary_ids(draft_id):
    assert draft_history.valid_draft_id(draft_id) is True


@pytest.mark.parametrize(
    "draft_id",
    ["", None, 5, "a/b", "/abs", "a\\b", "..", "a..b", "a\x00b", "a\nb", "a\tb", "a\x7fb"],
)
def test_valid_draft_id_rejects_unsafe_ids(draft_id):
    assert draft_history.valid_draft_id(draft_id) is False

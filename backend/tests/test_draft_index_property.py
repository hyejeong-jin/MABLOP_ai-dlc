"""Property test: the Draft_Index stays consistent with the stored drafts
(Req 22.1, 22.3, 22.4, 22.6; Property 35).

Only the draft_history functions (record, list_drafts, delete_draft) are called directly; the
Drafting_Engine integration belongs to task 9.5. A generation is simulated as the Drafting_Engine
does it (design D20): store the object first, then `record`, swallowing a failed index update.
Index write failures are injected with conftest `s3_calls.fail_on`. Draft objects without an index
entry (as after a legacy copy, or after an injected failure) are included. The results are compared
with a reference model of the index. Production code is not modified.

Feature: user-auth-admin, Property 35: Draft index stays consistent with the stored drafts
"""
import json
import re
from datetime import datetime, timezone

import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import draft_history
from conftest import ACTING_USER_ID, TEST_BUCKET, TEST_REGION
from errors import MablopError

BASE_S = 1_800_000_000  # epoch seconds; the sequence adds whole seconds plus .5
UNTITLED = "제목 없음"
DRAFT_NOT_FOUND = "대상 초안을 찾을 수 없습니다."
INDEX_KEY = f"users/{ACTING_USER_ID}/metadata/drafts.json"
ENTRY_FIELDS = {"draftId", "title", "createdAt"}
ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")


def _object_key(draft_id):
    return f"users/{ACTING_USER_ID}/generated-posts/{draft_id}.md"


_SETTINGS = settings(
    max_examples=40,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# --- strategies -------------------------------------------------------------------------

_ID_POOL = ["d1", "d2", "d3", "d4", "draft-5", "a.b", "가나다", "x_y"]
_draft_id = st.sampled_from(_ID_POOL)
# Plain text without '#' and without line separators other than "\n".
_text = st.text(alphabet="abcXYZ가나다라 0123\t", min_size=1, max_size=12).filter(lambda s: s.strip())
_heading = st.builds(lambda n, t: "#" * n + " " + t, st.integers(1, 3), _text)
_line = st.one_of(_text, _heading, st.just(""))
_body = st.lists(_line, min_size=0, max_size=5).map("\n".join)
_requested_title = st.one_of(
    st.none(),
    st.text(alphabet=" \t", max_size=4),
    _text,
    st.just(""),
)

_step = st.one_of(
    st.tuples(st.just("gen"), _draft_id, _requested_title, _body),
    st.tuples(st.just("gen_fail"), _draft_id, _requested_title, _body),
    st.tuples(st.just("put_only"), _draft_id, _requested_title, _body),
    st.tuples(st.just("list"), _draft_id, _requested_title, _body),
    st.tuples(st.just("delete"), _draft_id, _requested_title, _body),
    st.tuples(st.just("delete_fail"), _draft_id, _requested_title, _body),
)

# --- helpers ----------------------------------------------------------------------------


def _s3():
    return boto3.client("s3", region_name=TEST_REGION)


def _empty_bucket():
    s3 = _s3()
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            s3.delete_object(Bucket=TEST_BUCKET, Key=obj["Key"])


def _iso_ms_from_seconds(seconds):
    ms = int(seconds * 1000)
    dt = datetime.fromtimestamp(ms // 1000, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms % 1000:03d}Z"


def _last_modified_iso(draft_id):
    """LastModified of the stored object, read directly from S3, as UTC ISO-8601 with milliseconds."""
    dt = _s3().head_object(Bucket=TEST_BUCKET, Key=_object_key(draft_id))["LastModified"]
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def _stored_ids():
    """draftIds of the stored generated-posts/*.md objects of the acting user."""
    prefix = f"users/{ACTING_USER_ID}/generated-posts/"
    ids = set()
    for page in _s3().get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET, Prefix=prefix):
        for obj in page.get("Contents", []):
            name = obj["Key"][len(prefix):]
            if name.endswith(".md"):
                ids.add(name[: -len(".md")])
    return ids


def _raw_index():
    try:
        resp = _s3().get_object(Bucket=TEST_BUCKET, Key=INDEX_KEY)
    except ClientError as err:
        if err.response["Error"]["Code"] in ("NoSuchKey", "404"):
            return None
        raise
    return json.loads(resp["Body"].read().decode("utf-8"))


def _expected_title(requested, body):
    """Req 22.2 (also used for the back-fill with requested=None); bodies never hold a bare '#' line."""
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    for line in body.split("\n"):
        if line.startswith("#"):
            return line.lstrip("# ").strip()
    return UNTITLED


def _put_object(draft_id, body):
    _s3().put_object(
        Bucket=TEST_BUCKET, Key=_object_key(draft_id), Body=body.encode("utf-8"), ContentType="text/markdown"
    )


def _assert_index_matches_model(model):
    """The stored index file holds exactly the model entries, once each, with exactly three fields."""
    raw = _raw_index()
    if raw is None:
        assert model == {}
        return
    entries = raw["drafts"]
    assert all(set(e) == ENTRY_FIELDS for e in entries)
    ids = [e["draftId"] for e in entries]
    assert len(ids) == len(set(ids)), "duplicate draftId in the Draft_Index"
    assert {e["draftId"]: e for e in entries} == model
    for e in entries:
        assert ISO_RE.fullmatch(e["createdAt"])


# --- property ---------------------------------------------------------------------------


@_SETTINGS
@given(steps=st.lists(_step, min_size=1, max_size=25))
def test_draft_index_stays_consistent_with_stored_drafts(s3_calls, s3_bucket, steps):
    """Req 22.1, 22.3, 22.4, 22.6: record replaces, failed index writes are repaired, no duplicates."""
    _empty_bucket()
    s3_calls.clear()
    s3_calls.clear_failures()

    stored = {}  # draftId -> body, the objects under generated-posts/
    model = {}  # draftId -> entry, what the Draft_Index must hold (reference model)
    injected = False
    counter = 0

    for kind, draft_id, requested, body in steps:
        if kind in ("gen", "gen_fail"):
            counter += 1
            now = BASE_S + counter + 0.5
            _put_object(draft_id, body)  # the object is stored first (D20)
            stored[draft_id] = body
            if kind == "gen_fail":
                injected = True
                rule = s3_calls.fail_on("PutObject", key=INDEX_KEY)
                with pytest.raises(ClientError):
                    draft_history.record(draft_id, requested, body, now=now)
                assert rule["hits"] == 1
                s3_calls.clear_failures()
                # Req 22.4: the object stays; the index is unchanged (a previous entry may remain)
            else:
                draft_history.record(draft_id, requested, body, now=now)
                model[draft_id] = {
                    "draftId": draft_id,
                    "title": _expected_title(requested, body),
                    "createdAt": _iso_ms_from_seconds(now),
                }
            assert draft_id in _stored_ids()
        elif kind == "put_only":
            _put_object(draft_id, body)  # never indexed, as after a legacy copy
            stored[draft_id] = body
        elif kind == "list":
            result = draft_history.list_drafts({}, None)
            # Back-fill: every stored object that the index lacks is added once (Req 22.6).
            for stored_id, stored_body in stored.items():
                if stored_id not in model:
                    model[stored_id] = {
                        "draftId": stored_id,
                        "title": _expected_title(None, stored_body),
                        "createdAt": _last_modified_iso(stored_id),
                    }
            _assert_index_matches_model(model)
            # every stored object has exactly one entry
            assert {e["draftId"] for e in model.values()} >= set(stored)
            if not injected:
                assert set(model) == _stored_ids()
            expected = sorted(model.values(), key=lambda e: (e["createdAt"], e["draftId"]), reverse=True)
            assert result == {"drafts": expected[:100]}
            assert all(set(e) == ENTRY_FIELDS for e in result["drafts"])
            # a second list with no other change writes nothing
            s3_calls.clear()
            again = draft_history.list_drafts({}, None)
            assert again == result
            assert s3_calls.writes() == []
        elif kind in ("delete", "delete_fail"):
            exists = draft_id in stored or draft_id in model
            rule = None
            if kind == "delete_fail":
                rule = s3_calls.fail_on("DeleteObject", key=_object_key(draft_id))
            if not exists:
                with pytest.raises(MablopError) as err:
                    draft_history.delete_draft({"draftId": draft_id}, None)
                assert err.value.code == "not_found" and err.value.message == DRAFT_NOT_FOUND
            elif kind == "delete_fail" and draft_id in stored:
                # The index entry is removed first, then the object delete fails (Req 22.8, 22.11).
                with pytest.raises(ClientError):
                    draft_history.delete_draft({"draftId": draft_id}, None)
                assert rule["hits"] == 1
                model.pop(draft_id, None)
                injected = True  # the object stays without an entry until the next list-drafts
                assert draft_id in _stored_ids()
            else:
                assert draft_history.delete_draft({"draftId": draft_id}, None) == {
                    "draftId": draft_id,
                    "deleted": True,
                }
                model.pop(draft_id, None)
                stored.pop(draft_id, None)
                assert draft_id not in _stored_ids()
            s3_calls.clear_failures()
            if _raw_index() is not None:
                _assert_index_matches_model(model)

    # Final list: the index covers exactly the stored objects, whatever failures were injected.
    result = draft_history.list_drafts({}, None)
    final_ids = [e["draftId"] for e in result["drafts"]]
    assert len(final_ids) == len(set(final_ids))
    assert set(final_ids) == _stored_ids() == set(stored)
    for e in result["drafts"]:
        assert set(e) == ENTRY_FIELDS and ISO_RE.fullmatch(e["createdAt"])


# --- examples ---------------------------------------------------------------------------


def test_failed_index_write_keeps_object_and_next_list_backfills(s3_calls, s3_bucket):
    """Req 22.4, 22.6: a failed index update leaves the object; list-drafts adds it from the object."""
    _put_object("d1", "# 첫 제목\n본문")
    s3_calls.fail_on("PutObject", key=INDEX_KEY)
    with pytest.raises(ClientError):
        draft_history.record("d1", None, "# 첫 제목\n본문", now=BASE_S + 0.5)
    s3_calls.clear_failures()

    assert "d1" in _stored_ids()
    assert _raw_index() is None

    result = draft_history.list_drafts({}, None)
    assert result == {"drafts": [{"draftId": "d1", "title": "첫 제목", "createdAt": _last_modified_iso("d1")}]}
    assert [e["draftId"] for e in _raw_index()["drafts"]] == ["d1"]


def test_record_replaces_entry_of_same_draft_id(s3_bucket):
    """Req 22.1, 22.3: recording the same draftId again leaves one entry with the newer values."""
    draft_history.record("d1", "처음", "본문", now=BASE_S + 1.5)
    draft_history.record("d1", "나중", "본문", now=BASE_S + 2.5)
    assert _raw_index()["drafts"] == [
        {"draftId": "d1", "title": "나중", "createdAt": _iso_ms_from_seconds(BASE_S + 2.5)}
    ]
    assert _raw_index()["drafts"][0]["createdAt"].endswith(".500Z")

"""Property tests for VOC storage and status transitions.

Feature: voice-box-content-research-branding, Property 1: 사용자별 저장 격리
Feature: voice-box-content-research-branding, Property 2: VOC 원문·메타데이터 보존
Feature: voice-box-content-research-branding, Property 3: 상태 값과 변경 이벤트 불변식
"""
from datetime import datetime, timezone

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

import activity_store
import s3store
import user_scope
import voc_store
from errors import MablopError


_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=80,
).filter(lambda value: bool(value.strip()))
_NOTE = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    max_size=80,
)
_TAG = _TEXT.map(lambda value: value[:20]).filter(lambda value: bool(value.strip()))
_TAGS = st.lists(_TAG, max_size=5, unique=True)
_SOURCE_TYPE = st.sampled_from(sorted(voc_store.SOURCE_TYPES))


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(body=_TEXT, source_type=_SOURCE_TYPE, tags=_TAGS, note=_NOTE)
def test_voc_storage_isolation_uses_acting_user_prefix(
    s3_bucket, acting_user_id, other_user_id, body, source_type, tags, note
):
    """**Validates: Requirements 1.1, 1.2, 10.1, 13.3**"""
    record = voc_store.create_voc(body, source_type, tags, note)

    with user_scope.bind(other_user_id):
        assert voc_store.list_voc()["records"] == []
        with pytest.raises(MablopError) as error:
            voc_store.get_voc(record["vocId"])
        assert error.value.code == "not_found"

    keys = {item["key"] for item in s3store.list_keys("")}
    assert keys
    assert all(key.startswith(f"users/{acting_user_id}/") for key in keys)
    assert f"users/{other_user_id}/" not in "\n".join(sorted(keys))


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(body=_TEXT, source_type=_SOURCE_TYPE, tags=_TAGS, note=_NOTE)
def test_voc_round_trip_preserves_body_and_user_metadata(
    s3_bucket, body, source_type, tags, note
):
    """**Validates: Requirements 1.3**"""
    record = voc_store.create_voc(body, source_type, tags, note)
    loaded = voc_store.get_voc(record["vocId"])

    assert loaded["body"] == body
    assert loaded["userMetadata"] == {
        "sourceType": source_type,
        "tags": tags,
        "note": note,
    }
    assert loaded["body"] != loaded["userMetadata"]


@pytest.mark.property
@settings(
    max_examples=20,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    body=_TEXT,
    source_type=_SOURCE_TYPE,
    tags=_TAGS,
    note=_NOTE,
    new_status=st.sampled_from(sorted(voc_store.VOC_STATUSES - {"new"})),
)
def test_admin_status_transition_stores_valid_status_and_activity_event(
    s3_bucket, create_user, body, source_type, tags, note, new_status
):
    """**Validates: Requirements 1.5, 2.1, 2.4**"""
    target = create_user("property-target")
    actor_id = "u_admin0000000000"

    with user_scope.bind(target["userId"]):
        record = voc_store.create_voc(body, source_type, tags, note)
        updated = voc_store.admin_update_voc_status(
            target["userId"], record["vocId"], new_status, actor_id=actor_id
        )

        assert updated["status"] in voc_store.VOC_STATUSES
        assert updated["status"] == new_status
        assert updated["statusChangedBy"] == actor_id
        assert updated["statusChangedAt"]
        datetime.fromisoformat(updated["statusChangedAt"].replace("Z", "+00:00")).astimezone(
            timezone.utc
        )

        found = s3store.read_json_optional(
            s3store.user_key(
                f"metadata/activity-{datetime.now(timezone.utc).astimezone(activity_store.KST).strftime('%Y-%m')}.json"
            )
        )
        events = (found[0] if found is not None else {}).get("events", [])
        status_events = [
            event
            for event in events
            if event["relatedVocId"] == record["vocId"]
            and event["type"] == "voc-status-changed"
        ]
        assert status_events
        event = status_events[-1]
        assert event["actorUserId"] == actor_id
        assert event["metadata"] == {
            "previousStatus": "new",
            "newStatus": new_status,
        }
        assert event["occurredAt"]

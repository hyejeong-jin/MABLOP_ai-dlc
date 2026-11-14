"""Property tests for activity filtering, aggregation, and detail preservation."""
from hypothesis import HealthCheck, given, settings, strategies as st

import activity_store
import s3store


_EVENT_TYPES = st.sampled_from(["voc-created", "research-started", "draft-created"])
_COUNTS = st.integers(min_value=1, max_value=20)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=100)
@given(
    st.lists(
        st.fixed_dictionaries(
            {
                "eventId": st.text(min_size=1, max_size=12),
                "actor": st.sampled_from(["acting", "other"]),
                "type": _EVENT_TYPES,
                "count": _COUNTS,
                "hour": st.integers(min_value=0, max_value=14),
            }
        ),
        min_size=1,
        max_size=20,
    )
)
def test_activity_filters_acting_user_and_aggregates_selected_events(
    s3_bucket, acting_user_id, other_user_id, generated_events
):
    events = []
    for item in generated_events:
        event = {
            "eventId": item["eventId"],
            "actorUserId": acting_user_id if item["actor"] == "acting" else other_user_id,
            "type": item["type"],
            "occurredAt": f"2025-01-10T{item['hour']:02d}:00:00Z",
            "count": item["count"],
            "relatedVocId": f"voc-{item['eventId']}",
            "relatedRunId": None,
            "metadata": {"source": "property"},
        }
        events.append(event)

    s3store.put_json(
        s3store.user_key("metadata/activity-2025-01.json"),
        {"v": 1, "month": "2025-01", "events": events},
    )

    result = activity_store.get_activity(
        {"from": "2025-01-10", "to": "2025-01-10", "granularity": "hour"}
    )
    expected = sorted(
        [event for event in events if event["actorUserId"] == acting_user_id],
        key=lambda event: (event["occurredAt"], event["eventId"]),
    )
    assert result["events"] == expected

    expected_totals = {}
    expected_types = {}
    for event in expected:
        hour = int(event["occurredAt"][11:13]) + 9
        local_hour = hour % 24
        key = f"2025-01-10T{local_hour:02d}:00"
        expected_totals[key] = expected_totals.get(key, 0) + event["count"]
        by_type = expected_types.setdefault(key, {})
        by_type[event["type"]] = by_type.get(event["type"], 0) + event["count"]

    assert {
        bucket["date"] + f"T{bucket['hour']:02d}:00": bucket["total"]
        for bucket in result["daily"]
    } == expected_totals
    assert {
        bucket["date"] + f"T{bucket['hour']:02d}:00": bucket["byType"]
        for bucket in result["daily"]
    } == expected_types


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=100)
@given(
    st.fixed_dictionaries(
        {
            "type": _EVENT_TYPES,
            "count": _COUNTS,
            "hour": st.integers(min_value=0, max_value=14),
            "relatedVocId": st.one_of(st.none(), st.text(min_size=1, max_size=12)),
            "relatedRunId": st.one_of(st.none(), st.text(min_size=1, max_size=12)),
        }
    )
)
def test_activity_detail_dto_preserves_related_identifiers_and_text_fields(
    s3_bucket, acting_user_id, event_data
):
    event = {
        "eventId": "detail-event",
        "actorUserId": acting_user_id,
        "type": event_data["type"],
        "occurredAt": f"2025-01-10T{event_data['hour']:02d}:00:00Z",
        "count": event_data["count"],
        "relatedVocId": event_data["relatedVocId"],
        "relatedRunId": event_data["relatedRunId"],
        "metadata": {"detail": "kept"},
    }
    s3store.put_json(
        s3store.user_key("metadata/activity-2025-01.json"),
        {"events": [event]},
    )

    result = activity_store.get_activity({"from": "2025-01-10", "to": "2025-01-10"})
    assert result["events"] == [event]
    assert result["events"][0]["relatedVocId"] == event["relatedVocId"]
    assert result["events"][0]["relatedRunId"] == event["relatedRunId"]
    assert result["events"][0]["metadata"] == {"detail": "kept"}

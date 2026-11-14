import activity_store
import s3store


def test_get_activity_filters_user_and_builds_daily_dto(s3_bucket, acting_user_id, other_user_id):
    s3store.put_json(
        s3store.user_key("metadata/activity-2025-01.json"),
        {
            "v": 1,
            "month": "2025-01",
            "events": [
                {"eventId": "mine", "actorUserId": acting_user_id, "type": "voc-created", "occurredAt": "2025-01-10T01:00:00Z", "count": 1},
                {"eventId": "other", "actorUserId": other_user_id, "type": "research-started", "occurredAt": "2025-01-10T02:00:00Z", "count": 9},
            ],
        },
    )

    result = activity_store.get_activity({"from": "2025-01-10", "to": "2025-01-10"})

    assert [event["eventId"] for event in result["events"]] == ["mine"]
    assert result["daily"] == [{"date": "2025-01-10", "total": 1, "byType": {"voc-created": 1}, "eventIds": ["mine"]}]
    assert result["range"]["from"].startswith("2025-01-10T00:00:00")


def test_get_activity_hour_granularity_groups_by_kst_hour(s3_bucket, acting_user_id):
    s3store.put_json(
        s3store.user_key("metadata/activity-2025-01.json"),
        {"events": [
            {"eventId": "a", "actorUserId": acting_user_id, "type": "draft-created", "occurredAt": "2025-01-10T00:15:00Z", "count": 2},
            {"eventId": "b", "actorUserId": acting_user_id, "type": "draft-created", "occurredAt": "2025-01-10T00:45:00Z", "count": 1},
        ]},
    )

    result = activity_store.get_activity({"from": "2025-01-10", "to": "2025-01-10", "granularity": "hour"})

    assert result["daily"] == [{"date": "2025-01-10", "hour": 9, "total": 3, "byType": {"draft-created": 3}, "eventIds": ["a", "b"]}]


def test_get_activity_merges_existing_drafts_per_kst_day_read_only(s3_bucket, acting_user_id):
    s3store.put_json(
        s3store.user_key("metadata/drafts.json"),
        {"drafts": [
            {"id": "d1", "createdAt": "2025-01-10T00:30:00Z"},
            {"id": "d2", "createdAt": "2025-01-10T05:00:00Z"},
            {"id": "old", "createdAt": "2024-01-01T00:00:00Z"},
        ]},
    )
    before = s3store.read_json_optional(s3store.user_key("metadata/drafts.json"))

    result = activity_store.get_activity({"from": "2025-01-10", "to": "2025-01-10"})

    assert result["daily"][0]["date"] == "2025-01-10"
    assert result["daily"][0]["byType"] == {"draft": 2}
    assert {e["type"] for e in result["events"]} == {"draft"}
    assert s3store.read_json_optional(s3store.user_key("metadata/drafts.json")) == before


def test_get_activity_counts_raw_posts_as_learned_for_acting_user_only(s3_bucket, acting_user_id, other_user_id):
    s3store.put_bytes(s3store.user_key("raw-posts/p1.txt"), b"hello")
    s3store.put_bytes(f"users/{other_user_id}/raw-posts/p2.txt", b"other")
    today = activity_store._now().astimezone(activity_store.KST).strftime("%Y-%m-%d")

    result = activity_store.get_activity({"from": today, "to": today})

    assert result["daily"][0]["byType"] == {"learned": 1}

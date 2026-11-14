"""Minimal VOC store coverage for task 2.1."""
from datetime import datetime

import activity_store
import boto3
import pytest

import s3store
import user_scope
import voc_store
from errors import MablopError


def _kst_day(timestamp: str) -> str:
    """Activity ranges are KST dates; derive the KST day of a UTC timestamp."""
    moment = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    return moment.astimezone(activity_store.KST).strftime("%Y-%m-%d")


def test_create_round_trip_keeps_body_and_metadata_separate(s3_bucket, acting_user_id):
    record = voc_store.create_voc(
        "앱이 빠르고 이해하기 쉬워요.",
        "customer",
        ["온보딩", "모바일"],
        "후속 인터뷰에서 확인",
    )

    assert record["ownerId"] == acting_user_id
    assert record["body"] == "앱이 빠르고 이해하기 쉬워요."
    assert record["userMetadata"] == {
        "sourceType": "customer",
        "tags": ["온보딩", "모바일"],
        "note": "후속 인터뷰에서 확인",
    }
    assert voc_store.get_voc(record["vocId"]) == record
    day = _kst_day(record["createdAt"])
    activity = activity_store.get_activity({"from": day, "to": day})
    assert len(activity["events"]) == 1
    assert activity["events"][0]["type"] == "voc-created"
    assert activity["events"][0]["actorUserId"] == acting_user_id
    assert activity["events"][0]["relatedVocId"] == record["vocId"]
    assert activity["events"][0]["metadata"] == {"status": "new", "sourceType": "customer"}
    listed = voc_store.list_voc()
    assert listed["records"] == [
        {key: record[key] for key in ("vocId", "status", "createdAt", "updatedAt")} | {"preview": record["body"]}
    ]

    stored_keys = {item["key"] for item in s3store.list_keys("")}
    assert s3store.user_key("metadata/voc-index.json") in stored_keys
    assert s3store.user_key(f"metadata/voc/{record['vocId']}.json") in stored_keys
    assert all(key.startswith(f"users/{acting_user_id}/") for key in stored_keys)


def test_update_and_archive_refresh_index(s3_bucket):
    record = voc_store.create_voc("초기 원문", "observation")
    updated = voc_store.update_voc(
        record["vocId"], body="수정된 원문", tags=["변경"], note="메모 변경"
    )
    assert updated["body"] == "수정된 원문"
    assert updated["userMetadata"]["sourceType"] == "observation"

    archived = voc_store.archive_voc(record["vocId"])
    assert archived["status"] == "archived"
    assert archived["statusChangedBy"] == "u_0123456789abcdef"
    assert voc_store.list_voc(status="archived")["records"][0]["status"] == "archived"

    day = _kst_day(record["createdAt"])
    activity = activity_store.get_activity({"from": day, "to": day})
    assert [event["type"] for event in activity["events"]] == ["voc-created", "voc-updated", "voc-status-changed"]
    assert activity["events"][-1]["relatedVocId"] == record["vocId"]
    assert activity["events"][-1]["metadata"] == {"previousStatus": "new", "newStatus": "archived"}


def test_invalid_input_does_not_write(s3_bucket):
    with pytest.raises(MablopError) as error:
        voc_store.create_voc("원문", "invalid")
    assert error.value.code == "bad_request"
    assert s3store.list_keys("") == []


def test_other_user_cannot_read_acting_users_record(s3_bucket, other_user_id):
    record = voc_store.create_voc("개인 원문", "quote")
    with user_scope.bind(other_user_id):
        assert voc_store.list_voc()["records"] == []
        with pytest.raises(MablopError) as error:
            voc_store.get_voc(record["vocId"])
    assert error.value.code == "not_found"


def test_admin_list_filters_status_date_and_paginates(s3_bucket, create_user, make_session_token):
    target = create_user("target")
    with user_scope.bind(target["userId"]):
        first = voc_store.create_voc("첫 항목", "customer")
        second = voc_store.create_voc("둘째 항목", "quote")
        updated = voc_store.admin_update_voc_status(target["userId"], second["vocId"], "confirmed", actor_id="u_admin0000000000")

    result = voc_store.admin_list_voc(
        user_id=target["userId"], status="confirmed", from_date=first["createdAt"][:10],
        to_date=second["createdAt"][:10], limit=1,
    )
    assert result["records"] == [{**{key: updated[key] for key in ("vocId", "status", "createdAt", "updatedAt")},
                                  "preview": updated["body"], "ownerId": target["userId"]}]
    assert result["nextCursor"] == updated["vocId"]


def test_admin_status_update_rejects_invalid_status_without_changing_record(s3_bucket, create_user):
    target = create_user("target")
    with user_scope.bind(target["userId"]):
        record = voc_store.create_voc("상태 대상", "observation")
        with pytest.raises(MablopError) as error:
            voc_store.admin_update_voc_status(target["userId"], record["vocId"], "invalid", actor_id="u_admin0000000000")
        assert error.value.code == "bad_request"
        assert voc_store.get_voc(record["vocId"])["status"] == "new"

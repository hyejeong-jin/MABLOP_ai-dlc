"""Handler-level VOC integration coverage using real JSON, moto S3, and Session_Token."""
import importlib
import json

import boto3
import pytest


@pytest.fixture
def handler_module(s3_bucket, signing_secret):
    """Reload the handler boundary after moto and token fixtures are configured."""
    import s3store
    import access_controller
    import handler

    importlib.reload(s3store)
    importlib.reload(access_controller)
    importlib.reload(handler)
    access_controller._window = None
    return handler


def _event(action, token, **fields):
    body = {"action": action, **fields}
    headers = {"X-Mablop-Token": token} if token is not None else {}
    return {"headers": headers, "body": json.dumps(body, ensure_ascii=False)}


def _json(response):
    return json.loads(response["body"])


def _assert_error(response, status, code):
    assert response["statusCode"] == status
    assert _json(response)["error"]["code"] == code


def _create_voc(handler, token, *, body="고객의 모바일 온보딩 의견", **fields):
    response = handler.handler(
        _event(
            "create-voc",
            token,
            body=body,
            category=fields.pop("category", "suggestion"),
            title=fields.pop("title", "온보딩 의견"),
            **fields,
        )
    )
    assert response["statusCode"] == 200
    return _json(response)["voc"]


def test_voc_actions_round_trip_with_real_json_moto_s3_and_token_context(
    handler_module, create_user, make_session_token, acting_user_id
):
    """Create/list/get/update/archive use the verified token user and persist JSON in S3."""
    user = create_user("member1", user_id=acting_user_id)
    token = make_session_token(user["userId"])

    created = _create_voc(
        handler_module,
        token,
        body="  고객이 모바일에서 저장 버튼을 찾기 어렵다고 말했다.  ",
        userId="u_fedcba9876543210",
        loginId="spoofed-login",
        s3Key="users/u_fedcba9876543210/metadata/voc-index.json",
    )

    assert created["ownerId"] == acting_user_id
    assert created["body"].startswith("  고객이")
    assert (created["category"], created["title"], created["reply"]) == ("suggestion", "온보딩 의견", None)
    assert created["status"] == "new"

    listed = _json(handler_module.handler(_event("list-voc", token)))
    assert [item["vocId"] for item in listed["records"]] == [created["vocId"]]
    assert listed["records"][0]["status"] == "new"
    assert listed["records"][0]["hasReply"] is False

    fetched = _json(
        handler_module.handler(_event("get-voc", token, vocId=created["vocId"]))
    )["voc"]
    assert fetched == created

    updated = _json(
        handler_module.handler(
            _event(
                "update-voc",
                token,
                vocId=created["vocId"],
                body="고객은 모바일 저장 버튼을 더 분명히 보여 달라고 요청했다.",
                category="bug",
                title="수정된 제목",
            )
        )
    )["voc"]
    assert updated["ownerId"] == acting_user_id
    assert updated["body"].startswith("고객은 모바일")
    assert (updated["category"], updated["title"]) == ("bug", "수정된 제목")
    assert updated["updatedAt"] >= created["updatedAt"]

    archived = _json(
        handler_module.handler(_event("archive-voc", token, vocId=created["vocId"]))
    )["voc"]
    assert archived["status"] == "archived"
    assert archived["statusChangedBy"] == acting_user_id
    assert archived["statusChangedAt"]

    archived_list = _json(
        handler_module.handler(_event("list-voc", token, status="archived"))
    )
    assert [item["vocId"] for item in archived_list["records"]] == [created["vocId"]]

    objects = boto3.client("s3", region_name="us-east-1").list_objects_v2(
        Bucket="mablop-test", Prefix=f"users/{acting_user_id}/metadata/voc/"
    )["Contents"]
    keys = {item["Key"] for item in objects}
    assert f"users/{acting_user_id}/metadata/voc/{created['vocId']}.json" in keys
    assert all("u_fedcba9876543210" not in key for key in keys)


def test_voc_get_and_list_are_isolated_between_token_users(
    handler_module, create_user, make_session_token, acting_user_id, other_user_id
):
    owner = create_user("owner", user_id=acting_user_id)
    other = create_user("other", user_id=other_user_id)
    owner_token = make_session_token(owner["userId"])
    other_token = make_session_token(other["userId"])
    created = _create_voc(handler_module, owner_token)

    cross_user_get = handler_module.handler(
        _event("get-voc", other_token, vocId=created["vocId"])
    )
    _assert_error(cross_user_get, 404, "not_found")

    other_list = handler_module.handler(_event("list-voc", other_token))
    assert other_list["statusCode"] == 200
    assert _json(other_list)["records"] == []


def test_admin_can_filter_and_change_status_but_member_gets_403(
    handler_module, create_user, make_session_token, acting_user_id, other_user_id
):
    member = create_user("member1", user_id=acting_user_id)
    admin = create_user("admin1", user_id=other_user_id, role="admin")
    member_token = make_session_token(member["userId"])
    admin_token = make_session_token(admin["userId"])
    created = _create_voc(handler_module, member_token)

    member_admin_attempt = handler_module.handler(
        _event(
            "admin-update-voc-status",
            member_token,
            userId=member["userId"],
            vocId=created["vocId"],
            status="confirmed",
        )
    )
    _assert_error(member_admin_attempt, 403, "forbidden")

    admin_list = handler_module.handler(
        _event("admin-list-voc", admin_token, userId=member["userId"], status="new")
    )
    assert admin_list["statusCode"] == 200
    admin_list_body = _json(admin_list)
    assert admin_list_body["accessScope"] == {
        "kind": "admin",
        "requestedBy": admin["userId"],
        "userId": member["userId"],
    }
    assert [item["vocId"] for item in admin_list_body["records"]] == [created["vocId"]]
    assert admin_list_body["records"][0]["ownerId"] == member["userId"]

    changed = handler_module.handler(
        _event(
            "admin-update-voc-status",
            admin_token,
            userId=member["userId"],
            loginId="ignored-login",
            s3Key=f"users/{other_user_id}/metadata/voc/{created['vocId']}.json",
            vocId=created["vocId"],
            status="confirmed",
        )
    )
    assert changed["statusCode"] == 200
    changed_voc = _json(changed)["voc"]
    assert changed_voc["ownerId"] == member["userId"]
    assert changed_voc["status"] == "confirmed"
    assert changed_voc["statusChangedBy"] == admin["userId"]
    assert changed_voc["statusChangedAt"]

    confirmed = handler_module.handler(
        _event("admin-list-voc", admin_token, userId=member["userId"], status="confirmed")
    )
    assert [item["vocId"] for item in _json(confirmed)["records"]] == [created["vocId"]]

    activity = handler_module.handler(_event("get-activity", admin_token))
    assert activity["statusCode"] == 200
    events = _json(activity)["events"]
    status_events = [event for event in events if event["type"] == "voc-status-changed"]
    assert status_events
    assert status_events[-1]["actorUserId"] == admin["userId"]
    assert status_events[-1]["relatedVocId"] == created["vocId"]
    assert status_events[-1]["metadata"] == {
        "previousStatus": "new",
        "newStatus": "confirmed",
    }


def test_voc_action_requires_session_token(handler_module):
    response = handler_module.handler(_event("list-voc", None))
    _assert_error(response, 401, "unauthorized")


def test_admin_account_cannot_create_voc(handler_module, create_user, make_session_token, other_user_id):
    admin = create_user("admin1", user_id=other_user_id, role="admin")
    response = handler_module.handler(
        _event("create-voc", make_session_token(admin["userId"]), category="other", title="t", body="b")
    )
    _assert_error(response, 403, "forbidden")
    assert "관리자" in _json(response)["error"]["message"]


@pytest.mark.parametrize(
    "fields",
    [
        {"category": "nope", "title": "t", "body": "b"},
        {"category": "bug", "title": "", "body": "b"},
        {"category": "bug", "title": "x" * 101, "body": "b"},
        {"category": "bug", "title": "t", "body": " "},
    ],
)
def test_create_voc_validates_input(handler_module, create_user, make_session_token, acting_user_id, fields):
    member = create_user("member1", user_id=acting_user_id)
    response = handler_module.handler(_event("create-voc", make_session_token(member["userId"]), **fields))
    _assert_error(response, 400, "bad_request")


def test_admin_reply_flow_visible_to_owner_only(
    handler_module, create_user, make_session_token, acting_user_id, other_user_id
):
    member = create_user("member1", user_id=acting_user_id)
    admin = create_user("admin1", user_id=other_user_id, role="admin")
    other = create_user("member2", user_id="u_1111111111111111")
    member_token = make_session_token(member["userId"])
    admin_token = make_session_token(admin["userId"])
    created = _create_voc(handler_module, member_token)

    forbidden = handler_module.handler(
        _event("admin-reply", member_token, ownerId=member["userId"], vocId=created["vocId"], body="자가 답변")
    )
    _assert_error(forbidden, 403, "forbidden")

    for bad in ("", " ", "x" * 5001):
        resp = handler_module.handler(
            _event("admin-reply", admin_token, ownerId=member["userId"], vocId=created["vocId"], body=bad)
        )
        _assert_error(resp, 400, "bad_request")

    replied = _json(handler_module.handler(
        _event("admin-reply", admin_token, ownerId=member["userId"], vocId=created["vocId"], body="답변입니다")
    ))["voc"]
    assert replied["reply"]["body"] == "답변입니다"
    assert replied["reply"]["authorLoginId"] == admin["loginId"]
    first_created_at = replied["reply"]["createdAt"]

    edited = _json(handler_module.handler(
        _event("admin-reply", admin_token, ownerId=member["userId"], vocId=created["vocId"], body="수정 답변")
    ))["voc"]
    assert edited["reply"]["body"] == "수정 답변"
    assert edited["reply"]["createdAt"] == first_created_at

    fetched = _json(handler_module.handler(_event("get-voc", member_token, vocId=created["vocId"])))["voc"]
    assert fetched["reply"]["body"] == "수정 답변"
    listed = _json(handler_module.handler(_event("list-voc", member_token)))["records"]
    assert listed[0]["hasReply"] is True

    admin_records = _json(handler_module.handler(_event("admin-list-voc", admin_token)))["records"]
    assert admin_records[0]["hasReply"] is True
    assert admin_records[0]["title"] == "온보딩 의견"
    assert admin_records[0]["category"] == "suggestion"
    assert admin_records[0]["ownerLoginId"] == member["loginId"]

    other_get = handler_module.handler(
        _event("get-voc", make_session_token(other["userId"]), vocId=created["vocId"])
    )
    _assert_error(other_get, 404, "not_found")

    missing = handler_module.handler(
        _event("admin-reply", admin_token, ownerId=member["userId"], vocId="voc_" + "0" * 32, body="x")
    )
    _assert_error(missing, 404, "not_found")

def test_admin_get_voc_returns_full_record(
    handler_module, create_user, make_session_token, acting_user_id, other_user_id
):
    member = create_user("member1", user_id=acting_user_id)
    admin = create_user("admin1", user_id=other_user_id, role="admin")
    member_token = make_session_token(member["userId"])
    admin_token = make_session_token(admin["userId"])
    created = _create_voc(handler_module, member_token)
    handler_module.handler(
        _event("admin-reply", admin_token, ownerId=member["userId"], vocId=created["vocId"], body="전체 답변")
    )

    forbidden = handler_module.handler(
        _event("admin-get-voc", member_token, ownerId=member["userId"], vocId=created["vocId"])
    )
    _assert_error(forbidden, 403, "forbidden")

    voc = _json(handler_module.handler(
        _event("admin-get-voc", admin_token, ownerId=member["userId"], vocId=created["vocId"])
    ))["voc"]
    assert voc["body"] == created["body"]
    assert voc["reply"]["body"] == "전체 답변"
    assert voc["ownerLoginId"] == member["loginId"]

    missing = handler_module.handler(
        _event("admin-get-voc", admin_token, ownerId=member["userId"], vocId="voc_" + "0" * 32)
    )
    _assert_error(missing, 404, "not_found")

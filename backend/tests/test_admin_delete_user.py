"""admin-delete-user: permanent removal of a non-administrator account and its data."""
from types import SimpleNamespace

import pytest

import auth_service
import auth_store
import s3store
from errors import MablopError

NOW = 1_800_000_000


@pytest.fixture
def admin_ctx(s3_bucket, signing_secret, create_user):
    record = create_user("adminuser", role="admin")
    return SimpleNamespace(user=record, user_id=record["userId"])


def _keys(prefix):
    return sorted(o["key"] for o in s3store.list_keys(prefix))


def _seed(user_id):
    for rel in ("raw-posts/a.txt", "metadata/grants.json", "metadata/drafts.json", "generated-posts/d1.md", "images/i.png"):
        s3store.put_bytes(f"users/{user_id}/{rel}", b"x")


def test_delete_removes_all_user_data_and_lookup(admin_ctx, create_user):
    victim = create_user("jhe8887", status="disabled")
    other = create_user("otheruser")
    _seed(victim["userId"])
    _seed(other["userId"])
    s3store.put_bytes("raw-posts/legacy.txt", b"legacy")
    s3store.put_json("users/_auth/legacy-owner.json", {"userId": other["userId"]})
    other_before = _keys(f"users/{other['userId']}/")

    result = auth_service.admin_delete_user({"userId": victim["userId"]}, admin_ctx, now=NOW)

    assert result == {"userId": victim["userId"], "deleted": True}
    assert _keys(f"users/{victim['userId']}/") == []
    assert auth_store.find_user_id("jhe8887") is None
    assert auth_store.get_user(victim["userId"]) is None
    assert _keys(f"users/{other['userId']}/") == other_before
    assert _keys("raw-posts/") == ["raw-posts/legacy.txt"]
    assert s3store.get_json("users/_auth/legacy-owner.json") == {"userId": other["userId"]}


def test_same_login_id_can_register_again_after_delete(admin_ctx, create_user):
    victim = create_user("jhe8887", status="pending")
    auth_service.admin_delete_user({"userId": victim["userId"]}, admin_ctx, now=NOW)

    result = auth_service.register(
        {"loginId": "jhe8887", "name": "Again", "password": "long-enough-password"}, None, now=NOW
    )

    assert auth_store.find_user_id("jhe8887") is not None
    assert result


def test_delete_is_repeatable_after_a_partial_failure(admin_ctx, create_user):
    victim = create_user("jhe8887")
    _seed(victim["userId"])
    s3store.delete(f"users/{victim['userId']}/raw-posts/a.txt")  # simulate an interrupted earlier run

    auth_service.admin_delete_user({"userId": victim["userId"]}, admin_ctx, now=NOW)

    assert _keys(f"users/{victim['userId']}/") == []


@pytest.mark.parametrize("user_id", ["u_0000000000000000", "bad", "", 5, None])
def test_unknown_target_is_404(admin_ctx, user_id):
    with pytest.raises(MablopError) as err:
        auth_service.admin_delete_user({"userId": user_id}, admin_ctx, now=NOW)
    assert err.value.code == "not_found"
    assert err.value.status == 404


def test_administrator_cannot_be_deleted(admin_ctx):
    with pytest.raises(MablopError) as err:
        auth_service.admin_delete_user({"userId": admin_ctx.user_id}, admin_ctx, now=NOW)
    assert err.value.code == "bad_request"
    assert auth_store.get_user(admin_ctx.user_id) is not None


def test_member_gets_403_through_the_handler(s3_bucket, signing_secret, create_user, make_session_token):
    import json

    import handler

    member = create_user("member1")
    victim = create_user("victim01")
    resp = handler.handler(
        {
            "headers": {"X-Mablop-Token": make_session_token(member["userId"])},
            "body": json.dumps({"action": "admin-delete-user", "userId": victim["userId"]}),
        }
    )
    assert resp["statusCode"] == 403
    assert auth_store.get_user(victim["userId"]) is not None
"""Unit tests for auth_store (task 2.9; Req 1.6, 9.3, 17.9)."""
import pytest

import auth_crypto
import auth_store
import s3store
from errors import MablopError

NEW_USER_ID = "u_aaaaaaaaaaaaaaaa"
OTHER_USER_ID = "u_bbbbbbbbbbbbbbbb"
GHOST_USER_ID = "u_cccccccccccccccc"  # never has an account
T0 = 1_700_000_000_000  # base time in epoch ms


def _lookup(login_id):
    return s3store.get_json(f"users/_auth/login/{login_id}.json")


def _lookup_exists(login_id):
    return s3store.key_exists(f"users/_auth/login/{login_id}.json")


def _put_dangling_lookup(login_id, created_ms, user_id=GHOST_USER_ID):
    s3store.put_json(
        f"users/_auth/login/{login_id}.json",
        {"userId": user_id, "createdAt": auth_crypto.to_iso_ms(created_ms)},
    )


# --- claim_login_id ----------------------------------------------------------------------


def test_claim_created_writes_lookup(s3_bucket):
    assert auth_store.claim_login_id("newbie", NEW_USER_ID, T0) == "created"
    assert _lookup("newbie") == {"userId": NEW_USER_ID, "createdAt": auth_crypto.to_iso_ms(T0)}


def test_claim_exists_when_account_exists_keeps_lookup(s3_bucket, create_user):
    record = create_user("owner1")
    before = _lookup("owner1")

    # Even far past the 60 s window, a lookup whose account exists is never replaced.
    assert auth_store.claim_login_id("owner1", NEW_USER_ID, T0 + 10 * 60_000) == "exists"
    assert _lookup("owner1") == before
    assert before["userId"] == record["userId"]


def test_claim_exists_for_dangling_lookup_at_59_999_ms(s3_bucket):
    _put_dangling_lookup("dangle1", T0)
    before = _lookup("dangle1")

    assert auth_store.claim_login_id("dangle1", NEW_USER_ID, T0 + 59_999) == "exists"
    assert _lookup("dangle1") == before


def test_claim_healed_for_dangling_lookup_at_60_000_ms(s3_bucket):
    _put_dangling_lookup("dangle2", T0)

    now = T0 + 60_000
    assert auth_store.claim_login_id("dangle2", NEW_USER_ID, now) == "healed"
    assert _lookup("dangle2") == {"userId": NEW_USER_ID, "createdAt": auth_crypto.to_iso_ms(now)}


def test_claim_after_heal_is_exists_while_new_entry_is_young(s3_bucket):
    _put_dangling_lookup("dangle3", T0)
    assert auth_store.claim_login_id("dangle3", NEW_USER_ID, T0 + 60_000) == "healed"

    # The healed entry has no account yet and is fresh, so a second claimer must not steal it.
    assert auth_store.claim_login_id("dangle3", OTHER_USER_ID, T0 + 60_000 + 59_999) == "exists"
    assert _lookup("dangle3")["userId"] == NEW_USER_ID


def test_claim_rejects_invalid_login_id(s3_bucket):
    with pytest.raises(MablopError) as err:
        auth_store.claim_login_id("ab", NEW_USER_ID, T0)
    assert err.value.code == "bad_request"


# --- release_login_id --------------------------------------------------------------------


def test_release_deletes_lookup_pointing_to_same_user(s3_bucket):
    auth_store.claim_login_id("release1", NEW_USER_ID, T0)

    auth_store.release_login_id("release1", NEW_USER_ID)

    assert not _lookup_exists("release1")


def test_release_keeps_lookup_pointing_to_another_user(s3_bucket):
    auth_store.claim_login_id("release2", OTHER_USER_ID, T0)

    auth_store.release_login_id("release2", NEW_USER_ID)

    assert _lookup("release2")["userId"] == OTHER_USER_ID


def test_release_missing_lookup_is_noop(s3_bucket, s3_calls):
    auth_store.release_login_id("release3", NEW_USER_ID)

    assert s3_calls.writes() == []


# --- list_users --------------------------------------------------------------------------


def test_list_users_excludes_auth_and_skips_prefix_without_account(s3_bucket, create_user):
    first = create_user("listed1")
    second = create_user("listed2", role="admin")
    # A user prefix with data but no account.json must be skipped.
    s3store.put_json(f"users/{GHOST_USER_ID}/metadata/grants.json", {"grants": []})
    # Extra objects under users/_auth/ (lookups, pointer) must never be listed as users.
    s3store.put_json("users/_auth/admin.json", {"userId": second["userId"]})

    records = auth_store.list_users()

    assert sorted(r["userId"] for r in records) == sorted([first["userId"], second["userId"]])
    assert all(r["userId"] != GHOST_USER_ID for r in records)


def test_list_users_empty_bucket(s3_bucket):
    assert auth_store.list_users() == []


# --- admin pointer -----------------------------------------------------------------------


def test_admin_pointer_none_before_commit(s3_bucket):
    assert auth_store.admin_pointer() is None


def test_commit_admin_pointer_is_create_once(s3_bucket):
    assert auth_store.commit_admin_pointer(NEW_USER_ID) is True
    assert auth_store.admin_pointer() == NEW_USER_ID

    # A second (racing) bootstrap loses and does not overwrite the pointer.
    assert auth_store.commit_admin_pointer(OTHER_USER_ID) is False
    assert auth_store.admin_pointer() == NEW_USER_ID


# --- find_user_id ------------------------------------------------------------------------


def test_find_user_id_uses_single_s3_read(s3_bucket, create_user, s3_calls):
    record = create_user("finder1")
    s3_calls.clear()

    assert auth_store.find_user_id("finder1") == record["userId"]

    assert len(s3_calls.calls) == 1
    assert s3_calls.calls[0]["op"] == "GetObject"
    assert s3_calls.calls[0]["key"] == "users/_auth/login/finder1.json"


def test_find_user_id_unknown_login_returns_none(s3_bucket):
    assert auth_store.find_user_id("nobody1") is None


def test_find_user_id_malformed_login_returns_none_without_s3_read(s3_bucket, s3_calls):
    assert auth_store.find_user_id("A!") is None
    assert s3_calls.calls == []


# --- update_user -------------------------------------------------------------------------


def test_update_user_missing_account_raises_not_found_and_writes_nothing(s3_bucket, s3_calls):
    with pytest.raises(MablopError) as err:
        auth_store.update_user(NEW_USER_ID, lambda record: {**record, "name": "x"})

    assert err.value.code == "not_found"
    assert s3_calls.writes() == []


def test_update_user_persists_mutation(s3_bucket, create_user):
    record = create_user("updater1")

    updated = auth_store.update_user(record["userId"], lambda r: {**r, "name": "바뀐이름"})

    assert updated["name"] == "바뀐이름"
    assert auth_store.get_user(record["userId"])["name"] == "바뀐이름"

"""Unit tests for auth_service: one or two representative flows per action (user-auth-admin 4.13).

Covered: response messages equal to the Korean literals of the requirements (not the constants),
Session_Token lifetime of 8 hours with no refresh mechanism, registration closing at 10 pending
accounts, the empty workspace of a bootstrapped administrator, resuming an interrupted bootstrap,
and the `not_applicable` legacyBinding for approved accounts other than the Legacy_Owner.

Cases already covered by the property tests (test_auth_*_property.py, test_bootstrap_property.py)
are not repeated in depth. Runs against moto S3/SSM with a low PBKDF2 iteration count. The
approval of the Legacy_Owner `jhe8888` is covered by the wiring tests at the end (task 10.2).

Requirements: 1.2, 2.2, 5.8, 9.4, 9.18, 17.9
"""
import json
import time
from types import SimpleNamespace

import pytest
from botocore.exceptions import ClientError

import auth_crypto
import auth_service
import auth_store
import handler
import style_learner
import user_scope
from errors import MablopError

BASE_NOW = 1_800_000_000  # epoch seconds: 2027-01-15T08:00:00Z
PASSWORD = "correct-horse-battery"
NEW_PASSWORD = "another-long-password"

# Literals copied from requirements.md / design.md.
MSG_REGISTER_ACCEPTED = "가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다."
MSG_LOGIN_FAILED = "아이디 또는 비밀번호가 올바르지 않습니다."
MSG_PENDING = "승인 대기 중인 계정입니다."
MSG_DISABLED = "사용이 중지된 계정입니다."
MSG_LOGIN_BLOCKED = "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
MSG_REGISTER_CLOSED = "현재 가입 신청을 받을 수 없습니다. 관리자에게 문의하세요."
MSG_INVALID_LOGIN_ID = "아이디는 영문 소문자, 숫자, 밑줄(_), 하이픈(-)으로 4~32자여야 합니다."
MSG_INVALID_NAME = "이름은 1~30자여야 합니다."
MSG_INVALID_PASSWORD = "비밀번호는 10~128자여야 합니다."
MSG_SAME_PASSWORD = "새 비밀번호는 현재 비밀번호와 달라야 합니다."
MSG_BAD_CURRENT = "현재 비밀번호가 올바르지 않습니다."
MSG_BOOTSTRAP_DONE = "관리자 계정이 생성되었습니다."
MSG_ADMIN_EXISTS = "이미 관리자 계정이 있습니다."
MSG_ADMIN_TARGET = "관리자 계정은 변경할 수 없습니다."
MSG_NOT_FOUND_TARGET = "대상 사용자를 찾을 수 없습니다."
MSG_NEED_PENDING_APPROVE = "승인 대기 상태의 계정만 승인할 수 있습니다."
MSG_NEED_PENDING_REJECT = "승인 대기 상태의 계정만 거절할 수 있습니다."
MSG_NEED_ACTIVE_DISABLE = "활성 상태의 계정만 비활성화할 수 있습니다."
MSG_NEED_DISABLED_ENABLE = "비활성 상태의 계정만 재활성화할 수 있습니다."

ADMIN_POINTER_KEY = "users/_auth/admin.json"


@pytest.fixture(autouse=True)
def fast_pbkdf2(monkeypatch):
    """Low PBKDF2 iteration count for hashing and for the dummy verification."""
    original = auth_crypto.hash_password
    monkeypatch.setattr(
        auth_crypto, "hash_password", lambda password, iterations=4: original(password, iterations=4)
    )
    monkeypatch.setattr(auth_crypto, "PBKDF2_ITERATIONS", 4)


@pytest.fixture
def env(s3_bucket, signing_secret):
    """moto S3 bucket and Signing_Secret."""
    return None


def _ctx(record):
    return SimpleNamespace(user=record, user_id=record["userId"])


def _raises(code, message, fn, *args, **kwargs):
    with pytest.raises(MablopError) as excinfo:
        fn(*args, **kwargs)
    assert excinfo.value.code == code
    assert excinfo.value.message == message
    return excinfo.value


def _stored(login_id):
    user_id = auth_store.find_user_id(login_id)
    return auth_store.get_user(user_id) if user_id else None


def _login(login_id, password, now=BASE_NOW):
    return auth_service.login({"loginId": login_id, "password": password}, None, now=now)


def _register(login_id, name="홍길동", password=PASSWORD, now=BASE_NOW):
    return auth_service.register(
        {"loginId": login_id, "name": name, "password": password}, None, now=now
    )


# --- register ---------------------------------------------------------------------------


def test_register_creates_pending_member_with_literal_message(env):
    result = _register("newmember", name="  홍길동  ")

    assert result == {"message": MSG_REGISTER_ACCEPTED}
    record = _stored("newmember")
    assert record["role"] == "member"
    assert record["status"] == "pending"
    assert record["name"] == "홍길동"
    assert record["approvedAt"] is None
    assert record["credentialsChangedAt"] == record["createdAt"]


def test_register_duplicate_login_id_returns_same_message_without_overwrite(env):
    _register("newmember", name="먼저")
    first = _stored("newmember")

    assert _register("newmember", name="나중", password=NEW_PASSWORD) == {"message": MSG_REGISTER_ACCEPTED}
    assert _stored("newmember") == first


@pytest.mark.parametrize(
    "body, message",
    [
        ({"loginId": "AB", "name": "홍길동", "password": PASSWORD}, MSG_INVALID_LOGIN_ID),
        ({"loginId": "newmember", "name": "   ", "password": PASSWORD}, MSG_INVALID_NAME),
        ({"loginId": "newmember", "name": "홍길동", "password": "short"}, MSG_INVALID_PASSWORD),
    ],
)
def test_register_invalid_field_is_bad_request_with_field_message(env, body, message):
    _raises("bad_request", message, auth_service.register, body, None, now=BASE_NOW)
    assert auth_store.find_user_id("newmember") is None


def test_register_closes_at_ten_pending_accounts(env, create_user):
    for index in range(9):
        create_user(f"pending{index:02d}", status="pending")
    assert _register("ninth-pending")["message"] == MSG_REGISTER_ACCEPTED  # the 10th pending account

    error = _raises("rate_limited", MSG_REGISTER_CLOSED, _register, "eleventh-one")
    assert error.status == 429
    assert auth_store.find_user_id("eleventh-one") is None


def test_register_does_not_count_non_pending_accounts_toward_the_cap(env, create_user):
    for index in range(10):
        create_user(f"active{index:02d}", status="active")

    assert _register("fresh-member")["message"] == MSG_REGISTER_ACCEPTED


# --- login ------------------------------------------------------------------------------


def test_login_returns_token_valid_for_exactly_eight_hours(env, create_user):
    record = create_user("tester", name="테스터")

    result = _login("tester", "correct-horse-battery")

    assert result["user"] == {"name": "테스터", "loginId": "tester", "role": "member"}
    assert result["expiresAt"] == "2027-01-15T16:00:00.000Z"
    payload = auth_crypto.verify_token(result["token"], BASE_NOW * 1000)
    assert payload.uid == record["userId"]
    assert payload.iat == BASE_NOW * 1000
    assert payload.exp - payload.iat == 8 * 60 * 60 * 1000
    assert auth_crypto.TOKEN_TTL_MS == 8 * 60 * 60 * 1000
    # the token stops working once the 8 hours are over
    with pytest.raises(MablopError) as expired:
        auth_crypto.verify_token(result["token"], payload.exp + 1)
    assert expired.value.status == 401


def test_there_is_no_refresh_mechanism(env):
    names = [name for name in dir(auth_service) if "refresh" in name.lower() or "renew" in name.lower()]
    assert names == []
    names = [name for name in dir(auth_crypto) if "refresh" in name.lower() or "renew" in name.lower()]
    assert names == []
    assert [action for action in handler.DISPATCH if "refresh" in action or "renew" in action] == []


def test_login_wrong_password_and_unknown_login_id_share_one_401(env, create_user):
    create_user("tester")

    wrong = _raises("unauthorized", MSG_LOGIN_FAILED, _login, "tester", "not-the-password")
    unknown = _raises("unauthorized", MSG_LOGIN_FAILED, _login, "nobody-here", PASSWORD)
    assert wrong.status == unknown.status == 401


def test_login_pending_and_disabled_accounts_are_forbidden_with_literal_messages(env, create_user):
    create_user("waiting", status="pending")
    create_user("stopped", status="disabled")

    pending = _raises("forbidden", MSG_PENDING, _login, "waiting", PASSWORD)
    disabled = _raises("forbidden", MSG_DISABLED, _login, "stopped", PASSWORD)
    assert pending.status == disabled.status == 403


def test_login_is_blocked_after_five_failures_even_with_the_right_password(env, create_user):
    create_user("tester")
    # Throttle timestamps use the real clock (the failure path does not take `now`), so use it here.
    for _ in range(5):
        _raises("unauthorized", MSG_LOGIN_FAILED, _login, "tester", "not-the-password", now=None)

    error = _raises("rate_limited", MSG_LOGIN_BLOCKED, _login, "tester", PASSWORD, now=None)
    assert error.status == 429
    # ten minutes later the block is over
    assert _login("tester", PASSWORD, now=time.time() + 601)["user"]["loginId"] == "tester"


# --- get-me -----------------------------------------------------------------------------


def test_get_me_returns_exactly_the_public_fields(env, create_user):
    record = create_user("tester", name="테스터")

    result = auth_service.get_me({}, _ctx(record), now=BASE_NOW)

    assert result == {
        "userId": record["userId"],
        "loginId": "tester",
        "name": "테스터",
        "role": "member",
        "status": "active",
        "createdAt": record["createdAt"],
    }


# --- change-password --------------------------------------------------------------------


def test_change_password_replaces_credentials_and_returns_a_fresh_token(env, create_user):
    record = create_user("tester")

    result = auth_service.change_password(
        {"currentPassword": PASSWORD, "newPassword": NEW_PASSWORD}, _ctx(record), now=BASE_NOW
    )

    assert set(result) == {"token", "expiresAt"}
    assert result["expiresAt"] == "2027-01-15T16:00:00.000Z"
    stored = auth_store.get_user(record["userId"])
    assert stored["credentialsChangedAt"] == "2027-01-15T08:00:00.000Z"
    payload = auth_crypto.verify_token(result["token"], BASE_NOW * 1000)
    assert payload.iat == auth_crypto.from_iso_ms(stored["credentialsChangedAt"])
    assert _login("tester", NEW_PASSWORD, now=BASE_NOW + 1)["user"]["loginId"] == "tester"
    _raises("unauthorized", MSG_LOGIN_FAILED, _login, "tester", PASSWORD, now=BASE_NOW + 2)


def test_change_password_wrong_current_is_bad_request_not_401(env, create_user):
    record = create_user("tester")

    error = _raises(
        "bad_request",
        MSG_BAD_CURRENT,
        auth_service.change_password,
        {"currentPassword": "not-the-password", "newPassword": NEW_PASSWORD},
        _ctx(record),
        now=BASE_NOW,
    )
    assert error.status == 400
    assert auth_store.get_user(record["userId"]) == record


def test_change_password_rejects_same_and_short_new_passwords(env, create_user):
    record = create_user("tester")

    _raises(
        "bad_request",
        MSG_SAME_PASSWORD,
        auth_service.change_password,
        {"currentPassword": PASSWORD, "newPassword": PASSWORD},
        _ctx(record),
        now=BASE_NOW,
    )
    _raises(
        "bad_request",
        MSG_INVALID_PASSWORD,
        auth_service.change_password,
        {"currentPassword": PASSWORD, "newPassword": "short"},
        _ctx(record),
        now=BASE_NOW,
    )


# --- bootstrap-admin --------------------------------------------------------------------


def _bootstrap(login_id="adminuser", name="관리자", password="long-enough-password", now=BASE_NOW):
    return auth_service.bootstrap_admin(
        {"loginId": login_id, "name": name, "password": password}, None, now=now
    )


def test_bootstrap_creates_active_admin_then_closes_with_literal_message(env):
    result = _bootstrap()

    assert result == {
        "message": MSG_BOOTSTRAP_DONE,
        "user": {"loginId": "adminuser", "name": "관리자", "role": "admin"},
    }
    admin = _stored("adminuser")
    assert (admin["role"], admin["status"]) == ("admin", "active")
    assert auth_store.admin_pointer() == admin["userId"]

    error = _raises("forbidden", MSG_ADMIN_EXISTS, _bootstrap, "otheradmin")
    assert error.status == 403


def test_bootstrapped_admin_learning_status_is_empty(env):
    """get-learning-status is 0/0 for the new administrator."""
    _bootstrap()
    admin_id = _stored("adminuser")["userId"]

    with user_scope.bind(admin_id):
        assert style_learner.get_learning_status() == {"processedPosts": 0, "chunks": 0, "posts": []}


def test_bootstrapped_admin_learning_status_is_empty_even_with_legacy_status_file(env):
    import s3store

    s3store.put_json(
        "metadata/learning-status.json",
        {"processedPosts": 9, "chunks": 31, "posts": ["p1", "p2"]},
    )
    _bootstrap()
    admin_id = _stored("adminuser")["userId"]

    with user_scope.bind(admin_id):
        assert style_learner.get_learning_status() == {"processedPosts": 0, "chunks": 0, "posts": []}


def test_interrupted_bootstrap_resumes_the_same_admin_account(env, s3_calls):
    s3_calls.fail_on("PutObject", key=ADMIN_POINTER_KEY, times=1)
    with pytest.raises(ClientError):
        _bootstrap(name="처음 이름", password="first-long-password")
    interrupted = _stored("adminuser")
    assert interrupted["role"] == "admin"
    assert auth_store.admin_pointer() is None
    s3_calls.clear_failures()

    result = _bootstrap(name="다시 이름", password="second-long-password", now=BASE_NOW + 5)

    assert result["message"] == MSG_BOOTSTRAP_DONE
    resumed = _stored("adminuser")
    assert resumed["userId"] == interrupted["userId"]
    assert resumed["name"] == "다시 이름"
    assert auth_store.admin_pointer() == interrupted["userId"]
    assert _login("adminuser", "second-long-password", now=BASE_NOW + 6)["user"]["role"] == "admin"
    _raises("unauthorized", MSG_LOGIN_FAILED, _login, "adminuser", "first-long-password", now=BASE_NOW + 7)


# --- administrator account actions ------------------------------------------------------


@pytest.fixture
def admin_ctx(env, create_user):
    return _ctx(create_user("adminuser", role="admin", name="관리자"))


def test_admin_list_users_puts_pending_first_with_public_fields_only(admin_ctx, create_user):
    older_active = create_user("olderone", now_ms=1_700_000_000_000)
    pending = create_user("waiting", status="pending", now_ms=1_700_000_500_000)

    users = auth_service.admin_list_users({}, admin_ctx, now=BASE_NOW)["users"]

    assert [u["loginId"] for u in users] == ["waiting", "olderone", "adminuser"]
    assert users[0]["userId"] == pending["userId"]
    assert users[1]["userId"] == older_active["userId"]
    assert set(users[0]) == {"userId", "loginId", "name", "role", "status", "createdAt", "approvedAt"}


def test_admin_approve_user_activates_and_reports_not_applicable_binding(admin_ctx, create_user):
    target = create_user("waiting", status="pending")

    result = auth_service.admin_approve_user({"userId": target["userId"]}, admin_ctx, now=BASE_NOW)

    assert result == {
        "userId": target["userId"],
        "status": "active",
        "legacyBinding": {
            "status": "not_applicable",
            "copied": 0,
            "skipped": 0,
            "failedKeys": [],
            "divergedKeys": [],
            "message": None,
        },
    }
    stored = auth_store.get_user(target["userId"])
    assert stored["status"] == "active"
    assert stored["approvedAt"] == "2027-01-15T08:00:00.000Z"


def test_admin_approve_user_requires_pending_status(admin_ctx, create_user):
    active = create_user("already")

    error = _raises(
        "bad_request",
        MSG_NEED_PENDING_APPROVE,
        auth_service.admin_approve_user,
        {"userId": active["userId"]},
        admin_ctx,
        now=BASE_NOW,
    )
    assert error.status == 400
    assert auth_store.get_user(active["userId"]) == active


def test_admin_reject_user_removes_account_and_frees_the_login_id(admin_ctx, create_user):
    target = create_user("waiting", status="pending")

    result = auth_service.admin_reject_user({"userId": target["userId"]}, admin_ctx, now=BASE_NOW)

    assert result == {"userId": target["userId"], "removed": True}
    assert auth_store.get_user(target["userId"]) is None
    assert auth_store.find_user_id("waiting") is None
    assert _register("waiting")["message"] == MSG_REGISTER_ACCEPTED  # the loginId can register again


def test_admin_reject_user_requires_pending_status(admin_ctx, create_user):
    active = create_user("already")

    _raises(
        "bad_request",
        MSG_NEED_PENDING_REJECT,
        auth_service.admin_reject_user,
        {"userId": active["userId"]},
        admin_ctx,
        now=BASE_NOW,
    )
    assert auth_store.get_user(active["userId"]) == active


def test_admin_disable_then_enable_cycle_and_token_cutoff(admin_ctx, create_user):
    target = create_user("tester")

    disabled = auth_service.admin_disable_user({"userId": target["userId"]}, admin_ctx, now=BASE_NOW)
    assert disabled == {"userId": target["userId"], "status": "disabled"}
    stored = auth_store.get_user(target["userId"])
    assert stored["status"] == "disabled"
    assert stored["credentialsChangedAt"] == "2027-01-15T08:00:00.000Z"
    _raises("forbidden", MSG_DISABLED, _login, "tester", PASSWORD, now=BASE_NOW + 1)

    enabled = auth_service.admin_enable_user({"userId": target["userId"]}, admin_ctx, now=BASE_NOW + 2)
    assert enabled == {"userId": target["userId"], "status": "active"}
    assert _login("tester", PASSWORD, now=BASE_NOW + 3)["user"]["loginId"] == "tester"


def test_admin_disable_and_enable_require_the_right_status(admin_ctx, create_user):
    pending = create_user("waiting", status="pending")
    active = create_user("already")

    _raises(
        "bad_request",
        MSG_NEED_ACTIVE_DISABLE,
        auth_service.admin_disable_user,
        {"userId": pending["userId"]},
        admin_ctx,
        now=BASE_NOW,
    )
    _raises(
        "bad_request",
        MSG_NEED_DISABLED_ENABLE,
        auth_service.admin_enable_user,
        {"userId": active["userId"]},
        admin_ctx,
        now=BASE_NOW,
    )


def test_admin_reset_password_returns_temporary_password_once_and_clears_the_block(
    admin_ctx, create_user
):
    target = create_user("tester")
    for _ in range(5):
        _raises("unauthorized", MSG_LOGIN_FAILED, _login, "tester", "not-the-password", now=None)
    _raises("rate_limited", MSG_LOGIN_BLOCKED, _login, "tester", PASSWORD, now=None)

    result = auth_service.admin_reset_password({"userId": target["userId"]}, admin_ctx, now=time.time())

    temporary = result["temporaryPassword"]
    assert set(result) == {"userId", "temporaryPassword"}
    assert len(temporary) == 16
    stored = auth_store.get_user(target["userId"])
    assert temporary not in json.dumps(stored)
    assert _login("tester", temporary, now=None)["user"]["loginId"] == "tester"
    _raises("unauthorized", MSG_LOGIN_FAILED, _login, "tester", PASSWORD, now=None)


@pytest.mark.parametrize(
    "action",
    [
        "admin_approve_user",
        "admin_reject_user",
        "admin_disable_user",
        "admin_enable_user",
        "admin_reset_password",
    ],
)
def test_admin_actions_refuse_administrator_and_unknown_targets(admin_ctx, create_user, action):
    fn = getattr(auth_service, action)
    other_admin = create_user("secondadmin", role="admin")

    _raises("bad_request", MSG_ADMIN_TARGET, fn, {"userId": other_admin["userId"]}, admin_ctx, now=BASE_NOW)
    _raises("not_found", MSG_NOT_FOUND_TARGET, fn, {"userId": "u_0000000000000000"}, admin_ctx, now=BASE_NOW)
    error = _raises("not_found", MSG_NOT_FOUND_TARGET, fn, {"userId": "not-a-user-id"}, admin_ctx, now=BASE_NOW)
    assert error.status == 404
    assert auth_store.get_user(other_admin["userId"]) == other_admin


def test_approved_non_owner_accounts_all_report_not_applicable_binding(admin_ctx, create_user):
    for index in range(3):
        target = create_user(f"member{index:02d}", status="pending")

        result = auth_service.admin_approve_user({"userId": target["userId"]}, admin_ctx, now=BASE_NOW)

        assert result["legacyBinding"]["status"] == "not_applicable"
        assert result["legacyBinding"]["failedKeys"] == []


# --- legacy binding wiring (task 10.2) -----------------------------------------------------


def test_approving_legacy_owner_runs_migrator_once_and_reports_summary(admin_ctx, create_user, monkeypatch):
    owner = create_user("jhe8888", status="pending")
    calls = []

    def fake_run(target, now=None):
        calls.append((target, now))
        return {"status": "succeeded", "copied": 4, "skipped": 1, "failedKeys": [], "divergedKeys": []}

    monkeypatch.setattr(auth_service.legacy_migrator, "run", fake_run)

    result = auth_service.admin_approve_user({"userId": owner["userId"]}, admin_ctx, now=BASE_NOW)

    assert calls == [(owner["userId"], BASE_NOW)]
    assert result == {
        "userId": owner["userId"],
        "status": "active",
        "legacyBinding": {
            "status": "succeeded",
            "copied": 4,
            "skipped": 1,
            "failedKeys": [],
            "divergedKeys": [],
            "message": "기존 학습 데이터와 초안을 연결했습니다.",
        },
    }


def test_legacy_migrator_exception_keeps_approval_and_hides_exception_text(admin_ctx, create_user, monkeypatch):
    owner = create_user("jhe8888", status="pending")

    def boom(target, now=None):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(auth_service.legacy_migrator, "run", boom)

    result = auth_service.admin_approve_user({"userId": owner["userId"]}, admin_ctx, now=BASE_NOW)

    assert result["status"] == "active"
    assert result["legacyBinding"]["status"] == "failed"
    assert "secret-internal-detail" not in json.dumps(result, ensure_ascii=False)
    assert auth_store.get_user(owner["userId"])["status"] == "active"


def test_approving_other_accounts_never_calls_migrator(admin_ctx, create_user, monkeypatch):
    target = create_user("someoneelse", status="pending")
    monkeypatch.setattr(
        auth_service.legacy_migrator, "run", lambda *a, **k: pytest.fail("migrator must not run")
    )

    result = auth_service.admin_approve_user({"userId": target["userId"]}, admin_ctx, now=BASE_NOW)

    assert result["legacyBinding"]["status"] == "not_applicable"

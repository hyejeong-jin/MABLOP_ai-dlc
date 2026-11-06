"""Unit tests for the legacy migration (task 10.6).

Covers: the legacy owner loginId constant, a module that imports no deletion function, the
9 posts / 31 chunks example after approving ``jhe8888`` (``get-learning-status`` and
``list-drafts`` back-fill), default and unknown target resolution of ``migrate-legacy-data``,
``bootstrap-admin`` never calling the migrator, and the Korean message literals.

Requirements: 9.4, 9.10, 9.13, 9.17, 17.9, 22.14
"""
import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import auth_crypto
import auth_service
import auth_store
import draft_history
import legacy_migrator
import s3store
import style_learner
import user_scope
from errors import MablopError

BASE_NOW = 1_800_000_000  # epoch seconds
RAW_IDS = [f"post{i}" for i in range(1, 10)]  # 9 learned posts
DRAFT_BODIES = {
    "draft-a": "# 첫 번째 초안\n\n본문입니다.",
    "draft-b": "# 두 번째 초안\n\n본문입니다.",
}

MSG_ALREADY_BOUND = "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다."
MSG_SUCCEEDED = "기존 학습 데이터와 초안을 연결했습니다."
MSG_FAILED = (
    "기존 데이터 연결에 실패했습니다. 승인은 유지되었습니다. "
    "관리자 권한으로 데이터 연결을 다시 실행하세요."
)
MSG_NOT_FOUND_TARGET = "대상 사용자를 찾을 수 없습니다."


@pytest.fixture(autouse=True)
def fast_pbkdf2(monkeypatch):
    """Low PBKDF2 iteration count so bootstrap-admin hashing stays fast."""
    original = auth_crypto.hash_password
    monkeypatch.setattr(
        auth_crypto, "hash_password", lambda password, iterations=4: original(password, iterations=4)
    )
    monkeypatch.setattr(auth_crypto, "PBKDF2_ITERATIONS", 4)


@pytest.fixture
def env(s3_bucket, signing_secret):
    """moto S3 bucket and Signing_Secret."""
    return None


@pytest.fixture
def admin_ctx(env, create_user):
    record = create_user("adminuser", role="admin", name="관리자")
    return SimpleNamespace(user=record, user_id=record["userId"])


@pytest.fixture
def legacy_data(env):
    """The shared pre-existing data: 9 posts, 31 chunks, two drafts, and the other single keys."""
    for post_id in RAW_IDS:
        s3store.put_bytes(f"raw-posts/{post_id}.txt", f"원문 {post_id}".encode("utf-8"))
    for draft_id, body in DRAFT_BODIES.items():
        s3store.put_bytes(f"generated-posts/{draft_id}.md", body.encode("utf-8"))
    s3store.put_bytes("embeddings/index.npy", b"npy-bytes")
    s3store.put_json("embeddings/index-map.json", {"chunks": 31})
    s3store.put_json("vector-index/manifest.json", {"v": 1})
    s3store.put_json("style-profile/profile.json", {"tone": "친근함"})
    s3store.put_json(
        "metadata/learning-status.json",
        {"processedPosts": 9, "chunks": 31, "posts": RAW_IDS},
    )


def _raises(code, message, fn, *args, **kwargs):
    with pytest.raises(MablopError) as excinfo:
        fn(*args, **kwargs)
    assert excinfo.value.code == code
    assert excinfo.value.message == message
    return excinfo.value


def _user_keys(user_id):
    return sorted(obj["key"] for obj in s3store.list_keys(f"users/{user_id}/"))


# --- constants and module shape ---------------------------------------------------------


def test_legacy_owner_login_id_is_jhe8888():
    assert legacy_migrator.LEGACY_OWNER_LOGIN_ID == "jhe8888"
    assert auth_service.LEGACY_OWNER_LOGIN_ID == legacy_migrator.LEGACY_OWNER_LOGIN_ID


def test_module_imports_no_deletion_function():
    source = Path(legacy_migrator.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)

    imported_modules = set()
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported_modules.add(node.module)
            imported_names.update(alias.name for alias in node.names)
    attribute_names = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}

    assert imported_modules == {"time", "auth_crypto", "auth_messages", "s3store"}
    assert not [name for name in imported_names if "delete" in name.lower() or "remove" in name.lower()]
    assert not [name for name in attribute_names if name.lower().startswith(("delete", "remove"))]


# --- approval of jhe8888: 9 posts / 31 chunks ---------------------------------------------


def test_approving_owner_copies_status_so_get_learning_status_is_9_and_31(
    admin_ctx, create_user, legacy_data
):
    owner = create_user("jhe8888", status="pending", name="주인")

    result = auth_service.admin_approve_user({"userId": owner["userId"]}, admin_ctx, now=BASE_NOW)

    assert result["legacyBinding"] == {
        "status": "succeeded",
        "copied": 9 + 2 + 5,
        "skipped": 0,
        "failedKeys": [],
        "divergedKeys": [],
        "message": MSG_SUCCEEDED,
    }
    with user_scope.bind(owner["userId"]):
        status = style_learner.get_learning_status()
    assert status["processedPosts"] == 9
    assert status["chunks"] == 31
    assert status["posts"] == RAW_IDS


def test_approving_owner_makes_list_drafts_back_fill_the_copied_drafts(
    admin_ctx, create_user, legacy_data
):
    owner = create_user("jhe8888", status="pending")
    auth_service.admin_approve_user({"userId": owner["userId"]}, admin_ctx, now=BASE_NOW)
    # The migrator never writes a Draft_Index (Req 9.9 / design); list-drafts fills it in.
    assert f"users/{owner['userId']}/metadata/drafts.json" not in _user_keys(owner["userId"])

    with user_scope.bind(owner["userId"]):
        drafts = draft_history.list_drafts({}, None, now=BASE_NOW)["drafts"]

    assert {d["draftId"] for d in drafts} == set(DRAFT_BODIES)
    assert {d["draftId"]: d["title"] for d in drafts} == {
        "draft-a": "첫 번째 초안",
        "draft-b": "두 번째 초안",
    }
    assert f"users/{owner['userId']}/metadata/drafts.json" in _user_keys(owner["userId"])


def test_approving_owner_leaves_legacy_originals_in_place(admin_ctx, create_user, legacy_data):
    owner = create_user("jhe8888", status="pending")

    auth_service.admin_approve_user({"userId": owner["userId"]}, admin_ctx, now=BASE_NOW)

    originals = {obj["key"] for obj in s3store.list_keys("raw-posts/")}
    assert originals == {f"raw-posts/{post_id}.txt" for post_id in RAW_IDS}
    assert s3store.get_json("metadata/learning-status.json")["chunks"] == 31
    assert s3store.get_bytes("generated-posts/draft-a.md").decode("utf-8") == DRAFT_BODIES["draft-a"]


# --- target resolution of migrate-legacy-data -------------------------------------------


def test_migrate_legacy_data_defaults_to_the_jhe8888_account(admin_ctx, create_user, legacy_data):
    owner = create_user("jhe8888")  # already active; no approval-time migration happened

    result = auth_service.migrate_legacy_data({}, admin_ctx, now=BASE_NOW)

    migration = result["migration"]
    assert migration["status"] == "succeeded"
    assert migration["copied"] == 16
    assert migration["rawPosts"] == {"source": 9, "destination": 9}
    assert migration["learningStatusEqual"] is True
    assert f"users/{owner['userId']}/metadata/migration.json" in _user_keys(owner["userId"])


def test_migrate_legacy_data_accepts_an_explicit_existing_target(admin_ctx, create_user, legacy_data):
    target = create_user("someoneelse")

    result = auth_service.migrate_legacy_data({"targetUserId": target["userId"]}, admin_ctx, now=BASE_NOW)

    assert result["migration"]["status"] == "succeeded"
    assert f"users/{target['userId']}/raw-posts/post1.txt" in _user_keys(target["userId"])


@pytest.mark.parametrize("target", ["u_0000000000000000", "not-a-user-id", "", 7, ["u_1"]])
def test_migrate_legacy_data_unknown_target_is_404_and_copies_nothing(admin_ctx, legacy_data, target):
    before = sorted(obj["key"] for obj in s3store.list_keys("users/"))

    error = _raises(
        "not_found",
        MSG_NOT_FOUND_TARGET,
        auth_service.migrate_legacy_data,
        {"targetUserId": target},
        admin_ctx,
        now=BASE_NOW,
    )

    assert error.status == 404
    assert sorted(obj["key"] for obj in s3store.list_keys("users/")) == before


def test_migrate_legacy_data_without_a_jhe8888_account_is_404(admin_ctx, legacy_data):
    assert auth_store.find_user_id("jhe8888") is None

    error = _raises(
        "not_found", MSG_NOT_FOUND_TARGET, auth_service.migrate_legacy_data, {}, admin_ctx, now=BASE_NOW
    )

    assert error.status == 404
    assert s3store.read_json_optional(legacy_migrator.BINDING_KEY) is None


def test_second_user_gets_403_already_bound_after_the_owner_was_bound(admin_ctx, create_user, legacy_data):
    owner = create_user("jhe8888", status="pending")
    auth_service.admin_approve_user({"userId": owner["userId"]}, admin_ctx, now=BASE_NOW)
    other = create_user("someoneelse")

    error = _raises(
        "forbidden",
        MSG_ALREADY_BOUND,
        auth_service.migrate_legacy_data,
        {"targetUserId": other["userId"]},
        admin_ctx,
        now=BASE_NOW,
    )

    assert error.status == 403
    assert _user_keys(other["userId"]) == [f"users/{other['userId']}/account.json"]


# --- bootstrap-admin never runs the migrator ------------------------------------------------


@pytest.mark.parametrize("login_id", ["adminuser", "jhe8888"])
def test_bootstrap_admin_does_not_call_the_migrator(env, legacy_data, s3_calls, monkeypatch, login_id):
    monkeypatch.setattr(
        legacy_migrator, "run", lambda *a, **k: pytest.fail("bootstrap-admin must not run the migrator")
    )
    s3_calls.clear()

    result = auth_service.bootstrap_admin(
        {"loginId": login_id, "name": "관리자", "password": "long-enough-password"}, None, now=BASE_NOW
    )

    touched = s3_calls.touched_keys()  # only what bootstrap-admin itself touched
    assert result["message"] == "관리자 계정이 생성되었습니다."
    admin_id = auth_store.find_user_id(login_id)
    assert _user_keys(admin_id) == [f"users/{admin_id}/account.json"]
    assert s3store.read_json_optional(legacy_migrator.BINDING_KEY) is None
    assert not [k for k in touched if k.startswith(("raw-posts/", "generated-posts/", "embeddings/"))]
    assert "metadata/learning-status.json" not in touched
    assert legacy_migrator.BINDING_KEY not in touched
    with user_scope.bind(admin_id):
        assert style_learner.get_learning_status() == {"processedPosts": 0, "chunks": 0, "posts": []}


# --- Korean message literals --------------------------------------------------------------


def test_legacy_message_literals_are_the_specified_korean_text(env):
    owner_id = "u_" + "1" * 16
    other_id = "u_" + "2" * 16
    s3store.put_bytes("raw-posts/a.txt", b"aaa")
    legacy_migrator.run(owner_id, now=BASE_NOW)

    already_bound = legacy_migrator.summarize(legacy_migrator.run(other_id, now=BASE_NOW))
    succeeded = legacy_migrator.summarize({"status": "succeeded"})
    failed = legacy_migrator.summarize({"status": "failed"})

    assert already_bound["message"] == MSG_ALREADY_BOUND
    assert succeeded["message"] == MSG_SUCCEEDED
    assert failed["message"] == MSG_FAILED
    assert legacy_migrator.summarize(None)["message"] == MSG_FAILED
    assert legacy_migrator.NOT_APPLICABLE["message"] is None
    assert json.dumps(already_bound, ensure_ascii=False).count("레거시") == 1

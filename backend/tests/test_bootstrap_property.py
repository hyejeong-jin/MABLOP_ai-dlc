"""Property test: bootstrap creates at most one administrator with an empty workspace
(Req 9.2, 9.3, 9.4; Property 19).

Runs auth_service.bootstrap_admin against a moto S3 bucket that already holds legacy objects
(raw-posts, embeddings, vector-index, style-profile, learning-status, generated-posts, ...).
PBKDF2 uses a low iteration count. The Legacy_Migrator is replaced by a sentinel module that
fails on any use. Production code is not modified.

Covered:
  (a) exactly one administrator (role admin, status active) from the first valid request;
  (b) the only new objects are users/{id}/account.json, users/_auth/login/{loginId}.json and
      users/_auth/admin.json; every legacy object stays byte-identical and is never touched;
  (c) the administrator's workspace is empty (nothing but account.json under users/{id}/);
  (d) every later request is 403 "이미 관리자 계정이 있습니다." with nothing read or written
      beyond the administrator pointer, even when its input is invalid;
  (e) an interrupted run is resumed without a second account, and a lost pointer race leaves
      exactly one administrator.

Feature: user-auth-admin, Property 19: Bootstrap creates at most one administrator with an
empty workspace
"""
import json
import re
import sys
import types

import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
import auth_store
import s3store
import style_learner
import user_scope
from conftest import TEST_BUCKET, TEST_REGION
from errors import MablopError

ADMIN_EXISTS_MESSAGE = "이미 관리자 계정이 있습니다."
BOOTSTRAP_DONE_MESSAGE = "관리자 계정이 생성되었습니다."

ADMIN_POINTER_KEY = "users/_auth/admin.json"
_USER_ID_RE = re.compile(r"^u_[0-9a-f]{16}$")
BASE_NOW = 1_800_000_000  # epoch seconds

# Legacy objects that always exist in the bucket (all the kinds named by Req 9.4).
_BASE_LEGACY = {
    "raw-posts/post-1.txt": "첫 번째 글".encode("utf-8"),
    "raw-posts/post-2.txt": "두 번째 글".encode("utf-8"),
    "embeddings/index.npy": b"\x93NUMPY-legacy",
    "embeddings/index-map.json": b'{"0": {"postId": "post-1"}}',
    "vector-index/manifest.json": b'{"count": 2, "dim": 4}',
    "style-profile/profile.json": b'{"tone": "legacy"}',
    "metadata/learning-status.json": b'{"processedPosts": 2, "chunks": 2, "posts": ["post-1", "post-2"]}',
    "generated-posts/draft-legacy.md": "# 레거시 초안".encode("utf-8"),
}

# --- strategies -------------------------------------------------------------------------

_post_ids = st.from_regex(r"[a-z0-9-]{1,12}", fullmatch=True)
_extra_legacy = st.dictionaries(
    keys=st.one_of(
        st.sampled_from(
            [
                "metadata/learn-checkpoint-run1.json",
                "metadata/ratelimit.json",
                "embeddings/extra.json",
            ]
        ),
        _post_ids.map(lambda i: f"raw-posts/{i}.txt"),
        _post_ids.map(lambda i: f"generated-posts/{i}.md"),
        _post_ids.map(lambda i: f"images/{i}.png"),
    ),
    values=st.binary(max_size=64),
    max_size=6,
)
_legacy = _extra_legacy.map(lambda extra: {**extra, **_BASE_LEGACY})  # base content always wins

_valid_login_ids = st.from_regex(r"[a-z0-9_-]{4,32}", fullmatch=True)
_invalid_login_ids = st.one_of(
    st.sampled_from(["", "abc", "ABCD1234", "a" * 33, "bad id!", "한글아이디입니다", "jhe8888\n"]),
    st.integers(),
    st.none(),
)
_valid_names = st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=30)
_invalid_names = st.one_of(
    st.sampled_from(["", "   ", "a" * 31, "ab\x00c"]), st.integers(), st.none()
)
_valid_passwords = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=10, max_size=128
)
_invalid_passwords = st.one_of(
    st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), max_size=9),
    st.text(alphabet="x", min_size=129, max_size=140),
    st.integers(),
    st.none(),
)


@st.composite
def _requests(draw):
    """A bootstrap request body plus whether it is valid by construction."""
    kind = draw(st.sampled_from(["valid", "valid", "bad-field", "non-dict"]))
    if kind == "non-dict":
        return draw(st.one_of(st.none(), st.integers(), st.lists(st.integers(), max_size=3))), False
    login_ok = draw(st.booleans()) if kind == "bad-field" else True
    name_ok = draw(st.booleans()) if kind == "bad-field" else True
    pw_ok = draw(st.booleans()) if kind == "bad-field" else True
    body = {
        "loginId": draw(_valid_login_ids if login_ok else _invalid_login_ids),
        "name": draw(_valid_names if name_ok else _invalid_names),
        "password": draw(_valid_passwords if pw_ok else _invalid_passwords),
    }
    return body, login_ok and name_ok and pw_ok


_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)


# --- fixtures and helpers ---------------------------------------------------------------


@pytest.fixture
def fast_hash(monkeypatch):
    """Replace hash_password with a 4-iteration version and count how often it runs."""
    original = auth_crypto.hash_password
    calls = []

    def hashed(password, iterations=4):
        calls.append(1)
        return original(password, iterations=4)

    monkeypatch.setattr(auth_service.auth_crypto, "hash_password", hashed)
    return calls


class _SentinelModule(types.ModuleType):
    """Stands in for legacy_migrator; any attribute use fails the test (Req 9.4)."""

    def __getattr__(self, name):
        if name.startswith("__"):
            raise AttributeError(name)
        raise AssertionError(f"legacy_migrator.{name} was used by bootstrap")


@pytest.fixture
def no_legacy_migrator(monkeypatch):
    """Any import or use of legacy_migrator during the test reaches the sentinel."""
    monkeypatch.setitem(sys.modules, "legacy_migrator", _SentinelModule("legacy_migrator"))


@pytest.fixture
def bucket(s3_bucket, s3_calls, fast_hash, no_legacy_migrator):
    """Bucket + S3 call recorder + fast hash + legacy sentinel."""
    s3_calls.clear_failures()
    return s3_calls


def _client():
    return boto3.client("s3", region_name=TEST_REGION)


def _reset_bucket(legacy: dict, recorder) -> None:
    """Empty the bucket, store the legacy objects, forget the recorded setup calls."""
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()
    client = _client()
    for key, data in legacy.items():
        client.put_object(Bucket=TEST_BUCKET, Key=key, Body=data)
    recorder.clear()
    recorder.clear_failures()


def _snapshot() -> dict:
    """Every object in the bucket: key -> bytes."""
    client = _client()
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = client.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
    return out


def _accounts(snapshot: dict) -> list[dict]:
    return [
        json.loads(data.decode("utf-8"))
        for key, data in snapshot.items()
        if key.startswith("users/u_") and key.endswith("/account.json")
    ]


def _new_keys(snapshot: dict, legacy: dict) -> set:
    return set(snapshot) - set(legacy)


def _expected_new_keys(login_id: str, user_id: str) -> set:
    return {
        f"users/{user_id}/account.json",
        f"users/_auth/login/{login_id}.json",
        ADMIN_POINTER_KEY,
    }


def _assert_legacy_intact(snapshot: dict, legacy: dict) -> None:
    for key, data in legacy.items():
        assert snapshot.get(key) == data, f"legacy object changed or removed: {key}"


def _bootstrap(body, now=BASE_NOW):
    return auth_service.bootstrap_admin(body, None, now=now)


# --- (a) (b) (c) (d): arbitrary request sequences ---------------------------------------


@_SETTINGS
@given(legacy=_legacy, requests=st.lists(_requests(), min_size=1, max_size=5))
def test_bootstrap_sequence_creates_one_admin_and_leaves_legacy_untouched(
    bucket, fast_hash, legacy, requests
):
    """Validates: Requirements 9.2, 9.3, 9.4"""
    recorder = bucket
    _reset_bucket(legacy, recorder)
    fast_hash.clear()

    created = None  # (body, userId) of the one request that created the administrator
    for body, valid in requests:
        before = _snapshot()
        recorder.clear()
        fast_hash.clear()

        if created is not None:
            # (d) later requests: 403 whatever the input; only the pointer is read; nothing written
            with pytest.raises(MablopError) as excinfo:
                _bootstrap(body)
            assert excinfo.value.code == "forbidden"
            assert excinfo.value.status == 403
            assert excinfo.value.message == ADMIN_EXISTS_MESSAGE
            assert recorder.writes() == []
            assert set(recorder.touched_keys()) <= {ADMIN_POINTER_KEY}
            assert fast_hash == []  # no PBKDF2 work for a rejected request
            assert _snapshot() == before
        elif not valid:
            # an invalid first request is a plain bad_request and writes nothing
            with pytest.raises(MablopError) as excinfo:
                _bootstrap(body)
            assert excinfo.value.code == "bad_request"
            assert recorder.writes() == []
            assert fast_hash == []
            assert _snapshot() == before
        else:
            result = _bootstrap(body)
            calls = list(recorder.calls)
            after = _snapshot()
            accounts = _accounts(after)
            assert len(accounts) == 1
            record = accounts[0]
            user_id = record["userId"]
            created = (body, user_id)

            # (a) one administrator, role admin, status active, input validated
            assert _USER_ID_RE.fullmatch(user_id)
            assert record["role"] == "admin"
            assert record["status"] == "active"
            assert record["loginId"] == body["loginId"]
            assert record["name"] == body["name"].strip()
            assert auth_crypto.verify_password(body["password"], record["password"]) is True
            assert record["approvedAt"] == record["createdAt"] == record["credentialsChangedAt"]
            assert result["message"] == BOOTSTRAP_DONE_MESSAGE
            assert result["user"] == {
                "loginId": body["loginId"],
                "name": record["name"],
                "role": "admin",
            }
            assert json.loads(after[ADMIN_POINTER_KEY].decode("utf-8"))["userId"] == user_id
            assert len(fast_hash) == 1

            # (b) exactly three new objects, each written once; nothing else touched
            expected = _expected_new_keys(body["loginId"], user_id)
            assert _new_keys(after, legacy) == expected
            write_calls = [c for c in calls if c["op"] in ("PutObject", "CopyObject", "DeleteObject", "DeleteObjects")]
            assert sorted(c["key"] for c in write_calls) == sorted(expected)
            assert all(c["op"] == "PutObject" for c in write_calls)
            touched = {k for c in calls for k in (c["key"], c["source"]) if k}
            assert touched <= expected
            assert not [c for c in calls if c["op"].startswith("List")]
            assert not any(k.startswith("users/") and k not in expected for k in after)
            _assert_legacy_intact(after, legacy)

            # (c) the administrator's prefix holds account.json and nothing else
            under_admin = [k for k in after if k.startswith(f"users/{user_id}/")]
            assert under_admin == [f"users/{user_id}/account.json"]

    # whole-sequence result
    final = _snapshot()
    _assert_legacy_intact(final, legacy)
    if created is None:
        assert final == legacy  # no valid request: bucket exactly as seeded
    else:
        body, user_id = created
        assert len(_accounts(final)) == 1
        assert _new_keys(final, legacy) == _expected_new_keys(body["loginId"], user_id)


@_SETTINGS
@given(legacy=_legacy, body_and_valid=_requests().filter(lambda r: r[1]))
def test_new_admin_has_an_empty_learning_workspace(bucket, legacy, body_and_valid):
    """Validates: Requirements 9.2, 9.4

    Part (c), storage side: no learning-status.json (or any user_key based object) exists under
    the administrator's prefix, so the legacy status is not visible through the user key.
    """
    _reset_bucket(legacy, bucket)
    body, _valid = body_and_valid
    _bootstrap(body)

    user_id = _accounts(_snapshot())[0]["userId"]
    keys_under_user = [k for k in _snapshot() if k.startswith(f"users/{user_id}/")]
    assert keys_under_user == [f"users/{user_id}/account.json"]
    for relative in (
        "metadata/learning-status.json",
        "raw-posts/post-1.txt",
        "style-profile/profile.json",
        "embeddings/index.npy",
        "vector-index/manifest.json",
        "generated-posts/draft-legacy.md",
        "metadata/drafts.json",
    ):
        assert s3store.read_json_optional(s3store.user_key(relative, user_id)) is None


@_SETTINGS
@given(
    extra=_extra_legacy,
    body_and_valid=_requests().filter(lambda r: r[1]),
)
def test_new_admin_get_learning_status_is_empty(bucket, extra, body_and_valid):
    """Validates: Requirements 9.2, 9.4

    Part (c), component side: style_learner.get_learning_status under user_scope.bind(admin)
    reports processedPosts 0, chunks 0, posts [].

    The legacy shared metadata/learning-status.json is present in the bucket (it is part of
    _BASE_LEGACY); style_learner reads only the user key, so the legacy numbers are not visible.
    """
    _reset_bucket({**extra, **_BASE_LEGACY}, bucket)
    body, _valid = body_and_valid
    _bootstrap(body)

    admin_id = _accounts(_snapshot())[0]["userId"]
    with user_scope.bind(admin_id):
        assert style_learner.get_learning_status() == {"processedPosts": 0, "chunks": 0, "posts": []}


# --- legacy migrator is not involved ------------------------------------------------------


def test_bootstrap_does_not_reference_or_import_the_legacy_migrator(bucket):
    """Validates: Requirements 9.4

    The sentinel in sys.modules fails on any use. Here the action also runs on a bucket that
    holds every legacy kind, and its code path names neither the module nor its binding key.
    """
    import inspect

    _reset_bucket(_BASE_LEGACY, bucket)
    source = inspect.getsource(auth_service.bootstrap_admin)
    assert "legacy_migrator" not in source
    assert "legacy-owner" not in source

    _bootstrap({"loginId": "adminuser", "name": "관리자", "password": "long-enough-password"})
    after = _snapshot()
    assert not any(k.startswith("users/_auth/legacy") for k in after)
    assert not any(k.endswith("migration.json") for k in after)
    _assert_legacy_intact(after, _BASE_LEGACY)


# --- (e) interruption and resume ----------------------------------------------------------


@_SETTINGS
@given(
    legacy=_legacy,
    first=_requests().filter(lambda r: r[1]),
    second=_requests().filter(lambda r: r[1]),
)
def test_resume_after_interrupted_pointer_commit_keeps_a_single_account(
    bucket, legacy, first, second
):
    """Validates: Requirements 9.2, 9.3

    The run is interrupted after account.json is written and before the administrator pointer is
    committed. Repeating the request with the same loginId resumes that account (no second
    account) and commits the pointer; afterwards the endpoint is closed.
    """
    recorder = bucket
    _reset_bucket(legacy, recorder)
    first_body, _ = first
    second_body = {**second[0], "loginId": first_body["loginId"]}

    recorder.fail_on("PutObject", key=ADMIN_POINTER_KEY, times=1)
    with pytest.raises(ClientError):
        _bootstrap(first_body)
    interrupted = _snapshot()
    assert ADMIN_POINTER_KEY not in interrupted
    interrupted_accounts = _accounts(interrupted)
    assert len(interrupted_accounts) == 1
    user_id = interrupted_accounts[0]["userId"]
    recorder.clear_failures()

    result = _bootstrap(second_body, now=BASE_NOW + 5)
    assert result["message"] == BOOTSTRAP_DONE_MESSAGE

    after = _snapshot()
    accounts = _accounts(after)
    assert len(accounts) == 1
    record = accounts[0]
    assert record["userId"] == user_id
    assert record["role"] == "admin" and record["status"] == "active"
    assert auth_crypto.verify_password(second_body["password"], record["password"]) is True
    assert json.loads(after[ADMIN_POINTER_KEY].decode("utf-8"))["userId"] == user_id
    assert _new_keys(after, legacy) == _expected_new_keys(first_body["loginId"], user_id)
    _assert_legacy_intact(after, legacy)

    # closed afterwards, nothing written
    recorder.clear()
    with pytest.raises(MablopError) as excinfo:
        _bootstrap(first_body, now=BASE_NOW + 10)
    assert excinfo.value.status == 403 and excinfo.value.message == ADMIN_EXISTS_MESSAGE
    assert recorder.writes() == []
    assert _snapshot() == after


@_SETTINGS
@given(legacy=_legacy, first=_requests().filter(lambda r: r[1]))
def test_resume_after_interrupted_account_write_creates_one_account(bucket, legacy, first):
    """Validates: Requirements 9.2, 9.3

    The run is interrupted after the loginId lookup is claimed and before account.json is
    written. Once the dangling lookup is older than 60 seconds, the same request succeeds and
    leaves exactly one account.
    """
    recorder = bucket
    _reset_bucket(legacy, recorder)
    body, _ = first

    recorder.fail_on("PutObject", key_startswith="users/u_", times=1)
    with pytest.raises(ClientError):
        _bootstrap(body, now=BASE_NOW)
    interrupted = _snapshot()
    assert _accounts(interrupted) == []
    assert ADMIN_POINTER_KEY not in interrupted
    recorder.clear_failures()

    _bootstrap(body, now=BASE_NOW + 61)

    after = _snapshot()
    accounts = _accounts(after)
    assert len(accounts) == 1
    user_id = accounts[0]["userId"]
    assert accounts[0]["role"] == "admin" and accounts[0]["status"] == "active"
    lookup = json.loads(after[f"users/_auth/login/{body['loginId']}.json"].decode("utf-8"))
    assert lookup["userId"] == user_id
    assert json.loads(after[ADMIN_POINTER_KEY].decode("utf-8"))["userId"] == user_id
    assert _new_keys(after, legacy) == _expected_new_keys(body["loginId"], user_id)
    _assert_legacy_intact(after, legacy)


@_SETTINGS
@given(
    legacy=_legacy,
    outer=_requests().filter(lambda r: r[1]),
    inner=_requests().filter(lambda r: r[1]),
)
def test_losing_the_pointer_race_leaves_exactly_one_administrator(
    bucket, monkeypatch, legacy, outer, inner
):
    """Validates: Requirements 9.2, 9.3

    A second request completes between the first request's account write and its pointer commit.
    The first request then loses the create-once pointer: it answers 403 and the winner is the
    only administrator, with no leftover from the loser.
    """
    recorder = bucket
    _reset_bucket(legacy, recorder)
    outer_body, _ = outer
    inner_body, _ = inner

    real_commit = auth_store.commit_admin_pointer
    state = {"entered": False, "inner_result": None}

    def racing_commit(user_id):
        if not state["entered"]:
            state["entered"] = True
            state["inner_result"] = _bootstrap(inner_body, now=BASE_NOW)
        return real_commit(user_id)

    monkeypatch.setattr(auth_store, "commit_admin_pointer", racing_commit)
    with pytest.raises(MablopError) as excinfo:
        _bootstrap(outer_body, now=BASE_NOW)
    monkeypatch.setattr(auth_store, "commit_admin_pointer", real_commit)

    assert excinfo.value.code == "forbidden"
    assert excinfo.value.message == ADMIN_EXISTS_MESSAGE
    assert state["inner_result"]["message"] == BOOTSTRAP_DONE_MESSAGE

    after = _snapshot()
    accounts = _accounts(after)
    assert len(accounts) == 1
    winner = accounts[0]
    assert winner["loginId"] == inner_body["loginId"]
    assert winner["role"] == "admin" and winner["status"] == "active"
    assert json.loads(after[ADMIN_POINTER_KEY].decode("utf-8"))["userId"] == winner["userId"]
    assert _new_keys(after, legacy) == _expected_new_keys(winner["loginId"], winner["userId"])
    _assert_legacy_intact(after, legacy)

    # still closed, nothing written
    recorder.clear()
    with pytest.raises(MablopError) as again:
        _bootstrap(outer_body, now=BASE_NOW + 1)
    assert again.value.status == 403 and again.value.message == ADMIN_EXISTS_MESSAGE
    assert recorder.writes() == []
    assert _snapshot() == after


def test_existing_member_with_same_login_id_is_not_taken_over(bucket, create_user):
    """Validates: Requirements 9.2, 9.3

    A member account that already uses the loginId is never converted: bootstrap answers
    bad_request, creates no administrator and writes nothing.
    """
    _reset_bucket(_BASE_LEGACY, bucket)
    member = create_user("jhe8888", role="member", status="active")
    bucket.clear()
    before = _snapshot()

    with pytest.raises(MablopError) as excinfo:
        _bootstrap({"loginId": "jhe8888", "name": "제이", "password": "long-enough-password"})

    assert excinfo.value.code == "bad_request"
    assert excinfo.value.message == "이미 사용 중인 아이디입니다."
    # the only write attempted is the create-once claim of the lookup, which S3 refuses
    assert {c["key"] for c in bucket.writes()} <= {"users/_auth/login/jhe8888.json"}
    after = _snapshot()
    assert after == before
    assert ADMIN_POINTER_KEY not in after
    assert [a["userId"] for a in _accounts(after)] == [member["userId"]]

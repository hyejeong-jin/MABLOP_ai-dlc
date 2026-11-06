"""S3 helper tests: JSON + bytes round-trip over moto-mocked S3 (Req 4.4)."""
import importlib
import re

import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

from errors import MablopError

BUCKET = "mablop-test"


@pytest.fixture
def store(monkeypatch):
    """Fresh moto S3 with the test bucket and env var set; yield the s3store module."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import s3store

        importlib.reload(s3store)
        yield s3store


def test_json_round_trip(store):
    obj = {"version": 1, "tone": "친근한 구어체", "tags": ["a", "b"], "n": 3.5}
    store.put_json("metadata/x.json", obj)
    assert store.get_json("metadata/x.json") == obj


def test_bytes_round_trip(store):
    data = b"\x00\x01npy-ish-bytes\xff\xfe"
    store.put_bytes("embeddings/index.npy", data)
    assert store.get_bytes("embeddings/index.npy") == data


def test_missing_bucket_env_raises(store, monkeypatch):
    monkeypatch.delenv("MABLOP_BUCKET", raising=False)
    with pytest.raises(RuntimeError):
        store.put_json("metadata/x.json", {})


# JSON-serializable dict strategy (string keys, mixed scalar/list values).
_scalars = st.none() | st.booleans() | st.integers() | st.text() | st.floats(allow_nan=False, allow_infinity=False)
_dicts = st.dictionaries(st.text(), _scalars | st.lists(_scalars), max_size=8)


@settings(max_examples=100, deadline=None)
@given(obj=_dicts)
def test_arbitrary_dict_round_trips(obj):
    """put_json -> get_json reproduces an arbitrary dict exactly."""
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import os

        os.environ["MABLOP_BUCKET"] = BUCKET
        import s3store

        importlib.reload(s3store)
        s3store.put_json("metadata/obj.json", obj)
        assert s3store.get_json("metadata/obj.json") == obj


# --- user-auth-admin additions (Req 8.1, 8.2, 16.4, 16.5, 17.9) ---
UID = "u_0123456789abcdef"
OTHER_UID = "u_fedcba9876543210"
MSG_BAD_PATH = "허용되지 않는 경로입니다."
MSG_BAD_IMAGE_PATH = "허용되지 않는 이미지 경로입니다."


def _s3():
    return boto3.client("s3", region_name="us-east-1")


def _raw_etag(key):
    return _s3().head_object(Bucket=BUCKET, Key=key)["ETag"]


class _DeniedClient:
    """Stand-in client whose get_object fails with a fixed S3 error code."""

    def __init__(self, code):
        self.code = code

    def get_object(self, **kwargs):
        raise ClientError({"Error": {"Code": self.code, "Message": "x"}}, "GetObject")


# user_key / auth_key / assert_own_image_keys


def test_user_key_with_explicit_user_id(store):
    assert store.user_key("metadata/x.json", UID) == f"users/{UID}/metadata/x.json"


def test_user_key_uses_bound_user(store):
    import user_scope

    with user_scope.bind(OTHER_UID):
        assert store.user_key("raw-posts/a.txt") == f"users/{OTHER_UID}/raw-posts/a.txt"


@pytest.mark.parametrize("bad_id", ["", "u_123", "U_0123456789abcdef", "u_0123456789abcdeg", "_auth", "u_0123456789abcdef0", 5])
def test_user_key_rejects_malformed_user_id(store, bad_id):
    with pytest.raises(MablopError) as exc:
        store.user_key("a.txt", bad_id)
    assert exc.value.code == "internal_error"


def test_user_key_unbound_user_is_internal_error(store):
    import contextvars

    def run():
        with pytest.raises(MablopError) as exc:
            store.user_key("a.txt")
        assert exc.value.code == "internal_error"

    contextvars.Context().run(run)


@pytest.mark.parametrize("bad", ["", "/abs", "a/../b", "..", "a\\b", "a\nb", "a\x00b", "a\x7fb"])
def test_user_key_rejects_unsafe_relative_path(store, bad):
    with pytest.raises(MablopError) as exc:
        store.user_key(bad, UID)
    assert exc.value.code == "bad_request"
    assert exc.value.message == MSG_BAD_PATH


def test_auth_key_and_rejections(store):
    assert store.auth_key("legacy-owner.json") == "users/_auth/legacy-owner.json"
    with pytest.raises(MablopError) as exc:
        store.auth_key("../x")
    assert exc.value.code == "bad_request"
    assert exc.value.message == MSG_BAD_PATH


def test_assert_own_image_keys_accepts_own_prefix(store):
    import user_scope

    with user_scope.bind(UID):
        store.assert_own_image_keys([f"users/{UID}/images/a.png", f"users/{UID}/images/b.jpg"])


@pytest.mark.parametrize(
    "keys",
    [
        [f"users/{OTHER_UID}/images/a.png"],
        ["images/a.png"],
        [f"users/{UID}/images/"],
        [f"users/{UID}/images/../x.png"],
        [f"users/{UID}/images/a.png", f"users/{OTHER_UID}/images/b.png"],
        [123],
        "users",
    ],
)
def test_assert_own_image_keys_rejects_foreign_or_bad(store, keys):
    import user_scope

    with user_scope.bind(UID):
        with pytest.raises(MablopError) as exc:
            store.assert_own_image_keys(keys)
    assert exc.value.code == "bad_request"
    assert exc.value.message == MSG_BAD_IMAGE_PATH


# put_json_if_absent


def test_put_json_if_absent_creates_then_refuses(store):
    assert store.put_json_if_absent("metadata/once.json", {"v": 1}) is True
    assert store.put_json_if_absent("metadata/once.json", {"v": 2}) is False
    assert store.get_json("metadata/once.json") == {"v": 1}


# read_json_optional


def test_read_json_optional_missing_returns_none(store):
    assert store.read_json_optional("metadata/none.json") is None


def test_read_json_optional_returns_object_and_etag(store):
    store.put_json("metadata/o.json", {"name": "한글"})
    obj, etag = store.read_json_optional("metadata/o.json")
    assert obj == {"name": "한글"}
    assert etag == _raw_etag("metadata/o.json")


@pytest.mark.parametrize("code", ["AccessDenied", "403", "NoSuchKey", "404"])
def test_read_json_optional_treats_missing_and_access_denied_as_absent(store, monkeypatch, code):
    monkeypatch.setattr(store, "_client", lambda: _DeniedClient(code))
    assert store.read_json_optional("metadata/x.json") is None


def test_read_json_optional_reraises_other_errors(store, monkeypatch):
    monkeypatch.setattr(store, "_client", lambda: _DeniedClient("InternalError"))
    with pytest.raises(ClientError):
        store.read_json_optional("metadata/x.json")


# list_keys / key_exists


def test_list_keys_paginates_over_one_thousand_objects(store):
    s3 = _s3()
    for i in range(1005):
        s3.put_object(Bucket=BUCKET, Key=f"bulk/{i:04d}.txt", Body=b"x")
    s3.put_object(Bucket=BUCKET, Key="other/zzz.txt", Body=b"x")
    items = store.list_keys("bulk/")
    assert len(items) == 1005
    assert {item["key"] for item in items} == {f"bulk/{i:04d}.txt" for i in range(1005)}


def test_list_keys_item_shape(store):
    store.put_bytes("shape/a.txt", b"12345")
    (item,) = store.list_keys("shape/")
    assert set(item) == {"key", "size", "lastModified"}
    assert item["key"] == "shape/a.txt"
    assert item["size"] == 5
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", item["lastModified"])


def test_list_keys_empty_prefix_gives_empty_list(store):
    assert store.list_keys("nothing/") == []


def test_key_exists_exact_key_only(store):
    store.put_bytes("a/report", b"1")
    store.put_bytes("a/report.md", b"2")
    store.put_bytes("a/report/child", b"3")
    assert store.key_exists("a/report") is True
    assert store.key_exists("a/repor") is False
    assert store.key_exists("a/missing") is False


def test_key_exists_false_when_only_longer_keys_share_prefix(store):
    store.put_bytes("b/report.md", b"2")
    assert store.key_exists("b/report") is False


# update_json


def test_update_json_creates_missing_key_from_default_copy(store):
    default = {"v": 1, "items": []}

    def mutate(obj):
        obj["items"].append("a")
        return obj

    result = store.update_json("metadata/u.json", mutate, default=default)
    assert result == {"v": 1, "items": ["a"]}
    assert store.get_json("metadata/u.json") == {"v": 1, "items": ["a"]}
    assert default == {"v": 1, "items": []}


def test_update_json_modifies_existing_object(store):
    store.put_json("metadata/u.json", {"n": 1})
    store.update_json("metadata/u.json", lambda o: {"n": o["n"] + 1})
    assert store.get_json("metadata/u.json") == {"n": 2}


def test_update_json_retries_after_concurrent_write(store):
    store.put_json("metadata/c.json", {"n": 0})
    calls = []

    def mutate(obj):
        calls.append(obj["n"])
        if len(calls) == 1:
            store.put_json("metadata/c.json", {"n": 10})  # concurrent writer changes the ETag
        return {"n": obj["n"] + 1}

    result = store.update_json("metadata/c.json", mutate)
    assert calls == [0, 10]
    assert result == {"n": 11}
    assert store.get_json("metadata/c.json") == {"n": 11}


def test_update_json_raises_internal_error_when_retries_exhausted(store):
    store.put_json("metadata/c.json", {"n": 0})
    calls = []

    def mutate(obj):
        calls.append(1)
        store.put_json("metadata/c.json", {"n": 100 + len(calls)})  # always loses the race
        return {"n": -1}

    with pytest.raises(MablopError) as exc:
        store.update_json("metadata/c.json", mutate, retries=3)
    assert exc.value.code == "internal_error"
    assert len(calls) == 3
    assert store.get_json("metadata/c.json") == {"n": 103}


def test_update_json_retries_when_create_loses_race(store):
    calls = []

    def mutate(obj):
        calls.append(obj)
        if len(calls) == 1:
            store.put_json("metadata/new.json", {"items": ["other"]})
        return {"items": obj["items"] + ["mine"]}

    store.update_json("metadata/new.json", mutate, default={"items": []})
    assert calls == [{"items": []}, {"items": ["other"]}]
    assert store.get_json("metadata/new.json") == {"items": ["other", "mine"]}


def test_update_json_mutate_error_leaves_existing_object_unchanged(store):
    store.put_json("metadata/e.json", {"n": 1})
    etag = _raw_etag("metadata/e.json")

    def boom(obj):
        raise MablopError("bad_request", "거부")

    with pytest.raises(MablopError) as exc:
        store.update_json("metadata/e.json", boom)
    assert exc.value.code == "bad_request"
    assert store.get_json("metadata/e.json") == {"n": 1}
    assert _raw_etag("metadata/e.json") == etag


def test_update_json_mutate_error_does_not_create_missing_object(store):
    def boom(obj):
        raise MablopError("bad_request", "거부")

    with pytest.raises(MablopError):
        store.update_json("metadata/never.json", boom, default={})
    assert store.read_json_optional("metadata/never.json") is None
    assert store.key_exists("metadata/never.json") is False

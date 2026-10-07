"""S3 helper tests: JSON + bytes round-trip over moto-mocked S3 (Req 4.4)."""
import importlib

import boto3
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

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

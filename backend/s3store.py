"""S3 helpers over the single private bucket (Req 8.2, 8.4). JSON metadata round-trip (Req 4.4)."""
import json
import os

import boto3

BUCKET_ENV = "MABLOP_BUCKET"


def _bucket() -> str:
    """Bucket name from env; raise if unset."""
    name = os.environ.get(BUCKET_ENV)
    if not name:
        raise RuntimeError(f"{BUCKET_ENV} not set")
    return name


def _client():
    """boto3 S3 client, default config."""
    return boto3.client("s3")


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> str:
    """Put raw bytes at key; return key."""
    _client().put_object(Bucket=_bucket(), Key=key, Body=data, ContentType=content_type)
    return key


def get_bytes(key: str) -> bytes:
    """Get raw bytes at key."""
    return _client().get_object(Bucket=_bucket(), Key=key)["Body"].read()


def put_json(key: str, obj) -> str:
    """Serialize obj to JSON and put at key."""
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    return put_bytes(key, data, content_type="application/json")


def get_json(key: str):
    """Get and parse JSON at key."""
    return json.loads(get_bytes(key).decode("utf-8"))

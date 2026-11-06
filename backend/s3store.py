"""S3 helpers over the single private bucket (Req 8.2, 8.4). JSON metadata round-trip (Req 4.4)."""
import copy as copy_module
import json
import os
import re
from datetime import datetime, timezone

import boto3
from botocore.exceptions import ClientError

import auth_messages
import user_scope
from errors import MablopError

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

# --- user-auth-admin additions (D6, D7, D8, D21; Req 8.1, 8.2, 8.7) ---
USER_ID_PATTERN = r"^u_[0-9a-f]{16}$"
_USER_ID_RE = re.compile(USER_ID_PATTERN)
_MISSING_CODES = {"NoSuchKey", "404", "NotFound", "AccessDenied", "403"}
_PRECONDITION_CODES = {"PreconditionFailed", "412", "ConditionalRequestConflict", "409"}


def _error_code(err: ClientError) -> str:
    """Error code string of a botocore ClientError."""
    return str(err.response.get("Error", {}).get("Code", ""))


def _path_ok(relative) -> bool:
    """True when relative is a non-empty safe path (no leading '/', '..', backslash, control chars)."""
    if not isinstance(relative, str) or not relative or relative.startswith("/"):
        return False
    if ".." in relative or "\\" in relative:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in relative)


def user_key(relative: str, user_id: str | None = None) -> str:
    """Return 'users/{id}/{relative}'; id defaults to the user bound in user_scope.

    A malformed userId is internal_error (never user input); a bad relative path is bad_request.
    """
    if user_id is None:
        user_id = user_scope.current_user_id()
    if not isinstance(user_id, str) or _USER_ID_RE.fullmatch(user_id) is None:
        raise MablopError("internal_error")
    if not _path_ok(relative):
        raise MablopError("bad_request", auth_messages.BAD_PATH)
    return f"users/{user_id}/{relative}"


def auth_key(relative: str) -> str:
    """Return 'users/_auth/{relative}' with the same path rules as user_key."""
    if not _path_ok(relative):
        raise MablopError("bad_request", auth_messages.BAD_PATH)
    return f"users/_auth/{relative}"


def assert_own_image_keys(keys) -> None:
    """Require every key to be an image key of the acting user; raise bad_request otherwise (Req 8.7).

    Checks all supplied keys and reads no object.
    """
    prefix = user_key("images/")
    if not isinstance(keys, (list, tuple)):
        raise MablopError("bad_request", auth_messages.BAD_IMAGE_PATH)
    for key in keys:
        if not (isinstance(key, str) and key.startswith(prefix) and len(key) > len(prefix) and _path_ok(key)):
            raise MablopError("bad_request", auth_messages.BAD_IMAGE_PATH)


def _iso_ms(dt: datetime) -> str:
    """UTC ISO-8601 with millisecond precision, e.g. 2024-01-02T03:04:05.678Z."""
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def list_keys(prefix: str) -> list[dict]:
    """List objects under prefix (all pages) as [{"key", "size", "lastModified"}]."""
    out = []
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []):
            out.append(
                {"key": obj["Key"], "size": obj["Size"], "lastModified": _iso_ms(obj["LastModified"])}
            )
    return out


def key_exists(key: str) -> bool:
    """True when the exact key exists, checked by ListObjectsV2 instead of HEAD (D21)."""
    resp = _client().list_objects_v2(Bucket=_bucket(), Prefix=key, MaxKeys=1)
    # The exact key sorts before any longer key sharing it as a prefix, so one result suffices.
    return any(obj["Key"] == key for obj in resp.get("Contents", []))


def read_json_optional(key: str):
    """Return (object, etag), or None when the key is missing.

    NoSuchKey/404 and AccessDenied/403 (S3 answers 403 for a missing key without bucket-wide
    ListBucket) both mean missing; this is for objects that are optional by design.
    """
    try:
        resp = _client().get_object(Bucket=_bucket(), Key=key)
    except ClientError as err:
        if _error_code(err) in _MISSING_CODES:
            return None
        raise
    return json.loads(resp["Body"].read().decode("utf-8")), resp["ETag"]


def list_user_ids() -> list[str]:
    """User ids found as common prefixes of 'users/' (excludes '_auth' and anything not matching the pattern)."""
    ids = []
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix="users/", Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            name = cp["Prefix"][len("users/"):].rstrip("/")
            if _USER_ID_RE.fullmatch(name):
                ids.append(name)
    return ids


def head(key: str) -> dict | None:
    """Return {"size"} for an existing key, None when absent (404 or 403)."""
    try:
        resp = _client().head_object(Bucket=_bucket(), Key=key)
    except ClientError as err:
        if _error_code(err) in _MISSING_CODES:
            return None
        raise
    return {"size": resp["ContentLength"]}


def copy(src_key: str, dst_key: str) -> None:
    """Server-side copy within the bucket."""
    bucket = _bucket()
    _client().copy_object(Bucket=bucket, Key=dst_key, CopySource={"Bucket": bucket, "Key": src_key})


def delete(key: str) -> None:
    """Delete the object at key."""
    _client().delete_object(Bucket=_bucket(), Key=key)


def _put_conditional(key: str, obj, **condition) -> bool:
    """Put JSON with a precondition; False when S3 reports the precondition failed."""
    data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    try:
        _client().put_object(
            Bucket=_bucket(), Key=key, Body=data, ContentType="application/json", **condition
        )
    except ClientError as err:
        if _error_code(err) in _PRECONDITION_CODES:
            return False
        raise
    return True


def put_json_if_absent(key: str, obj) -> bool:
    """Create key with If-None-Match: *; False when it already exists."""
    return _put_conditional(key, obj, IfNoneMatch="*")


def update_json(key: str, mutate, default=None, retries: int = 3) -> dict:
    """Read-modify-write key with optimistic concurrency (D7, D19).

    Reads the object and ETag (absent object starts from a copy of default and is created with
    If-None-Match: *), calls mutate(obj) -> new object, and writes with If-Match. On a precondition
    failure it re-reads and calls mutate again, up to `retries` attempts in total, then raises
    internal_error. If mutate raises, nothing is written. mutate must be a pure function of its argument.
    """
    for _ in range(max(1, retries)):
        found = read_json_optional(key)
        if found is None:
            current = copy_module.deepcopy(default)
            condition = {"IfNoneMatch": "*"}
        else:
            current, etag = found
            condition = {"IfMatch": etag}
        new_obj = mutate(current)
        if _put_conditional(key, new_obj, **condition):
            return new_obj
    raise MablopError("internal_error")

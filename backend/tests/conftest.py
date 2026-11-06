"""pytest config: put backend package root on sys.path for test imports.

Shared fixtures for user-auth-admin (design "Testing Strategy"):

- ``_bind_acting_user`` (autouse): binds a fixed userId into ``user_scope`` so the
  existing component tests keep running unchanged. Opt out with
  ``@pytest.mark.unbound_user`` or by requesting the ``unbound_user`` fixture.
  Tests whose name contains ``without_bound_user`` or ``unbound`` are opted out
  automatically (see ``pytest_collection_modifyitems``).
- ``aws_mock`` / ``s3_bucket``: moto AWS mock with fake credentials and one bucket.
- ``signing_secret``: Signing_Secret in moto SSM, ``auth_crypto.reset_cache()`` before/after.
- ``bootstrap_token``: Bootstrap_Token in moto SSM at a path different from the signing path.
- ``make_user_record`` / ``create_user`` / ``make_session_token``: user and token helpers.
- ``s3_calls``: boto3 event hook that records S3 calls (key, order) and injects failures.
"""
import hashlib
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import boto3  # noqa: E402
import pytest  # noqa: E402
from botocore.exceptions import ClientError  # noqa: E402

import user_scope  # noqa: E402

# --- constants ---------------------------------------------------------------------------

ACTING_USER_ID = "u_0123456789abcdef"
OTHER_USER_ID = "u_fedcba9876543210"
TEST_BUCKET = "mablop-test"
TEST_REGION = "us-east-1"
SIGNING_SECRET_PATH = "/mablop/signing-secret"
SIGNING_SECRET_VALUE = "test-signing-secret-0123456789abcdef-0123456789"  # >= 32 chars
BOOTSTRAP_TOKEN_PATH = "/mablop/token"
BOOTSTRAP_TOKEN_VALUE = "test-bootstrap-token-value"
TEST_PASSWORD = "correct-horse-battery"  # 10..128 chars
TEST_HASH_ITERATIONS = 1000  # real default (600000) is too slow for most tests

_UNBOUND_NAME_RE = re.compile(r"without_bound_user|unbound")

_WRITE_OPS = frozenset({"PutObject", "CopyObject", "DeleteObject", "DeleteObjects"})


# --- pytest hooks ------------------------------------------------------------------------


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "unbound_user: do not bind the fixed test userId into user_scope for this test",
    )


def pytest_collection_modifyitems(items):
    """Opt tests that check the *unbound* state out of the autouse userId binding."""
    for item in items:
        if _UNBOUND_NAME_RE.search(item.name):
            item.add_marker(pytest.mark.unbound_user)


# --- user_scope binding ------------------------------------------------------------------


@pytest.fixture
def unbound_user():
    """Request this fixture to run a test without the autouse userId binding."""
    return None


@pytest.fixture
def acting_user_id():
    """The userId bound by the autouse fixture."""
    return ACTING_USER_ID


@pytest.fixture
def other_user_id():
    """A second, valid userId for isolation tests."""
    return OTHER_USER_ID


@pytest.fixture(autouse=True)
def _bind_acting_user(request):
    """Bind ACTING_USER_ID for the test unless it opts out (marker or fixture)."""
    if request.node.get_closest_marker("unbound_user") or "unbound_user" in request.fixturenames:
        yield
        return
    with user_scope.bind(ACTING_USER_ID):
        yield


# --- AWS mock ----------------------------------------------------------------------------


@pytest.fixture
def aws_mock(monkeypatch):
    """moto mock_aws with fake credentials; no real AWS call can happen."""
    from moto import mock_aws

    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", TEST_REGION)
    monkeypatch.setenv("MABLOP_BEDROCK_REGION", TEST_REGION)
    with mock_aws():
        yield


@pytest.fixture
def s3_bucket(aws_mock, monkeypatch):
    """Create the private bucket under moto and point MABLOP_BUCKET at it."""
    monkeypatch.setenv("MABLOP_BUCKET", TEST_BUCKET)
    boto3.client("s3", region_name=TEST_REGION).create_bucket(Bucket=TEST_BUCKET)
    return TEST_BUCKET


# --- secrets -----------------------------------------------------------------------------


@pytest.fixture
def signing_secret(aws_mock, monkeypatch):
    """Signing_Secret in moto SSM; env points to it; auth_crypto cache is reset around the test."""
    import auth_crypto

    boto3.client("ssm", region_name=TEST_REGION).put_parameter(
        Name=SIGNING_SECRET_PATH, Value=SIGNING_SECRET_VALUE, Type="SecureString"
    )
    monkeypatch.setenv("MABLOP_SIGNING_SECRET_SSM_PATH", SIGNING_SECRET_PATH)
    auth_crypto.reset_cache()
    yield SIGNING_SECRET_VALUE
    auth_crypto.reset_cache()


@pytest.fixture
def bootstrap_token(aws_mock, monkeypatch):
    """Bootstrap_Token in moto SSM (MABLOP_TOKEN unset so access_controller reads SSM).

    The path differs from the signing-secret path, as auth_crypto requires.
    """
    import access_controller

    boto3.client("ssm", region_name=TEST_REGION).put_parameter(
        Name=BOOTSTRAP_TOKEN_PATH, Value=BOOTSTRAP_TOKEN_VALUE, Type="SecureString"
    )
    monkeypatch.delenv("MABLOP_TOKEN", raising=False)
    monkeypatch.setenv("MABLOP_TOKEN_SSM_PATH", BOOTSTRAP_TOKEN_PATH)
    monkeypatch.setattr(access_controller, "_cached_token", None)
    yield BOOTSTRAP_TOKEN_VALUE
    access_controller._cached_token = None


# --- users and tokens --------------------------------------------------------------------


def _derived_user_id(login_id: str) -> str:
    return "u_" + hashlib.sha256(login_id.encode("utf-8")).hexdigest()[:16]


@pytest.fixture
def make_user_record():
    """Return a pure builder for a User_Record dict (no S3, no AWS)."""
    import auth_crypto

    def build(
        login_id="tester",
        *,
        user_id=None,
        name="테스터",
        role="member",
        status="active",
        password=TEST_PASSWORD,
        iterations=TEST_HASH_ITERATIONS,
        now_ms=None,
        credentials_changed_at=None,
        approved_at="auto",
    ):
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        created = auth_crypto.to_iso_ms(now_ms)
        if approved_at == "auto":
            approved_at = created if status in ("active", "disabled") else None
        return {
            "v": 1,
            "userId": user_id or _derived_user_id(login_id),
            "loginId": login_id,
            "name": name,
            "role": role,
            "status": status,
            "password": auth_crypto.hash_password(password, iterations=iterations),
            "createdAt": created,
            "approvedAt": approved_at,
            "credentialsChangedAt": credentials_changed_at or created,
        }

    return build


@pytest.fixture
def create_user(s3_bucket, make_user_record):
    """Return a function that stores a User_Record and its login lookup; returns the record."""
    import s3store

    def create(login_id="tester", **kwargs):
        record = make_user_record(login_id, **kwargs)
        s3store.put_json(f"users/{record['userId']}/account.json", record)
        s3store.put_json(
            f"users/_auth/login/{login_id}.json",
            {"userId": record["userId"], "createdAt": record["createdAt"]},
        )
        return record

    return create


@pytest.fixture
def make_session_token(signing_secret):
    """Return a function (user_id, now_ms=None) -> Session_Token string."""
    import auth_crypto

    def make(user_id=ACTING_USER_ID, now_ms=None):
        now_ms = int(time.time() * 1000) if now_ms is None else now_ms
        token, _exp = auth_crypto.sign_token(user_id, now_ms)
        return token

    return make


# --- S3 call recorder and failure injection ----------------------------------------------


class S3CallRecorder:
    """Records S3 calls (operation, key, order) and raises injected ClientErrors.

    ``calls`` is a list of dicts in call order:
    ``{"op", "key", "prefix", "source", "failed"}`` (``source`` is the CopyObject source key).
    """

    def __init__(self):
        self.calls = []
        self._rules = []

    # recording -----------------------------------------------------------------------
    def clear(self):
        """Forget recorded calls (failure rules are kept)."""
        self.calls.clear()

    def ops(self, *names):
        """Operation names in order, optionally filtered."""
        return [c["op"] for c in self.calls if not names or c["op"] in names]

    def keys(self, *ops):
        """Keys (or prefixes for list operations) of recorded calls, optionally filtered by op."""
        return [
            c["key"] or c["prefix"]
            for c in self.calls
            if (not ops or c["op"] in ops) and (c["key"] or c["prefix"])
        ]

    def writes(self):
        """Calls that change objects (Put, Copy, Delete)."""
        return [c for c in self.calls if c["op"] in _WRITE_OPS]

    def count(self, op, key=None):
        """Number of recorded calls of op, optionally for one exact key."""
        return sum(1 for c in self.calls if c["op"] == op and (key is None or c["key"] == key))

    def touched_keys(self):
        """Every object key named by any call (including copy sources)."""
        out = []
        for c in self.calls:
            for k in (c["key"], c["source"]):
                if k:
                    out.append(k)
        return out

    # failure injection ---------------------------------------------------------------
    def fail_on(self, op, key=None, *, key_startswith=None, code="InternalError", times=None, skip=0):
        """Make matching calls raise ClientError(code).

        Matches ``op`` and, when given, the exact ``key`` or a ``key_startswith`` prefix. The first
        ``skip`` matches pass, then ``times`` matches fail (``None`` = all later matches).
        Returns the rule dict (``rule["hits"]`` counts failures).
        """
        rule = {
            "op": op,
            "key": key,
            "key_startswith": key_startswith,
            "code": code,
            "times": times,
            "skip": skip,
            "seen": 0,
            "hits": 0,
        }
        self._rules.append(rule)
        return rule

    def clear_failures(self):
        self._rules.clear()

    # hook ----------------------------------------------------------------------------
    def _matching_rule(self, op, key):
        for rule in self._rules:
            if rule["op"] != op:
                continue
            if rule["key"] is not None and rule["key"] != key:
                continue
            if rule["key_startswith"] is not None and not (key or "").startswith(rule["key_startswith"]):
                continue
            rule["seen"] += 1
            if rule["seen"] <= rule["skip"]:
                continue
            if rule["times"] is not None and rule["hits"] >= rule["times"]:
                continue
            return rule
        return None

    def handler(self, params, event_name=None, **_kwargs):
        """botocore ``before-parameter-build.s3.*`` handler."""
        op = (event_name or "").rsplit(".", 1)[-1]
        key = params.get("Key")
        source = params.get("CopySource")
        if isinstance(source, dict):
            source = source.get("Key")
        elif isinstance(source, str):
            source = source.split("/", 1)[1] if "/" in source else source
        entry = {
            "op": op,
            "key": key,
            "prefix": params.get("Prefix"),
            "source": source,
            "failed": False,
        }
        self.calls.append(entry)
        rule = self._matching_rule(op, key)
        if rule is not None:
            rule["hits"] += 1
            entry["failed"] = True
            raise ClientError(
                {"Error": {"Code": rule["code"], "Message": "injected failure"}}, op
            )


@pytest.fixture
def s3_calls(aws_mock, monkeypatch):
    """Record every S3 call made through ``boto3.client`` and allow failure injection.

    Installs a fresh default boto3 session (restored on teardown) whose event system
    carries the hook; s3store creates a new client per call, so every call is seen.
    """
    session = boto3.session.Session(region_name=TEST_REGION)
    recorder = S3CallRecorder()
    session.events.register("before-parameter-build.s3", recorder.handler)
    monkeypatch.setattr(boto3, "DEFAULT_SESSION", session)
    return recorder

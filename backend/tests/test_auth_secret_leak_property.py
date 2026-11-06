"""Property test: secrets and personal data never leak (Req 3.5, 6.3, 6.4, 6.5, 7.4; Property 25).

Random sequences of `register`, `login`, `change-password`, `admin-reset-password` and
`bootstrap-admin` requests (plus the other administrator account actions, `get-me`, tampered
tokens, and injected S3 failures) run through the real Lambda `handler.handler` against a moto
bucket and moto SSM. Passwords are random; unknown loginIds are random "ghost" ids. After every
request the test checks that:

  * no response, log line, or stderr output contains a submitted plaintext password, any salt or
    hash that was ever generated, the Signing_Secret, the Bootstrap_Token, or a submitted
    loginId that has no record (the single exception is the temporary password in the one
    `admin-reset-password` response);
  * Session_Tokens appear in no log line, and in no response except the `login` and
    `change-password` answers that issue them;
  * no response carries the keys `password`, `salt`, `hash`, `alg` or `iterations`;
  * every log line is a JSON object whose keys are only `event`, `outcome` and `userId`, with
    `userId` present only for a user that exists in the bucket.

At the end of every sequence no S3 object contains a plaintext password (temporary passwords
included), the Signing_Secret, or the Bootstrap_Token. PBKDF2 uses a low iteration count.
Production code is not modified.

Feature: user-auth-admin, Property 25: Secrets and personal data never leak
"""
import json
import re

import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import auth_crypto
import handler
from conftest import (
    BOOTSTRAP_TOKEN_VALUE,
    SIGNING_SECRET_VALUE,
    TEST_BUCKET,
    TEST_REGION,
)

_USER_ID_RE = re.compile(r"^u_[0-9a-f]{16}$")
_SAFE_LOGIN_ID_RE = re.compile(r"^[a-z0-9_-]{4,32}$")
_LOG_KEYS = {"event", "outcome", "userId"}
_FORBIDDEN_RESPONSE_KEYS = {"password", "salt", "hash", "alg", "iterations"}
_TOKEN_ACTIONS = {"login", "change-password"}

ADMIN_LOGIN = "adminuser"
ADMIN_PASSWORD = "Pw#admin-initial-password"
LOGIN_POOL = ["alice_01", "bob-02", "carol_03", "dave-04"]
BOGUS_USER_ID = "u_" + "0" * 16

# --- strategies -------------------------------------------------------------------------

_hex = "0123456789abcdef"
_ghost_ids = st.text(alphabet=_hex, min_size=8, max_size=8).map(lambda h: "ghost_" + h)
_pool_ids = st.sampled_from(LOGIN_POOL)
_any_ids = st.one_of(_pool_ids, _pool_ids, _ghost_ids)
_names = st.text(alphabet=st.characters(whitelist_categories=("L", "N")), min_size=1, max_size=30)
# "Pw#" never occurs in any message, loginId, name or generated value, so a hit is a real leak.
_passwords = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=7, max_size=60
).map(lambda s: "Pw#" + s)
_bad_length_passwords = st.one_of(
    st.text(alphabet="abc", max_size=6).map(lambda s: "Pw#" + s),
    st.just("Pw#" + "x" * 130),
)
_any_passwords = st.one_of(_passwords, _passwords, _bad_length_passwords)

_ops = st.one_of(
    st.tuples(st.just("register"), _any_ids, _names, _any_passwords),
    st.tuples(st.just("register"), _pool_ids, _names, _passwords),
    st.tuples(st.just("login"), _any_ids, st.one_of(st.none(), _any_passwords)),
    st.tuples(st.just("login"), _pool_ids, st.none()),
    st.tuples(st.just("change"), _pool_ids, _any_passwords, st.booleans()),
    st.tuples(st.just("approve"), _any_ids),
    st.tuples(st.just("approve"), _pool_ids),
    st.tuples(st.sampled_from(["reject", "disable", "enable"]), _any_ids),
    st.tuples(st.just("reset"), _pool_ids),
    st.tuples(st.just("list")),
    st.tuples(st.just("me"), _pool_ids),
    st.tuples(st.just("badtoken"), _pool_ids),
    st.tuples(st.just("bootstrap"), _any_ids, _names, _any_passwords, st.booleans()),
)
_fail = st.sampled_from([None] * 7 + ["PutObject", "GetObject", "ListObjectsV2"])
_steps = st.lists(st.tuples(_ops, _fail), min_size=1, max_size=12)

_SETTINGS = settings(
    max_examples=15,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# --- text helpers -----------------------------------------------------------------------


def _texts(obj):
    """Every string in a parsed JSON value, keys included."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for key, value in obj.items():
            yield from _texts(key)
            yield from _texts(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _texts(item)


def _keys(obj):
    """Every object key in a parsed JSON value."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            yield key
            yield from _keys(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _keys(item)


def _leaks(secret, raw, parsed):
    """True when `secret` is in the raw text or in any decoded string of the parsed value."""
    if secret in raw:
        return True
    return any(secret in text for text in _texts(parsed))


# --- fixtures ---------------------------------------------------------------------------


@pytest.fixture
def generated_hashes(monkeypatch):
    """Low iteration counts; records every salt and hash that is ever generated."""
    original = auth_crypto.hash_password
    seen = []

    def hashed(password, iterations=4):
        out = original(password, iterations=4)
        seen.append(out["salt"])
        seen.append(out["hash"])
        return out

    monkeypatch.setattr(auth_crypto, "hash_password", hashed)
    monkeypatch.setattr(auth_crypto, "PBKDF2_ITERATIONS", 4)
    return seen


@pytest.fixture
def env(s3_bucket, s3_calls, signing_secret, bootstrap_token, generated_hashes, monkeypatch):
    """Bucket, S3 recorder, both secrets in SSM, fast hashing, and a rate limit that never triggers."""
    monkeypatch.setenv("MABLOP_MAX_RATE", "1000000")
    monkeypatch.setattr(access_controller, "_window", None)
    return s3_calls


def _client():
    return boto3.client("s3", region_name=TEST_REGION)


def _empty_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()
    access_controller._window = None


# --- harness ----------------------------------------------------------------------------


class Harness:
    """Sends requests through handler.handler and checks every response and log line."""

    def __init__(self, capsys, recorder, hashes):
        self.capsys = capsys
        self.recorder = recorder
        self.hashes = hashes
        self.passwords = set()
        self.temp_passwords = set()
        self.tokens = set()
        self.wrong_tokens = set()
        self.known_user_ids = set()
        self.guess = {}  # loginId -> password most likely to be accepted
        self.session = {}  # loginId -> latest Session_Token
        self.statuses = []

    # S3 views -----------------------------------------------------------------------
    def user_ids(self):
        found = set()
        page = _client().list_objects_v2(Bucket=TEST_BUCKET, Prefix="users/", Delimiter="/")
        for prefix in page.get("CommonPrefixes", []):
            name = prefix["Prefix"][len("users/") : -1]
            if _USER_ID_RE.fullmatch(name):
                found.add(name)
        return found

    def lookup_user_id(self, login_id):
        try:
            data = _client().get_object(
                Bucket=TEST_BUCKET, Key=f"users/_auth/login/{login_id}.json"
            )["Body"].read()
        except ClientError:
            return None
        return json.loads(data.decode("utf-8")).get("userId")

    def target_id(self, login_id):
        return self.lookup_user_id(login_id) or BOGUS_USER_ID

    # request ------------------------------------------------------------------------
    def forbidden_values(self):
        values = {SIGNING_SECRET_VALUE, BOOTSTRAP_TOKEN_VALUE}
        values |= self.passwords | self.temp_passwords | self.wrong_tokens | set(self.hashes)
        return values

    def call(self, body, token=None, fail=None):
        """Send one request; check the response, stdout and stderr; return (status, parsed body)."""
        for field in ("password", "currentPassword", "newPassword"):
            if isinstance(body.get(field), str):
                self.passwords.add(body[field])
        submitted = body.get("loginId")
        before_ids = self.user_ids()
        recorded_before = isinstance(submitted, str) and self.lookup_user_id(submitted) is not None

        self.recorder.clear()
        self.recorder.clear_failures()
        if fail == "ListObjectsV2":
            self.recorder.fail_on("ListObjectsV2", times=1)
        elif fail:
            self.recorder.fail_on(fail, key_startswith="users/", times=1)
        headers = {} if token is None else {"X-Mablop-Token": token}
        self.capsys.readouterr()
        resp = handler.handler({"headers": headers, "body": json.dumps(body)}, None)
        captured = self.capsys.readouterr()
        self.recorder.clear_failures()

        after_ids = self.user_ids()
        self.known_user_ids |= before_ids | after_ids
        recorded_after = isinstance(submitted, str) and self.lookup_user_id(submitted) is not None

        status = resp["statusCode"]
        raw = resp["body"]
        parsed = json.loads(raw)
        action = body.get("action")
        self.statuses.append((action, status))

        # --- responses ---
        forbidden = self.forbidden_values()
        if not (action in _TOKEN_ACTIONS and status == 200):
            for token_value in self.tokens:
                forbidden |= {token_value, *token_value.split(".")}
        for secret in forbidden:
            assert not _leaks(secret, raw, parsed), f"response of {action} leaks a secret"
        if (
            isinstance(submitted, str)
            and not recorded_before
            and not recorded_after
            and _SAFE_LOGIN_ID_RE.fullmatch(submitted)
        ):
            assert not _leaks(submitted, raw, parsed), "response echoes an unknown loginId"
        if action == "admin-reset-password" and status == 200:
            assert set(parsed) == {"userId", "temporaryPassword"}
        for key in _keys(parsed):
            assert key not in _FORBIDDEN_RESPONSE_KEYS, f"response of {action} has key {key!r}"
        if action == "admin-list-users" and status == 200:
            for entry in parsed["users"]:
                assert set(entry) == {
                    "userId", "loginId", "name", "role", "status", "createdAt", "approvedAt",
                }

        # --- temporary password: only in this one response, never in logs ---
        if action == "admin-reset-password" and status == 200:
            temp = parsed["temporaryPassword"]
            assert raw.count(temp) == 1
            self.temp_passwords.add(temp)

        # --- tokens issued by this response ---
        if action in _TOKEN_ACTIONS and status == 200:
            self.tokens.add(parsed["token"])

        # --- logs and stderr ---
        log_forbidden = self.forbidden_values()
        for token_value in self.tokens:
            log_forbidden |= {token_value, *token_value.split(".")}
        self._check_logs(captured.out, log_forbidden, submitted, recorded_before, recorded_after)
        for secret in log_forbidden:
            assert secret not in captured.err, "stderr leaks a secret"
        return status, parsed

    def _check_logs(self, out, forbidden, submitted, recorded_before, recorded_after):
        for line in out.splitlines():
            if not line.strip():
                continue
            record = json.loads(line)  # every stdout line must be a JSON log record
            assert isinstance(record, dict)
            assert set(record) <= _LOG_KEYS, f"unexpected log keys: {sorted(record)}"
            assert isinstance(record.get("event"), str) and record["event"]
            assert isinstance(record.get("outcome"), str) and record["outcome"]
            if "userId" in record:
                assert _USER_ID_RE.fullmatch(record["userId"])
                assert record["userId"] in self.known_user_ids, "userId of a user that never existed"
            for secret in forbidden:
                assert not _leaks(secret, line, record), "log line leaks a secret"
            if (
                isinstance(submitted, str)
                and not recorded_before
                and not recorded_after
                and _SAFE_LOGIN_ID_RE.fullmatch(submitted)
            ):
                assert not _leaks(submitted, line, record), "log line has an unknown loginId"

    def assert_store_clean(self):
        """No S3 object holds a plaintext password, the Signing_Secret, or the Bootstrap_Token."""
        secrets_ = {SIGNING_SECRET_VALUE, BOOTSTRAP_TOKEN_VALUE} | self.passwords | self.temp_passwords
        client = _client()
        for page in client.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
            for obj in page.get("Contents", []):
                data = client.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
                text = data.decode("utf-8", "replace")
                try:
                    parsed = json.loads(text)
                except ValueError:
                    parsed = None
                for secret in secrets_:
                    assert not _leaks(secret, text, parsed), f"{obj['Key']} stores a secret"

    # setup --------------------------------------------------------------------------
    def setup_admin(self):
        status, _ = self.call(
            {"action": "bootstrap-admin", "loginId": ADMIN_LOGIN, "name": "관리자", "password": ADMIN_PASSWORD},
            token=BOOTSTRAP_TOKEN_VALUE,
        )
        assert status == 200
        status, body = self.call({"action": "login", "loginId": ADMIN_LOGIN, "password": ADMIN_PASSWORD})
        assert status == 200
        self.admin_token = body["token"]

    def admin(self, body, fail=None):
        return self.call(body, token=self.admin_token, fail=fail)

    # one random operation -----------------------------------------------------------
    def run(self, op, fail):
        kind = op[0]
        if kind == "register":
            _, lid, name, pw = op
            status, _ = self.call(
                {"action": "register", "loginId": lid, "name": name, "password": pw}, fail=fail
            )
            if status == 200:
                self.guess.setdefault(lid, pw)
        elif kind == "login":
            _, lid, pw = op
            status, body = self.call(
                {"action": "login", "loginId": lid, "password": pw or self.guess.get(lid, "Pw#unknown-yet")},
                fail=fail,
            )
            if status == 200:
                self.session[lid] = body["token"]
        elif kind == "change":
            _, lid, new_pw, wrong = op
            current = "Pw#definitely-wrong" if wrong else self.guess.get(lid, "Pw#unknown-yet")
            status, body = self.call(
                {"action": "change-password", "currentPassword": current, "newPassword": new_pw},
                token=self.session.get(lid),
                fail=fail,
            )
            if status == 200:
                self.guess[lid] = new_pw
                self.session[lid] = body["token"]
        elif kind in ("approve", "reject", "disable", "enable"):
            self.admin({"action": f"admin-{kind}-user", "userId": self.target_id(op[1])}, fail=fail)
        elif kind == "reset":
            status, body = self.admin(
                {"action": "admin-reset-password", "userId": self.target_id(op[1])}, fail=fail
            )
            if status == 200:
                self.guess[op[1]] = body["temporaryPassword"]
        elif kind == "list":
            self.admin({"action": "admin-list-users"}, fail=fail)
        elif kind == "me":
            self.call({"action": "get-me"}, token=self.session.get(op[1]), fail=fail)
        elif kind == "badtoken":
            base = self.session.get(op[1]) or self.admin_token
            tampered = base[:-2] + ("AA" if not base.endswith("AA") else "BB")
            self.wrong_tokens.add(tampered)
            self.call({"action": "get-me"}, token=tampered, fail=fail)
        elif kind == "bootstrap":
            _, lid, name, pw, good_token = op
            wrong = "wrong-bootstrap-" + pw.encode("utf-8").hex()[:12]
            if not good_token:
                self.wrong_tokens.add(wrong)
            self.call(
                {"action": "bootstrap-admin", "loginId": lid, "name": name, "password": pw},
                token=BOOTSTRAP_TOKEN_VALUE if good_token else wrong,
                fail=fail,
            )
        else:  # pragma: no cover
            raise AssertionError(kind)


# --- tests ------------------------------------------------------------------------------


@_SETTINGS
@given(steps=_steps)
def test_secrets_and_personal_data_never_leak(env, capsys, generated_hashes, steps):
    """Validates: Requirements 3.5, 6.3, 6.4, 6.5, 7.4"""
    _empty_bucket()
    generated_hashes.clear()
    h = Harness(capsys, env, generated_hashes)
    h.setup_admin()
    for op, fail in steps:
        h.run(op, fail)
    h.assert_store_clean()


def test_full_account_flow_has_no_leaks(env, capsys, generated_hashes):
    """Validates: Requirements 3.5, 6.3, 6.4, 6.5, 7.4

    A fixed happy path (register, approve, login, change password, reset password, list users,
    failed logins, tampered token, second bootstrap) so the leak checks above run on successful
    responses too, not only on error paths.
    """
    _empty_bucket()
    h = Harness(capsys, env, generated_hashes)
    h.setup_admin()

    lid = "alice_01"
    assert h.call({"action": "register", "loginId": lid, "name": "앨리스", "password": "Pw#alice-first-pass"})[0] == 200
    uid = h.target_id(lid)
    assert h.admin({"action": "admin-approve-user", "userId": uid})[0] == 200
    status, body = h.call({"action": "login", "loginId": lid, "password": "Pw#alice-first-pass"})
    assert status == 200 and body["user"] == {"name": "앨리스", "loginId": lid, "role": "member"}
    token = body["token"]
    assert h.call({"action": "get-me"}, token=token)[0] == 200
    status, body = h.call(
        {"action": "change-password", "currentPassword": "Pw#alice-first-pass", "newPassword": "Pw#alice-second-pass"},
        token=token,
    )
    assert status == 200
    assert h.call({"action": "login", "loginId": lid, "password": "Pw#alice-first-pass"})[0] == 401
    assert h.call({"action": "login", "loginId": "ghost_deadbeef", "password": "Pw#whatever-123"})[0] == 401
    status, body = h.admin({"action": "admin-reset-password", "userId": uid})
    assert status == 200 and len(body["temporaryPassword"]) == 16
    temp = body["temporaryPassword"]
    assert h.call({"action": "login", "loginId": lid, "password": temp})[0] == 200
    status, body = h.admin({"action": "admin-list-users"})
    assert status == 200 and {u["loginId"] for u in body["users"]} == {ADMIN_LOGIN, lid}
    assert h.call({"action": "get-me"}, token=token[:-2] + "AA")[0] == 401
    status, body = h.call(
        {"action": "bootstrap-admin", "loginId": "zed_9999", "name": "제드", "password": "Pw#zed-password-1"},
        token=BOOTSTRAP_TOKEN_VALUE,
    )
    assert status == 403
    h.assert_store_clean()

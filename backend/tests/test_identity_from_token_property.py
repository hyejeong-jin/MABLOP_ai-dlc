"""Property-based test: the acting identity comes only from the Session_Token.

Property 9 (Req 4.7). Hypothesis + moto + pytest. For any request body that adds
`userId`, `loginId`, or an S3 key prefix belonging to another user, and any valid
token of user A:

- the userId bound for dispatch (user_scope and ctx.user_id) is A, and
- no S3 key outside A's prefix is read or written by the action (the only other
  key touched is the global rate-limit window counter), and
- the other user's stored objects are unchanged.

The tests run through `handler.handler` (Access_Controller, then dispatch). The
test opts out of the autouse userId binding so only the token can supply identity.
"""
import json

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import access_controller
import handler
import s3store
import user_scope

pytestmark = pytest.mark.unbound_user

PBT_SETTINGS = settings(
    max_examples=30,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

login_ids = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=4, max_size=24)
# Body fields a caller might use to try to act as someone else; values are resolved per example.
identity_fields = st.sampled_from(
    ["userId", "loginId", "uid", "user", "prefix", "userPrefix", "key", "s3Key", "role"]
)
value_kinds = st.sampled_from(
    ["other_user_id", "other_login", "other_prefix", "other_object_key", "own_object_key", "admin", "junk"]
)
extra_specs = st.lists(st.tuples(identity_fields, value_kinds, st.text(max_size=12)), max_size=5)
# Actions that run in the acting user's scope and need no Bedrock Grant.
user_actions = st.sampled_from(
    ["get-me", "list-drafts", "delete-draft", "grant-list-mine", "get-learning-status"]
)
draft_ids = st.sampled_from(["adraft", "bdraft"])


@pytest.fixture(autouse=True)
def _environment(monkeypatch):
    """Neutralize the global rate limit and the warm window state."""
    monkeypatch.setenv("MABLOP_MAX_RATE", str(10**9))
    monkeypatch.setattr(access_controller, "_window", None)


def _seed_draft(user_id: str, draft_id: str) -> None:
    """A stored draft plus its Draft_Index entry under user_id's prefix."""
    s3store.put_bytes(
        s3store.user_key(f"generated-posts/{draft_id}.md", user_id),
        f"# {user_id} {draft_id}".encode("utf-8"),
        content_type="text/markdown",
    )
    s3store.put_json(
        s3store.user_key("metadata/drafts.json", user_id),
        {
            "v": 1,
            "drafts": [{"draftId": draft_id, "title": draft_id, "createdAt": "2024-01-01T00:00:00.000Z"}],
        },
    )


def _snapshot(user_id: str) -> dict:
    """Every object under user_id's prefix: key -> bytes."""
    prefix = f"users/{user_id}/"
    return {o["key"]: s3store.get_bytes(o["key"]) for o in s3store.list_keys(prefix)}


def _extras(specs, a, b) -> dict:
    """Resolve (field, kind, junk) triples into body fields that point at user B."""
    values = {
        "other_user_id": b["userId"],
        "other_login": b["loginId"],
        "other_prefix": f"users/{b['userId']}/",
        "other_object_key": f"users/{b['userId']}/generated-posts/bdraft.md",
        "own_object_key": f"users/{a['userId']}/generated-posts/adraft.md",
        "admin": "admin",
    }
    return {field: values.get(kind, junk) for field, kind, junk in specs}


def _event(token: str, body: dict) -> dict:
    return {"headers": {"X-Mablop-Token": token}, "body": json.dumps(body)}


def _wipe_users() -> None:
    """The moto bucket is shared by all examples of a test; start each example from an empty one."""
    for obj in s3store.list_keys("users/"):
        s3store.delete(obj["key"])


def _setup(create_user, make_session_token, login_a, login_b):
    _wipe_users()
    a = create_user(login_a, role="member", status="active")
    b = create_user(login_b, role="member", status="active")
    _seed_draft(a["userId"], "adraft")
    _seed_draft(b["userId"], "bdraft")
    return a, b, make_session_token(a["userId"])


@PBT_SETTINGS
@given(
    login_a=login_ids,
    login_b=login_ids,
    action=user_actions,
    draft_id=draft_ids,
    specs=extra_specs,
)
def test_property_actions_stay_inside_token_users_prefix(
    s3_bucket,
    s3_calls,
    signing_secret,
    create_user,
    make_session_token,
    login_a,
    login_b,
    action,
    draft_id,
    specs,
):
    """Feature: user-auth-admin, Property 9 - identity comes only from the token.

    Validates: Requirements 4.7
    """
    if login_a == login_b:
        return
    a, b, token = _setup(create_user, make_session_token, login_a, login_b)
    assert a["userId"] != b["userId"]

    body = {**_extras(specs, a, b), "action": action}
    if action == "delete-draft":
        body["draftId"] = draft_id

    other_before = _snapshot(b["userId"])
    s3_calls.clear()
    resp = handler.handler(_event(token, body))

    # No S3 key outside A's prefix is read or written (apart from the global rate-limit counter).
    allowed_prefix = f"users/{a['userId']}/"
    touched = set(s3_calls.keys()) | set(s3_calls.touched_keys())
    stray = {k for k in touched if not k.startswith(allowed_prefix) and k != access_controller.RATELIMIT_KEY}
    assert not stray, f"keys outside the token user's prefix: {stray}"
    assert {c["key"] for c in s3_calls.writes()} <= {access_controller.RATELIMIT_KEY} | {
        k for k in touched if k.startswith(allowed_prefix)
    }

    # The other user's data is untouched.
    assert _snapshot(b["userId"]) == other_before

    payload = json.loads(resp["body"])
    if action == "get-me":
        assert resp["statusCode"] == 200
        assert payload["userId"] == a["userId"]
        assert payload["loginId"] == login_a
        assert payload["role"] == "member"
    elif action == "list-drafts":
        assert resp["statusCode"] == 200
        assert [d["draftId"] for d in payload["drafts"]] == ["adraft"]
    elif action == "delete-draft":
        if draft_id == "adraft":
            assert resp["statusCode"] == 200
            assert payload == {"draftId": "adraft", "deleted": True}
        else:
            # B's draft id is not visible to A.
            assert resp["statusCode"] == 404


@PBT_SETTINGS
@given(
    login_a=login_ids,
    login_b=login_ids,
    action=user_actions,
    specs=extra_specs,
)
def test_property_bound_user_is_the_token_user(
    s3_bucket,
    s3_calls,
    signing_secret,
    create_user,
    make_session_token,
    monkeypatch,
    login_a,
    login_b,
    action,
    specs,
):
    """Feature: user-auth-admin, Property 9 - the userId bound for dispatch is the token's user.

    Validates: Requirements 4.7
    """
    if login_a == login_b:
        return
    a, b, token = _setup(create_user, make_session_token, login_a, login_b)
    seen = []

    def probe(body, ctx, now=None):
        seen.append((user_scope.current_user_id(), ctx.user_id, ctx.user["loginId"]))
        return {}

    monkeypatch.setitem(handler.DISPATCH, action, probe)
    body = {**_extras(specs, a, b), "action": action}
    resp = handler.handler(_event(token, body))

    assert resp["statusCode"] == 200
    assert seen == [(a["userId"], a["userId"], login_a)]


def test_identity_unbound_outside_dispatch():
    """After a request finishes, no userId stays bound (nothing carries over between requests)."""
    with pytest.raises(Exception) as excinfo:
        user_scope.current_user_id()
    assert getattr(excinfo.value, "code", None) == "internal_error"

"""Handler tests: access-control-first + action dispatch (Req 5.2, 6.2, 8.1, 8.5).

Function URL v2 events are faked. moto backs the rate-limit S3 path. Component
functions are stubbed to sentinels so routing is tested without the (later)
real pipelines. No stack trace must leak on error.
"""
import base64
import importlib
import json

import pytest

UNAUTHENTICATED_MSG = "로그인이 필요합니다. 다시 로그인하세요."
_state = {}
_ADMIN = object()


@pytest.fixture
def h(s3_bucket, signing_secret, create_user, make_session_token):
    """Fresh moto S3/SSM + an admin Session_Token; reload modules so warm rate-limit state is reset.

    Existing Bedrock actions run as an admin token, which is exempt from Grant verification.
    """
    import s3store

    importlib.reload(s3store)
    import access_controller

    importlib.reload(access_controller)
    import handler

    importlib.reload(handler)
    admin = create_user("admin1", role="admin")
    _state["admin"] = make_session_token(admin["userId"])
    yield handler


def _event(body, token=_ADMIN, base64_encode=False):
    """Build a minimal Function URL v2 event with optional Session_Token / base64 body."""
    raw = body if isinstance(body, str) else json.dumps(body)
    if token is _ADMIN:
        token = _state["admin"]
    headers = {}
    if token is not None:
        headers["X-Mablop-Token"] = token
    evt = {"headers": headers}
    if base64_encode:
        evt["body"] = base64.b64encode(raw.encode("utf-8")).decode("ascii")
        evt["isBase64Encoded"] = True
    else:
        evt["body"] = raw
    return evt

def _body(resp):
    """Parse the JSON body of a handler response."""
    return json.loads(resp["body"])


# --- (a) missing token -> 401 (Req 5.2) ---

def test_missing_token_returns_401(h):
    resp = h.handler(_event({"action": "learn-style"}, token=None))
    assert resp["statusCode"] == 401
    assert _body(resp)["error"]["code"] == "unauthorized"
    assert _body(resp)["error"]["message"] == UNAUTHENTICATED_MSG


# --- (b) valid token + known action routes to the right component -> 200 ---

@pytest.mark.parametrize(
    "action, module, fn, payload_key",
    [
        ("issue-presigned-urls", "image_analyzer", "issue_presigned_urls", "files"),
        ("learn-style", "style_learner", "learn_style", None),
        ("analyze-images", "image_analyzer", "analyze_images", "images"),
        ("generate-draft", "drafting_engine", "generate_draft", None),
    ],
)
def test_known_action_routes_to_component(h, monkeypatch, action, module, fn, payload_key):
    mod = importlib.import_module(module)
    sentinel = {"routed": action}
    monkeypatch.setattr(mod, fn, lambda *a, **k: sentinel)
    body = {"action": action}
    if payload_key:
        body[payload_key] = []
    resp = h.handler(_event(body))
    assert resp["statusCode"] == 200
    assert _body(resp) == sentinel


def test_get_result_routes_to_component(h, monkeypatch):
    import drafting_engine

    monkeypatch.setattr(drafting_engine, "get_result", lambda *a, **k: {"status": "ready"})
    resp = h.handler(_event({"action": "get-result", "kind": "draft", "id": "d1"}))
    assert resp["statusCode"] == 200
    assert _body(resp)["status"] == "ready"


def test_base64_encoded_body_is_decoded(h, monkeypatch):
    import style_learner

    monkeypatch.setattr(style_learner, "learn_style", lambda *a, **k: {"ok": True})
    resp = h.handler(_event({"action": "learn-style"}, base64_encode=True))
    assert resp["statusCode"] == 200
    assert _body(resp)["ok"] is True


# --- (c) unknown action -> handled error, not a crash (Req 8.1, 8.5) ---

def test_unknown_action_is_handled(h):
    resp = h.handler(_event({"action": "nope"}))
    assert resp["statusCode"] == 400
    body = _body(resp)
    assert body["error"]["code"] == "bad_request"
    assert "nope" in body["error"]["message"]


def test_non_object_body_is_handled(h):
    resp = h.handler(_event("[1, 2, 3]"))
    assert resp["statusCode"] == 400
    assert _body(resp)["error"]["code"] == "bad_request"


def test_malformed_json_is_handled(h):
    resp = h.handler(_event("{not json"))
    assert resp["statusCode"] == 500
    assert _body(resp)["error"]["code"] == "internal_error"


# --- (d) component raising -> 500 internal_error, no stack trace in body ---

def test_component_error_becomes_500_without_stacktrace(h, monkeypatch):
    import style_learner

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(style_learner, "learn_style", boom)
    resp = h.handler(_event({"action": "learn-style"}))
    assert resp["statusCode"] == 500
    body = _body(resp)
    assert body["error"]["code"] == "internal_error"
    # Only the error code/message shape is returned; no formatted traceback leaks.
    assert "Traceback" not in resp["body"]
    assert 'File "' not in resp["body"]
    assert set(body["error"].keys()) == {"code", "message"}


def test_generate_draft_without_learned_index_uses_general_style(h, monkeypatch):
    """Real component on an empty workspace: a general-style draft, flagged with generalStyle."""
    import drafting_engine

    monkeypatch.setattr(drafting_engine.bedrock, "text_generate", lambda *a, **k: "# t\n\nbody")
    resp = h.handler(_event({"action": "generate-draft", "title": "t"}))
    assert resp["statusCode"] == 200
    assert _body(resp)["generalStyle"] is True


def test_unexpected_generate_draft_failure_becomes_500(h, monkeypatch):
    """An unexpected exception in the real component surfaces as a clean 500."""
    import drafting_engine

    def boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(drafting_engine, "_retrieve_chunks", boom)
    resp = h.handler(_event({"action": "generate-draft", "title": "t"}))
    assert resp["statusCode"] == 500
    assert _body(resp)["error"]["code"] == "internal_error"
    assert "Traceback" not in resp["body"]


def test_get_activity_routes_authenticated_request_with_detail_ids(h, monkeypatch):
    import activity_store

    expected = {
        "events": [
            {
                "eventId": "evt1",
                "type": "voc-created",
                "occurredAt": "2025-01-10T01:00:00Z",
                "count": 1,
                "relatedVocId": "voc_abc",
                "relatedRunId": "run_def",
            }
        ],
        "daily": [
            {
                "date": "2025-01-10",
                "total": 1,
                "byType": {"voc-created": 1},
                "eventIds": ["evt1"],
            }
        ],
        "range": {"from": "2025-01-10T00:00:00+09:00", "to": "2025-01-11T00:00:00+09:00"},
    }
    monkeypatch.setattr(activity_store, "get_activity", lambda body, ctx: expected)

    resp = h.handler(_event({"action": "get-activity", "from": "2025-01-10", "to": "2025-01-10"}))

    assert resp["statusCode"] == 200
    assert _body(resp) == expected

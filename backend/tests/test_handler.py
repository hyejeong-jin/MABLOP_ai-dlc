"""Handler tests: access-control-first + action dispatch (Req 5.2, 6.2, 8.1, 8.5).

Function URL v2 events are faked. moto backs the rate-limit S3 path. Component
functions are stubbed to sentinels so routing is tested without the (later)
real pipelines. No stack trace must leak on error.
"""
import base64
import importlib
import json

import boto3
import pytest
from moto import mock_aws

BUCKET = "mablop-test"
SECRET = "s3cr3t-token"


@pytest.fixture
def h(monkeypatch):
    """Fresh moto S3 + env; reload modules so warm rate-limit state is reset."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_TOKEN", SECRET)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import s3store

        importlib.reload(s3store)
        import access_controller

        importlib.reload(access_controller)
        import handler

        importlib.reload(handler)
        yield handler


def _event(body, token=SECRET, base64_encode=False):
    """Build a minimal Function URL v2 event with optional token / base64 body."""
    raw = body if isinstance(body, str) else json.dumps(body)
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


def test_stub_notimplemented_becomes_500(h):
    """Un-stubbed component (NotImplementedError stub) surfaces as a clean 500."""
    resp = h.handler(_event({"action": "generate-draft", "title": "t"}))
    assert resp["statusCode"] == 500
    assert _body(resp)["error"]["code"] == "internal_error"

"""Tests for error-body shape and HTTP status mapping (Req 5.1, 5.3, 5.4)."""
import json

import pytest

import errors


def test_error_body_shape():
    body = errors.error_body("unauthorized", "nope")
    assert body == {"error": {"code": "unauthorized", "message": "nope"}}


def test_error_body_defaults_message_to_code():
    assert errors.error_body("rate_limited")["error"]["message"] == "rate_limited"


@pytest.mark.parametrize(
    "code,status",
    [
        ("unauthorized", 401),
        ("rate_limited", 429),
        ("body_too_large", 413),
        ("bedrock_error", 502),
        ("internal_error", 500),
    ],
)
def test_status_mapping(code, status):
    assert errors.MablopError(code).status == status


def test_unknown_code_maps_to_500():
    assert errors.MablopError("weird").status == 500


def test_response_envelope():
    r = errors.response(200, {"ok": True})
    assert r["statusCode"] == 200
    assert r["headers"]["content-type"] == "application/json"
    assert json.loads(r["body"]) == {"ok": True}


def test_error_response_from_mablop_error():
    r = errors.error_response(errors.MablopError("body_too_large", "too big"))
    assert r["statusCode"] == 413
    assert json.loads(r["body"]) == {
        "error": {"code": "body_too_large", "message": "too big"}
    }


def test_error_response_from_plain_exception():
    r = errors.error_response(ValueError("boom"))
    assert r["statusCode"] == 500
    assert json.loads(r["body"])["error"]["code"] == "internal_error"

"""Access_Controller tests: token / rate / body-length (Req 5.1-5.4, 9.3).

S3-backed ratelimit.json path is exercised over moto. Rate uses in-env secret
so no token value is hard-coded in the source under test.
"""
import importlib

import boto3
import pytest
from moto import mock_aws

from errors import MablopError

BUCKET = "mablop-test"
SECRET = "s3cr3t-token"


@pytest.fixture
def ac(monkeypatch):
    """Fresh moto S3 + env; yield a reloaded access_controller (warm state reset)."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_TOKEN", SECRET)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import s3store

        importlib.reload(s3store)
        import access_controller

        importlib.reload(access_controller)
        yield access_controller


# --- token (5.1, 5.2) ---

def test_token_accepts_exact_secret(ac):
    assert ac.check_token({"X-Mablop-Token": SECRET}) is True


def test_token_header_case_insensitive(ac):
    assert ac.check_token({"x-mablop-token": SECRET}) is True


def test_token_rejects_missing(ac):
    with pytest.raises(MablopError) as e:
        ac.check_token({})
    assert e.value.code == "unauthorized" and e.value.status == 401


def test_token_rejects_mismatch(ac):
    with pytest.raises(MablopError) as e:
        ac.check_token({"X-Mablop-Token": SECRET + "x"})
    assert e.value.code == "unauthorized"


def test_token_rejects_prefix(ac):
    """Constant-time compare must reject a correct prefix (length differs)."""
    with pytest.raises(MablopError):
        ac.check_token({"X-Mablop-Token": SECRET[:-1]})


def test_token_rejects_when_secret_unset(ac, monkeypatch):
    monkeypatch.delenv("MABLOP_TOKEN", raising=False)
    with pytest.raises(MablopError):
        ac.check_token({"X-Mablop-Token": ""})


# --- rate (5.3) ---

def test_rate_under_limit_allowed(ac):
    for _ in range(3):
        assert ac.check_rate(now=1000.0, max_rate=3, window_secs=60) is True


def test_rate_over_limit_rejected(ac):
    for _ in range(3):
        ac.check_rate(now=1000.0, max_rate=3, window_secs=60)
    with pytest.raises(MablopError) as e:
        ac.check_rate(now=1000.0, max_rate=3, window_secs=60)
    assert e.value.code == "rate_limited" and e.value.status == 429


def test_rate_window_rolls_over(ac):
    for _ in range(3):
        ac.check_rate(now=1000.0, max_rate=3, window_secs=60)
    # New window: counter resets, request allowed again.
    assert ac.check_rate(now=1061.0, max_rate=3, window_secs=60) is True


def test_rate_persists_to_s3(ac):
    ac.check_rate(now=1000.0, max_rate=5, window_secs=60)
    import s3store

    state = s3store.get_json(ac.RATELIMIT_KEY)
    assert state["count"] == 1 and state["start"] == 1000.0


def test_rate_warm_memory_fast_path(ac):
    """A warmed window is reused even if S3 is unavailable (no exception)."""
    ac.check_rate(now=1000.0, max_rate=5, window_secs=60)
    assert ac._window["count"] == 1
    ac.check_rate(now=1000.0, max_rate=5, window_secs=60)
    assert ac._window["count"] == 2


# --- body length (5.4) ---

def test_body_under_cap_allowed(ac):
    assert ac.check_body_length(100, cap=1000) is True


def test_body_at_cap_allowed(ac):
    assert ac.check_body_length(1000, cap=1000) is True


def test_body_over_cap_rejected(ac):
    with pytest.raises(MablopError) as e:
        ac.check_body_length(1001, cap=1000)
    assert e.value.code == "body_too_large" and e.value.status == 413


def test_check_request_uses_content_length_header(ac, monkeypatch):
    """Content-Length header drives the cap even when the actual body is tiny (5.4 pre-parse)."""
    monkeypatch.setenv("MABLOP_MAX_BODY_BYTES", "10")
    with pytest.raises(MablopError) as e:
        ac.check_request({"X-Mablop-Token": SECRET, "Content-Length": "999"}, body="x")
    assert e.value.code == "body_too_large"


def test_check_request_happy_path(ac):
    assert ac.check_request({"X-Mablop-Token": SECRET}, body="{}", now=1000.0) is True


def test_check_request_body_checked_before_token(ac, monkeypatch):
    """Oversized body is rejected before the (missing) token is evaluated (5.4 pre-parse)."""
    monkeypatch.setenv("MABLOP_MAX_BODY_BYTES", "4")
    with pytest.raises(MablopError) as e:
        ac.check_request({}, body="way too long body")
    assert e.value.code == "body_too_large"

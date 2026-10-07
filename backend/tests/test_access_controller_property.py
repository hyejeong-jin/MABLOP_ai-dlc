"""Property-based tests for Access_Controller: token / rate / body-length.

Properties 14, 15, 16 (Req 5.1-5.4). Hypothesis + moto + pytest. The rate
property reloads access_controller + s3store per example over a fresh moto
bucket because `_window` is module-global warm state persisted to S3.
"""
import importlib
import os

import boto3
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

from errors import MablopError

BUCKET = "mablop-test"


# --- Property 14: token (3.5) ---

@settings(max_examples=100, deadline=None)
@given(
    secret=st.text(min_size=0, max_size=40),
    candidate=st.text(min_size=0, max_size=40),
)
def test_property_token_accepts_iff_exact_secret(secret, candidate):
    """Feature: mablop-mvp, Property 14 - accept IFF candidate == secret (non-empty).

    Validates: Requirements 5.1, 5.2
    """
    import access_controller

    headers = {"X-Mablop-Token": candidate}
    should_accept = bool(secret) and candidate == secret
    if should_accept:
        assert access_controller.check_token(headers, secret=secret) is True
    else:
        try:
            access_controller.check_token(headers, secret=secret)
            raise AssertionError("expected MablopError('unauthorized')")
        except MablopError as e:
            assert e.code == "unauthorized"


# --- Property 15: rate (3.6) ---

@settings(max_examples=100, deadline=None)
@given(
    max_rate=st.integers(min_value=1, max_value=20),
    extra=st.integers(min_value=0, max_value=10),
    offsets=st.lists(st.floats(min_value=0, max_value=59), min_size=0, max_size=30),
)
def test_property_rate_bounds_requests_per_window(max_rate, extra, offsets):
    """Feature: mablop-mvp, Property 15 - first max_rate allowed in a window, rest rejected; new window resets.

    Validates: Requirements 5.3
    """
    window = 60.0
    now = 1000.0
    total = max_rate + extra  # drive at least max_rate requests, maybe over
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        os.environ["MABLOP_BUCKET"] = BUCKET
        import s3store

        importlib.reload(s3store)
        import access_controller

        importlib.reload(access_controller)

        # All within one window (same `now`): first max_rate succeed, rest raise.
        for i in range(total):
            if i < max_rate:
                assert access_controller.check_rate(now=now, max_rate=max_rate, window_secs=window) is True
            else:
                try:
                    access_controller.check_rate(now=now, max_rate=max_rate, window_secs=window)
                    raise AssertionError("expected MablopError('rate_limited')")
                except MablopError as e:
                    assert e.code == "rate_limited"

        # Varying offsets inside the same window must not grant extra capacity.
        for off in offsets:
            try:
                access_controller.check_rate(now=now + off, max_rate=max_rate, window_secs=window)
                # Only acceptable if we never exhausted the window (total < max_rate).
                assert total < max_rate
            except MablopError as e:
                assert e.code == "rate_limited"

        # Advancing beyond the window resets the counter: a fresh request is allowed.
        assert access_controller.check_rate(now=now + window, max_rate=max_rate, window_secs=window) is True


# --- Property 16: body-length (3.7) ---

@settings(max_examples=100, deadline=None)
@given(
    body_len=st.integers(min_value=0, max_value=10**7),
    cap=st.integers(min_value=0, max_value=10**6),
)
def test_property_body_length_rejects_iff_over_cap(body_len, cap):
    """Feature: mablop-mvp, Property 16 - reject IFF body size > cap.

    Validates: Requirements 5.4
    """
    import access_controller

    if body_len > cap:
        try:
            access_controller.check_body_length(body_len, cap=cap)
            raise AssertionError("expected MablopError('body_too_large')")
        except MablopError as e:
            assert e.code == "body_too_large"
    else:
        assert access_controller.check_body_length(body_len, cap=cap) is True

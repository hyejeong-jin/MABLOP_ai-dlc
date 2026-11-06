"""Property-based tests for auth_crypto: Session_Token round trip and structure.

Property 1 (Req 2.2, 2.3, 4.3). Hypothesis + moto + pytest. The Signing_Secret
comes from moto SSM through the `signing_secret` fixture; each example writes
its own generated secret to the same SSM parameter and resets the auth_crypto
cache, so the real `get_signing_secret` path is exercised.
"""
import base64
import hashlib
import hmac
import json
import re

import boto3
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto

from conftest import SIGNING_SECRET_PATH, TEST_REGION

_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")

user_ids = st.builds(
    lambda h: "u_" + h,
    st.text(alphabet="0123456789abcdef", min_size=16, max_size=16),
)
# Any secret of at least 32 characters (no surrogates: SSM stores UTF-8 text).
secrets_st = st.text(
    alphabet=st.characters(blacklist_categories=("Cs", "Cc")),
    min_size=auth_crypto.SIGNING_SECRET_MIN_LEN,
    max_size=96,
)
issue_times = st.integers(min_value=0, max_value=10**13)
# Verification instant: from the issue time up to (but excluding) expiry.
verify_offsets = st.integers(min_value=0, max_value=auth_crypto.TOKEN_TTL_MS - 1)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(
    user_id=user_ids,
    secret=secrets_st,
    now_ms=issue_times,
    offset=verify_offsets,
)
def test_property_session_token_round_trip_and_structure(
    signing_secret, user_id, secret, now_ms, offset
):
    """Feature: user-auth-admin, Property 1 - Session_Token round trip and structure.

    Validates: Requirements 2.2, 2.3, 4.3
    """
    boto3.client("ssm", region_name=TEST_REGION).put_parameter(
        Name=SIGNING_SECRET_PATH, Value=secret, Type="SecureString", Overwrite=True
    )
    auth_crypto.reset_cache()

    token, exp = auth_crypto.sign_token(user_id, now_ms)

    # Structure: <base64url>.<base64url>, no padding.
    assert token.count(".") == 1
    payload_segment, signature_segment = token.split(".")
    assert _B64URL_RE.match(payload_segment)
    assert _B64URL_RE.match(signature_segment)
    assert "=" not in token
    assert len(token) <= auth_crypto.TOKEN_MAX_LEN

    # Signature equals HMAC-SHA256 of the first segment.
    expected_sig = hmac.new(
        secret.encode("utf-8"), payload_segment.encode("ascii"), hashlib.sha256
    ).digest()
    assert signature_segment == _b64url(expected_sig)

    # Payload content: exp - iat is exactly 8 hours.
    payload = json.loads(
        base64.urlsafe_b64decode(payload_segment + "=" * (-len(payload_segment) % 4))
    )
    assert payload == {"v": 1, "uid": user_id, "iat": now_ms, "exp": exp}
    assert exp - now_ms == 8 * 60 * 60 * 1000

    # Round trip at any time in [iat, exp).
    verified = auth_crypto.verify_token(token, now_ms + offset)
    assert verified.uid == user_id
    assert verified.iat == now_ms
    assert verified.exp == exp

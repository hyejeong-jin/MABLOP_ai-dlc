"""Property-based tests for auth_crypto: altered, expired, or foreign tokens.

Property 2 (Req 2.2, 4.3, 4.4). Hypothesis + moto + pytest. The Signing_Secret
comes from moto SSM through the `signing_secret` fixture (function-scoped, so
the health check for function-scoped fixtures is suppressed; the fixture is
read-only for these tests). Tokens that `sign_token` cannot produce (future
`iat`, foreign secret, malformed payloads) are built by hand with the same
format: base64url(payload) + "." + base64url(HMAC-SHA256(secret, payload_b64)).

Every rejected case must raise `MablopError("unauthorized")` with the same
Korean message, and no other exception type.
"""
import base64
import hashlib
import hmac
import json

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

import auth_crypto
from errors import MablopError

UNAUTHENTICATED_LITERAL = "로그인이 필요합니다. 다시 로그인하세요."

_B64URL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_TTL = auth_crypto.TOKEN_TTL_MS
_SKEW = auth_crypto.CLOCK_SKEW_MS

PBT_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

user_ids = st.builds(
    lambda h: "u_" + h,
    st.text(alphabet="0123456789abcdef", min_size=16, max_size=16),
)
issue_times = st.integers(min_value=0, max_value=10**13)


# --- helpers -----------------------------------------------------------------------------


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign_segments(secret: str, payload_segment: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), payload_segment.encode("ascii"), hashlib.sha256)
    return f"{payload_segment}.{_b64url(mac.digest())}"


def _hand_token(secret: str, payload_bytes: bytes) -> str:
    return _sign_segments(secret, _b64url(payload_bytes))


def _payload(user, iat, exp, **overrides) -> dict:
    payload = {"v": 1, "uid": user, "iat": iat, "exp": exp}
    payload.update(overrides)
    return payload


def _json_bytes(obj) -> bytes:
    return json.dumps(obj, separators=(",", ":")).encode("utf-8")


def assert_rejected(token, now_ms):
    """The token is rejected with the one uniform unauthorized error."""
    with pytest.raises(MablopError) as excinfo:
        auth_crypto.verify_token(token, now_ms)
    err = excinfo.value
    assert err.code == "unauthorized"
    assert err.status == 401
    assert err.message == UNAUTHENTICATED_LITERAL


# --- Property 2 --------------------------------------------------------------------------


@PBT_SETTINGS
@given(
    user_id=user_ids,
    now_ms=issue_times,
    offset=st.integers(min_value=0, max_value=_TTL - 1),
    position=st.integers(min_value=0, max_value=10**6),
    replacement=st.characters(blacklist_categories=("Cs",)),
)
def test_property_single_character_change_is_rejected(
    signing_secret, user_id, now_ms, offset, position, replacement
):
    """Feature: user-auth-admin, Property 2 - a valid token with one character changed is rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    token, _exp = auth_crypto.sign_token(user_id, now_ms)
    index = position % len(token)
    assume(replacement != token[index])
    altered = token[:index] + replacement + token[index + 1 :]

    assert_rejected(altered, now_ms + offset)


@PBT_SETTINGS
@given(
    user_id=user_ids,
    now_ms=issue_times,
    offset=st.integers(min_value=0, max_value=_TTL - 1),
    foreign_secret=st.text(
        alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=96
    ),
)
def test_property_token_signed_with_other_secret_is_rejected(
    signing_secret, user_id, now_ms, offset, foreign_secret
):
    """Feature: user-auth-admin, Property 2 - a token signed with a different secret is rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    assume(foreign_secret != signing_secret)
    payload = _payload(user_id, now_ms, now_ms + _TTL)
    token = _hand_token(foreign_secret, _json_bytes(payload))

    assert_rejected(token, now_ms + offset)


@PBT_SETTINGS
@given(
    user_id=user_ids,
    now_ms=issue_times,
    after_expiry=st.integers(min_value=0, max_value=10**10),
)
def test_property_token_at_or_after_expiry_is_rejected(
    signing_secret, user_id, now_ms, after_expiry
):
    """Feature: user-auth-admin, Property 2 - a token verified at or after exp is rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    token, exp = auth_crypto.sign_token(user_id, now_ms)

    assert_rejected(token, exp + after_expiry)


@PBT_SETTINGS
@given(
    user_id=user_ids,
    now_ms=issue_times,
    ahead=st.integers(min_value=_SKEW + 1, max_value=10**10),
)
def test_property_token_with_future_iat_is_rejected(signing_secret, user_id, now_ms, ahead):
    """Feature: user-auth-admin, Property 2 - a correctly signed token with iat beyond the clock skew is rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    iat = now_ms + ahead
    token = _hand_token(signing_secret, _json_bytes(_payload(user_id, iat, iat + _TTL)))

    assert_rejected(token, now_ms)


@PBT_SETTINGS
@given(
    user_id=user_ids,
    now_ms=issue_times,
    kind=st.sampled_from(["pad_signature", "std_alphabet", "trailing_bits_sig", "trailing_bits_payload"]),
    variant=st.integers(min_value=1, max_value=15),
    pad_count=st.integers(min_value=1, max_value=3),
)
def test_property_non_canonical_base64url_is_rejected(
    signing_secret, user_id, now_ms, kind, variant, pad_count
):
    """Feature: user-auth-admin, Property 2 - padded, non-url-safe, or non-canonical encodings are rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    token, _exp = auth_crypto.sign_token(user_id, now_ms)
    payload_segment, signature_segment = token.split(".")

    if kind == "pad_signature":
        # Padding is not part of the canonical form.
        candidate = f"{payload_segment}.{signature_segment}{'=' * pad_count}"
    elif kind == "std_alphabet":
        # Standard-alphabet characters in place of the url-safe ones.
        candidate = f"{payload_segment}.{signature_segment[:-1]}+"
    elif kind == "trailing_bits_sig":
        # The last character of a 43-character signature carries 2 unused bits.
        last = _B64URL_ALPHABET.index(signature_segment[-1])
        changed = (last & ~0x3) | ((last & 0x3) ^ ((variant & 0x3) or 1))
        candidate = f"{payload_segment}.{signature_segment[:-1]}{_B64URL_ALPHABET[changed]}"
    else:
        # Correctly signed, but the payload segment has non-zero unused trailing bits.
        raw = _json_bytes(_payload(user_id, now_ms, now_ms + _TTL))
        while len(raw) % 3 == 0:
            raw += b" "  # trailing whitespace keeps the JSON valid
        segment = _b64url(raw)
        last = _B64URL_ALPHABET.index(segment[-1])
        unused = 4 if len(raw) % 3 == 1 else 2
        mask = (1 << unused) - 1
        assume((last & mask) == 0)
        changed = (last & ~mask) | ((variant & mask) or 1)
        candidate = _sign_segments(signing_secret, segment[:-1] + _B64URL_ALPHABET[changed])

    assert_rejected(candidate, now_ms)


@PBT_SETTINGS
@given(
    user_id=user_ids,
    now_ms=issue_times,
    extra=st.integers(min_value=1, max_value=2000),
    filler=st.sampled_from(["A", "_", "-", "0"]),
    where=st.sampled_from(["signature", "payload"]),
)
def test_property_over_long_token_is_rejected(signing_secret, user_id, now_ms, extra, filler, where):
    """Feature: user-auth-admin, Property 2 - a token longer than 512 characters is rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    token, _exp = auth_crypto.sign_token(user_id, now_ms)
    payload_segment, signature_segment = token.split(".")
    fill = filler * (auth_crypto.TOKEN_MAX_LEN - len(token) + extra)
    if where == "signature":
        candidate = f"{payload_segment}.{signature_segment}{fill}"
    else:
        candidate = f"{payload_segment}{fill}.{signature_segment}"
    assert len(candidate) > auth_crypto.TOKEN_MAX_LEN

    assert_rejected(candidate, now_ms)


@PBT_SETTINGS
@given(
    token=st.one_of(
        st.text(max_size=700),
        st.text(alphabet=_B64URL_ALPHABET + ".=+/ ", max_size=700),
        st.builds(
            lambda a, b: f"{a}.{b}",
            st.text(alphabet=_B64URL_ALPHABET, max_size=120),
            st.text(alphabet=_B64URL_ALPHABET, max_size=120),
        ),
        st.none(),
        st.integers(),
        st.binary(max_size=64),
        st.lists(st.text(max_size=5), max_size=3),
    ),
    now_ms=issue_times,
)
def test_property_arbitrary_input_is_rejected(signing_secret, token, now_ms):
    """Feature: user-auth-admin, Property 2 - an arbitrary string or non-string is rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    assert_rejected(token, now_ms)


def _payload_variants(uid, iat):
    """Correctly signed payloads that break one field rule each."""
    exp = iat + _TTL
    return [
        _json_bytes(_payload(uid, iat, exp, extra=1)),  # additional key
        _json_bytes({"v": 1, "uid": uid, "iat": iat}),  # missing exp
        _json_bytes(_payload(uid, iat, exp, v=2)),  # wrong version
        _json_bytes(_payload(uid, iat, exp, v=True)),  # boolean version
        _json_bytes(_payload(uid, iat, exp, uid="u_" + "G" * 16)),  # bad userId
        _json_bytes(_payload(uid, iat, exp, uid=123)),  # non-string userId
        _json_bytes(_payload(uid, True, exp)),  # boolean iat
        _json_bytes(_payload(uid, float(iat), exp)),  # non-integer iat
        _json_bytes(_payload(uid, iat, iat + _TTL + 1)),  # lifetime above 8 hours
        _json_bytes(_payload(uid, iat, iat)),  # zero lifetime
        _json_bytes(_payload(uid, iat, iat - 1)),  # exp before iat
        _json_bytes([1, 2, 3]),  # not an object
        b"not json at all",  # not JSON
        b"\xff\xfe\x00",  # not UTF-8
    ]


@PBT_SETTINGS
@given(
    user_id=user_ids,
    now_ms=issue_times,
    index=st.integers(min_value=0, max_value=13),
)
def test_property_signed_token_with_invalid_payload_is_rejected(
    signing_secret, user_id, now_ms, index
):
    """Feature: user-auth-admin, Property 2 - a correctly signed token with an invalid payload is rejected.

    Validates: Requirements 2.2, 4.3, 4.4
    """
    variants = _payload_variants(user_id, now_ms)
    token = _hand_token(signing_secret, variants[index])

    assert_rejected(token, now_ms)

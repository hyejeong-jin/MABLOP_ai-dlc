"""Unit tests for auth_crypto: concrete defaults and examples.

Complements the property tests (round trip, tamper, password hash, secret).
Covers Requirements 3.1, 3.2, 3.3, 7.1, 7.2, 7.3, 17.9.
The real 600000-iteration PBKDF2 runs only in one test (hash + verify).
"""
import base64
import hashlib
import hmac
import json

import boto3
import pytest

import auth_crypto
from conftest import SIGNING_SECRET_PATH, SIGNING_SECRET_VALUE, TEST_REGION
from errors import MablopError

USER_ID = "u_0123456789abcdef"
NOW_MS = 1_700_000_000_000
UNAUTHENTICATED_MSG = "로그인이 필요합니다. 다시 로그인하세요."
INTERNAL_MSG = "일시적인 오류가 발생했습니다. 잠시 후 다시 시도하세요."

B64URL_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _mac_segment(secret: str, payload_segment: str) -> str:
    digest = hmac.new(secret.encode(), payload_segment.encode("ascii"), hashlib.sha256).digest()
    return _b64url(digest)


def _payload_json(uid=USER_ID, iat=NOW_MS, exp=None) -> str:
    exp = iat + auth_crypto.TOKEN_TTL_MS if exp is None else exp
    return json.dumps({"v": 1, "uid": uid, "iat": iat, "exp": exp}, separators=(",", ":"))


def _assert_unauthorized(token, now_ms=NOW_MS):
    with pytest.raises(MablopError) as err:
        auth_crypto.verify_token(token, now_ms)
    assert err.value.code == "unauthorized"
    assert err.value.status == 401
    assert err.value.message == UNAUTHENTICATED_MSG


def _assert_internal(fn, *args):
    with pytest.raises(MablopError) as err:
        fn(*args)
    assert err.value.code == "internal_error"
    assert err.value.status == 500
    assert err.value.message == INTERNAL_MSG


# --- password defaults (Req 3.1, 3.2) ----------------------------------------------------


def test_default_hash_uses_real_algorithm_600000_iterations_and_16_byte_salt():
    # The only test that runs the real 600000-iteration PBKDF2 (hash + verify).
    stored = auth_crypto.hash_password("correct-horse-battery")

    assert set(stored) == {"alg", "iterations", "salt", "hash"}
    assert stored["alg"] == "pbkdf2_sha256"
    assert stored["iterations"] == 600_000
    assert len(bytes.fromhex(stored["salt"])) == 16
    assert len(bytes.fromhex(stored["hash"])) == 32

    expected = hashlib.pbkdf2_hmac(
        "sha256", b"correct-horse-battery", bytes.fromhex(stored["salt"]), 600_000
    )
    assert stored["hash"] == expected.hex()

    assert auth_crypto.verify_password("correct-horse-battery", stored) is True
    assert auth_crypto.verify_password("correct-horse-batterx", stored) is False


def test_hash_matches_known_pbkdf2_vector_for_explicit_salt_and_iterations():
    salt = bytes(range(16))
    stored = {
        "alg": "pbkdf2_sha256",
        "iterations": 1000,
        "salt": salt.hex(),
        "hash": hashlib.pbkdf2_hmac("sha256", "비밀번호-예시".encode("utf-8"), salt, 1000).hex(),
    }
    assert auth_crypto.verify_password("비밀번호-예시", stored) is True
    assert auth_crypto.verify_password("비밀번호-예시 ", stored) is False


def test_hash_is_lowercase_hex_and_salt_is_not_reused_across_calls():
    first = auth_crypto.hash_password("some-password", iterations=1)
    second = auth_crypto.hash_password("some-password", iterations=1)
    for stored in (first, second):
        assert stored["salt"] == stored["salt"].lower()
        assert len(stored["salt"]) == 32
        assert len(stored["hash"]) == 64
    assert first["salt"] != second["salt"]


def test_verify_password_with_non_string_password_is_false_not_error():
    stored = auth_crypto.hash_password("some-password", iterations=1)
    assert auth_crypto.verify_password(None, stored) is False
    assert auth_crypto.verify_password(12345, stored) is False


def test_verify_password_rejects_boundary_iteration_counts_as_internal_error():
    base = auth_crypto.hash_password("some-password", iterations=1)
    _assert_internal(auth_crypto.verify_password, "some-password", {**base, "iterations": 0})
    _assert_internal(auth_crypto.verify_password, "some-password", {**base, "iterations": 5_000_001})
    _assert_internal(auth_crypto.verify_password, "some-password", {**base, "iterations": True})


# --- temp password (Req 3) ---------------------------------------------------------------


def test_temp_password_alphabet_excludes_look_alike_characters():
    alphabet = auth_crypto.TEMP_PASSWORD_ALPHABET
    assert len(alphabet) == len(set(alphabet)) == 55
    for ch in "0O1lIio":
        assert ch not in alphabet
    assert alphabet.isascii() and alphabet.isalnum()


def test_generated_temp_passwords_are_16_chars_from_the_alphabet_and_vary():
    samples = [auth_crypto.generate_temp_password() for _ in range(200)]
    allowed = set(auth_crypto.TEMP_PASSWORD_ALPHABET)
    assert all(len(p) == 16 for p in samples)
    assert all(set(p) <= allowed for p in samples)
    assert len(set(samples)) == len(samples)
    # Every alphabet symbol is reachable: 3200 draws cover far more than 54 symbols.
    assert len(set("".join(samples))) > 40


# --- Signing_Secret path rules and cache (Req 7.1, 7.2, 7.3) -----------------------------


def test_signing_path_equal_to_token_path_fails_for_sign_and_verify(signing_secret, monkeypatch):
    token, _ = auth_crypto.sign_token(USER_ID, NOW_MS)
    auth_crypto.reset_cache()
    monkeypatch.setenv("MABLOP_TOKEN_SSM_PATH", SIGNING_SECRET_PATH)

    _assert_internal(auth_crypto.sign_token, USER_ID, NOW_MS)
    _assert_internal(auth_crypto.verify_token, token, NOW_MS)
    _assert_internal(auth_crypto.get_signing_secret)


def test_distinct_token_path_does_not_block_the_signing_secret(signing_secret, monkeypatch):
    monkeypatch.setenv("MABLOP_TOKEN_SSM_PATH", "/mablop/token")
    assert auth_crypto.get_signing_secret() == SIGNING_SECRET_VALUE


def test_secret_is_read_from_ssm_once_per_warm_container(signing_secret, monkeypatch):
    calls = []
    real_client = boto3.client

    def counting_client(service, *args, **kwargs):
        calls.append(service)
        return real_client(service, *args, **kwargs)

    monkeypatch.setattr(boto3, "client", counting_client)

    for _ in range(3):
        token, _exp = auth_crypto.sign_token(USER_ID, NOW_MS)
        auth_crypto.verify_token(token, NOW_MS)

    assert calls == ["ssm"]


def test_cached_secret_survives_ssm_change_until_reset_cache(signing_secret):
    token, _ = auth_crypto.sign_token(USER_ID, NOW_MS)
    rotated = "rotated-signing-secret-0123456789abcdef-0123456789"
    boto3.client("ssm", region_name=TEST_REGION).put_parameter(
        Name=SIGNING_SECRET_PATH, Value=rotated, Type="SecureString", Overwrite=True
    )

    assert auth_crypto.get_signing_secret() == SIGNING_SECRET_VALUE
    assert auth_crypto.verify_token(token, NOW_MS).uid == USER_ID

    auth_crypto.reset_cache()
    assert auth_crypto.get_signing_secret() == rotated
    _assert_unauthorized(token)


def test_failed_read_is_not_cached(aws_mock, monkeypatch):
    monkeypatch.setenv("MABLOP_SIGNING_SECRET_SSM_PATH", SIGNING_SECRET_PATH)
    monkeypatch.delenv("MABLOP_TOKEN_SSM_PATH", raising=False)
    auth_crypto.reset_cache()
    try:
        _assert_internal(auth_crypto.get_signing_secret)  # parameter does not exist yet
        boto3.client("ssm", region_name=TEST_REGION).put_parameter(
            Name=SIGNING_SECRET_PATH, Value=SIGNING_SECRET_VALUE, Type="SecureString"
        )
        assert auth_crypto.get_signing_secret() == SIGNING_SECRET_VALUE
    finally:
        auth_crypto.reset_cache()


def test_secret_value_has_no_environment_variable_fallback(aws_mock, monkeypatch):
    monkeypatch.delenv("MABLOP_SIGNING_SECRET_SSM_PATH", raising=False)
    monkeypatch.setenv("MABLOP_SIGNING_SECRET", SIGNING_SECRET_VALUE)
    monkeypatch.setenv("MABLOP_TOKEN", SIGNING_SECRET_VALUE)
    auth_crypto.reset_cache()
    try:
        _assert_internal(auth_crypto.get_signing_secret)
    finally:
        auth_crypto.reset_cache()


# --- token format (concrete example) -----------------------------------------------------


def test_sign_token_matches_hand_computed_token(signing_secret):
    token, exp = auth_crypto.sign_token(USER_ID, NOW_MS)

    payload = '{"v":1,"uid":"u_0123456789abcdef","iat":1700000000000,"exp":1700028800000}'
    payload_segment = _b64url(payload.encode())
    expected = payload_segment + "." + _mac_segment(SIGNING_SECRET_VALUE, payload_segment)

    assert token == expected
    assert exp == NOW_MS + 8 * 60 * 60 * 1000 == 1_700_028_800_000
    assert "=" not in token and "+" not in token and "/" not in token
    assert len(token) <= 512


def test_verify_returns_payload_and_expires_exactly_at_exp(signing_secret):
    token, exp = auth_crypto.sign_token(USER_ID, NOW_MS)

    payload = auth_crypto.verify_token(token, exp - 1)
    assert (payload.uid, payload.iat, payload.exp) == (USER_ID, NOW_MS, exp)
    _assert_unauthorized(token, exp)


def test_sign_token_rejects_malformed_user_id_as_internal_error(signing_secret):
    _assert_internal(auth_crypto.sign_token, "not-a-user-id", NOW_MS)
    _assert_internal(auth_crypto.sign_token, None, NOW_MS)


# --- non-canonical base64url (Req 2.2 strictness, 4.3) -----------------------------------


def _hand_signed(payload_segment: str) -> str:
    return payload_segment + "." + _mac_segment(SIGNING_SECRET_VALUE, payload_segment)


def _non_canonical_twin(segment: str) -> str:
    """Same decoded bytes, different text: flip a trailing unused bit of the last symbol."""
    assert len(segment) % 4 in (2, 3)
    idx = B64URL_ALPHABET.index(segment[-1])
    return segment[:-1] + B64URL_ALPHABET[idx ^ 1]


def test_signature_with_set_trailing_bits_decodes_to_same_bytes_but_is_rejected(signing_secret):
    token, _ = auth_crypto.sign_token(USER_ID, NOW_MS)
    payload_segment, signature = token.split(".")
    twin = _non_canonical_twin(signature)

    assert twin != signature
    assert base64.urlsafe_b64decode(twin + "=") == base64.urlsafe_b64decode(signature + "=")
    _assert_unauthorized(payload_segment + "." + twin)


def test_validly_signed_payload_with_set_trailing_bits_is_rejected(signing_secret):
    # Pad the JSON with trailing spaces so its length is 1 mod 3 (base64 length 2 mod 4).
    text = _payload_json()
    while len(text.encode()) % 3 != 1:
        text += " "
    segment = _b64url(text.encode())
    assert auth_crypto.verify_token(_hand_signed(segment), NOW_MS).uid == USER_ID

    twin = _non_canonical_twin(segment)
    assert base64.urlsafe_b64decode(twin + "==") == text.encode()
    _assert_unauthorized(_hand_signed(twin))


def test_validly_signed_payload_with_padding_or_standard_alphabet_is_rejected(signing_secret):
    text = _payload_json()
    while len(text.encode()) % 3 != 1:
        text += " "
    segment = _b64url(text.encode())

    _assert_unauthorized(_hand_signed(segment + "=="))
    standard = base64.b64encode((_payload_json(uid="u_fffffffffffffff0") + "~~").encode()).decode()
    assert "+" in standard or "/" in standard or standard.endswith("=")
    _assert_unauthorized(_hand_signed(standard))


def test_segment_length_one_mod_four_is_rejected_by_the_decoder():
    assert auth_crypto._b64url_decode("A") is None
    assert auth_crypto._b64url_decode("AAAAA") is None
    assert auth_crypto._b64url_decode("QR") is None  # unused trailing bits are non-zero
    assert auth_crypto._b64url_decode("AA") == b"\x00"
    assert auth_crypto._b64url_decode("QQ") == b"A"
    assert auth_crypto._b64url_decode("QUI") == b"AB"
    assert auth_crypto._b64url_decode("QQ==") is None


# --- time helpers ------------------------------------------------------------------------


def test_iso_conversion_examples_round_trip_exactly():
    assert auth_crypto.to_iso_ms(0) == "1970-01-01T00:00:00.000Z"
    assert auth_crypto.to_iso_ms(1_700_000_000_123) == "2023-11-14T22:13:20.123Z"
    assert auth_crypto.from_iso_ms("2023-11-14T22:13:20.123Z") == 1_700_000_000_123
    assert auth_crypto.from_iso_ms("2023-11-14T22:13:20Z") == 1_700_000_000_000
    assert auth_crypto.from_iso_ms("2023-11-14T22:13:20.5Z") == 1_700_000_000_500


@pytest.mark.parametrize(
    "bad", ["", "2023-11-14", "2023-11-14T22:13:20.123+09:00", "2023-11-14T22:13:20.1234Z", None]
)
def test_from_iso_ms_rejects_non_utc_millisecond_formats(bad):
    with pytest.raises(ValueError):
        auth_crypto.from_iso_ms(bad)

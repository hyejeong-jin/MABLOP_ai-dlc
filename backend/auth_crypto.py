"""Password hashing, Signing_Secret loading, and Session_Token sign/verify.

Design: user-auth-admin "auth_crypto.py" (D2-D5). Stdlib only (hashlib, hmac,
secrets, base64, json) plus boto3 for the SSM read, written in the same style
as access_controller._secret.

Token format (D2): base64url(payload_json) + "." + base64url(HMAC-SHA256(secret,
base64url(payload_json))), no padding. Payload keys: v (always 1), uid, iat,
exp; iat and exp are integer epoch milliseconds.
"""
import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import NamedTuple

import auth_messages
import s3store
from errors import MablopError

PBKDF2_ALG = "pbkdf2_sha256"
PBKDF2_ITERATIONS = 600_000
SALT_BYTES = 16
MIN_STORED_ITERATIONS = 1
MAX_STORED_ITERATIONS = 5_000_000

TOKEN_TTL_MS = 8 * 60 * 60 * 1000
TOKEN_MAX_LEN = 512
TOKEN_VERSION = 1
CLOCK_SKEW_MS = 60_000
SIGNING_SECRET_MIN_LEN = 32

# No look-alike characters (0/O, 1/l/I). 54 symbols ^ 16 is about 92 bits.
TEMP_PASSWORD_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
TEMP_PASSWORD_LEN = 16

_USER_ID_RE = re.compile(s3store.USER_ID_PATTERN)
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_TOKEN_KEYS = {"v", "uid", "iat", "exp"}
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_ISO_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,3}))?Z$"
)

# Fixed salt for burn_verify; its value is irrelevant, only the work matters.
_BURN_SALT = b"\x00" * SALT_BYTES

# Signing_Secret cached for the lifetime of a warm container (Req 7.2).
_cached_secret = None


class TokenPayload(NamedTuple):
    """Verified Session_Token payload."""

    uid: str
    iat: int
    exp: int


# --- Passwords ---------------------------------------------------------------


def _pw_bytes(password: str) -> bytes:
    """UTF-8 bytes of a password; lone surrogates from JSON do not raise."""
    return password.encode("utf-8", "surrogatepass")


def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> dict:
    """PBKDF2-HMAC-SHA256 with a fresh 16-byte salt (Req 3.1).

    `iterations` exists so tests can use small counts; production callers
    never pass it.
    """
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", _pw_bytes(password), salt, iterations)
    return {
        "alg": PBKDF2_ALG,
        "iterations": iterations,
        "salt": salt.hex(),
        "hash": digest.hex(),
    }


def _internal() -> MablopError:
    return MablopError("internal_error", auth_messages.INTERNAL)


def verify_password(password: str, stored: dict) -> bool:
    """Check a password against a stored hash object (Req 3.2, 3.3).

    Uses the stored `alg` and `iterations`. An unknown algorithm, an iteration
    count outside 1..5,000,000, or a malformed object is `internal_error`.
    """
    if not isinstance(stored, dict) or stored.get("alg") != PBKDF2_ALG:
        raise _internal()
    iterations = stored.get("iterations")
    if (
        isinstance(iterations, bool)
        or not isinstance(iterations, int)
        or not MIN_STORED_ITERATIONS <= iterations <= MAX_STORED_ITERATIONS
    ):
        raise _internal()
    try:
        salt = bytes.fromhex(stored["salt"])
        expected = bytes.fromhex(stored["hash"])
    except (KeyError, TypeError, ValueError):
        raise _internal()
    if not isinstance(password, str):
        # Still burn the work so the failure path costs the same.
        hashlib.pbkdf2_hmac("sha256", b"", salt, iterations)
        return False
    actual = hashlib.pbkdf2_hmac("sha256", _pw_bytes(password), salt, iterations)
    return hmac.compare_digest(actual, expected)


def burn_verify(password) -> None:
    """One PBKDF2 at the current iteration count; result discarded (D5, Req 2.5)."""
    data = _pw_bytes(password) if isinstance(password, str) else b""
    hashlib.pbkdf2_hmac("sha256", data, _BURN_SALT, PBKDF2_ITERATIONS)


def generate_temp_password() -> str:
    """16 random characters from an alphabet without look-alike characters."""
    return "".join(secrets.choice(TEMP_PASSWORD_ALPHABET) for _ in range(TEMP_PASSWORD_LEN))


# --- Signing_Secret ----------------------------------------------------------


def reset_cache() -> None:
    """Forget the cached Signing_Secret (tests, secret rotation)."""
    global _cached_secret
    _cached_secret = None


def get_signing_secret() -> str:
    """Read the Signing_Secret from SSM once per warm container (Req 7.1-7.4).

    The path comes only from MABLOP_SIGNING_SECRET_SSM_PATH; there is no
    environment-variable fallback for the value. A missing path, a path equal
    to MABLOP_TOKEN_SSM_PATH, a failed read, or a value shorter than 32
    characters raises internal_error and is not cached.
    """
    global _cached_secret
    if _cached_secret is not None:
        return _cached_secret
    path = os.environ.get("MABLOP_SIGNING_SECRET_SSM_PATH", "")
    if not path or path == os.environ.get("MABLOP_TOKEN_SSM_PATH", ""):
        raise _internal()
    try:
        import boto3

        region = os.environ.get("MABLOP_BEDROCK_REGION", "us-east-1")
        value = boto3.client("ssm", region_name=region).get_parameter(
            Name=path, WithDecryption=True
        )["Parameter"]["Value"]
    except Exception:
        raise _internal()
    if not isinstance(value, str) or len(value) < SIGNING_SECRET_MIN_LEN:
        raise _internal()
    _cached_secret = value
    return value


# --- Base64url ---------------------------------------------------------------


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64url_decode(segment: str):
    """Strict base64url decode; returns None for anything non-canonical.

    Rejects characters outside the alphabet, padding, and encodings whose
    unused trailing bits are set (decode + re-encode must reproduce the input).
    """
    if not isinstance(segment, str) or not _B64URL_RE.match(segment):
        return None
    if len(segment) % 4 == 1:
        return None
    try:
        raw = base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))
    except (binascii.Error, ValueError):
        return None
    if _b64url_encode(raw) != segment:
        return None
    return raw


# --- Tokens ------------------------------------------------------------------


def _unauthorized() -> MablopError:
    return MablopError("unauthorized", auth_messages.UNAUTHENTICATED)


def _mac(secret: str, payload_segment: str) -> bytes:
    return hmac.new(
        secret.encode("utf-8"), payload_segment.encode("ascii"), hashlib.sha256
    ).digest()


def sign_token(user_id: str, now_ms: int):
    """Return (token, exp_ms) for user_id issued at now_ms (D2)."""
    if not isinstance(user_id, str) or not _USER_ID_RE.match(user_id):
        raise _internal()
    secret = get_signing_secret()
    exp = now_ms + TOKEN_TTL_MS
    payload = json.dumps(
        {"v": TOKEN_VERSION, "uid": user_id, "iat": now_ms, "exp": exp},
        separators=(",", ":"),
    )
    payload_segment = _b64url_encode(payload.encode("utf-8"))
    signature = _b64url_encode(_mac(secret, payload_segment))
    return f"{payload_segment}.{signature}", exp


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def verify_token(token, now_ms: int) -> TokenPayload:
    """Verify a Session_Token (D3). Steps 1-6 fail with the same `unauthorized`.

    Status and credentialsChangedAt checks need the User_Record and are done
    by the Access_Controller after this returns.
    """
    # 1. Shape and length.
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= TOKEN_MAX_LEN
        or not token.isascii()
        or token.count(".") != 1
    ):
        raise _unauthorized()
    payload_segment, signature_segment = token.split(".")
    if not payload_segment or not signature_segment:
        raise _unauthorized()

    # 2. Secret (a failure here is internal_error, Req 7.3).
    secret = get_signing_secret()

    # 3. Strict signature decode.
    signature = _b64url_decode(signature_segment)
    if signature is None:
        raise _unauthorized()

    # 4. Constant-time signature comparison.
    if not hmac.compare_digest(_mac(secret, payload_segment), signature):
        raise _unauthorized()

    # 5. Payload decode and field checks (only after the signature is valid).
    raw = _b64url_decode(payload_segment)
    if raw is None:
        raise _unauthorized()
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise _unauthorized()
    if not isinstance(payload, dict) or set(payload) != _TOKEN_KEYS:
        raise _unauthorized()
    uid, iat, exp = payload["uid"], payload["iat"], payload["exp"]
    if (
        not _is_int(payload["v"])
        or payload["v"] != TOKEN_VERSION
        or not isinstance(uid, str)
        or not _USER_ID_RE.match(uid)
        or not _is_int(iat)
        or not _is_int(exp)
    ):
        raise _unauthorized()

    # 6. Lifetime window.
    if not 0 < exp - iat <= TOKEN_TTL_MS or iat > now_ms + CLOCK_SKEW_MS or exp <= now_ms:
        raise _unauthorized()
    return TokenPayload(uid=uid, iat=iat, exp=exp)


# --- Time --------------------------------------------------------------------


def to_iso_ms(ms: int) -> str:
    """Epoch milliseconds -> 'YYYY-MM-DDTHH:MM:SS.mmmZ' using integer arithmetic."""
    dt = _EPOCH + timedelta(milliseconds=ms)
    return dt.strftime("%Y-%m-%dT%H:%M:%S") + f".{dt.microsecond // 1000:03d}Z"


def from_iso_ms(value: str) -> int:
    """ISO-8601 UTC (millisecond precision) -> epoch milliseconds, exactly.

    Raises ValueError for anything that is not 'YYYY-MM-DDTHH:MM:SS[.f{1,3}]Z'.
    """
    match = _ISO_RE.match(value) if isinstance(value, str) else None
    if not match:
        raise ValueError("invalid ISO-8601 UTC timestamp")
    year, month, day, hour, minute, second = (int(g) for g in match.groups()[:6])
    millis = int((match.group(7) or "0").ljust(3, "0"))
    dt = datetime(year, month, day, hour, minute, second, tzinfo=timezone.utc)
    return (dt - _EPOCH) // timedelta(milliseconds=1) + millis

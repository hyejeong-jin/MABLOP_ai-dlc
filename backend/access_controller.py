"""Access_Controller (Req 5; user-auth-admin Req 4, 20): body-length, rate, authentication, role, Grant.

Stdlib-only (hmac/os/time/json) over s3store. Fixed-window rate limit sized
for 2-3 users: one global window counter, warm in module memory, persisted to
metadata/ratelimit.json. YAGNI: no token bucket, no Redis.
"""
import hmac
import json
import os
from dataclasses import dataclass

import auth_messages
from errors import MablopError

RATELIMIT_KEY = "metadata/ratelimit.json"

# Warm invocation fast-path window state: {"start": float, "count": int}.
_window = None

# Cached pre-shared token resolved from SSM (per warm container).
_cached_token = None


def _env(name: str, default: str) -> str:
    """Env var or default (secret/caps never hard-coded, Req 9.3)."""
    return os.environ.get(name, default)


def _secret() -> str:
    """Resolve the Pre_Shared_Token.

    Prefer the MABLOP_TOKEN env var (used by tests). Otherwise read it once
    from SSM at MABLOP_TOKEN_SSM_PATH (CloudFormation cannot inject an
    ssm-secure value into a Lambda env var, so the Lambda fetches it at
    runtime). Empty result blocks all requests (Req 9.3).
    """
    global _cached_token
    direct = _env("MABLOP_TOKEN", "")
    if direct:
        return direct
    if _cached_token is not None:
        return _cached_token
    path = _env("MABLOP_TOKEN_SSM_PATH", "")
    if not path:
        return ""
    try:
        import boto3

        region = _env("MABLOP_BEDROCK_REGION", "us-east-1")
        value = boto3.client("ssm", region_name=region).get_parameter(
            Name=path, WithDecryption=True
        )["Parameter"]["Value"]
    except Exception:
        return ""
    _cached_token = value
    return value


def _max_rate() -> int:
    """Allowed requests per window."""
    return int(_env("MABLOP_MAX_RATE", "60"))


def _window_secs() -> float:
    """Fixed-window length in seconds."""
    return float(_env("MABLOP_RATE_WINDOW", "60"))


def _max_body_bytes() -> int:
    """Request body cap in bytes."""
    return int(_env("MABLOP_MAX_BODY_BYTES", str(1 << 20)))


def _header(headers: dict, name: str):
    """Case-insensitive header lookup."""
    if not headers:
        return None
    low = name.lower()
    for k, v in headers.items():
        if k.lower() == low:
            return v
    return None


def check_token(headers: dict, secret: str = None) -> bool:
    """Constant-time compare X-Mablop-Token vs secret. 401 on miss (5.1, 5.2)."""
    if secret is None:
        secret = _secret()
    got = _header(headers, "X-Mablop-Token")
    # Encode to bytes: hmac.compare_digest only supports ASCII str operands,
    # so a non-ASCII token (secret or candidate) would otherwise raise TypeError.
    if (
        not secret
        or got is None
        or not hmac.compare_digest(str(got).encode("utf-8"), str(secret).encode("utf-8"))
    ):
        raise MablopError("unauthorized")
    return True


def _load_window():
    """Warm memory first; fall back to persisted ratelimit.json; None if neither."""
    global _window
    if _window is not None:
        return _window
    try:
        import s3store

        _window = s3store.get_json(RATELIMIT_KEY)
    except Exception:
        _window = None
    return _window


def _save_window(state) -> None:
    """Persist window state best-effort (warm memory already updated)."""
    try:
        import s3store

        s3store.put_json(RATELIMIT_KEY, state)
    except Exception:
        pass


def check_rate(now: float, max_rate: int = None, window_secs: float = None) -> bool:
    """Fixed-window counter. 429 over rate (5.3). Rolls window when expired."""
    global _window
    if max_rate is None:
        max_rate = _max_rate()
    if window_secs is None:
        window_secs = _window_secs()
    state = _load_window()
    if not state or now - state.get("start", 0) >= window_secs:
        state = {"start": now, "count": 0}
    state["count"] += 1
    _window = state
    _save_window(state)
    if state["count"] > max_rate:
        raise MablopError("rate_limited")
    return True


def check_body_length(body_len: int, cap: int = None) -> bool:
    """Reject oversized body before parse. 413 (5.4)."""
    if cap is None:
        cap = _max_body_bytes()
    if body_len is not None and body_len > cap:
        raise MablopError("body_too_large")
    return True


def _body_len(headers: dict, body) -> int:
    """Prefer Content-Length header; else decoded body length (bytes)."""
    cl = _header(headers, "Content-Length")
    if cl is not None:
        try:
            return int(cl)
        except (TypeError, ValueError):
            pass
    if body is None:
        return 0
    if isinstance(body, str):
        return len(body.encode("utf-8"))
    return len(body)



# Actions served without a Session_Token (Req 4.1). Exactly these three.
AUTH_EXEMPT_ACTIONS = frozenset({"login", "register", "bootstrap-admin"})

# Actions only the administrator role may call (Req 4.8, 19.16).
ADMIN_ONLY_ACTIONS = frozenset(
    {
        "migrate-legacy-data",
        "admin-list-users",
        "admin-approve-user",
        "admin-reject-user",
        "admin-disable-user",
        "admin-enable-user",
        "admin-delete-user",
        "admin-reset-password",
        "admin-grant-list",
        "admin-grant-approve",
        "admin-grant-reject",
        "admin-grant-revoke",
    }
)

# Actions that call Bedrock (directly or through learn_style) (Req 20.1).
BEDROCK_ACTIONS = frozenset(
    {"learn-style", "analyze-images", "generate-draft", "rebuild-from-stored", "get-result"}
)


@dataclass(frozen=True)
class RequestContext:
    """Result of check_request: the action and, for authenticated calls, the user."""

    action: str
    user_id: str
    role: str
    user: dict


def _peek_action(body):
    """Tolerant JSON peek: the `action` value only when it is a string, else None."""
    try:
        if isinstance(body, (bytes, bytearray)):
            body = bytes(body).decode("utf-8")
        parsed = json.loads(body) if body else None
    except Exception:
        return None
    if isinstance(parsed, dict) and isinstance(parsed.get("action"), str):
        return parsed["action"]
    return None


def _unauthenticated() -> MablopError:
    """The single 401 used for every authentication failure (Req 4.4)."""
    return MablopError("unauthorized", auth_messages.UNAUTHENTICATED)


def _authenticate(headers: dict, now: float) -> RequestContext:
    """Verify the Session_Token and load the User_Record (one S3 read). Same 401 on any failure."""
    import auth_crypto
    import auth_store

    now_ms = int(now * 1000)
    payload = auth_crypto.verify_token(_header(headers, "X-Mablop-Token"), now_ms)
    user = auth_store.get_user(payload.uid)
    if user is None or user.get("status") != "active":
        raise _unauthenticated()
    try:
        cutoff = auth_crypto.from_iso_ms(user.get("credentialsChangedAt"))
    except Exception:
        raise _unauthenticated()
    if payload.iat < cutoff:
        raise _unauthenticated()
    return RequestContext(None, payload.uid, user.get("role"), user)


def check_request(headers: dict, body, now: float = None) -> RequestContext:
    """Body length, rate, then authenticate and gate by role. Raises MablopError on reject.

    Order (Req 4.2): body length, global rate, read `action`, authenticate.
    """
    import time

    if now is None:
        now = time.time()
    check_body_length(_body_len(headers, body))
    check_rate(now)
    action = _peek_action(body)

    if action in ("login", "register"):
        return RequestContext(action, None, None, None)

    try:
        if action == "bootstrap-admin":
            import auth_throttle

            auth_throttle.guard_global(now)
            try:
                check_token(headers)
            except MablopError:
                auth_throttle.record_failure(None, now)
                raise _unauthenticated()
            return RequestContext(action, None, None, None)

        ctx = _authenticate(headers, now)
        if action in ADMIN_ONLY_ACTIONS and ctx.role != "admin":
            raise MablopError("forbidden", auth_messages.ADMIN_REQUIRED)
        return RequestContext(action, ctx.user_id, ctx.role, ctx.user)
    except MablopError:
        raise
    except Exception:
        raise MablopError("internal_error", auth_messages.INTERNAL)


def requires_grant(action, body, role) -> bool:
    """Pure predicate: a member calling a Bedrock action (except get-result kind=draft) needs a Grant."""
    if role != "member" or action not in BEDROCK_ACTIONS:
        return False
    if action == "get-result" and isinstance(body, dict) and body.get("kind") == "draft":
        return False
    return True


def check_grant(ctx: RequestContext, body: dict, now: float = None) -> None:
    """Verify the body's grantId for the acting user; identical 403 for every failure cause (Req 20)."""
    import time

    if not requires_grant(ctx.action, body, ctx.role):
        return
    if now is None:
        now = time.time()
    grant_id = body.get("grantId") if isinstance(body, dict) else None
    import grant_service

    if not grant_service.verify(ctx.user_id, grant_id, int(now * 1000)):
        import auth_log

        auth_log.log("grant", "blocked", ctx.user_id)
        raise MablopError("forbidden", auth_messages.GRANT_REQUIRED)

"""Access_Controller (Req 5): token, rate, body-length checks. Runs first.

Stdlib-only (hmac/os/time/json) over s3store. Fixed-window rate limit sized
for 2-3 users: one global window counter, warm in module memory, persisted to
metadata/ratelimit.json. YAGNI: no token bucket, no Redis.
"""
import hmac
import os

from errors import MablopError

RATELIMIT_KEY = "metadata/ratelimit.json"

# Warm invocation fast-path window state: {"start": float, "count": int}.
_window = None


def _env(name: str, default: str) -> str:
    """Env var or default (secret/caps never hard-coded, Req 9.3)."""
    return os.environ.get(name, default)


def _secret() -> str:
    """Pre_Shared_Token from env (empty default blocks all, Req 9.3)."""
    return _env("MABLOP_TOKEN", "")


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


def check_request(headers: dict, body, now: float = None) -> bool:
    """Run body-length (before parse), token, then rate. Raises MablopError on reject."""
    import time

    if now is None:
        now = time.time()
    check_body_length(_body_len(headers, body))
    check_token(headers)
    check_rate(now)
    return True

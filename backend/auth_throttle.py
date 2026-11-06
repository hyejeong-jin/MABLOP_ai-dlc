"""Persisted Login_Throttle state and its pure rules (design: auth_throttle.py; Req 5.2-5.10).

State lives in one JSON object at users/_auth/throttle.json. The rule functions are pure over
(state, now); the wrappers below read or update that object through s3store. All times are epoch
seconds.
"""
import copy
import re
import time

import auth_messages
import s3store
from errors import MablopError

LOGIN_FAIL_LIMIT = 5
LOGIN_WINDOW_S = 600
LOGIN_BLOCK_S = 600
GLOBAL_FAIL_LIMIT = 30
GLOBAL_WINDOW_S = 600
GLOBAL_BLOCK_S = 600
REGISTER_LIMIT = 5
REGISTER_WINDOW_S = 3600
PENDING_CAP = 10
MAX_ENTRIES = 200

THROTTLE_KEY_RELATIVE = "throttle.json"
_LOGIN_ID_RE = re.compile(r"^[a-z0-9_-]{4,32}$")


def _empty_state() -> dict:
    """A fresh throttle state."""
    return {"v": 1, "login": {}, "globalFails": [], "globalBlockedUntil": 0, "registers": []}


def _key() -> str:
    """S3 key of the throttle state."""
    return s3store.auth_key(THROTTLE_KEY_RELATIVE)


def _valid_login_id(login_id) -> bool:
    """True when login_id is a string in the loginId format (only these get a per-loginId entry)."""
    return isinstance(login_id, str) and _LOGIN_ID_RE.fullmatch(login_id) is not None


def _normalize(state) -> dict:
    """Return a deep copy of state with every expected field present."""
    out = _empty_state()
    if isinstance(state, dict):
        if isinstance(state.get("login"), dict):
            out["login"] = copy.deepcopy(state["login"])
        if isinstance(state.get("globalFails"), list):
            out["globalFails"] = list(state["globalFails"])
        if isinstance(state.get("globalBlockedUntil"), (int, float)):
            out["globalBlockedUntil"] = state["globalBlockedUntil"]
        if isinstance(state.get("registers"), list):
            out["registers"] = list(state["registers"])
    return out


# --- pure rules -------------------------------------------------------------------------------

def prune(state: dict, now) -> dict:
    """Return a copy of state without expired entries and with at most MAX_ENTRIES loginId entries.

    Evicts the entries with the oldest `last` first when over the cap (Req 5.9).
    """
    out = _normalize(state)
    login = {}
    for login_id, entry in out["login"].items():
        if not isinstance(entry, dict):
            continue
        fails = [t for t in entry.get("fails", []) if now - t < LOGIN_WINDOW_S]
        blocked_until = entry.get("blockedUntil", 0)
        if not fails and blocked_until <= now:
            continue
        login[login_id] = {
            "fails": fails,
            "blockedUntil": blocked_until if blocked_until > now else 0,
            "last": entry.get("last", 0),
        }
    if len(login) > MAX_ENTRIES:
        keep = sorted(login, key=lambda k: login[k]["last"], reverse=True)[:MAX_ENTRIES]
        login = {k: login[k] for k in keep}
    out["login"] = login
    out["globalFails"] = [t for t in out["globalFails"] if now - t < GLOBAL_WINDOW_S]
    if out["globalBlockedUntil"] <= now:
        out["globalBlockedUntil"] = 0
    out["registers"] = [t for t in out["registers"] if now - t < REGISTER_WINDOW_S]
    return out


def is_global_blocked(state: dict, now) -> bool:
    """True while the global failure block is in force."""
    return now < state.get("globalBlockedUntil", 0)


def is_login_blocked(state: dict, login_id, now) -> bool:
    """True while the loginId (or the whole service) is blocked."""
    if is_global_blocked(state, now):
        return True
    if not _valid_login_id(login_id):
        return False
    entry = state.get("login", {}).get(login_id)
    return bool(entry) and now < entry.get("blockedUntil", 0)


def apply_failure(state: dict, login_id, now) -> dict:
    """Return the state after one failed attempt.

    The global list always records the failure. A per-loginId entry is created only for a valid
    loginId. A failure that arrives during a block does not extend that block.
    """
    out = prune(state, now)
    if not is_global_blocked(out, now):
        out["globalFails"].append(now)
        if len(out["globalFails"]) >= GLOBAL_FAIL_LIMIT:
            out["globalBlockedUntil"] = now + GLOBAL_BLOCK_S
    if _valid_login_id(login_id):
        entry = out["login"].get(login_id) or {"fails": [], "blockedUntil": 0, "last": now}
        if not now < entry["blockedUntil"]:
            entry["fails"].append(now)
            if len(entry["fails"]) >= LOGIN_FAIL_LIMIT:
                entry["blockedUntil"] = now + LOGIN_BLOCK_S
        entry["last"] = now
        out["login"][login_id] = entry
        out = prune(out, now)
    return out


def apply_login_reset(state: dict, login_id, now) -> dict:
    """Return the state without the loginId entry (success or administrator reset)."""
    out = prune(state, now)
    out["login"].pop(login_id, None)
    return out


def apply_register(state: dict, now) -> dict:
    """Return the state after admitting one registration; raise rate_limited when the window is full."""
    out = prune(state, now)
    if len(out["registers"]) >= REGISTER_LIMIT:
        raise MablopError("rate_limited", auth_messages.REGISTER_RATE)
    out["registers"].append(now)
    return out


# --- persistence wrappers ---------------------------------------------------------------------

def _now(now):
    """Epoch seconds; defaults to the current time."""
    return time.time() if now is None else now


def _read_state() -> dict:
    """Current stored state (empty when absent). Read only."""
    found = s3store.read_json_optional(_key())
    return _normalize(found[0] if found else None)


def _blocked_error() -> MablopError:
    return MablopError("rate_limited", auth_messages.LOGIN_BLOCKED)


def check_login(login_id, now=None) -> None:
    """Raise rate_limited (LOGIN_BLOCKED) when the loginId or the service is blocked. Read only."""
    now = _now(now)
    if is_login_blocked(_read_state(), login_id, now):
        raise _blocked_error()


def record_failure(login_id, now=None) -> None:
    """Record one failed login, change-password, or bootstrap attempt."""
    now = _now(now)
    s3store.update_json(
        _key(), lambda state: apply_failure(state, login_id, now), default=_empty_state()
    )


def _has_entry(login_id) -> bool:
    """True when the stored state has an entry for the loginId."""
    return _valid_login_id(login_id) and login_id in _read_state()["login"]


def record_success(login_id, now=None) -> None:
    """Remove the loginId entry after a successful login; write only when an entry exists (Req 5.5)."""
    now = _now(now)
    if not _has_entry(login_id):
        return
    s3store.update_json(
        _key(), lambda state: apply_login_reset(state, login_id, now), default=_empty_state()
    )


def reset_login(login_id, now=None) -> None:
    """Remove the loginId entry (used by admin-reset-password)."""
    record_success(login_id, now)


def guard_global(now=None) -> None:
    """Raise rate_limited (LOGIN_BLOCKED) while the global failure block is in force (bootstrap-admin gate)."""
    now = _now(now)
    if is_global_blocked(_read_state(), now):
        raise _blocked_error()


def admit_register(now=None) -> None:
    """Atomically check the registration window and record this request; raise rate_limited when full (Req 5.7)."""
    now = _now(now)
    s3store.update_json(_key(), lambda state: apply_register(state, now), default=_empty_state())

"""Bedrock usage Grants (design: grant_service.py; D17-D19; Req 19, 20.2, 20.3).

The six Grant actions plus `verify` for the Access_Controller hook. Every action has the signature
``fn(body, ctx, now=None)``: `now` is epoch seconds (default ``time.time()``) and `ctx` is the
RequestContext (only ``user_id`` is read). All Grants of one user live in
``users/{userId}/metadata/grants.json``. Every write goes through ``s3store.update_json`` and every
precondition runs inside the mutation, so a failed call leaves the object unchanged (D19).
The user id is always explicit (never read from user_scope) because administrator actions operate on
other users. The stored status never becomes "expired": `expired` is derived on read (D18).
"""
import re
import secrets
import time

import auth_crypto
import auth_log
import auth_messages
import auth_store
import s3store
from errors import MablopError

GRANT_ID_PATTERN = r"^g_[0-9a-f]{16}$"
EXPIRY_CHOICES = (7, 30, 90)
DEFAULT_EXPIRY_DAYS = 30
MAX_GRANTS = 20
PURPOSE_MAX = 100

STORE_REL = "metadata/grants.json"
_GRANT_ID_RE = re.compile(GRANT_ID_PATTERN)
_USER_ID_RE = re.compile(s3store.USER_ID_PATTERN)
_DAY_MS = 24 * 60 * 60 * 1000
_PUBLIC_FIELDS = ("id", "status", "purpose", "requestedAt", "decidedAt", "expiresAt")


def _default_store() -> dict:
    return {"v": 1, "grants": []}


def _now_ms(now) -> int:
    """Integer epoch milliseconds for an epoch-seconds value (default: the current time)."""
    seconds = time.time() if now is None else now
    return int(round(seconds * 1000))


def _body(body) -> dict:
    return body if isinstance(body, dict) else {}


def _store_key(user_id: str) -> str:
    return s3store.user_key(STORE_REL, user_id)


def _grants_of(store) -> list:
    """The grants list of a stored object; a malformed store is internal_error (never overwritten)."""
    if not isinstance(store, dict) or not isinstance(store.get("grants"), list):
        raise MablopError("internal_error")
    return store["grants"]


def _readable_grants(found) -> list:
    """Grant dicts of a read_json_optional result; a missing or malformed store gives an empty list."""
    if found is None:
        return []
    store = found[0]
    if not isinstance(store, dict) or not isinstance(store.get("grants"), list):
        return []
    return [g for g in store["grants"] if isinstance(g, dict)]


def _expires_ms(grant: dict):
    """expiresAt as epoch milliseconds, or None when absent or unparseable."""
    try:
        return auth_crypto.from_iso_ms(grant.get("expiresAt"))
    except Exception:
        return None


def is_expired(grant: dict, now_ms: int) -> bool:
    """D18: an approved Grant is expired when expiresAt <= now (an unreadable expiresAt counts as expired)."""
    if not isinstance(grant, dict) or grant.get("status") != "approved":
        return False
    expires = _expires_ms(grant)
    return expires is None or expires <= now_ms


def _public(grant: dict, now_ms: int) -> dict:
    """The documented Grant fields plus the derived `expired` flag."""
    out = {name: grant.get(name) for name in _PUBLIC_FIELDS}
    out["expired"] = is_expired(grant, now_ms)
    return out


def _sort_newest(items: list) -> list:
    """Newest requestedAt first, ties broken by id descending."""
    return sorted(
        items,
        key=lambda g: (str(g.get("requestedAt") or ""), str(g.get("id") or "")),
        reverse=True,
    )


def _check_purpose(body: dict):
    """Validated purpose: None for absent/null/blank, else the submitted string (Req 19.4)."""
    purpose = body.get("purpose")
    if purpose is None:
        return None
    if not isinstance(purpose, str) or len(purpose) > PURPOSE_MAX:
        raise MablopError("bad_request", auth_messages.PURPOSE_TOO_LONG)
    return purpose if purpose.strip() else None


def _resolve_target(body: dict) -> str:
    """userId of an existing User_Record, or 404 (Req 19.15)."""
    user_id = body.get("userId")
    if (
        not isinstance(user_id, str)
        or _USER_ID_RE.fullmatch(user_id) is None
        or auth_store.get_user(user_id) is None
    ):
        raise MablopError("not_found", auth_messages.NOT_FOUND_TARGET)
    return user_id


def _expires_in_days(body: dict) -> int:
    """expiresInDays: 30 when the key is absent, else exactly 7, 30, or 90 (Req 19.9, 19.10)."""
    if "expiresInDays" not in body:
        return DEFAULT_EXPIRY_DAYS
    days = body["expiresInDays"]
    if type(days) is not int or days not in EXPIRY_CHOICES:
        raise MablopError("bad_request", auth_messages.GRANT_EXPIRY_INVALID)
    return days


def _actor(ctx):
    return getattr(ctx, "user_id", None)


# --- member actions ---------------------------------------------------------


def request(body, ctx, now=None) -> dict:
    """grant-request: append a `requested` Grant to the acting user's store (Req 19.3-19.5, 19.17, 19.18)."""
    body = _body(body)
    purpose = _check_purpose(body)
    now_ms = _now_ms(now)
    user_id = ctx.user_id
    created = {}

    def _mutate(store):
        grants = list(_grants_of(store))
        if any(isinstance(g, dict) and g.get("status") == "requested" for g in grants):
            raise MablopError("bad_request", auth_messages.GRANT_ALREADY_REQUESTED)
        if len(grants) >= MAX_GRANTS:
            removable = [
                g
                for g in grants
                if isinstance(g, dict)
                and (g.get("status") in ("rejected", "revoked") or is_expired(g, now_ms))
            ]
            if not removable:
                raise MablopError("bad_request", auth_messages.GRANT_LIMIT)
            oldest = min(
                removable, key=lambda g: (str(g.get("requestedAt") or ""), str(g.get("id") or ""))
            )
            grants = [g for g in grants if g is not oldest]
        existing = {g.get("id") for g in grants if isinstance(g, dict)}
        grant_id = "g_" + secrets.token_hex(8)
        while grant_id in existing:
            grant_id = "g_" + secrets.token_hex(8)
        grant = {
            "id": grant_id,
            "status": "requested",
            "purpose": purpose,
            "requestedAt": auth_crypto.to_iso_ms(now_ms),
            "decidedAt": None,
            "expiresAt": None,
        }
        created["grant"] = grant
        return {**store, "grants": grants + [grant]}

    s3store.update_json(_store_key(user_id), _mutate, default=_default_store())
    auth_log.log("grant-request", "success", user_id)
    return {"grant": _public(created["grant"], now_ms)}


def list_mine(body, ctx, now=None) -> dict:
    """grant-list-mine: the acting user's Grants, newest first (Req 19.6)."""
    now_ms = _now_ms(now)
    found = s3store.read_json_optional(_store_key(ctx.user_id))
    grants = _sort_newest(_readable_grants(found))
    return {"grants": [_public(g, now_ms) for g in grants]}


# --- administrator actions --------------------------------------------------


def admin_list(body, ctx, now=None) -> dict:
    """admin-grant-list: every user's Grants, `requested` first, then newest first (Req 19.7)."""
    now_ms = _now_ms(now)
    rows = []
    for user in auth_store.list_users():
        user_id = user.get("userId")
        try:
            found = s3store.read_json_optional(_store_key(user_id))
        except MablopError:
            continue
        for grant in _readable_grants(found):
            row = _public(grant, now_ms)
            row.update({"userId": user_id, "loginId": user.get("loginId"), "name": user.get("name")})
            rows.append(row)
    rows = _sort_newest(rows)
    rows.sort(key=lambda g: g["status"] != "requested")  # stable: requested group first
    return {"grants": rows}


def _decide(body, ctx, now, required: str, required_message: str, apply, event: str) -> dict:
    """Shared flow of approve, reject, and revoke on the target user's store."""
    now_ms = _now_ms(now)
    result = {}

    def _mutate(store):
        grants = list(_grants_of(store))
        grant_id = body.get("grantId")
        index = next(
            (
                i
                for i, g in enumerate(grants)
                if isinstance(g, dict) and isinstance(grant_id, str) and g.get("id") == grant_id
            ),
            None,
        )
        if index is None:
            raise MablopError("not_found", auth_messages.GRANT_NOT_FOUND)
        if grants[index].get("status") != required:
            raise MablopError("bad_request", required_message)
        updated = apply(dict(grants[index]), now_ms)
        grants[index] = updated
        result["grant"] = updated
        return {**store, "grants": grants}

    return _run_decision(body, ctx, event, _mutate, result, now_ms)


def _run_decision(body, ctx, event, mutate, result, now_ms) -> dict:
    user_id = body["userId"]
    s3store.update_json(_store_key(user_id), mutate, default=_default_store())
    auth_log.log(event, "success", _actor(ctx))
    return {"userId": user_id, "grant": _public(result["grant"], now_ms)}


def admin_approve(body, ctx, now=None) -> dict:
    """admin-grant-approve: requested -> approved with decidedAt now and expiresAt now + N days (Req 19.8-19.10, 19.13-19.15)."""
    body = _body(body)
    _resolve_target(body)
    days = _expires_in_days(body)  # validated before the store is read

    def _apply(grant, now_ms):
        grant["status"] = "approved"
        grant["decidedAt"] = auth_crypto.to_iso_ms(now_ms)
        grant["expiresAt"] = auth_crypto.to_iso_ms(now_ms + days * _DAY_MS)
        return grant

    return _decide(
        body, ctx, now, "requested", auth_messages.GRANT_NEED_REQUESTED_APPROVE, _apply, "grant-approve"
    )


def admin_reject(body, ctx, now=None) -> dict:
    """admin-grant-reject: requested -> rejected, expiresAt stays null (Req 19.11, 19.13-19.15)."""
    body = _body(body)
    _resolve_target(body)

    def _apply(grant, now_ms):
        grant["status"] = "rejected"
        grant["decidedAt"] = auth_crypto.to_iso_ms(now_ms)
        return grant

    return _decide(
        body, ctx, now, "requested", auth_messages.GRANT_NEED_REQUESTED_REJECT, _apply, "grant-reject"
    )


def admin_revoke(body, ctx, now=None) -> dict:
    """admin-grant-revoke: approved -> revoked, expiresAt kept (Req 19.12-19.15). An expired Grant can still be revoked."""
    body = _body(body)
    _resolve_target(body)

    def _apply(grant, now_ms):
        grant["status"] = "revoked"
        grant["decidedAt"] = auth_crypto.to_iso_ms(now_ms)
        return grant

    return _decide(
        body, ctx, now, "approved", auth_messages.GRANT_NEED_APPROVED_REVOKE, _apply, "grant-revoke"
    )


# --- verification hook ------------------------------------------------------


def verify(user_id, grant_id, now_ms) -> bool:
    """True only for an approved Grant of user_id with that id and expiresAt > now (Req 20.2, 20.3).

    A malformed grant_id returns False without any S3 read; otherwise exactly one
    read_json_optional of the user's store. A missing, malformed, or unreadable store gives False.
    """
    if not isinstance(grant_id, str) or _GRANT_ID_RE.fullmatch(grant_id) is None:
        return False
    try:
        found = s3store.read_json_optional(_store_key(user_id))
        for grant in _readable_grants(found):
            if grant.get("id") != grant_id:
                continue
            if grant.get("status") != "approved":
                return False
            expires = _expires_ms(grant)
            return expires is not None and expires > now_ms
    except Exception:
        return False
    return False

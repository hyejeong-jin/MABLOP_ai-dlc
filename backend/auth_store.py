"""S3 layout for accounts (design: auth_store.py; D7, D8, D9; Req 1.5, 1.6, 8.1, 9.2, 9.3, 11.1, 11.3).

User_Record I/O, loginId lookup, user listing, and the create-once administrator pointer.
All functions take explicit userIds; none reads the user_scope binding.
"""
import re
import time

import auth_crypto
import auth_messages
import s3store
from errors import MablopError

_LOGIN_ID_RE = re.compile(r"^[a-z0-9_-]{4,32}$")
_USER_ID_RE = re.compile(s3store.USER_ID_PATTERN)
DANGLING_AGE_MS = 60_000  # D9: a lookup without an account counts as dangling after this age


def _account_key(user_id: str) -> str:
    """Key of users/{userId}/account.json (raises internal_error for a malformed userId)."""
    return s3store.user_key("account.json", user_id)


def _login_key(login_id: str) -> str:
    """Key of users/_auth/login/{loginId}.json; a loginId outside the allowed format is bad_request."""
    if not isinstance(login_id, str) or _LOGIN_ID_RE.fullmatch(login_id) is None:
        raise MablopError("bad_request", auth_messages.INVALID_LOGIN_ID)
    return s3store.auth_key(f"login/{login_id}.json")


def _admin_key() -> str:
    return s3store.auth_key("admin.json")


def get_user(user_id: str) -> dict | None:
    """Return the User_Record (one S3 GET), or None when the account does not exist."""
    found = s3store.read_json_optional(_account_key(user_id))
    return None if found is None else found[0]


def _read_lookup(login_id: str) -> dict | None:
    """Return the lookup object for login_id (one S3 GET), or None when absent or unusable."""
    found = s3store.read_json_optional(_login_key(login_id))
    if found is None:
        return None
    obj = found[0]
    if not isinstance(obj, dict):
        return None
    return obj


def find_user_id(login_id: str) -> str | None:
    """Return the userId a loginId points to with one S3 read; None when unknown or malformed."""
    if not isinstance(login_id, str) or _LOGIN_ID_RE.fullmatch(login_id) is None:
        return None
    obj = _read_lookup(login_id)
    if obj is None:
        return None
    user_id = obj.get("userId")
    if isinstance(user_id, str) and _USER_ID_RE.fullmatch(user_id):
        return user_id
    return None


def put_user(record: dict) -> None:
    """Write the User_Record to users/{userId}/account.json."""
    s3store.put_json(_account_key(record["userId"]), record)


def update_user(user_id: str, mutate) -> dict:
    """Read-modify-write the User_Record with update_json (D7). not_found when the account is missing."""

    def _mutate(current):
        if current is None:
            raise MablopError("not_found")
        return mutate(current)

    return s3store.update_json(_account_key(user_id), _mutate)


class _LookupChanged(Exception):
    """Raised inside update_json when the lookup no longer holds the dangling entry that was observed."""


def claim_login_id(login_id: str, user_id: str, now_ms: int) -> str:
    """Claim the loginId lookup for user_id (D9). Returns "created", "exists", or "healed".

    "created": the lookup was absent and is now written (create-once).
    "exists": the lookup exists and its account exists, or the lookup is dangling but younger than 60 s.
    "healed": the lookup exists, its account is missing, and it is at least 60 s old; it is overwritten.
    """
    key = _login_key(login_id)
    _ = _account_key(user_id)  # validate the userId before writing anything
    entry = {"userId": user_id, "createdAt": auth_crypto.to_iso_ms(now_ms)}
    for _attempt in range(2):
        if s3store.put_json_if_absent(key, entry):
            return "created"
        current = _read_lookup(login_id)
        if current is None:
            continue  # deleted (or unreadable) between the put and the read; try the create again
        target = current.get("userId")
        if isinstance(target, str) and _USER_ID_RE.fullmatch(target) and get_user(target) is not None:
            return "exists"
        try:
            created_ms = auth_crypto.from_iso_ms(current.get("createdAt"))
        except Exception:  # unreadable timestamp: age unknown, keep the claim (fail closed)
            return "exists"
        if now_ms - created_ms < DANGLING_AGE_MS:
            return "exists"

        def _overwrite(latest, observed=current):
            if latest != observed:
                raise _LookupChanged()
            return entry

        try:
            s3store.update_json(key, _overwrite)
        except _LookupChanged:
            return "exists"
        return "healed"
    return "exists"


def release_login_id(login_id: str, user_id: str) -> None:
    """Delete the loginId lookup only when it still names user_id."""
    if not isinstance(login_id, str) or _LOGIN_ID_RE.fullmatch(login_id) is None:
        return
    current = _read_lookup(login_id)
    if current is not None and current.get("userId") == user_id:
        s3store.delete(_login_key(login_id))


def delete_user(user_id: str) -> None:
    """Delete users/{userId}/account.json (the only delete the IAM policy allows besides lookups and drafts)."""
    s3store.delete(_account_key(user_id))


def list_users() -> list[dict]:
    """Return every User_Record (D8): one delimiter LIST plus one GET per user prefix.

    users/_auth/ never matches the userId pattern; prefixes without an account.json are skipped.
    """
    records = []
    for user_id in s3store.list_user_ids():
        record = get_user(user_id)
        if record is not None:
            records.append(record)
    return records


def admin_pointer() -> str | None:
    """Return the administrator's userId from users/_auth/admin.json, or None when not committed."""
    found = s3store.read_json_optional(_admin_key())
    if found is None or not isinstance(found[0], dict):
        return None
    user_id = found[0].get("userId")
    return user_id if isinstance(user_id, str) and _USER_ID_RE.fullmatch(user_id) else None


def commit_admin_pointer(user_id: str) -> bool:
    """Create the administrator pointer once (If-None-Match: *). False when it already exists."""
    _ = _account_key(user_id)  # validate the userId
    entry = {"userId": user_id, "createdAt": auth_crypto.to_iso_ms(_now_ms())}
    return s3store.put_json_if_absent(_admin_key(), entry)


def _now_ms() -> int:
    return int(time.time() * 1000)

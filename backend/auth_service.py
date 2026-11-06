"""Account actions of the Auth_Service (design: auth_service.py; user-auth-admin Req 1, 2, 3, 5, 6, 12).

This part holds the shared validation and helpers plus `register`, `login`, `get_me`, and
`change_password`. Every action has the signature ``fn(body, ctx, now=None)``: `now` is epoch
seconds (default ``time.time()``), and `ctx` is the Access_Controller's RequestContext (only the
``user`` and ``user_id`` attributes are read). Each function returns a plain dict and raises only
``MablopError``; unexpected exceptions are mapped by the handler.
"""
import copy
import re
import secrets
import time
import unicodedata

import auth_crypto
import auth_log
import auth_messages
import auth_store
import auth_throttle
import legacy_migrator
import s3store
from errors import MablopError

LEGACY_OWNER_LOGIN_ID = "jhe8888"  # Req 9.13; the loginId whose approval triggers the legacy binding
BOOTSTRAP_DONE = "관리자 계정이 생성되었습니다."

LOGIN_ID_PATTERN = r"^[a-z0-9_-]{4,32}$"
NAME_MIN_LEN = 1
NAME_MAX_LEN = 30
PASSWORD_MIN_LEN = 10
PASSWORD_MAX_LEN = 128

_LOGIN_ID_RE = re.compile(LOGIN_ID_PATTERN)
_USER_ID_RE = re.compile(s3store.USER_ID_PATTERN)


# --- shared helpers -------------------------------------------------------------------------


def _now_s(now) -> float:
    """Epoch seconds; defaults to the current time."""
    return time.time() if now is None else now


def _now_ms(now_s) -> int:
    """Integer epoch milliseconds for an epoch-seconds value."""
    return int(round(now_s * 1000))


def _bad_request(message: str) -> MablopError:
    return MablopError("bad_request", message)


def _body(body) -> dict:
    """The request body as a dict (anything else behaves like an empty body)."""
    return body if isinstance(body, dict) else {}


def _valid_login_id(value) -> bool:
    return isinstance(value, str) and _LOGIN_ID_RE.fullmatch(value) is not None


def _valid_password(value) -> bool:
    return isinstance(value, str) and PASSWORD_MIN_LEN <= len(value) <= PASSWORD_MAX_LEN


def validate_login_id(value) -> str:
    """Return a loginId that matches ^[a-z0-9_-]{4,32}$; otherwise raise bad_request (Req 1.4)."""
    if not _valid_login_id(value):
        raise _bad_request(auth_messages.INVALID_LOGIN_ID)
    return value


def validate_name(value) -> str:
    """Return the stripped name (1-30 characters, no control characters); otherwise bad_request."""
    if not isinstance(value, str):
        raise _bad_request(auth_messages.INVALID_NAME)
    name = value.strip()
    if not NAME_MIN_LEN <= len(name) <= NAME_MAX_LEN:
        raise _bad_request(auth_messages.INVALID_NAME)
    if any(unicodedata.category(ch) in ("Cc", "Cs") for ch in name):
        raise _bad_request(auth_messages.INVALID_NAME)
    return name


def validate_password(value) -> str:
    """Return a password of 10-128 characters (no other rule); otherwise bad_request (Req 3.4)."""
    if not _valid_password(value):
        raise _bad_request(auth_messages.INVALID_PASSWORD)
    return value


def new_user_id() -> str:
    """A random userId: 'u_' plus 16 lowercase hex characters."""
    return "u_" + secrets.token_hex(8)


def build_user_record(
    user_id: str, login_id: str, name: str, password_hash: dict, role: str, status: str, now_ms: int
) -> dict:
    """A new User_Record (design Data Models).

    `credentialsChangedAt` equals `createdAt`; `approvedAt` equals `createdAt` for an `active`
    account and is null otherwise.
    """
    created = auth_crypto.to_iso_ms(now_ms)
    return {
        "v": 1,
        "userId": user_id,
        "loginId": login_id,
        "name": name,
        "role": role,
        "status": status,
        "password": password_hash,
        "createdAt": created,
        "approvedAt": created if status == "active" else None,
        "credentialsChangedAt": created,
    }


def _blocked(event: str, user_id=None):
    """Log a throttle block and return nothing; the caller lets the rate_limited error propagate."""
    auth_log.log(event, "blocked", user_id)


def _login_failed(login_id, user_id=None) -> MablopError:
    """Count one failed attempt, log it, and return the uniform 401 (Req 2.4, 6.1)."""
    auth_throttle.record_failure(login_id)
    auth_log.log("login", "failure", user_id)
    return MablopError("unauthorized", auth_messages.LOGIN_FAILED)


# --- register -------------------------------------------------------------------------------


def register(body, ctx, now=None) -> dict:
    """Create a pending member account, or silently do nothing when the loginId exists (Req 1, 5.7, 5.8, 6.2).

    Order (D5): validate, admit_register, pending cap, hash (always), claim loginId, write record.
    The success response is identical whether or not the loginId existed.
    """
    body = _body(body)
    login_id = validate_login_id(body.get("loginId"))
    name = validate_name(body.get("name"))
    password = validate_password(body.get("password"))

    now_s = _now_s(now)
    try:
        auth_throttle.admit_register(now_s)
    except MablopError:
        _blocked("register")
        raise
    pending = sum(1 for user in auth_store.list_users() if user.get("status") == "pending")
    if pending >= auth_throttle.PENDING_CAP:
        _blocked("register")
        raise MablopError("rate_limited", auth_messages.REGISTER_CLOSED)

    password_hash = auth_crypto.hash_password(password)  # always, before the loginId lookup
    now_ms = _now_ms(now_s)
    user_id = new_user_id()
    outcome = auth_store.claim_login_id(login_id, user_id, now_ms)
    if outcome in ("created", "healed"):
        auth_store.put_user(
            build_user_record(user_id, login_id, name, password_hash, "member", "pending", now_ms)
        )
        auth_log.log("register", "success", user_id)
    else:
        auth_log.log("register", "ignored")
    return {"message": auth_messages.REGISTER_ACCEPTED}


# --- login ----------------------------------------------------------------------------------


def login(body, ctx, now=None) -> dict:
    """Verify credentials and issue a Session_Token (Req 2, 5.2-5.5, 6.1).

    A blocked loginId is rejected before any PBKDF2 work. An unknown or malformed loginId costs
    one dummy PBKDF2. Every credential failure returns the same 401.
    """
    body = _body(body)
    login_id = body.get("loginId")
    password = body.get("password")
    now_s = _now_s(now)

    try:
        auth_throttle.check_login(login_id, now_s)
    except MablopError:
        _blocked("login")
        raise

    user = None
    if _valid_login_id(login_id):
        user_id = auth_store.find_user_id(login_id)
        user = auth_store.get_user(user_id) if user_id else None

    if user is None:
        auth_crypto.burn_verify(password)
        raise _login_failed(login_id)
    if not auth_crypto.verify_password(password, user.get("password")):
        raise _login_failed(login_id, user["userId"])

    status = user.get("status")
    if status == "pending":
        auth_log.log("login", "blocked", user["userId"])
        raise MablopError("forbidden", auth_messages.ACCOUNT_PENDING)
    if status != "active":  # "disabled", or anything unexpected: fail closed
        auth_log.log("login", "blocked", user["userId"])
        raise MablopError("forbidden", auth_messages.ACCOUNT_DISABLED)

    auth_throttle.record_success(login_id, now_s)
    token, exp_ms = auth_crypto.sign_token(user["userId"], _now_ms(now_s))
    auth_log.log("login", "success", user["userId"])
    return {
        "token": token,
        "expiresAt": auth_crypto.to_iso_ms(exp_ms),
        "user": {"name": user["name"], "loginId": user["loginId"], "role": user["role"]},
    }


# --- get-me ---------------------------------------------------------------------------------


def get_me(body, ctx, now=None) -> dict:
    """The acting user's public fields, taken from ctx.user without another read (Req 12.1)."""
    user = ctx.user
    return {
        "userId": user["userId"],
        "loginId": user["loginId"],
        "name": user["name"],
        "role": user["role"],
        "status": user["status"],
        "createdAt": user["createdAt"],
    }


# --- change-password ------------------------------------------------------------------------


def change_password(body, ctx, now=None) -> dict:
    """Change the acting user's password and return a fresh Session_Token (Req 5.10, 12.2-12.5).

    A blocked loginId also blocks this action. A wrong current password is `bad_request`
    (never 401) and counts toward the loginId's failure counter. The new credentialsChangedAt and
    the new token's iat use the same millisecond value (D4).
    """
    body = _body(body)
    user = ctx.user
    login_id = user["loginId"]
    now_s = _now_s(now)

    try:
        auth_throttle.check_login(login_id, now_s)
    except MablopError:
        _blocked("change-password", user["userId"])
        raise

    current_password = body.get("currentPassword")
    new_password = validate_password(body.get("newPassword"))
    if isinstance(current_password, str) and new_password == current_password:
        raise _bad_request(auth_messages.SAME_PASSWORD)

    if not auth_crypto.verify_password(current_password, user.get("password")):
        auth_throttle.record_failure(login_id, now_s)
        auth_log.log("change-password", "failure", user["userId"])
        raise _bad_request(auth_messages.BAD_CURRENT)

    new_hash = auth_crypto.hash_password(new_password)
    now_ms = _now_ms(now_s)
    changed_at = auth_crypto.to_iso_ms(now_ms)

    def _apply(record):
        updated = dict(record)
        updated["password"] = new_hash
        updated["credentialsChangedAt"] = changed_at
        return updated

    auth_store.update_user(user["userId"], _apply)
    token, exp_ms = auth_crypto.sign_token(user["userId"], now_ms)
    auth_log.log("change-password", "success", user["userId"])
    return {"token": token, "expiresAt": auth_crypto.to_iso_ms(exp_ms)}


# --- bootstrap-admin ------------------------------------------------------------------------


def bootstrap_admin(body, ctx, now=None) -> dict:
    """Create the one administrator with an empty workspace (Req 9.1-9.4, D15).

    The Access_Controller already verified the Bootstrap_Token. This action never calls the
    Legacy_Migrator and writes nothing except account.json (plus the loginId lookup and the
    create-once administrator pointer under users/_auth/).
    """
    if auth_store.admin_pointer() is not None:  # before any input is read (Req 9.3)
        auth_log.log("bootstrap", "blocked")
        raise MablopError("forbidden", auth_messages.ADMIN_EXISTS)

    body = _body(body)
    login_id = validate_login_id(body.get("loginId"))
    name = validate_name(body.get("name"))
    password = validate_password(body.get("password"))

    password_hash = auth_crypto.hash_password(password)
    now_ms = _now_ms(_now_s(now))
    user_id = new_user_id()
    outcome = auth_store.claim_login_id(login_id, user_id, now_ms)

    if outcome == "exists":
        # Only an interrupted bootstrap of an admin account may be resumed (D9).
        existing_id = auth_store.find_user_id(login_id)
        existing = auth_store.get_user(existing_id) if existing_id else None
        if existing is None or existing.get("role") != "admin":
            raise _bad_request(auth_messages.LOGIN_ID_TAKEN)
        user_id = existing_id
        changed_at = auth_crypto.to_iso_ms(now_ms)

        def _resume(record):
            updated = dict(record)
            updated["name"] = name
            updated["password"] = password_hash
            updated["credentialsChangedAt"] = changed_at
            return updated

        record = auth_store.update_user(user_id, _resume)
    else:
        record = build_user_record(user_id, login_id, name, password_hash, "admin", "active", now_ms)
        auth_store.put_user(record)

    if not auth_store.commit_admin_pointer(user_id):
        # Lost the race. Clean up our own leftovers unless the pointer names this very account.
        if auth_store.admin_pointer() != user_id:
            auth_store.release_login_id(login_id, user_id)
            auth_store.delete_user(user_id)
        auth_log.log("bootstrap", "blocked")
        raise MablopError("forbidden", auth_messages.ADMIN_EXISTS)

    auth_log.log("bootstrap", "success", user_id)
    return {
        "message": BOOTSTRAP_DONE,
        "user": {"loginId": record["loginId"], "name": record["name"], "role": record["role"]},
    }


# --- administrator account actions ----------------------------------------------------------


def _resolve_target(body) -> dict:
    """The member User_Record named by body.userId (design: target resolution order).

    A malformed userId or a missing record is 404; an administrator account is bad_request.
    """
    user_id = _body(body).get("userId")
    if not isinstance(user_id, str) or _USER_ID_RE.fullmatch(user_id) is None:
        raise MablopError("not_found", auth_messages.NOT_FOUND_TARGET)
    user = auth_store.get_user(user_id)
    if user is None:
        raise MablopError("not_found", auth_messages.NOT_FOUND_TARGET)
    if user.get("role") == "admin":
        raise _bad_request(auth_messages.ADMIN_TARGET)
    return user


def _update_target(user_id: str, required_status, message: str, change) -> dict:
    """update_user with the precondition checked inside the mutation (the record stays unchanged on failure).

    `required_status` None means any status. `change(updated)` edits a copy of the record in place.
    """

    def _mutate(record):
        if record.get("role") == "admin":
            raise _bad_request(auth_messages.ADMIN_TARGET)
        if required_status is not None and record.get("status") != required_status:
            raise _bad_request(message)
        updated = dict(record)
        change(updated)
        return updated

    return auth_store.update_user(user_id, _mutate)


def admin_list_users(body, ctx, now=None) -> dict:
    """Every User with seven public fields: pending first, then ascending createdAt (Req 11.1)."""
    users = sorted(
        auth_store.list_users(),
        key=lambda u: (0 if u.get("status") == "pending" else 1, u.get("createdAt") or ""),
    )
    fields = ("userId", "loginId", "name", "role", "status", "createdAt", "approvedAt")
    return {"users": [{field: user.get(field) for field in fields} for user in users]}


def _legacy_binding_for(user: dict, now_s) -> dict:
    """The `legacyBinding` object of an approval (Req 9.18).

    Only the LEGACY_OWNER_LOGIN_ID account runs the Legacy_Migrator (once); its result is converted
    with legacy_migrator.summarize. Any exception becomes a `failed` binding without the exception
    text (Req 9.19). Every other target gets the `not_applicable` structure.
    """
    if user.get("loginId") != LEGACY_OWNER_LOGIN_ID:
        return copy.deepcopy(legacy_migrator.NOT_APPLICABLE)
    try:
        result = legacy_migrator.run(user["userId"], now_s)
    except Exception:
        result = {"status": "failed"}
    binding = legacy_migrator.summarize(result)
    auth_log.log("legacy-bind", binding["status"], user["userId"])
    return binding


def admin_approve_user(body, ctx, now=None) -> dict:
    """Approve a pending User and report the legacy binding outcome (Req 11.2, 9.14, 9.18, 9.19)."""
    target = _resolve_target(body)
    now_s = _now_s(now)
    approved_at = auth_crypto.to_iso_ms(_now_ms(now_s))

    def _change(updated):
        updated["status"] = "active"
        updated["approvedAt"] = approved_at

    _update_target(target["userId"], "pending", auth_messages.NEED_PENDING_APPROVE, _change)
    auth_log.log("admin-approve-user", "success", target["userId"])
    binding = _legacy_binding_for(target, now_s)  # the approval is stored; nothing here can undo it
    return {"userId": target["userId"], "status": "active", "legacyBinding": binding}


def admin_reject_user(body, ctx, now=None) -> dict:
    """Delete a pending User's lookup entry first, then the record (Req 11.3)."""
    target = _resolve_target(body)
    if target.get("status") != "pending":
        raise _bad_request(auth_messages.NEED_PENDING_REJECT)
    auth_store.release_login_id(target["loginId"], target["userId"])
    auth_store.delete_user(target["userId"])
    auth_log.log("admin-reject-user", "success", target["userId"])
    return {"userId": target["userId"], "removed": True}


def admin_disable_user(body, ctx, now=None) -> dict:
    """Disable an active member; earlier tokens stop working via credentialsChangedAt (D4, Req 11.4, 11.10)."""
    target = _resolve_target(body)
    changed_at = auth_crypto.to_iso_ms(_now_ms(_now_s(now)))

    def _change(updated):
        updated["status"] = "disabled"
        updated["credentialsChangedAt"] = changed_at

    _update_target(target["userId"], "active", auth_messages.NEED_ACTIVE_DISABLE, _change)
    auth_log.log("admin-disable-user", "success", target["userId"])
    return {"userId": target["userId"], "status": "disabled"}


def admin_enable_user(body, ctx, now=None) -> dict:
    """Re-enable a disabled member (Req 11.5)."""
    target = _resolve_target(body)

    def _change(updated):
        updated["status"] = "active"

    _update_target(target["userId"], "disabled", auth_messages.NEED_DISABLED_ENABLE, _change)
    auth_log.log("admin-enable-user", "success", target["userId"])
    return {"userId": target["userId"], "status": "active"}


def admin_reset_password(body, ctx, now=None) -> dict:
    """Set a random temporary password, store only its hash, and return it once (Req 11.6, 11.10)."""
    target = _resolve_target(body)
    now_s = _now_s(now)
    temporary = auth_crypto.generate_temp_password()
    new_hash = auth_crypto.hash_password(temporary)
    changed_at = auth_crypto.to_iso_ms(_now_ms(now_s))

    def _change(updated):
        updated["password"] = new_hash
        updated["credentialsChangedAt"] = changed_at

    _update_target(target["userId"], None, "", _change)
    auth_throttle.reset_login(target["loginId"], now_s)
    auth_log.log("admin-reset-password", "success", target["userId"])
    return {"userId": target["userId"], "temporaryPassword": temporary}


def admin_delete_user(body, ctx, now=None) -> dict:
    """Permanently delete a non-administrator account and all of its data.

    Data objects go first; the loginId lookup and account.json go last, so an interrupted run can
    be repeated (deletes are idempotent). Legacy originals and the legacy binding are untouched.
    """
    target = _resolve_target(body)
    user_id = target["userId"]
    prefix = f"users/{user_id}/"
    account_key = f"{prefix}account.json"
    for obj in s3store.list_keys(prefix):
        if obj["key"] != account_key:
            s3store.delete(obj["key"])
    auth_store.release_login_id(target["loginId"], user_id)
    auth_store.delete_user(user_id)
    auth_log.log("admin-delete-user", "success", user_id)
    return {"userId": user_id, "deleted": True}

# --- migrate-legacy-data --------------------------------------------------------------------


def _resolve_legacy_target(body) -> str:
    """The userId for a legacy migration: targetUserId, or the LEGACY_OWNER_LOGIN_ID account when absent."""
    raw = _body(body).get("targetUserId")
    target = auth_store.find_user_id(LEGACY_OWNER_LOGIN_ID) if raw is None else raw
    if not isinstance(target, str) or _USER_ID_RE.fullmatch(target) is None:
        raise MablopError("not_found", auth_messages.NOT_FOUND_TARGET)
    if auth_store.get_user(target) is None:
        raise MablopError("not_found", auth_messages.NOT_FOUND_TARGET)
    return target


def migrate_legacy_data(body, ctx, now=None) -> dict:
    """Administrator retry of the legacy copy (Req 9.12, 9.17).

    Target resolution is done here; the copy itself lives in legacy_migrator. The target's role and
    status are not restricted: the Legacy_Binding_Record is the safeguard (Req 9.17).
    """
    target = _resolve_legacy_target(body)
    result = legacy_migrator.run(target, now)
    status = result.get("status") if isinstance(result, dict) else None
    auth_log.log("legacy-bind", "blocked" if status == "already_bound" else str(status), target)
    if status == "already_bound":
        raise MablopError("forbidden", auth_messages.LEGACY_ALREADY_BOUND)
    return {"migration": result}

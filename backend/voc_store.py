"""User-scoped VOC storage.

VOC records keep the user supplied body separate from user metadata.  All keys
are built through :func:`s3store.user_key`, so callers must have an authenticated
user bound in ``user_scope`` before using this module.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta
import re
from typing import Any

import activity_store
import s3store
import user_scope
from errors import MablopError

INDEX_KEY = "metadata/voc-index.json"
RECORD_KEY_PREFIX = "metadata/voc/"

VOC_STATUSES = frozenset({"new", "reviewing", "confirmed", "archived"})
CATEGORIES = frozenset({"inquiry", "suggestion", "bug", "other"})
DEFAULT_CATEGORY = "other"
_MAX_BODY_LENGTH = 20_000
_MAX_TITLE_LENGTH = 100
_MAX_REPLY_LENGTH = 5_000
_PREVIEW_LENGTH = 160


def _now() -> str:
    """Return a UTC timestamp with millisecond precision."""
    value = datetime.now(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def _validation(message: str) -> MablopError:
    return MablopError("bad_request", message)


def _required_text(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _validation(f"{field} must be a non-empty string")
    if len(value) > maximum:
        raise _validation(f"{field} exceeds the maximum length")
    return value


def _category(value: Any) -> str:
    if value not in CATEGORIES:
        raise _validation("category is invalid")
    return value


def _title(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _validation("title must be a non-empty string")
    if len(value.strip()) > _MAX_TITLE_LENGTH:
        raise _validation("title exceeds the maximum length")
    return value.strip()


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults for records stored before category/title/reply existed."""
    if not isinstance(record, dict):
        return record
    body = record.get("body") if isinstance(record.get("body"), str) else ""
    if record.get("category") not in CATEGORIES:
        record["category"] = DEFAULT_CATEGORY
    if not isinstance(record.get("title"), str) or not record["title"].strip():
        record["title"] = (body.strip().splitlines() or [""])[0][:_MAX_TITLE_LENGTH]
    if not isinstance(record.get("reply"), dict):
        record["reply"] = None
    return record


def _normalize_entry(item: dict[str, Any]) -> dict[str, Any]:
    """Fill defaults for index entries written before the new list fields."""
    item = dict(item)
    if item.get("category") not in CATEGORIES:
        item["category"] = DEFAULT_CATEGORY
    if not isinstance(item.get("title"), str) or not item["title"].strip():
        item["title"] = str(item.get("preview") or "")[:_MAX_TITLE_LENGTH]
    item["hasReply"] = bool(item.get("hasReply"))
    return item


def _record_key(voc_id: str, user_id: str | None = None) -> str:
    if not isinstance(voc_id, str) or not voc_id.startswith("voc_"):
        raise _validation("vocId is invalid")
    return s3store.user_key(f"{RECORD_KEY_PREFIX}{voc_id}.json", user_id)


def _index_key(user_id: str | None = None) -> str:
    return s3store.user_key(INDEX_KEY, user_id)


def _validate_target_user(user_id: Any) -> str:
    if not isinstance(user_id, str) or re.fullmatch(r"u_[0-9a-f]{16}", user_id) is None:
        raise MablopError("not_found", "target user not found")
    import auth_store
    if auth_store.get_user(user_id) is None:
        raise MablopError("not_found", "target user not found")
    return user_id


def _parse_filter_date(value: Any, field: str, *, end: bool = False) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _validation(f"{field} must be an ISO date")
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _validation(f"{field} must be an ISO date") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if len(text) == 10 and end:
        parsed += timedelta(days=1)
    return parsed.astimezone(timezone.utc)


def _new_id() -> str:
    return f"voc_{uuid.uuid4().hex}"


def _index_default() -> dict[str, Any]:
    return {"v": 1, "records": []}


def _index_entry(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "vocId": record["vocId"],
        "title": record["title"],
        "category": record["category"],
        "hasReply": record.get("reply") is not None,
        "preview": record["body"][:_PREVIEW_LENGTH],
        "status": record["status"],
        "createdAt": record["createdAt"],
        "updatedAt": record["updatedAt"],
    }


def _validate_status(status: Any) -> str:
    if status not in VOC_STATUSES:
        raise _validation("status is invalid")
    return status


def _record_activity(
    event_type: str,
    record: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
    actor_user_id: str | None = None,
) -> dict[str, Any]:
    """Record a VOC event after the VOC/index writes have completed."""
    return activity_store.record_event(
        event_type,
        related_voc_id=record["vocId"],
        metadata=metadata,
        actor_user_id=actor_user_id,
    )


def create_voc(category: Any, title: Any, body: Any) -> dict[str, Any]:
    """Create and persist an inquiry for the bound acting user."""
    category = _category(category)
    title = _title(title)
    body = _required_text(body, "body", _MAX_BODY_LENGTH)
    now = _now()
    record = {
        "v": 2,
        "vocId": _new_id(),
        "ownerId": user_scope.current_user_id(),
        "category": category,
        "title": title,
        "body": body,
        "reply": None,
        "status": "new",
        "createdAt": now,
        "updatedAt": now,
        "statusChangedAt": None,
        "statusChangedBy": None,
    }
    s3store.put_json(_record_key(record["vocId"]), record)
    entry = _index_entry(record)

    def add(current: dict[str, Any] | None) -> dict[str, Any]:
        current = current if isinstance(current, dict) else _index_default()
        records = list(current.get("records", []))
        records = [item for item in records if item.get("vocId") != record["vocId"]]
        records.append(entry)
        return {"v": 1, "records": records}

    s3store.update_json(s3store.user_key(INDEX_KEY), add, default=_index_default())
    _record_activity(
        "voc-created",
        record,
        metadata={
            "status": record["status"],
            "category": record["category"],
        },
    )
    return record


def get_voc(voc_id: str) -> dict[str, Any]:
    """Return one VOC from the acting user's prefix."""
    try:
        record = s3store.get_json(_record_key(voc_id))
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            raise MablopError("not_found", "VOC record not found") from exc
        raise
    if not isinstance(record, dict) or record.get("ownerId") != user_scope.current_user_id():
        raise MablopError("not_found", "VOC record not found")
    return normalize_record(record)


def list_voc(*, status: str | None = None, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
    """List lightweight VOC entries from the acting user's index."""
    if status is not None:
        _validate_status(status)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _validation("limit must be between 1 and 100")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise _validation("cursor is invalid")
    index = s3store.read_json_optional(s3store.user_key(INDEX_KEY))
    records = [] if index is None else index[0].get("records", [])
    records = [_normalize_entry(item) for item in records if status is None or item.get("status") == status]
    records.sort(key=lambda item: (item.get("createdAt", ""), item.get("vocId", "")), reverse=True)
    start = 0
    if cursor:
        ids = [item.get("vocId") for item in records]
        if cursor in ids:
            start = ids.index(cursor) + 1
    page = records[start : start + limit]
    return {"records": page, "nextCursor": page[-1]["vocId"] if len(page) == limit else None}


def update_voc(voc_id: str, *, category: Any = None, title: Any = None, body: Any = None) -> dict[str, Any]:
    """Update user-editable VOC fields and refresh the lightweight index."""
    record = get_voc(voc_id)
    if category is None and title is None and body is None:
        raise _validation("at least one field is required")
    changed_fields = []
    if category is not None:
        record["category"] = _category(category)
        changed_fields.append("category")
    if title is not None:
        record["title"] = _title(title)
        changed_fields.append("title")
    if body is not None:
        record["body"] = _required_text(body, "body", _MAX_BODY_LENGTH)
        changed_fields.append("body")
    record["updatedAt"] = _now()
    s3store.put_json(_record_key(voc_id), record)
    _replace_index(record)
    _record_activity("voc-updated", record, metadata={"changedFields": changed_fields})
    return record


def archive_voc(voc_id: str) -> dict[str, Any]:
    """Archive a VOC while preserving its body and metadata."""
    record = get_voc(voc_id)
    if record["status"] != "archived":
        previous_status = record["status"]
        now = _now()
        record["status"] = "archived"
        record["updatedAt"] = now
        record["statusChangedAt"] = now
        record["statusChangedBy"] = user_scope.current_user_id()
        s3store.put_json(_record_key(voc_id), record)
        _replace_index(record)
        _record_activity(
            "voc-status-changed",
            record,
            metadata={"previousStatus": previous_status, "newStatus": "archived"},
        )
    return record


def _replace_index(record: dict[str, Any]) -> None:
    entry = _index_entry(record)

    def replace(current: dict[str, Any] | None) -> dict[str, Any]:
        current = current if isinstance(current, dict) else _index_default()
        records = [item for item in current.get("records", []) if item.get("vocId") != record["vocId"]]
        records.append(entry)
        return {"v": 1, "records": records}

    s3store.update_json(s3store.user_key(INDEX_KEY), replace, default=_index_default())


def admin_list_voc(*, user_id: Any = None, status: str | None = None,
                   from_date: Any = None, to_date: Any = None,
                   limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
    """List VOC index entries for an admin, optionally scoped to one user."""
    if status is not None:
        _validate_status(status)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _validation("limit must be between 1 and 100")
    if cursor is not None and (not isinstance(cursor, str) or not cursor):
        raise _validation("cursor is invalid")
    start = _parse_filter_date(from_date, "from")
    end = _parse_filter_date(to_date, "to", end=True)
    if start is not None and end is not None and end <= start:
        raise _validation("from must be before to")

    login_ids: dict[str, Any] = {}
    if user_id is None:
        import auth_store
        users = auth_store.list_users()
        login_ids = {u.get("userId"): u.get("loginId") for u in users if isinstance(u.get("userId"), str)}
        target_ids = list(login_ids)
    else:
        target_ids = [_validate_target_user(user_id)]
        import auth_store
        login_ids = {target_ids[0]: (auth_store.get_user(target_ids[0]) or {}).get("loginId")}

    records: list[dict[str, Any]] = []
    for target_id in target_ids:
        found = s3store.read_json_optional(_index_key(target_id))
        index = found[0] if found is not None else {}
        for item in index.get("records", []) if isinstance(index, dict) else []:
            if status is not None and item.get("status") != status:
                continue
            try:
                created = datetime.fromisoformat(str(item.get("createdAt", "")).replace("Z", "+00:00"))
                if created.tzinfo is None:
                    created = created.replace(tzinfo=timezone.utc)
                created = created.astimezone(timezone.utc)
            except (TypeError, ValueError):
                continue
            if start is not None and created < start:
                continue
            if end is not None and created >= end:
                continue
            records.append({**_normalize_entry(item), "ownerId": target_id,
                            "ownerLoginId": login_ids.get(target_id)})

    records.sort(key=lambda item: (item.get("createdAt", ""), item.get("ownerId", ""), item.get("vocId", "")), reverse=True)
    if cursor:
        matching = [i for i, item in enumerate(records) if item.get("vocId") == cursor]
        if matching:
            records = records[matching[0] + 1:]
    page = records[:limit]
    return {"records": page, "nextCursor": page[-1]["vocId"] if len(page) == limit else None}


def _load_for_user(voc_id: Any, target_id: str) -> dict[str, Any]:
    try:
        record = s3store.get_json(_record_key(voc_id, target_id))
    except Exception as exc:
        if getattr(exc, "response", {}).get("Error", {}).get("Code") in {"NoSuchKey", "404", "NotFound"}:
            raise MablopError("not_found", "VOC record not found") from exc
        raise
    if not isinstance(record, dict) or record.get("ownerId") != target_id:
        raise MablopError("not_found", "VOC record not found")
    return normalize_record(record)


def admin_reply_voc(user_id: Any, voc_id: Any, body: Any, *, actor_login_id: str | None = None) -> dict[str, Any]:
    """Write or edit the administrator reply on a target user's VOC (authorization is the handler's job)."""
    target_id = _validate_target_user(user_id)
    text = _required_text(body, "body", _MAX_REPLY_LENGTH)
    record = _load_for_user(voc_id, target_id)
    now = _now()
    previous = record.get("reply")
    record["reply"] = {
        "body": text,
        "createdAt": previous.get("createdAt", now) if previous else now,
        "updatedAt": now,
        "authorLoginId": actor_login_id,
    }
    record["updatedAt"] = now
    s3store.put_json(_record_key(record["vocId"], target_id), record)
    _replace_index_for_user(record, target_id)
    return record


def admin_get_voc(user_id: Any, voc_id: Any) -> dict[str, Any]:
    """Return one target user's full VOC record (body and reply included); authorization is the handler's job."""
    target_id = _validate_target_user(user_id)
    record = _load_for_user(voc_id, target_id)
    try:
        import auth_store
        login_id = (auth_store.get_user(target_id) or {}).get("loginId")
    except Exception:
        login_id = None
    return {**record, "ownerLoginId": login_id}


def admin_update_voc_status(user_id: Any, voc_id: Any, status: Any, *, actor_id: str | None = None) -> dict[str, Any]:
    """Change a target user's VOC status; caller authorization is enforced by the handler."""
    target_id = _validate_target_user(user_id)
    new_status = _validate_status(status)
    record = _load_for_user(voc_id, target_id)
    previous_status = record.get("status")
    if previous_status != new_status:
        now = _now()
        record["status"] = new_status
        record["updatedAt"] = now
        record["statusChangedAt"] = now
        record["statusChangedBy"] = actor_id or user_scope.current_user_id()
        s3store.put_json(_record_key(voc_id, target_id), record)
        _replace_index_for_user(record, target_id)
        _record_activity(
            "voc-status-changed",
            record,
            actor_user_id=actor_id,
            metadata={"previousStatus": previous_status, "newStatus": new_status},
        )
    return record


def _replace_index_for_user(record: dict[str, Any], user_id: str) -> None:
    entry = _index_entry(record)

    def replace(current: dict[str, Any] | None) -> dict[str, Any]:
        current = current if isinstance(current, dict) else _index_default()
        records = [item for item in current.get("records", []) if item.get("vocId") != record["vocId"]]
        records.append(entry)
        return {"v": 1, "records": records}

    s3store.update_json(_index_key(user_id), replace, default=_index_default())

"""Per-user activity event storage and range/granularity DTOs.

Activity documents are kept as monthly JSON objects under the acting user's
metadata prefix.  This module never accepts an owner from request data: all
keys are produced by ``s3store.user_key`` while ``user_scope`` is bound.
"""
from __future__ import annotations

import datetime as _dt
import time
import uuid

import s3store
import user_scope
from errors import MablopError

KST = _dt.timezone(_dt.timedelta(hours=9))
DEFAULT_RANGE_DAYS = 30
MAX_EVENTS = 1000
_GRANULARITIES = frozenset({"day", "hour"})


def _month_key(month: str) -> str:
    return s3store.user_key(f"metadata/activity-{month}.json")


def _now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_timestamp(value, *, end_of_day: bool = False) -> _dt.datetime:
    """Parse an ISO timestamp or date and return an aware UTC datetime."""
    if isinstance(value, (int, float)):
        return _dt.datetime.fromtimestamp(value, _dt.timezone.utc)
    if isinstance(value, _dt.datetime):
        moment = value
    else:
        text = str(value or "").strip()
        if not text:
            raise MablopError("bad_request", "activity range must include valid dates")
        try:
            moment = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise MablopError("bad_request", "activity range must use ISO dates") from exc
        if isinstance(value, str) and len(text) == 10 and end_of_day:
            moment = moment + _dt.timedelta(days=1)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=KST)
    return moment.astimezone(_dt.timezone.utc)


def _month_range(start: _dt.datetime, end: _dt.datetime) -> list[str]:
    """Return YYYY-MM values intersecting [start, end), oldest first."""
    local = start.astimezone(KST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    last = end.astimezone(KST).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    result = []
    while local <= last:
        result.append(local.strftime("%Y-%m"))
        if local.month == 12:
            local = local.replace(year=local.year + 1, month=1)
        else:
            local = local.replace(month=local.month + 1)
    return result


def _event_time(event: dict) -> _dt.datetime | None:
    try:
        value = event.get("occurredAt")
        if not value:
            return None
        return _parse_timestamp(value)
    except MablopError:
        return None


def _event_dto(event: dict) -> dict:
    """Return the stable detail shape used by scatter/list consumers."""
    allowed = (
        "eventId",
        "actorUserId",
        "type",
        "occurredAt",
        "count",
        "relatedVocId",
        "relatedRunId",
        "metadata",
    )
    dto = {key: event[key] for key in allowed if key in event}
    dto.setdefault("count", 1)
    dto.setdefault("metadata", {})
    return dto


def _aggregate(events: list[dict], granularity: str) -> list[dict]:
    buckets: dict[str, dict] = {}
    for event in events:
        moment = _event_time(event)
        if moment is None:
            continue
        local = moment.astimezone(KST)
        date = local.strftime("%Y-%m-%d")
        key = date if granularity == "day" else f"{date}T{local.hour:02d}:00"
        row = buckets.setdefault(
            key,
            {"date": date, "total": 0, "byType": {}, "eventIds": []},
        )
        if granularity == "hour":
            row["hour"] = local.hour
        count = event.get("count", 1)
        count = count if isinstance(count, int) and count > 0 else 1
        row["total"] += count
        event_type = event.get("type") or "unknown"
        row["byType"][event_type] = row["byType"].get(event_type, 0) + count
        if event.get("eventId") is not None:
            row["eventIds"].append(event["eventId"])
    return [buckets[key] for key in sorted(buckets)]


def record_event(event_type: str, *, occurred_at=None, count: int = 1,
                 related_voc_id=None, related_run_id=None, metadata=None,
                 actor_user_id=None) -> dict:
    """Append one event to the acting user's monthly activity document."""
    if not isinstance(event_type, str) or not event_type.strip():
        raise MablopError("bad_request", "activity type is required")
    if not isinstance(count, int) or count < 1:
        raise MablopError("bad_request", "activity count must be positive")
    moment = _parse_timestamp(occurred_at or _now())
    event = {
        "eventId": f"evt_{uuid.uuid4().hex[:16]}",
        "actorUserId": actor_user_id or user_scope.current_user_id(),
        "type": event_type,
        "occurredAt": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        "count": count,
        "relatedVocId": related_voc_id,
        "relatedRunId": related_run_id,
        "metadata": dict(metadata or {}),
    }
    key = _month_key(moment.astimezone(KST).strftime("%Y-%m"))

    def mutate(doc):
        doc = dict(doc) if isinstance(doc, dict) else {}
        events = list(doc.get("events") or [])
        events.append(event)
        return {"v": 1, "month": moment.astimezone(KST).strftime("%Y-%m"), "events": events}

    s3store.update_json(key, mutate, default={"v": 1, "events": []})
    return event


def _legacy_events(start: _dt.datetime, end: _dt.datetime) -> list[dict]:
    """Read-only derived events from existing data of the acting user.

    Drafts (metadata/drafts.json createdAt) become ``draft`` events and
    raw-posts objects (lastModified) become ``learned`` events. Nothing is stored.
    """
    acting_id = user_scope.current_user_id()
    result: list[dict] = []

    def add(kind: str, ident: str, value) -> None:
        try:
            moment = _parse_timestamp(value)
        except (MablopError, ValueError, TypeError, OverflowError):
            return
        if not (start <= moment < end):
            return
        result.append({
            "eventId": f"{kind}-{ident}",
            "actorUserId": acting_id,
            "type": kind,
            "occurredAt": moment.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "count": 1,
            "metadata": {"source": "derived"},
        })

    found = s3store.read_json_optional(s3store.user_key("metadata/drafts.json"))
    if found is not None and isinstance(found[0], dict):
        for index, entry in enumerate(found[0].get("drafts") or []):
            if isinstance(entry, dict) and entry.get("createdAt"):
                add("draft", str(entry.get("id") or entry.get("draftId") or index), entry["createdAt"])
    for obj in s3store.list_keys(s3store.user_key("raw-posts/")):
        if obj.get("lastModified") is not None:
            add("learned", str(obj.get("key") or len(result)), obj["lastModified"])
    return result


def get_activity(body=None, ctx=None, now=None) -> dict:
    """Return acting-user events and aggregate DTOs for an inclusive date range."""
    body = body if isinstance(body, dict) else {}
    granularity = body.get("granularity", "day")
    if granularity not in _GRANULARITIES:
        raise MablopError("bad_request", "granularity must be 'day' or 'hour'")

    current = _parse_timestamp(now or _now())
    if body.get("from") is None and body.get("to") is None:
        start = current - _dt.timedelta(days=DEFAULT_RANGE_DAYS - 1)
        end = current + _dt.timedelta(milliseconds=1)
    else:
        start = _parse_timestamp(body.get("from") or current - _dt.timedelta(days=DEFAULT_RANGE_DAYS - 1))
        end = _parse_timestamp(body.get("to") or current, end_of_day=True)
        if end <= start:
            raise MablopError("bad_request", "activity range must have from before to")

    acting_id = user_scope.current_user_id()
    selected = []
    for month in _month_range(start, end):
        found = s3store.read_json_optional(_month_key(month))
        document = found[0] if found is not None else {}
        for event in document.get("events", []) if isinstance(document, dict) else []:
            if not isinstance(event, dict) or event.get("actorUserId") != acting_id:
                continue
            moment = _event_time(event)
            if moment is not None and start <= moment < end:
                selected.append(_event_dto(event))

    selected.extend(_legacy_events(start, end))
    selected.sort(key=lambda event: (event.get("occurredAt") or "", event.get("eventId") or ""))
    truncated = len(selected) > MAX_EVENTS
    selected = selected[-MAX_EVENTS:] if truncated else selected
    result = {
        "events": selected,
        "daily": _aggregate(selected, granularity),
        "range": {
            "from": start.astimezone(KST).isoformat(timespec="milliseconds"),
            "to": end.astimezone(KST).isoformat(timespec="milliseconds"),
        },
    }
    if truncated:
        result["truncated"] = True
    return result


# Small aliases keep the storage API convenient for VOC/research callers.
append_event = record_event
query_activity = get_activity

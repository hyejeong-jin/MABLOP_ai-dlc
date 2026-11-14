"""Shared contracts for the Voice Box and content-research actions.

The handler remains the authentication boundary.  User-owned operations must use
``RequestContext.user_id`` (the verified Session_Token subject) and execute while
``user_scope.bind`` is active.  Identity-like fields in JSON are deliberately not
part of the ownership contract and must never be used to build an S3 key.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
import uuid
from typing import Literal, TypedDict

from errors import MablopError


# Action names are defined here once so backend handlers and the frontend can
# implement the same public contract incrementally.
VOC_ACTIONS = frozenset(
    {
        "create-voc",
        "list-voc",
        "get-voc",
        "update-voc",
        "archive-voc",
        "admin-list-voc",
        "admin-update-voc-status",
    }
)
RESEARCH_ACTIONS = frozenset({"run-research", "get-research-run", "list-research-runs"})
ACTIVITY_ACTIONS = frozenset({"get-activity"})
NEW_ACTIONS = VOC_ACTIONS | RESEARCH_ACTIONS | ACTIVITY_ACTIONS

# These are the identity fields accepted by older clients or future payloads.
# They are documented as ignored, rather than copied into a user-owned model.
IGNORED_IDENTITY_FIELDS = frozenset({"userId", "loginId", "s3Key", "key", "userPrefix"})


SourceLabel = Literal["fact", "generated", "user-input", "unverified"]
VocStatus = Literal["new", "reviewing", "confirmed", "archived"]
VOC_STATUSES = ("new", "reviewing", "confirmed", "archived")
# New stores pass only these relative paths to s3store.user_key().
USER_DATA_RELATIVE_PREFIXES = (
    "metadata/voc-index.json",
    "metadata/voc/",
    "metadata/research-runs/",
    "metadata/research-summaries/",
    "metadata/info-sources/",
    "metadata/research-cache/",
    "metadata/activity-",
)

_ID_PREFIXES = frozenset({"voc", "src", "run", "summary", "evt", "draft"})
_IDENTIFIER_RE = re.compile(r"^(?P<prefix>[a-z][a-z0-9-]*)_[0-9a-f]{32}$")
_ISO_MS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
)


class Pagination(TypedDict, total=False):
    limit: int
    cursor: str | None


def utc_now() -> datetime:
    """Return the current timezone-aware UTC time."""
    return datetime.now(timezone.utc)


def iso_ms(value: datetime | None = None) -> str:
    """Format a UTC timestamp using the JSON contract's millisecond precision."""
    value = value or utc_now()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    value = value.astimezone(timezone.utc)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


def parse_iso_ms(value: str) -> datetime:
    """Parse the canonical UTC timestamp, rejecting ambiguous user input."""
    if not isinstance(value, str) or not _ISO_MS_RE.fullmatch(value):
        raise MablopError("validation_error", "올바른 UTC 시각이 필요합니다.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise MablopError("validation_error", "올바른 UTC 시각이 필요합니다.") from exc


def new_id(prefix: str) -> str:
    """Create a collision-resistant identifier for a supported user-owned record."""
    if prefix not in _ID_PREFIXES:
        raise ValueError("unsupported identifier prefix")
    return f"{prefix}_{uuid.uuid4().hex}"


def validate_id(value: str, prefix: str | None = None) -> str:
    """Validate a generated identifier and return it unchanged."""
    if not isinstance(value, str) or not _IDENTIFIER_RE.fullmatch(value):
        raise MablopError("validation_error", "식별자 형식이 올바르지 않습니다.")
    actual = value.split("_", 1)[0]
    if prefix is not None and actual != prefix:
        raise MablopError("validation_error", "식별자 형식이 올바르지 않습니다.")
    return value


def pagination(payload: dict, *, default_limit: int = 50, max_limit: int = 100) -> Pagination:
    """Validate the common limit/cursor request fields without interpreting ownership."""
    if not isinstance(payload, dict):
        raise MablopError("validation_error", "요청 형식이 올바르지 않습니다.")
    raw_limit = payload.get("limit", default_limit)
    if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or not 1 <= raw_limit <= max_limit:
        raise MablopError("validation_error", f"limit은 1에서 {max_limit} 사이여야 합니다.")
    cursor = payload.get("cursor")
    if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 512):
        raise MablopError("validation_error", "cursor 형식이 올바르지 않습니다.")
    return {"limit": raw_limit, "cursor": cursor}


def enum_value(value: str, allowed: frozenset[str] | set[str], field: str) -> str:
    """Validate a string enum used by the action contracts."""
    if not isinstance(value, str) or value not in allowed:
        raise MablopError("validation_error", f"{field} 값이 올바르지 않습니다.")
    return value


class ErrorPayload(TypedDict):
    code: str
    message: str


class ErrorResponse(TypedDict):
    error: ErrorPayload


class VocResponse(TypedDict):
    voc: dict


class VocListResponse(TypedDict, total=False):
    records: list[dict]
    nextCursor: str | None


class ResearchResponse(TypedDict, total=False):
    runId: str
    status: str
    summary: dict
    sources: list[dict]
    usage: dict
    budget: dict
    cacheHit: bool
    controls: list[str]


class ResearchRunResponse(TypedDict):
    run: dict
    summary: dict
    sources: list[dict]


class ResearchListResponse(TypedDict, total=False):
    runs: list[dict]
    nextCursor: str | None


class ActivityResponse(TypedDict):
    events: list[dict]
    daily: list[dict]
    range: dict


@dataclass(frozen=True)
class ActingUser:
    """The only identity allowed for user-owned action execution."""

    user_id: str


def acting_user(ctx) -> ActingUser:
    """Return the verified acting user; never read identity from request JSON."""
    user_id = getattr(ctx, "user_id", None)
    if not isinstance(user_id, str) or not user_id:
        # Import lazily to keep this contract module usable by type tooling.
        from errors import MablopError

        raise MablopError("unauthorized")
    return ActingUser(user_id)

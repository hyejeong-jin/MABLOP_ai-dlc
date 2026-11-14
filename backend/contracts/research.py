"""Contracts for user-scoped content research storage."""
from __future__ import annotations

from typing import Any

from errors import MablopError
from research import normalize_url, URLValidationError

SOURCE_TYPES = frozenset({"primary-link", "web-search", "voc"})
SOURCE_STATUSES = frozenset({"succeeded", "failed", "skipped"})
RUN_STATUSES = frozenset({"completed", "partial", "failed", "budget-exhausted"})


def _invalid(message: str) -> MablopError:
    return MablopError("validation_error", message)


def source_url(value: Any) -> str:
    """Return the canonical URL used by source records and cache keys."""
    if not isinstance(value, str):
        raise _invalid("url must be a string")
    try:
        return normalize_url(value)
    except URLValidationError as exc:
        raise _invalid(str(exc)) from exc


def validate_source(source: dict[str, Any]) -> dict[str, Any]:
    """Validate the stable fields of an Info_Source without dropping failures."""
    if not isinstance(source, dict):
        raise _invalid("source must be an object")
    if not isinstance(source.get("sourceId"), str) or not source["sourceId"].startswith("src_"):
        raise _invalid("sourceId is invalid")
    if source.get("sourceType") not in SOURCE_TYPES:
        raise _invalid("sourceType is invalid")
    if source.get("status") not in SOURCE_STATUSES:
        raise _invalid("source status is invalid")
    if source.get("normalizedUrl") is not None:
        source["normalizedUrl"] = source_url(source["normalizedUrl"])
    if source.get("status") == "failed" and not source.get("errorMessage") and not source.get("errorCode"):
        raise _invalid("failed source must include an error")
    return source


def validate_run(run: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(run, dict) or not isinstance(run.get("runId"), str) or not run["runId"].startswith("run_"):
        raise _invalid("runId is invalid")
    if run.get("status") not in RUN_STATUSES:
        raise _invalid("run status is invalid")
    return run


def validate_summary(summary: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(summary, dict) or not isinstance(summary.get("summaryId"), str) or not summary["summaryId"].startswith("summary_"):
        raise _invalid("summaryId is invalid")
    return summary

"""User-scoped persistence models for content research.

This module only stores research metadata.  Fetching, summarising, and grant
checks belong to the research pipeline and handler.  Every key is created from
a relative path through ``s3store.user_key`` while the acting user is bound.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

import s3store
import user_scope
from contracts.research import source_url, validate_run, validate_source, validate_summary
from errors import MablopError

SOURCE_PREFIX = "metadata/info-sources/"
RUN_PREFIX = "metadata/research-runs/"
SUMMARY_PREFIX = "metadata/research-summaries/"
CACHE_PREFIX = "metadata/research-cache/"
RUN_INDEX = RUN_PREFIX + "index.json"


def _missing(exc: Exception) -> bool:
    return getattr(exc, "response", {}).get("Error", {}).get("Code") in {
        "NoSuchKey", "404", "NotFound", "AccessDenied", "403"
    }


def _key(prefix: str, identifier: str) -> str:
    if not isinstance(identifier, str) or not identifier or "/" in identifier or "\\" in identifier or ".." in identifier:
        raise MablopError("validation_error", "research identifier is invalid")
    return s3store.user_key(f"{prefix}{identifier}.json")


def _index_entry(record: dict[str, Any]) -> dict[str, Any]:
    return {
        key: record[key]
        for key in ("runId", "status", "summaryId", "requestedAt", "completedAt", "cacheKey")
        if key in record
    }


def save_info_source(source: dict[str, Any]) -> dict[str, Any]:
    """Persist an Info_Source, including failed and skipped source details."""
    record = deepcopy(source)
    if record.get("normalizedUrl") is None and record.get("url") is not None:
        record["normalizedUrl"] = source_url(record["url"])
    validate_source(record)
    record["v"] = int(record.get("v", 1))
    record["ownerId"] = user_scope.current_user_id()
    record.setdefault("collectedAt", None)
    record.setdefault("errorCode", None)
    record.setdefault("errorMessage", None)
    record.setdefault("excerpt", "")
    s3store.put_json(_key(SOURCE_PREFIX, record["sourceId"]), record)
    return record


def get_info_source(source_id: str) -> dict[str, Any]:
    try:
        record = s3store.get_json(_key(SOURCE_PREFIX, source_id))
    except Exception as exc:
        if _missing(exc):
            raise MablopError("not_found", "information source not found") from exc
        raise
    if not isinstance(record, dict) or record.get("ownerId") != user_scope.current_user_id():
        raise MablopError("not_found", "information source not found")
    return record


def list_info_sources(run_id: str | None = None, *, source_type: str | None = None) -> list[dict[str, Any]]:
    """List only the acting user's sources, optionally for one research run."""
    rows = []
    for item in s3store.list_keys(s3store.user_key(SOURCE_PREFIX)):
        key = item["key"]
        if not key.endswith(".json") or key.endswith("/index.json"):
            continue
        try:
            source = s3store.get_json(key)
        except Exception:
            continue
        if not isinstance(source, dict) or source.get("ownerId") != user_scope.current_user_id():
            continue
        if run_id is not None and source.get("runId") != run_id:
            continue
        if source_type is not None and source.get("sourceType") != source_type:
            continue
        rows.append(source)
    rows.sort(key=lambda value: (value.get("collectedAt") or "", value.get("sourceId") or ""))
    return rows


def save_research_summary(summary: dict[str, Any]) -> dict[str, Any]:
    record = deepcopy(summary)
    validate_summary(record)
    record["v"] = int(record.get("v", 1))
    record["ownerId"] = user_scope.current_user_id()
    record.setdefault("facts", [])
    record.setdefault("generatedInterpretation", [])
    record.setdefault("unverified", [])
    record.setdefault("sourceIds", [])
    record.setdefault("partial", False)
    s3store.put_json(_key(SUMMARY_PREFIX, record["summaryId"]), record)
    return record


def get_research_summary(summary_id: str) -> dict[str, Any]:
    try:
        record = s3store.get_json(_key(SUMMARY_PREFIX, summary_id))
    except Exception as exc:
        if _missing(exc):
            raise MablopError("not_found", "research summary not found") from exc
        raise
    if not isinstance(record, dict) or record.get("ownerId") != user_scope.current_user_id():
        raise MablopError("not_found", "research summary not found")
    return record


def save_research_run(run: dict[str, Any]) -> dict[str, Any]:
    record = deepcopy(run)
    validate_run(record)
    record["v"] = int(record.get("v", 1))
    record["ownerId"] = user_scope.current_user_id()
    record.setdefault("primarySourceIds", [])
    record.setdefault("searchSourceIds", [])
    record.setdefault("controls", [])
    s3store.put_json(_key(RUN_PREFIX, record["runId"]), record)

    def update_index(current):
        current = current if isinstance(current, dict) else {"v": 1, "runs": []}
        entries = [entry for entry in current.get("runs", []) if entry.get("runId") != record["runId"]]
        entries.append(_index_entry(record))
        entries.sort(key=lambda entry: (entry.get("requestedAt") or "", entry.get("runId") or ""), reverse=True)
        return {"v": 1, "runs": entries}

    s3store.update_json(s3store.user_key(RUN_INDEX), update_index, default={"v": 1, "runs": []})
    return record


def get_research_run(run_id: str) -> dict[str, Any]:
    try:
        record = s3store.get_json(_key(RUN_PREFIX, run_id))
    except Exception as exc:
        if _missing(exc):
            raise MablopError("not_found", "research run not found") from exc
        raise
    if not isinstance(record, dict) or record.get("ownerId") != user_scope.current_user_id():
        raise MablopError("not_found", "research run not found")
    return record


def list_research_runs(*, limit: int = 100, cursor: str | None = None) -> dict[str, Any]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise MablopError("validation_error", "limit must be between 1 and 100")
    found = s3store.read_json_optional(s3store.user_key(RUN_INDEX))
    entries = [] if found is None else found[0].get("runs", [])
    entries = [entry for entry in entries if isinstance(entry, dict)]
    if cursor and cursor in [entry.get("runId") for entry in entries]:
        entries = entries[[entry.get("runId") for entry in entries].index(cursor) + 1 :]
    page = entries[:limit]
    return {"runs": page, "nextCursor": page[-1]["runId"] if len(page) == limit else None}


def save_cache(cache_key: str, value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(cache_key, str) or not cache_key:
        raise MablopError("validation_error", "cacheKey is required")
    record = deepcopy(value)
    record["v"] = int(record.get("v", 1))
    record["cacheKey"] = cache_key
    record["ownerId"] = user_scope.current_user_id()
    # Hash keys are safe, but reject arbitrary path input before building S3 keys.
    safe_key = cache_key.replace("sha256:", "")
    if not safe_key or any(char not in "0123456789abcdef" for char in safe_key.lower()):
        raise MablopError("validation_error", "cacheKey is invalid")
    s3store.put_json(_key(CACHE_PREFIX, safe_key), record)
    return record


def get_cache(cache_key: str) -> dict[str, Any] | None:
    safe_key = cache_key.replace("sha256:", "") if isinstance(cache_key, str) else ""
    if not safe_key or any(char not in "0123456789abcdef" for char in safe_key.lower()):
        raise MablopError("validation_error", "cacheKey is invalid")
    found = s3store.read_json_optional(_key(CACHE_PREFIX, safe_key))
    if found is None:
        return None
    record = found[0]
    if not isinstance(record, dict) or record.get("ownerId") != user_scope.current_user_id():
        return None
    return record

# Naming aliases used by pipeline callers.
put_info_source = save_info_source
put_research_run = save_research_run
put_research_summary = save_research_summary
put_cache = save_cache
get_research_cache = get_cache
list_runs = list_research_runs

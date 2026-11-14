"""Per-user monthly Bedrock usage meter (users/{userId}/metadata/usage-YYYY-MM.json).

``record`` is called by bedrock.invoke after every successful model call. It never raises: metering
must not break a draft. ``get_usage`` is the ``get-usage`` action: the acting user's own month so far,
an estimated cost from the list prices below, and the next reset time (KST month boundary).
"""
import datetime
import os
import time

import auth_crypto
import auth_messages
import s3store
from errors import MablopError

KST = datetime.timezone(datetime.timedelta(hours=9))

# USD per 1,000,000 tokens (Bedrock on-demand list prices; an estimate, not an invoice).
PRICES = [
    {"match": "claude-sonnet-4-5", "label": "Claude Sonnet 4.5", "input": 3.0, "output": 15.0},
    {"match": "titan-embed-text-v2", "label": "Titan Text Embeddings V2", "input": 0.02, "output": 0.0},
]


def _price_for(model_id: str):
    for price in PRICES:
        if price["match"] in (model_id or ""):
            return price
    return None


def _month(now_s: float) -> str:
    return datetime.datetime.fromtimestamp(now_s, KST).strftime("%Y-%m")


def _key(month: str) -> str:
    return s3store.user_key(f"metadata/usage-{month}.json")


def tokens_of(out) -> tuple[int, int]:
    """(input, output) token counts from a Bedrock response body (Claude usage or Titan count)."""
    if not isinstance(out, dict):
        return 0, 0
    usage = out.get("usage")
    if isinstance(usage, dict):
        return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)
    return int(out.get("inputTextTokenCount") or 0), 0


def record(model_id: str, out, now=None) -> None:
    """Add one call to the acting user's month counter. Swallows every error."""
    try:
        tokens_in, tokens_out = tokens_of(out)
        month = _month(time.time() if now is None else now)

        def mutate(doc):
            doc = dict(doc) if isinstance(doc, dict) else {}
            models = dict(doc.get("models") or {})
            entry = dict(models.get(model_id) or {"calls": 0, "inputTokens": 0, "outputTokens": 0})
            entry["calls"] = int(entry.get("calls", 0)) + 1
            entry["inputTokens"] = int(entry.get("inputTokens", 0)) + tokens_in
            entry["outputTokens"] = int(entry.get("outputTokens", 0)) + tokens_out
            models[model_id] = entry
            doc["v"] = 1
            doc["month"] = month
            doc["models"] = models
            activity = dict(doc.get("activity") or {})
            day = datetime.datetime.fromtimestamp(time.time() if now is None else now, KST).strftime("%Y-%m-%d")
            activity[day] = int(activity.get(day, 0)) + tokens_in + tokens_out
            doc["activity"] = activity
            return doc

        s3store.update_json(_key(month), mutate, default={"v": 1, "month": month, "models": {}})
    except Exception as err:  # noqa: BLE001 - metering must never fail a model call
        print(f'{{"event": "usage-record", "outcome": "error:{type(err).__name__}"}}')
        return


RESEARCH_FIELDS = ("runs", "cacheHits", "cacheMisses", "requests", "inputChars", "outputTokens", "searchCalls")


def record_research(usage, cache_hit: bool, now=None) -> None:
    """Add one research run (or cache hit) to the month's ``research`` block.

    The existing ``models``/``activity`` fields are left untouched. Swallows every error.
    """
    try:
        usage = usage if isinstance(usage, dict) else {}
        month = _month(time.time() if now is None else now)

        def mutate(doc):
            doc = dict(doc) if isinstance(doc, dict) else {}
            research = {name: int((doc.get("research") or {}).get(name, 0)) for name in RESEARCH_FIELDS}
            research["runs"] += 1
            research["cacheHits" if cache_hit else "cacheMisses"] += 1
            if not cache_hit:
                for name in ("requests", "inputChars", "outputTokens", "searchCalls"):
                    research[name] += int(usage.get(name) or 0)
            doc.setdefault("v", 1)
            doc.setdefault("month", month)
            doc.setdefault("models", {})
            doc["research"] = research
            return doc

        s3store.update_json(_key(month), mutate, default={"v": 1, "month": month, "models": {}})
    except Exception as err:  # noqa: BLE001 - metering must never fail a research run
        print(f'{{"event": "usage-record-research", "outcome": "error:{type(err).__name__}"}}')


LITE_FIELD = "liteDrafts"  # integer count of lite-tier drafts this month
_LITE_LIMIT_ENV = "MABLOP_LITE_MONTHLY_LIMIT"
_DEFAULT_LITE_LIMIT = 5


def lite_limit() -> int:
    """Monthly lite-tier draft cap per user (env MABLOP_LITE_MONTHLY_LIMIT, default 5). 0 disables lite."""
    raw = os.environ.get(_LITE_LIMIT_ENV)
    if raw is None:
        return _DEFAULT_LITE_LIMIT
    try:
        value = int(raw)
    except ValueError:
        return _DEFAULT_LITE_LIMIT
    return value if value >= 0 else _DEFAULT_LITE_LIMIT


def _lite_used(doc) -> int:
    """Lite drafts counted in a usage document; anything unreadable counts as zero."""
    value = doc.get(LITE_FIELD) if isinstance(doc, dict) else None
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _empty_month_doc(month: str) -> dict:
    return {"v": 1, "month": month, "models": {}}


def reserve_lite_draft(now=None) -> int:
    """Count one lite draft before it is generated. Returns how many remain this month.

    Raises quota_exceeded (HTTP 429) when the month's count already reached the limit, and then writes
    nothing. The check and the increment share one optimistic-concurrency update, so two concurrent
    requests cannot both take the last slot.
    """
    limit = lite_limit()
    month = _month(time.time() if now is None else now)

    def mutate(doc):
        doc = dict(doc) if isinstance(doc, dict) else {}
        used = _lite_used(doc)
        if used >= limit:
            raise MablopError("quota_exceeded", auth_messages.LITE_QUOTA_EXCEEDED)
        doc[LITE_FIELD] = used + 1
        doc.setdefault("v", 1)
        doc.setdefault("month", month)
        doc.setdefault("models", {})
        return doc

    doc = s3store.update_json(_key(month), mutate, default=_empty_month_doc(month))
    return max(0, limit - _lite_used(doc))


def release_lite_draft(now=None) -> None:
    """Return one reserved lite draft after its generation failed. Never raises."""
    month = _month(time.time() if now is None else now)

    def mutate(doc):
        doc = dict(doc) if isinstance(doc, dict) else {}
        doc[LITE_FIELD] = max(0, _lite_used(doc) - 1)
        return doc

    try:
        s3store.update_json(_key(month), mutate, default=_empty_month_doc(month))
    except Exception as err:  # noqa: BLE001 - the failed draft already reported its own error
        print(f'{{"event": "usage-release-lite", "outcome": "error:{type(err).__name__}"}}')


def _reset_at(now_s: float) -> str:
    local = datetime.datetime.fromtimestamp(now_s, KST)
    first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (first + datetime.timedelta(days=32)).replace(day=1)
    return auth_crypto.to_iso_ms(int(nxt.timestamp() * 1000))


def _kst_date(value) -> str | None:
    """'YYYY-MM-DD' (KST) for an ISO timestamp string or datetime; None when unreadable."""
    try:
        if isinstance(value, datetime.datetime):
            moment = value
        else:
            moment = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=datetime.timezone.utc)
        return moment.astimezone(KST).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _recent_months(now_s: float, count: int = 6) -> list[str]:
    """The current KST month and the previous count-1 months, newest first."""
    local = datetime.datetime.fromtimestamp(now_s, KST).replace(day=1)
    months = []
    for _ in range(count):
        months.append(local.strftime("%Y-%m"))
        local = (local - datetime.timedelta(days=1)).replace(day=1)
    return months


def _activity(now_s: float) -> dict:
    """Writing activity per KST day, separated into drafts written and posts learned."""
    counts: dict[str, dict[str, int]] = {}

    def bump(day: str | None, kind: str):
        if not day:
            return
        row = counts.setdefault(day, {"drafts": 0, "learned": 0})
        row[kind] += 1

    found = s3store.read_json_optional(s3store.user_key("metadata/drafts.json"))
    if found is not None and isinstance(found[0], dict):
        for entry in found[0].get("drafts") or []:
            if isinstance(entry, dict):
                bump(_kst_date(entry.get("createdAt")), "drafts")
    for obj in s3store.list_keys(s3store.user_key("raw-posts/")):
        bump(_kst_date(obj.get("lastModified")), "learned")
    for row in counts.values():
        row["total"] = row["drafts"] + row["learned"]
    return counts

def get_usage(body, ctx, now=None) -> dict:
    """The acting user's usage this month (KST) per model with an estimated USD cost, the token
    total of the last six months, and the daily writing activity."""
    now_s = time.time() if now is None else now
    month = _month(now_s)
    stored = {}
    total_tokens = 0
    for m in _recent_months(now_s):
        found = s3store.read_json_optional(_key(m))
        doc = found[0] if found is not None and isinstance(found[0], dict) else {}
        if m == month:
            stored = doc
        for entry in (doc.get("models") or {}).values():
            if isinstance(entry, dict):
                total_tokens += int(entry.get("inputTokens", 0)) + int(entry.get("outputTokens", 0))
    rows = []
    total = 0.0
    for model_id, entry in sorted((stored.get("models") or {}).items()):
        if not isinstance(entry, dict):
            continue
        price = _price_for(model_id)
        tin, tout = int(entry.get("inputTokens", 0)), int(entry.get("outputTokens", 0))
        usd = 0.0 if price is None else (tin * price["input"] + tout * price["output"]) / 1_000_000
        total += usd
        rows.append(
            {
                "modelId": model_id,
                "label": price["label"] if price else model_id,
                "calls": int(entry.get("calls", 0)),
                "inputTokens": tin,
                "outputTokens": tout,
                "usd": round(usd, 6),
                "priced": price is not None,
            }
        )
    return {
        "month": month,
        "totalUsd": round(total, 6),
        "totalTokens": total_tokens,
        "resetAt": _reset_at(now_s),
        "models": rows,
        "activity": _activity(now_s),
        "research": {name: int((stored.get("research") or {}).get(name, 0)) for name in RESEARCH_FIELDS},
        "liteDrafts": {"used": _lite_used(stored), "limit": lite_limit()},
        "pricing": [
            {"label": p["label"], "inputPerMillion": p["input"], "outputPerMillion": p["output"]}
            for p in PRICES
        ],
    }
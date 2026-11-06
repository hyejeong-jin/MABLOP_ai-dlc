"""Per-user monthly Bedrock usage meter (users/{userId}/metadata/usage-YYYY-MM.json).

``record`` is called by bedrock.invoke after every successful model call. It never raises: metering
must not break a draft. ``get_usage`` is the ``get-usage`` action: the acting user's own month so far,
an estimated cost from the list prices below, and the next reset time (KST month boundary).
"""
import datetime
import time

import auth_crypto
import s3store

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
            return doc

        s3store.update_json(_key(month), mutate, default={"v": 1, "month": month, "models": {}})
    except Exception:  # noqa: BLE001 - metering must never fail a model call
        return


def _reset_at(now_s: float) -> str:
    local = datetime.datetime.fromtimestamp(now_s, KST)
    first = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    nxt = (first + datetime.timedelta(days=32)).replace(day=1)
    return auth_crypto.to_iso_ms(int(nxt.timestamp() * 1000))


def get_usage(body, ctx, now=None) -> dict:
    """The acting user's usage this month (KST), per model, with an estimated USD cost."""
    now_s = time.time() if now is None else now
    month = _month(now_s)
    found = s3store.read_json_optional(_key(month))
    stored = found[0] if found is not None and isinstance(found[0], dict) else {}
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
        "resetAt": _reset_at(now_s),
        "models": rows,
        "pricing": [
            {"label": p["label"], "inputPerMillion": p["input"], "outputPerMillion": p["output"]}
            for p in PRICES
        ],
    }
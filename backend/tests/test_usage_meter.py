"""usage_meter: monthly per-user Bedrock usage (record via bedrock.invoke, read via get-usage)."""
import io
import json

import pytest

import bedrock
import s3store
import usage_meter
import user_scope
from conftest import ACTING_USER_ID, OTHER_USER_ID

NOW = 1_792_000_000  # 2026-10-14 (KST)


class _FakeClient:
    def __init__(self, payload):
        self.payload = payload

    def invoke_model(self, **kwargs):
        return {"body": io.BytesIO(json.dumps(self.payload).encode())}


def _call(monkeypatch, model_id, payload):
    monkeypatch.setattr(bedrock, "get_client", lambda: _FakeClient(payload))
    return bedrock.invoke(model_id, {})


CLAUDE = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
TITAN = "amazon.titan-embed-text-v2:0"


def test_invoke_records_tokens_and_get_usage_prices_them(s3_bucket, monkeypatch):
    monkeypatch.setattr(usage_meter.time, "time", lambda: NOW)
    _call(monkeypatch, CLAUDE, {"content": [], "usage": {"input_tokens": 1000, "output_tokens": 200}})
    _call(monkeypatch, CLAUDE, {"content": [], "usage": {"input_tokens": 500, "output_tokens": 100}})
    _call(monkeypatch, TITAN, {"embedding": [0.1], "inputTextTokenCount": 2000})

    out = usage_meter.get_usage({}, None, now=NOW)

    by_id = {m["modelId"]: m for m in out["models"]}
    assert by_id[CLAUDE]["calls"] == 2
    assert (by_id[CLAUDE]["inputTokens"], by_id[CLAUDE]["outputTokens"]) == (1500, 300)
    assert by_id[CLAUDE]["usd"] == pytest.approx((1500 * 3 + 300 * 15) / 1e6)
    assert by_id[TITAN]["inputTokens"] == 2000 and by_id[TITAN]["outputTokens"] == 0
    assert out["totalUsd"] == pytest.approx(by_id[CLAUDE]["usd"] + by_id[TITAN]["usd"])
    assert out["month"] == "2026-10"
    assert out["resetAt"] == "2026-10-31T15:00:00.000Z"  # 2026-11-01 00:00 KST


def test_usage_is_per_user_and_per_month(s3_bucket, monkeypatch):
    monkeypatch.setattr(usage_meter.time, "time", lambda: NOW)
    _call(monkeypatch, CLAUDE, {"usage": {"input_tokens": 10, "output_tokens": 5}})
    with user_scope.bind(OTHER_USER_ID):
        assert usage_meter.get_usage({}, None, now=NOW)["models"] == []
    assert usage_meter.get_usage({}, None, now=NOW + 40 * 86400)["models"] == []
    assert s3store.key_exists(f"users/{ACTING_USER_ID}/metadata/usage-2026-10.json")


def test_unknown_model_is_listed_but_not_priced(s3_bucket, monkeypatch):
    monkeypatch.setattr(usage_meter.time, "time", lambda: NOW)
    _call(monkeypatch, "vendor.other-model", {"usage": {"input_tokens": 10, "output_tokens": 5}})
    row = usage_meter.get_usage({}, None, now=NOW)["models"][0]
    assert row["priced"] is False and row["usd"] == 0 and row["label"] == "vendor.other-model"


def test_metering_failure_never_breaks_the_model_call(monkeypatch):
    monkeypatch.setattr(usage_meter.s3store, "update_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("s3 down")))
    out = _call(monkeypatch, CLAUDE, {"usage": {"input_tokens": 1, "output_tokens": 1}})
    assert out == {"usage": {"input_tokens": 1, "output_tokens": 1}}


def test_empty_month_returns_zero(s3_bucket):
    out = usage_meter.get_usage({}, None, now=NOW)
    assert out["totalUsd"] == 0 and out["models"] == []
    assert {p["label"] for p in out["pricing"]} == {"Claude Sonnet 4.5", "Claude Haiku 4.5", "Titan Text Embeddings V2"}


def test_lite_haiku_model_is_priced(s3_bucket, monkeypatch):
    import drafting_engine

    monkeypatch.setattr(usage_meter.time, "time", lambda: NOW)
    lite = drafting_engine._DEFAULT_LITE_MODEL
    _call(monkeypatch, lite, {"usage": {"input_tokens": 2000, "output_tokens": 1000}})
    _call(monkeypatch, "anthropic.claude-haiku-4-5-20251001-v1:0", {"usage": {"input_tokens": 1, "output_tokens": 1}})
    rows = {m["modelId"]: m for m in usage_meter.get_usage({}, None, now=NOW)["models"]}
    assert rows[lite]["priced"] is True and rows[lite]["label"] == "Claude Haiku 4.5"
    assert rows[lite]["usd"] == pytest.approx((2000 * 1 + 1000 * 5) / 1e6)
    assert rows["anthropic.claude-haiku-4-5-20251001-v1:0"]["priced"] is True


def test_get_usage_through_handler_needs_login_but_no_grant(s3_bucket, signing_secret, create_user, make_session_token):
    import handler

    member = create_user("member1")
    ok = handler.handler({"headers": {"X-Mablop-Token": make_session_token(member["userId"])}, "body": json.dumps({"action": "get-usage"})})
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["models"] == []
    anon = handler.handler({"headers": {}, "body": json.dumps({"action": "get-usage"})})
    assert anon["statusCode"] == 401
"""23.1 / 23.2: Google AI Overview via SerpApi and pasted material. No network: all calls are stubbed."""
import pytest

import drafting_engine
import handler
import serp_ai_overview
from errors import MablopError

BLOCKS = [
    {"type": "heading", "snippet": "제주 웨딩 스냅 부케"},
    {"type": "paragraph", "snippet": "생화와 조화 중 고를 수 있어요."},
    {"type": "list", "list": [{"title": "생화", "snippet": "자연스러운 질감"}, {"snippet": "바람에 약함"}]},
]


def test_flatten_turns_text_blocks_into_plain_lines():
    lines = []
    serp_ai_overview._flatten(BLOCKS, lines)
    assert lines == ["## 제주 웨딩 스냅 부케", "생화와 조화 중 고를 수 있어요.", "- 생화: 자연스러운 질감", "- 바람에 약함"]



def test_missing_key_reports_provider_unavailable(monkeypatch):
    monkeypatch.setattr(serp_ai_overview, "_api_key", lambda: "")
    assert serp_ai_overview.fetch_ai_overview("제주 부케") == (None, "serp_provider_unavailable")


def test_inline_overview_is_one_call(monkeypatch):
    calls = []

    def fake_get(params, timeout=10.0):
        calls.append(params)
        return {"ai_overview": {"text_blocks": BLOCKS}}

    monkeypatch.setattr(serp_ai_overview, "_api_key", lambda: "k")
    monkeypatch.setattr(serp_ai_overview, "_get_json", fake_get)
    overview, control = serp_ai_overview.fetch_ai_overview("제주 웨딩 스냅 부케")
    assert control is None and "## 제주 웨딩 스냅 부케" in overview["text"]
    assert len(calls) == 1 and calls[0]["engine"] == "google" and calls[0]["gl"] == "kr"


def test_page_token_triggers_one_follow_up_call(monkeypatch):
    calls = []

    def fake_get(params, timeout=10.0):
        calls.append(params)
        if params["engine"] == "google":
            return {"ai_overview": {"page_token": "pt-1"}}
        return {"ai_overview": {"text_blocks": BLOCKS}}

    monkeypatch.setattr(serp_ai_overview, "_api_key", lambda: "k")
    monkeypatch.setattr(serp_ai_overview, "_get_json", fake_get)
    overview, _ = serp_ai_overview.fetch_ai_overview("부케")
    assert overview is not None
    assert [c["engine"] for c in calls] == ["google", "google_ai_overview"]
    assert len(calls) <= serp_ai_overview.MAX_CALLS


def test_no_overview_reports_unavailable(monkeypatch):
    monkeypatch.setattr(serp_ai_overview, "_api_key", lambda: "k")
    monkeypatch.setattr(serp_ai_overview, "_get_json", lambda params, timeout=10.0: {"organic_results": []})
    assert serp_ai_overview.fetch_ai_overview("아무말") == (None, "ai_overview_unavailable")


def test_request_errors_never_expose_the_key(monkeypatch):
    import urllib.request

    def boom(request, timeout=10.0):
        raise OSError("connection refused for " + request.full_url)

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(RuntimeError) as info:
        serp_ai_overview._get_json({"engine": "google", "q": "부케", "api_key": "SECRET-KEY-123"})
    assert "SECRET-KEY-123" not in str(info.value)
    assert "OSError" in str(info.value)


def test_handler_adds_overview_as_google_source(monkeypatch):
    monkeypatch.setattr(serp_ai_overview, "fetch_ai_overview", lambda keyword: ({"text": "## 개요\n- 항목: 설명"}, None))
    source, controls = handler._collect_ai_overview("제주 부케")
    assert controls == []
    assert source["sourceType"] == "google-ai-overview"
    assert source["status"] == "succeeded"
    assert source["excerpt"].startswith("## 개요")
    assert source["url"].startswith("https://www.google.com/search?q=")


def test_handler_records_control_when_overview_missing(monkeypatch):
    monkeypatch.setattr(serp_ai_overview, "fetch_ai_overview", lambda keyword: (None, "ai_overview_unavailable"))
    assert handler._collect_ai_overview("부케") == (None, ["ai_overview_unavailable"])


def test_handler_continues_when_serp_request_fails(monkeypatch):
    def boom(keyword):
        raise RuntimeError("serpapi request failed: OSError")

    monkeypatch.setattr(serp_ai_overview, "fetch_ai_overview", boom)
    assert handler._collect_ai_overview("부케") == (None, ["serp_request_failed"])


def test_pasted_material_is_a_user_input_item():
    entry = drafting_engine._material_entry({"materialText": "  구글 AI 개요 내용  "})
    assert entry == {"vocId": "pasted-material", "body": "구글 AI 개요 내용", "sourceLabel": "user-input"}
    assert drafting_engine._material_entry({}) is None
    assert drafting_engine._material_entry({"materialText": "   "}) is None


def test_pasted_material_is_capped_at_8000_chars():
    with pytest.raises(MablopError):
        drafting_engine._material_entry({"materialText": "가" * 8001})
    with pytest.raises(MablopError):
        drafting_engine._material_entry({"materialText": 123})
"""Handler-level research integration: auth, Grant, validation, partial, budget, cache, isolation.

Uses moto S3 and real Session_Tokens. The collector (handler._research_fetch) and the
Bedrock provider (bedrock.text_generate) are replaced, so no network call is made.
Covers Requirements 1.4, 2.3, 4.3, 5.5, 7.5, 10.1-10.4, 13.5.
"""
import importlib
import json
from types import SimpleNamespace

import pytest

ADMIN_ACTOR = SimpleNamespace(user_id="u_0123456789abcdef")
SUMMARY_JSON = json.dumps(
    {
        "facts": [{"text": "사실 문장", "sourceIds": ["src_01"]}],
        "generatedInterpretation": [{"text": "해석"}],
        "unverified": [],
    },
    ensure_ascii=False,
)
KEYWORD = "제주 웨딩 스냅 부케"
LINKS = [
    {"url": "https://one.example/a"},
    {"url": "https://two.example/b"},
    {"url": "https://three.example/c"},
]


@pytest.fixture
def h(s3_bucket, signing_secret):
    import s3store
    import access_controller

    importlib.reload(s3store)
    importlib.reload(access_controller)
    import handler

    importlib.reload(handler)
    access_controller._window = None
    return handler


@pytest.fixture
def calls(h, monkeypatch):
    """Replace the collector and Bedrock; record every invocation."""
    import bedrock

    record = {"fetch": [], "bedrock": [], "fail_urls": set()}

    def fake_fetch(url):
        record["fetch"].append(url)
        if url in record["fail_urls"]:
            raise RuntimeError("blocked")
        return f"본문 내용 {url} " * 5

    def fake_generate(prompt, model, max_tokens=0, **_kw):
        record["bedrock"].append(prompt)
        return SUMMARY_JSON

    monkeypatch.setattr(h, "_research_fetch", fake_fetch)
    monkeypatch.setattr(h.research, "default_search_provider", lambda: None)
    monkeypatch.setattr(bedrock, "text_generate", fake_generate)
    return record


def _event(action, token, **fields):
    headers = {"X-Mablop-Token": token} if token is not None else {}
    return {"headers": headers, "body": json.dumps({"action": action, **fields}, ensure_ascii=False)}


def _body(response):
    return json.loads(response["body"])


def _assert_error(response, status, code):
    assert response["statusCode"] == status
    assert _body(response)["error"]["code"] == code


def _member_with_grant(h, create_user, make_session_token, login_id, user_id=None):
    import grant_service

    kwargs = {"user_id": user_id} if user_id else {}
    user = create_user(login_id, **kwargs)
    gid = grant_service.request({}, SimpleNamespace(user_id=user["userId"]))["grant"]["id"]
    grant_service.admin_approve(
        {"userId": user["userId"], "grantId": gid, "expiresInDays": 30}, ADMIN_ACTOR
    )
    return make_session_token(user["userId"]), gid


def _run(h, token, gid, **fields):
    fields.setdefault("links", LINKS)
    fields.setdefault("keyword", KEYWORD)
    return h.handler(_event("run-research", token, grantId=gid, **fields))


# --- authentication / authorization (Req 10.1-10.4) ---------------------------------


def test_run_research_without_token_returns_401(h, calls):
    _assert_error(h.handler(_event("run-research", None, links=LINKS, keyword=KEYWORD)), 401, "unauthorized")
    assert calls["fetch"] == [] and calls["bedrock"] == []


def test_member_without_grant_gets_403_and_no_work_is_done(h, calls, create_user, make_session_token):
    token = make_session_token(create_user("member1")["userId"])
    _assert_error(h.handler(_event("run-research", token, links=LINKS, keyword=KEYWORD)), 403, "forbidden")
    assert calls["fetch"] == [] and calls["bedrock"] == []


def test_member_cannot_call_admin_voc_actions(h, calls, create_user, make_session_token):
    token = make_session_token(create_user("member1")["userId"])
    for action in ("admin-list-voc", "admin-update-voc-status"):
        _assert_error(h.handler(_event(action, token)), 403, "forbidden")


# --- validation (Req 1.4, 2.3) -------------------------------------------------------


@pytest.mark.parametrize("count", [6])
def test_more_than_five_links_is_validation_error_without_collection(
    h, calls, create_user, make_session_token, count
):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    links = [{"url": f"https://site{i}.example/p"} for i in range(count)]

    _assert_error(_run(h, token, gid, links=links), 400, "validation_error")
    assert calls["fetch"] == [] and calls["bedrock"] == []


# --- success / partial (Req 4.3, 5.5) ------------------------------------------------


def test_successful_run_collects_every_link_and_summarizes_once(h, calls, create_user, make_session_token):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")

    response = _run(h, token, gid)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["status"] == "completed"
    assert body["cacheHit"] is False
    assert len(calls["fetch"]) == 3 and len(calls["bedrock"]) == 1
    assert [s["status"] for s in body["sources"]] == ["succeeded"] * 3


def test_one_failed_link_yields_partial_and_keeps_failed_source(h, calls, create_user, make_session_token):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    calls["fail_urls"].add("https://two.example/b")

    response = _run(h, token, gid)

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["status"] == "partial"
    statuses = sorted(s["status"] for s in body["sources"])
    assert statuses == ["failed", "succeeded", "succeeded"]
    failed = next(s for s in body["sources"] if s["status"] == "failed")
    assert failed["normalizedUrl"].startswith("https://two.example")
    assert body["summary"]["partial"] is True
    # A partial run is not cached.
    assert _body(_run(h, token, gid))["cacheHit"] is False


# --- budget (Req 7.5) ----------------------------------------------------------------


def test_zero_request_budget_returns_budget_exhausted_with_controls(h, calls, create_user, make_session_token):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")

    response = _run(h, token, gid, budget={"maxRequests": 0})

    assert response["statusCode"] == 200
    body = _body(response)
    assert body["status"] == "budget-exhausted"
    assert "maxRequests_reached" in body["controls"]
    assert body["budget"] == {"maxRequests": 0}
    assert calls["fetch"] == [] and calls["bedrock"] == []
    assert body["cacheHit"] is False


# --- cache (Req 13.5) ----------------------------------------------------------------


def test_second_identical_request_is_cache_hit_without_refetch_or_bedrock(
    h, calls, create_user, make_session_token
):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")

    first = _body(_run(h, token, gid))
    fetches, bedrocks = len(calls["fetch"]), len(calls["bedrock"])
    second = _body(_run(h, token, gid))

    assert first["cacheHit"] is False and second["cacheHit"] is True
    assert second["runId"] == first["runId"]
    assert len(calls["fetch"]) == fetches and len(calls["bedrock"]) == bedrocks


def test_cache_is_not_shared_between_users(
    h, calls, create_user, make_session_token, acting_user_id, other_user_id
):
    token_a, gid_a = _member_with_grant(h, create_user, make_session_token, "membera", acting_user_id)
    token_b, gid_b = _member_with_grant(h, create_user, make_session_token, "memberb", other_user_id)

    first = _body(_run(h, token_a, gid_a))
    other = _body(_run(h, token_b, gid_b))

    assert first["cacheHit"] is False and other["cacheHit"] is False
    assert other["runId"] != first["runId"]
    assert len(calls["fetch"]) == 6 and len(calls["bedrock"]) == 2


# --- isolation of reads (task 4.4: read actions need no Grant) ------------------


def test_get_and_list_research_runs_are_isolated_per_user(
    h, calls, create_user, make_session_token, acting_user_id, other_user_id
):
    token_a, gid_a = _member_with_grant(h, create_user, make_session_token, "membera", acting_user_id)
    token_b, gid_b = _member_with_grant(h, create_user, make_session_token, "memberb", other_user_id)
    run_id = _body(_run(h, token_a, gid_a))["runId"]

    own = h.handler(_event("get-research-run", token_a, runId=run_id))
    assert own["statusCode"] == 200
    assert _body(own)["run"]["runId"] == run_id

    _assert_error(h.handler(_event("get-research-run", token_b, runId=run_id)), 404, "not_found")

    listed_own = _body(h.handler(_event("list-research-runs", token_a)))
    listed_other = _body(h.handler(_event("list-research-runs", token_b)))
    assert [r["runId"] for r in listed_own["runs"]] == [run_id]
    assert listed_other["runs"] == []


# --- task 12.1: reads need no Grant; successful runs are recorded as activity ----------


def test_member_without_grant_can_read_research_runs(h, calls, create_user, make_session_token):
    token = make_session_token(create_user("member1")["userId"])
    response = h.handler(_event("list-research-runs", token))
    assert response["statusCode"] == 200
    assert _body(response)["runs"] == []


def test_successful_run_records_research_run_activity_and_survives_activity_failure(
    h, calls, create_user, make_session_token, monkeypatch
):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    run_id = _body(_run(h, token, gid))["runId"]

    activity = _body(h.handler(_event("get-activity", token)))
    events = [e for e in activity["events"] if e["type"] == "research-run"]
    assert [e["relatedRunId"] for e in events] == [run_id]

    def boom(*_a, **_k):
        raise RuntimeError("activity down")

    monkeypatch.setattr(h.activity_store, "record_event", boom)
    other = _run(h, token, gid, links=[{"url": f"https://x{i}.example/p"} for i in range(3)])
    assert other["statusCode"] == 200



# --- keyword-driven research (task 21.1 / 21.2 / 21.5) ---------------------------------

import re


def _brave_results():
    return {"results": [
        {"title": "네이버글", "url": "https://blog.naver.com/a/1", "description": "막힘"},
        {"title": "스냅 후기", "url": "https://snap.example/1", "description": "제주 웨딩 스냅 후기"},
        {"title": "부케 팁", "url": "https://bouquet.example/2", "description": "부케 고르는 법"},
    ]}


def _material_generate(calls):
    def generate(prompt, model, max_tokens=0, **_kw):
        calls["bedrock"].append((prompt, model, max_tokens))
        ids = re.findall(r"source (src_[0-9a-f]+)", prompt)
        return json.dumps({
            "overview": "제주 웨딩 스냅 정리입니다. 두 번째 문장입니다.",
            "sections": [{"heading": "부케", "points": [
                {"text": "근거 있는 내용", "sourceIds": [ids[0]]},
                {"text": "근거 없는 내용", "sourceIds": ["src_fake"]},
            ]}],
            "titleIdeas": ["제목 아이디어"],
            "keywords": ["제주", "부케"],
        }, ensure_ascii=False)
    return generate


@pytest.mark.parametrize("keyword", [None, "", "   ", "가" * 81, 5])
def test_keyword_is_required_and_bounded(h, calls, create_user, make_session_token, keyword):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    fields = {} if keyword is None else {"keyword": keyword}
    response = h.handler(_event("run-research", token, grantId=gid, links=LINKS, **fields))
    _assert_error(response, 400, "validation_error")
    assert calls["fetch"] == [] and calls["bedrock"] == []


def test_zero_links_are_allowed_with_keyword_only(h, calls, create_user, make_session_token):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    response = h.handler(_event("run-research", token, grantId=gid, keyword=KEYWORD))
    assert response["statusCode"] == 200
    body = _body(response)
    assert body["keyword"] == KEYWORD
    assert "search_provider_unavailable" in body["controls"]  # no Brave key: search skipped
    assert body["sources"] == [] and calls["bedrock"] == []


def test_naver_link_is_rejected_with_guidance(h, calls, create_user, make_session_token):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    response = _run(h, token, gid, links=[{"url": "https://blog.naver.com/x/1"}])
    _assert_error(response, 400, "validation_error")
    assert "네이버 블로그는 외부 수집이 막혀 있어" in _body(response)["error"]["message"]
    assert calls["fetch"] == []


def test_empty_body_source_is_failed_not_succeeded_and_no_summary_call(h, calls, create_user, make_session_token, monkeypatch):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    monkeypatch.setattr(h, "_research_fetch", lambda url: "")
    body = _body(_run(h, token, gid))
    assert [s["status"] for s in body["sources"]] == ["failed"] * 3
    assert all(s["errorMessage"] == "본문을 가져오지 못했어요" for s in body["sources"])
    assert body["status"] == "failed"
    assert calls["bedrock"] == []


def test_brave_results_become_sources_and_sectioned_summary(h, calls, create_user, make_session_token, monkeypatch, caplog):
    import bedrock
    import research

    secret = "BRAVE-SECRET-VALUE"
    queries = []

    def provider(query):
        queries.append(query)
        return _brave_results()

    monkeypatch.setattr(h.research, "default_search_provider", lambda: provider)
    monkeypatch.setattr(bedrock, "text_generate", _material_generate(calls))
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")

    response = _run(h, token, gid, links=[])
    body = _body(response)

    assert queries == [KEYWORD]
    assert body["status"] == "completed" and body["keyword"] == KEYWORD
    web = [s for s in body["sources"] if s["sourceType"] == "web-search"]
    assert [s["url"] for s in web] == ["https://snap.example/1", "https://bouquet.example/2"]
    assert all(s["query"] == KEYWORD and s["collectedAt"] for s in web)
    assert len(calls["fetch"]) == 2  # top non-naver results enriched via crawler
    summary = body["summary"]
    assert summary["overview"].startswith("제주 웨딩 스냅")
    (section,) = summary["sections"]
    assert [p["text"] for p in section["points"]] == ["근거 있는 내용"]
    assert section["points"][0]["sourceIds"][0] in {s["sourceId"] for s in web}
    assert [u["text"] for u in summary["unverified"]] == ["근거 없는 내용"]
    assert summary["titleIdeas"] == ["제목 아이디어"] and summary["keywords"] == ["제주", "부케"]
    assert [f["text"] for f in summary["facts"]] == ["근거 있는 내용"]  # legacy field
    assert {"제목 아이디어"} <= {g["text"] for g in summary["generatedInterpretation"]}
    prompt, model, max_tokens = calls["bedrock"][0]
    assert KEYWORD in prompt and "haiku" in model and max_tokens == 1500
    assert secret not in response["body"] and secret not in caplog.text


def test_list_research_runs_includes_keyword_and_overview_preview(h, calls, create_user, make_session_token, monkeypatch):
    import bedrock

    monkeypatch.setattr(h.research, "default_search_provider", lambda: (lambda q: _brave_results()))
    long_overview = "가" * 300
    monkeypatch.setattr(bedrock, "text_generate", lambda p, m, max_tokens=0, **k: json.dumps(
        {"overview": long_overview, "sections": [], "titleIdeas": [], "keywords": []}))
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    _run(h, token, gid, links=[])
    (item,) = _body(h.handler(_event("list-research-runs", token)))["runs"]
    assert item["keyword"] == KEYWORD
    assert item["overview"] == "가" * 120


def test_cache_key_depends_on_keyword(h, calls, create_user, make_session_token):
    token, gid = _member_with_grant(h, create_user, make_session_token, "member1")
    first = _body(_run(h, token, gid))
    same = _body(_run(h, token, gid))
    other = _body(_run(h, token, gid, keyword="부산 웨딩 스냅"))
    assert same["cacheHit"] is True and same["runId"] == first["runId"]
    assert other["cacheHit"] is False and other["runId"] != first["runId"]

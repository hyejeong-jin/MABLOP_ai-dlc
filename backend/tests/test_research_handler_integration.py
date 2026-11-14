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
    return h.handler(_event("run-research", token, grantId=gid, **fields))


# --- authentication / authorization (Req 10.1-10.4) ---------------------------------


def test_run_research_without_token_returns_401(h, calls):
    _assert_error(h.handler(_event("run-research", None, links=LINKS)), 401, "unauthorized")
    assert calls["fetch"] == [] and calls["bedrock"] == []


def test_member_without_grant_gets_403_and_no_work_is_done(h, calls, create_user, make_session_token):
    token = make_session_token(create_user("member1")["userId"])
    _assert_error(h.handler(_event("run-research", token, links=LINKS)), 403, "forbidden")
    assert calls["fetch"] == [] and calls["bedrock"] == []


def test_member_cannot_call_admin_voc_actions(h, calls, create_user, make_session_token):
    token = make_session_token(create_user("member1")["userId"])
    for action in ("admin-list-voc", "admin-update-voc-status"):
        _assert_error(h.handler(_event(action, token)), 403, "forbidden")


# --- validation (Req 1.4, 2.3) -------------------------------------------------------


@pytest.mark.parametrize("count", [2, 6])
def test_link_count_outside_three_to_five_is_validation_error_without_collection(
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

"""Single Lambda handler (Req 8.1): Access_Controller then dispatch on `action`.

YAGNI: one function, a plain dict dispatch table, stdlib json/base64. No
framework, no middleware. Access control runs first (Req 5.2); any failure
returns the common error shape (Req 6.2 keeps Bedrock behind the Lambda, so
nothing leaks). Unknown action -> 400 bad_request (Req 8.1, 8.5).
"""
import base64
import json

import access_controller
import auth_log
import auth_messages
import auth_service
import contracts
import draft_history
import activity_store
import drafting_engine
import errors
import grant_service
import image_analyzer
import research
import research_store
import crawler
import style_learner
import usage_meter
import user_scope
import voc_store
from errors import MablopError

# action -> component callable taking (body, ctx). Component bodies land in tasks 5-9;
# stubs raise NotImplementedError, which the outer guard turns into a clean 500.
FEATURE_ACTIONS = contracts.NEW_ACTIONS
DISPATCH = {
    "issue-presigned-urls": lambda body, ctx: image_analyzer.issue_presigned_urls(body.get("files", [])),
    "learn-style": lambda body, ctx: style_learner.learn_style(body),
    "analyze-images": lambda body, ctx: image_analyzer.analyze_images(body.get("images", [])),
    "generate-draft": lambda body, ctx: drafting_engine.generate_draft(body, tier=ctx.tier),
    "get-result": lambda body, ctx: _get_result(body),
    "get-learning-status": lambda body, ctx: style_learner.get_learning_status(),
    "preview-learning-sources": lambda body, ctx: style_learner.preview_learning_sources(body),
    "rebuild-from-stored": lambda body, ctx: style_learner.rebuild_from_stored(),
    # Account actions (user-auth-admin Req 17.4). No refresh action exists.
    "login": auth_service.login,
    "register": auth_service.register,
    "bootstrap-admin": auth_service.bootstrap_admin,
    "get-me": auth_service.get_me,
    "change-password": auth_service.change_password,
    "migrate-legacy-data": auth_service.migrate_legacy_data,
    "admin-list-users": auth_service.admin_list_users,
    "admin-approve-user": auth_service.admin_approve_user,
    "admin-reject-user": auth_service.admin_reject_user,
    "admin-disable-user": auth_service.admin_disable_user,
    "admin-enable-user": auth_service.admin_enable_user,
    "admin-delete-user": auth_service.admin_delete_user,
    "admin-reset-password": auth_service.admin_reset_password,
    # Bedrock usage Grants
    "grant-request": grant_service.request,
    "grant-list-mine": grant_service.list_mine,
    "admin-grant-list": grant_service.admin_list,
    "admin-grant-approve": grant_service.admin_approve,
    "admin-grant-reject": grant_service.admin_reject,
    "admin-grant-revoke": grant_service.admin_revoke,
    # Draft history
    "list-drafts": draft_history.list_drafts,
    "delete-draft": draft_history.delete_draft,
    "get-usage": usage_meter.get_usage,
    "get-learning-post": lambda body, ctx: style_learner.get_learning_post(body),
    "rename-learning-post": lambda body, ctx: style_learner.rename_learning_post(body),
    # VOC / administrator operations
    "create-voc": lambda body, ctx: _create_voc(body, ctx),
    "list-voc": lambda body, ctx: voc_store.list_voc(status=body.get("status"), limit=body.get("limit", 100), cursor=body.get("cursor")),
    "get-voc": lambda body, ctx: {"voc": voc_store.get_voc(body.get("vocId"))},
    "update-voc": lambda body, ctx: {"voc": voc_store.update_voc(body.get("vocId"), category=body.get("category"), title=body.get("title"), body=body.get("body"))},
    "archive-voc": lambda body, ctx: {"voc": voc_store.archive_voc(body.get("vocId"))},
    "admin-list-voc": lambda body, ctx: {
        **voc_store.admin_list_voc(user_id=body.get("userId"), status=body.get("status"),
                                   from_date=body.get("from"), to_date=body.get("to"),
                                   limit=body.get("limit", 100), cursor=body.get("cursor")),
        "accessScope": {"kind": "admin", "requestedBy": ctx.user_id, "userId": body.get("userId")},
    },
    "admin-update-voc-status": lambda body, ctx: {"voc": voc_store.admin_update_voc_status(
        body.get("userId"), body.get("vocId"), body.get("status"), actor_id=ctx.user_id
    )},
    "admin-reply": lambda body, ctx: {"voc": voc_store.admin_reply_voc(
        body.get("ownerId"), body.get("vocId"), body.get("body"),
        actor_login_id=(ctx.user or {}).get("loginId"),
    )},
    "admin-get-voc": lambda body, ctx: {"voc": voc_store.admin_get_voc(body.get("ownerId"), body.get("vocId"))},
    "get-activity": lambda body, ctx: activity_store.get_activity(body, ctx),
    # Research actions execute only inside the verified user scope. The browser
    # reaches these through postJson; S3 and the collector remain server-side.
    "run-research": lambda body, ctx: _run_research(body),
    "get-research-run": lambda body, ctx: _get_research_run(body),
    "list-research-runs": lambda body, ctx: _list_research_runs(body),
}

# The twenty actions added by user-auth-admin: an unexpected exception there is
# reported with a generic message only (Req 6.6, 22.11).
AUTH_SCOPE_ACTIONS = frozenset(
    {
        "login",
        "register",
        "bootstrap-admin",
        "get-me",
        "change-password",
        "migrate-legacy-data",
        "admin-list-users",
        "admin-approve-user",
        "admin-reject-user",
        "admin-disable-user",
        "admin-enable-user",
        "admin-delete-user",
        "admin-reset-password",
        "grant-request",
        "grant-list-mine",
        "admin-grant-list",
        "admin-grant-approve",
        "admin-grant-reject",
        "admin-grant-revoke",
        "list-drafts",
        "delete-draft",
        "get-usage",
        "get-learning-post",
        "rename-learning-post",
        "run-research",
        "get-research-run",
        "list-research-runs",
        "create-voc",
        "list-voc",
        "get-voc",
        "update-voc",
        "archive-voc",
        "admin-list-voc",
        "admin-update-voc-status",
        "admin-reply",
        "admin-get-voc",
        "get-activity",
    }
)


def _research_fetch(url: str) -> str:
    """Fetch and reduce one public source on the backend only."""
    return crawler.extract_body(crawler.crawl(url))


def _research_model() -> str:
    # Low-cost Claude Haiku 4.5 (env MABLOP_LITE_MODEL), same id the lite tier uses.
    return drafting_engine._lite_model()


def _create_voc(body: dict, ctx) -> dict:
    # The administrator is the inquiry recipient, so that account cannot post.
    if ctx.role == "admin":
        raise MablopError("forbidden", "관리자 계정은 의견을 등록할 수 없습니다.")
    return {"voc": voc_store.create_voc(body.get("category"), body.get("title"), body.get("body"))}


def _collect_ai_overview(keyword: str):
    """Google AI Overview for the keyword as one 'google-ai-overview' source (SerpApi, 23.1).

    Returns (source or None, controls). A missing key, a query without an overview, or a failed
    request each add one control; none of them fails the run.
    """
    import urllib.parse

    import serp_ai_overview
    from contracts import iso_ms

    try:
        overview, control = serp_ai_overview.fetch_ai_overview(keyword)
    except Exception:  # noqa: BLE001 - the reason stays private; the run continues without it
        return None, ["serp_request_failed"]
    if overview is None:
        return None, [control]
    url = "https://www.google.com/search?q=" + urllib.parse.quote(keyword)
    source = {
        "sourceId": "ai_overview",
        "source": "ai_overview",
        "sourceType": "google-ai-overview",
        "query": keyword,
        "url": url,
        "normalizedUrl": url,
        "title": f"구글 AI 개요: {keyword}",
        "collectedAt": iso_ms(),
        "status": "succeeded",
        "excerpt": overview["text"],
        "errorCode": None,
        "errorMessage": None,
    }
    return source, []

def _run_research(body: dict) -> dict:
    """Collect the required primary links and persist the basic run contract."""
    from contracts import iso_ms, new_id

    keyword = body.get("keyword")
    if not isinstance(keyword, str) or not 1 <= len(keyword.strip()) <= research.MAX_KEYWORD_CHARS:
        raise MablopError("validation_error", "keyword must be a string of 1 to 80 characters")
    keyword = keyword.strip()
    links = body.get("links")
    if links is None:
        links = []
    if not isinstance(links, list):
        raise MablopError("validation_error", "links must be an array")
    # The keyword drives one Brave web search; any legacy `search` field is ignored.
    search_setting = {"enabled": True, "queries": [keyword]}
    try:
        budget = body.get("budget") if isinstance(body.get("budget"), dict) else {}
        max_search_calls = budget.get("maxSearchCalls", 1)
        research.validate_primary_links(links)  # cardinality/URL errors before any cache or fetch work
        summary_settings = body.get("summary") if isinstance(body.get("summary"), dict) else {}
        cache_key = research.build_cache_key(
            links, {**summary_settings, "search": search_setting}, budget, keyword=keyword
        )
        cached = research.lookup_cache(cache_key, research_store.get_cache)
        if cached is not None:
            try:
                run = research_store.get_research_run(cached["runId"])
                summary = research_store.get_research_summary(run["summaryId"])
            except MablopError:
                run = None
            if run is not None:
                usage = research.merge_research_usage(run.get("usage"), cache_hit=True)
                usage_meter.record_research(usage, True)
                return {
                    "runId": run["runId"],
                    "status": run["status"],
                    "summary": summary,
                    "sources": research_store.list_info_sources(run["runId"]),
                    "usage": usage,
                    "budget": body.get("budget") or {},
                    "cacheHit": True,
                    "cacheExpiresAt": cached["expiresAt"],
                    "controls": run.get("controls", []),
                    "keyword": run.get("keyword", keyword),
                }
        collected = research.run_research(
            links,
            _research_fetch,
            search=search_setting,
            search_provider=research.default_search_provider(),
            max_search_calls=max_search_calls,
            budget=budget,
            enrich_top=3,
        )
    except (research.ResearchValidationError, research.URLValidationError) as exc:
        raise MablopError("validation_error", str(exc)) from exc

    overview_source, overview_controls = _collect_ai_overview(keyword)
    collected = {**collected, "sources": list(collected["sources"]) + ([overview_source] if overview_source else []), "controls": list(collected["controls"]) + overview_controls}

    run_id = new_id("run")
    summary_id = new_id("summary")
    sources = []
    remapped = []
    primary_ids = []
    search_ids = []
    for item in collected["sources"]:
        source_id = new_id("src")
        source = dict(item)
        source["sourceId"] = source_id
        source["runId"] = run_id
        source["title"] = source.get("title") or source.get("normalizedUrl") or source.get("query") or "검색 결과"
        normalized_url = source.get("normalizedUrl")
        source["domain"] = normalized_url.split("/", 3)[2] if isinstance(normalized_url, str) else None
        source["sourceLabel"] = "fact"
        saved = research_store.save_info_source(source)
        sources.append(saved)
        remapped.append(source)
        if source.get("sourceType") in ("web-search", "google-ai-overview"):
            search_ids.append(source_id)
        else:
            primary_ids.append(source_id)

    # One budgeted summary call; skipped when no source was collected.
    evaluator = research.CostBudgetEvaluator(budget)
    for key, value in collected["usage"].items():
        evaluator.usage[key] = value
    evaluator.controls.extend(c for c in collected["controls"] if c.endswith("_reached"))
    import bedrock

    summarized, info = research.summarize_sources(
        remapped,
        lambda prompt, tokens: bedrock.text_generate(prompt, _research_model(), max_tokens=tokens),
        evaluator=evaluator,
        keyword=keyword,
    )
    controls = list(collected["controls"])
    controls.extend(c for c in info["controls"] if c not in controls)
    has_success = any(s["status"] == "succeeded" for s in remapped)
    summary_ok = not (has_success and summarized["partial"])
    partial = collected["status"] == "partial" or not summary_ok
    omitted = summarized["omitted"] if not summary_ok else collected["summary"].get("omitted")
    summary = {
        "summaryId": summary_id,
        "runId": run_id,
        "keyword": keyword,
        "overview": summarized.get("overview", ""),
        "sections": summarized.get("sections", []),
        "titleIdeas": summarized.get("titleIdeas", []),
        "keywords": summarized.get("keywords", []),
        "facts": summarized["facts"],
        "generatedInterpretation": summarized["generatedInterpretation"],
        "unverified": summarized["unverified"],
        "sourceIds": [source["sourceId"] for source in sources if source["status"] == "succeeded"],
        "partial": partial,
        "omitted": omitted,
    }
    saved_summary = research_store.save_research_summary(summary)
    run = {
        "runId": run_id,
        "status": collected["status"],
        "keyword": keyword,
        "overview": (summarized.get("overview") or "")[:120],
        "primarySourceIds": primary_ids,
        "searchSourceIds": search_ids,
        "summaryId": summary_id,
        "requestedAt": iso_ms(),
        "completedAt": iso_ms(),
        "usage": research.merge_research_usage(
            {
                "requests": collected["usage"].get("requests", 0) + (1 if info["called"] else 0),
                "inputChars": collected["usage"].get("inputChars", 0) + info["inputChars"],
                "outputTokens": info["outputTokens"],
                "searchCalls": collected["searchCalls"],
            },
            cache_hit=False,
        ),
        "controls": controls,
    }
    cache_expires_at = None
    if collected["status"] == "completed" and summary_ok:
        entry = research.build_cache_entry(
            cache_key,
            run_id,
            {
                source["normalizedUrl"]: research.source_fingerprint(source.get("excerpt"))
                for source in sources
                if source.get("status") == "succeeded" and source.get("normalizedUrl")
            },
        )
        cache_expires_at = entry["expiresAt"]
        run["cacheKey"] = cache_key
        run["cacheExpiresAt"] = cache_expires_at
    saved_run = research_store.save_research_run(run)
    if cache_expires_at is not None:
        research_store.save_cache(cache_key, entry)
    usage_meter.record_research(saved_run["usage"], False)
    if saved_run["status"] != "failed":
        try:
            # Activity is best-effort: a failure here must never break the saved run.
            activity_store.record_event(
                "research-run", related_run_id=run_id, metadata={"status": saved_run["status"]}
            )
        except Exception:  # noqa: BLE001
            pass
    return {
        "runId": run_id,
        "status": saved_run["status"],
        "summary": saved_summary,
        "sources": sources,
        "usage": saved_run["usage"],
        "budget": body.get("budget") or {},
        "cacheHit": False,
        "cacheExpiresAt": cache_expires_at,
        "controls": saved_run["controls"],
        "keyword": keyword,
    }


def _get_research_run(body: dict) -> dict:
    from contracts import validate_id

    run_id = validate_id(body.get("runId"), "run")
    run = research_store.get_research_run(run_id)
    summary = research_store.get_research_summary(run["summaryId"])
    return {"run": run, "summary": summary, "sources": research_store.list_info_sources(run_id)}


def _list_research_runs(body: dict) -> dict:
    from contracts import pagination

    page = pagination(body)
    return research_store.list_research_runs(limit=page["limit"], cursor=page["cursor"])


def _get_result(body: dict):
    """get-result routes by kind: draft -> Drafting_Engine retrieval, else Style_Learner.

    draft kind returns the stored Markdown for a draft id (task 9.8); other
    kinds fall through to the Style_Learner run-status retrieval (task 6.10).
    """
    kind = body.get("kind")
    if kind == "draft":
        return drafting_engine.get_result(body)
    return style_learner.learn_style(body)


def _raw_body(event: dict) -> str:
    """Decode the Function URL v2 body, honoring isBase64Encoded."""
    body = event.get("body")
    if body is None:
        return ""
    if event.get("isBase64Encoded"):
        return base64.b64decode(body).decode("utf-8")
    return body


def _generic_failure(err: Exception, event: str, user_id=None) -> dict:
    """internal_error with the generic message; only the exception class name is logged (Req 6.6)."""
    auth_log.log(event, f"error:{type(err).__name__}", user_id)
    return errors.error_response(MablopError("internal_error", auth_messages.INTERNAL))


def handler(event: dict, context=None) -> dict:
    """Authenticate, parse, look up the action, check the Grant, then dispatch in the user's scope."""
    event = event or {}
    headers = event.get("headers") or {}
    action = None
    user_id = None
    # Exceptions from these early stages always get the generic message.
    generic_stage = True
    try:
        raw = _raw_body(event)
        ctx = access_controller.check_request(headers, raw)
        user_id = ctx.user_id
        generic_stage = False
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise MablopError("bad_request", "body must be a JSON object")
        action = body.get("action")
        fn = DISPATCH.get(action)
        if fn is None:
            raise MablopError("bad_request", f"unknown action: {action!r}")
        generic_stage = True  # Grant lookup is storage I/O; keep its failures generic.
        ctx = access_controller.authorize(ctx, body)  # Grant gate + server-side draft tier
        generic_stage = False
        if ctx.user_id:
            with user_scope.bind(ctx.user_id):
                result = fn(body, ctx)
        else:
            result = fn(body, ctx)
        return errors.response(200, result)
    except MablopError as err:
        return errors.error_response(err)
    except Exception as err:  # noqa: BLE001 - no stack trace leaks to client
        if generic_stage or action in AUTH_SCOPE_ACTIONS:
            return _generic_failure(err, action if isinstance(action, str) else "request", user_id)
        return errors.error_response(err)

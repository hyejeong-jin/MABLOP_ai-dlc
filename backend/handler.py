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
import draft_history
import drafting_engine
import errors
import grant_service
import image_analyzer
import style_learner
import usage_meter
import user_scope
from errors import MablopError

# action -> component callable taking (body, ctx). Component bodies land in tasks 5-9;
# stubs raise NotImplementedError, which the outer guard turns into a clean 500.
DISPATCH = {
    "issue-presigned-urls": lambda body, ctx: image_analyzer.issue_presigned_urls(body.get("files", [])),
    "learn-style": lambda body, ctx: style_learner.learn_style(body),
    "analyze-images": lambda body, ctx: image_analyzer.analyze_images(body.get("images", [])),
    "generate-draft": lambda body, ctx: drafting_engine.generate_draft(body),
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
    }
)


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
        access_controller.check_grant(ctx, body)
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

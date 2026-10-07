"""Single Lambda handler (Req 8.1): Access_Controller then dispatch on `action`.

YAGNI: one function, a plain dict dispatch table, stdlib json/base64. No
framework, no middleware. Access control runs first (Req 5.2); any failure
returns the common error shape (Req 6.2 keeps Bedrock behind the Lambda, so
nothing leaks). Unknown action -> 400 bad_request (Req 8.1, 8.5).
"""
import base64
import json

import access_controller
import drafting_engine
import errors
import image_analyzer
import style_learner
from errors import MablopError

# action -> component callable. Component bodies land in tasks 5-9; stubs raise
# NotImplementedError, which the outer guard turns into a clean 500.
DISPATCH = {
    "issue-presigned-urls": lambda body: image_analyzer.issue_presigned_urls(body.get("files", [])),
    "learn-style": lambda body: style_learner.learn_style(body),
    "analyze-images": lambda body: image_analyzer.analyze_images(body.get("images", [])),
    "generate-draft": lambda body: drafting_engine.generate_draft(body),
    "get-result": lambda body: _get_result(body),
}


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


def handler(event: dict, context=None) -> dict:
    """Access control first, then dispatch on `action`; common error shape on fail."""
    event = event or {}
    headers = event.get("headers") or {}
    raw = _raw_body(event)
    try:
        # Access_Controller gates every request before any work (Req 5.2).
        access_controller.check_request(headers, raw)
        body = json.loads(raw) if raw else {}
        if not isinstance(body, dict):
            raise MablopError("bad_request", "body must be a JSON object")
        action = body.get("action")
        fn = DISPATCH.get(action)
        if fn is None:
            raise MablopError("bad_request", f"unknown action: {action!r}")
        result = fn(body)
        return errors.response(200, result)
    except MablopError as err:
        return errors.error_response(err)
    except Exception as err:  # noqa: BLE001 - no stack trace leaks to client
        return errors.error_response(err)

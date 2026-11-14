"""Backend contracts for the Voice Box/research action boundary.

Task 1.3: the handler must authenticate every new action, apply the admin role
only to administrator actions, apply the Bedrock Grant gate to research, and
bind user-owned work to the verified Session_Token subject only.
"""
import importlib
import json

import pytest

import contracts

ADMIN_MESSAGE = "관리자 권한이 필요합니다."
GRANT_MESSAGE = "Bedrock 사용 권한이 필요합니다. 마이페이지에서 신청하세요."
UNAUTHENTICATED_MESSAGE = "로그인이 필요합니다. 다시 로그인하세요."

PERSONAL_ACTIONS = frozenset(
    {
        "create-voc",
        "list-voc",
        "get-voc",
        "update-voc",
        "archive-voc",
        "get-research-run",
        "list-research-runs",
        "get-activity",
    }
)
ADMIN_ACTIONS = frozenset({"admin-list-voc", "admin-update-voc-status", "admin-reply", "admin-get-voc"})
GRANT_ACTIONS = frozenset({"run-research"})


@pytest.fixture
def h(s3_bucket, signing_secret):
    """Reload the dispatch boundary against the moto-backed test stores."""
    import s3store

    importlib.reload(s3store)
    import access_controller

    importlib.reload(access_controller)
    import handler

    importlib.reload(handler)
    return handler


def _event(body, token=None):
    headers = {}
    if token is not None:
        headers["X-Mablop-Token"] = token
    return {"headers": headers, "body": json.dumps(body)}


def _body(response):
    return json.loads(response["body"])


def test_new_action_contract_is_registered_across_contract_handler_and_access_controller(h):
    """Every contract action has a dispatch entry and auth-scope treatment."""
    import access_controller

    assert contracts.NEW_ACTIONS <= set(h.DISPATCH)
    assert contracts.NEW_ACTIONS <= h.AUTH_SCOPE_ACTIONS
    assert ADMIN_ACTIONS <= access_controller.ADMIN_ONLY_ACTIONS
    assert GRANT_ACTIONS <= access_controller.BEDROCK_ACTIONS
    assert PERSONAL_ACTIONS.isdisjoint(access_controller.ADMIN_ONLY_ACTIONS)


@pytest.mark.parametrize("action", sorted(PERSONAL_ACTIONS | ADMIN_ACTIONS | GRANT_ACTIONS))
def test_new_actions_without_session_token_return_the_same_401(h, action):
    response = h.handler(_event({"action": action}))

    assert response["statusCode"] == 401
    assert _body(response)["error"] == {
        "code": "unauthorized",
        "message": UNAUTHENTICATED_MESSAGE,
    }


def test_member_cannot_call_new_admin_actions_even_with_another_user_id(
    h, create_user, make_session_token, other_user_id
):
    member = create_user("member1")
    response = h.handler(
        _event(
            {
                "action": "admin-list-voc",
                "userId": other_user_id,
                "loginId": "admin1",
                "s3Key": f"users/{other_user_id}/metadata/voc-index.json",
            },
            make_session_token(member["userId"]),
        )
    )

    assert response["statusCode"] == 403
    assert _body(response)["error"] == {
        "code": "forbidden",
        "message": ADMIN_MESSAGE,
    }


def test_member_research_requires_a_grant_before_dispatch(h, create_user, make_session_token):
    member = create_user("member1")
    response = h.handler(
        _event(
            {
                "action": "run-research",
                "links": [{"url": "https://one.example"}] * 3,
                "grantId": "g_0000000000000000",
            },
            make_session_token(member["userId"]),
        )
    )

    assert response["statusCode"] == 403
    assert _body(response)["error"] == {
        "code": "forbidden",
        "message": GRANT_MESSAGE,
    }


def test_personal_action_uses_token_user_and_ignores_request_identity(
    h, create_user, make_session_token, other_user_id, monkeypatch
):
    member = create_user("member1")
    seen = {}

    def capture(body, ctx):
        import user_scope

        seen["ctx_user_id"] = ctx.user_id
        seen["scope_user_id"] = user_scope.current_user_id()
        seen["body"] = body
        return {"ok": True}

    h.DISPATCH["create-voc"] = capture
    response = h.handler(
        _event(
            {
                "action": "create-voc",
                "body": "고객 의견",
                "userId": other_user_id,
                "loginId": "other-user",
                "s3Key": f"users/{other_user_id}/metadata/voc-index.json",
                "userPrefix": f"users/{other_user_id}/",
            },
            make_session_token(member["userId"]),
        )
    )

    assert response["statusCode"] == 200
    assert seen["ctx_user_id"] == member["userId"]
    assert seen["scope_user_id"] == member["userId"]
    assert seen["body"]["userId"] == other_user_id
    assert seen["body"]["s3Key"].startswith(f"users/{other_user_id}/")


def test_admin_action_does_not_use_request_user_id_for_role_authorization(
    h, create_user, make_session_token, other_user_id
):
    member = create_user("member1")
    response = h.handler(
        _event(
            {"action": "admin-update-voc-status", "userId": other_user_id, "vocId": "voc_" + "0" * 32, "status": "archived"},
            make_session_token(member["userId"]),
        )
    )

    assert response["statusCode"] == 403
    assert _body(response)["error"] == {
        "code": "forbidden",
        "message": ADMIN_MESSAGE,
    }

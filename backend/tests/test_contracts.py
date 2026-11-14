import pytest

import contracts
from errors import MablopError


class Context:
    def __init__(self, user_id):
        self.user_id = user_id


def test_feature_action_contract_contains_user_owned_actions():
    assert contracts.NEW_ACTIONS == (
        contracts.VOC_ACTIONS | contracts.RESEARCH_ACTIONS | contracts.ACTIVITY_ACTIONS
    )
    assert "create-voc" in contracts.NEW_ACTIONS
    assert "run-research" in contracts.NEW_ACTIONS
    assert "get-activity" in contracts.NEW_ACTIONS


def test_acting_user_uses_verified_context_not_request_identity():
    acting = contracts.acting_user(Context("u_0123456789abcdef"))
    assert acting.user_id == "u_0123456789abcdef"
    assert "userId" in contracts.IGNORED_IDENTITY_FIELDS
    assert "loginId" in contracts.IGNORED_IDENTITY_FIELDS


@pytest.mark.parametrize("ctx", [Context(None), Context(""), object()])
def test_acting_user_fails_closed_without_verified_user(ctx):
    with pytest.raises(MablopError) as exc:
        contracts.acting_user(ctx)
    assert exc.value.code == "unauthorized"


def test_common_identifier_and_timestamp_contracts_are_canonical():
    record_id = contracts.new_id("voc")
    assert contracts.validate_id(record_id, "voc") == record_id
    timestamp = contracts.iso_ms()
    assert timestamp.endswith("Z") and len(timestamp) == 24
    assert contracts.parse_iso_ms(timestamp).tzinfo is not None


def test_pagination_and_enum_validation_contract():
    assert contracts.pagination({"limit": 2, "cursor": "next"}) == {
        "limit": 2,
        "cursor": "next",
    }
    assert contracts.enum_value("confirmed", frozenset(contracts.VOC_STATUSES), "status") == "confirmed"
    with pytest.raises(MablopError) as exc:
        contracts.pagination({"limit": 0})
    assert exc.value.code == "validation_error"


def test_user_owned_key_contract_remains_relative_only():
    assert contracts.USER_DATA_RELATIVE_PREFIXES
    assert all(not prefix.startswith("users/") for prefix in contracts.USER_DATA_RELATIVE_PREFIXES)

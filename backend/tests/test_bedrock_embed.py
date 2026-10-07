"""Bedrock embedding client tests (Req 1.4). bedrock-runtime stubbed via botocore Stubber; no real calls."""
import importlib
import io
import json

import boto3
import pytest
from botocore.stub import Stubber


def _canned_response(vector):
    """Build an invoke_model stub response whose streaming body carries {"embedding": vector}."""
    payload = json.dumps({"embedding": vector}).encode("utf-8")
    return {
        "body": io.BytesIO(payload),
        "contentType": "application/json",
    }


@pytest.fixture
def bedrock(monkeypatch):
    """Reload module, stub its shared bedrock-runtime client. Yields (module, stubber, captured)."""
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")
    import bedrock as mod

    importlib.reload(mod)

    client = boto3.client("bedrock-runtime", region_name="us-east-1")
    stubber = Stubber(client)
    monkeypatch.setattr(mod, "get_client", lambda: client)
    yield mod, stubber


def test_embed_returns_parsed_float_vector(bedrock):
    mod, stubber = bedrock
    stubber.add_response("invoke_model", _canned_response([0.1, 0.2, 0.3]))
    with stubber:
        vec = mod.embed("안녕하세요")
    assert vec == [0.1, 0.2, 0.3]
    assert all(isinstance(x, float) for x in vec)
    stubber.assert_no_pending_responses()


def test_embed_sends_env_configured_model_id(bedrock):
    mod, stubber = bedrock
    stubber.add_response(
        "invoke_model",
        _canned_response([1.0]),
        expected_params={
            "modelId": "test-embed-model",
            "body": json.dumps({"inputText": "hello"}).encode("utf-8"),
            "accept": "application/json",
            "contentType": "application/json",
        },
    )
    with stubber:
        mod.embed("hello")  # expected_params match asserts the env model id + body were sent
    stubber.assert_no_pending_responses()


def test_embed_coerces_ints_to_float(bedrock):
    mod, stubber = bedrock
    stubber.add_response("invoke_model", _canned_response([1, 2, 3]))
    with stubber:
        vec = mod.embed("x")
    assert vec == [1.0, 2.0, 3.0]
    assert all(isinstance(x, float) for x in vec)


def test_get_client_region_defaults_us_east_1(monkeypatch):
    monkeypatch.delenv("MABLOP_BEDROCK_REGION", raising=False)
    import bedrock as mod

    importlib.reload(mod)
    assert mod.get_client().meta.region_name == "us-east-1"


def test_get_client_region_from_env(monkeypatch):
    monkeypatch.setenv("MABLOP_BEDROCK_REGION", "eu-west-1")
    import bedrock as mod

    importlib.reload(mod)
    assert mod.get_client().meta.region_name == "eu-west-1"

"""Style_Profile generation + storage tests (Req 1.6). bedrock.invoke monkeypatched; S3 via moto."""
import importlib
import json

import boto3
import pytest
from moto import mock_aws

BUCKET = "mablop-test"

# A canned model response: valid JSON wrapped in explanatory prose (the hard case).
_PROFILE_JSON = {
    "tone": "친근하고 다정한 구어체",
    "frequentExpressions": ["그래서 말이죠", "~했답니다"],
    "paragraphStructure": {"avgSentencesPerParagraph": 3, "usesHeadings": True, "listUsage": "occasional"},
    "sentenceLength": "short-to-medium",
    "emojiUsage": "light",
    "sampleChunkIds": ["post3#2"],
    # Model-supplied builtAt/sourcePostCount must be ignored/overwritten by us.
    "builtAt": "1999-01-01T00:00:00Z",
    "sourcePostCount": 999,
}
_WRAPPED_TEXT = f"Sure! Here is the style profile:\n```json\n{json.dumps(_PROFILE_JSON)}\n```\nHope that helps."


@pytest.fixture
def mod(monkeypatch):
    """Reload style_profile with a stubbed env model id; yield the module."""
    monkeypatch.setenv("MABLOP_STYLE_MODEL", "test-style-model")
    import style_profile

    importlib.reload(style_profile)
    return style_profile


def test_generate_profile_parses_wrapped_json_and_has_schema_keys(mod, monkeypatch):
    captured = {}

    def fake_invoke(model_id, body):
        captured["model_id"] = model_id
        captured["body"] = body
        return {"outputText": _WRAPPED_TEXT}

    monkeypatch.setattr(mod.bedrock, "invoke", fake_invoke)

    profile = mod.generate_profile(["본문 하나", "본문 둘", "본문 셋"])

    expected_keys = {
        "tone", "frequentExpressions", "paragraphStructure", "sentenceLength",
        "emojiUsage", "sampleChunkIds", "builtAt", "sourcePostCount",
    }
    assert expected_keys.issubset(profile.keys())
    assert profile["tone"] == "친근하고 다정한 구어체"
    assert captured["model_id"] == "test-style-model"


def test_generate_profile_sets_builtat_and_count_itself(mod, monkeypatch):
    monkeypatch.setattr(mod.bedrock, "invoke", lambda m, b: {"outputText": _WRAPPED_TEXT})

    profile = mod.generate_profile(["a", "b", "c", "d"])

    # We own these -- model's bogus values are overwritten.
    assert profile["sourcePostCount"] == 4
    assert profile["builtAt"] != "1999-01-01T00:00:00Z"
    assert profile["builtAt"].endswith("Z") and "T" in profile["builtAt"]


def test_generate_profile_parses_bare_json(mod, monkeypatch):
    monkeypatch.setattr(mod.bedrock, "invoke", lambda m, b: {"outputText": json.dumps(_PROFILE_JSON)})
    profile = mod.generate_profile(["x"])
    assert profile["sentenceLength"] == "short-to-medium"
    assert profile["sourcePostCount"] == 1


def test_store_profile_round_trips_via_s3(mod, monkeypatch):
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        importlib.reload(mod.s3store)

        profile = {"tone": "t", "builtAt": "2026-01-01T00:00:00Z", "sourcePostCount": 2}
        key = mod.store_profile(profile)

        assert key == "style-profile/profile.json"
        assert mod.s3store.get_json(key) == profile

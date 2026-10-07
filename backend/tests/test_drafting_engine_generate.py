"""Draft generation tests (Req 2.7-2.9, 3.6-3.7, 4.1-4.2; design steps 6-8).

moto S3 for storage; bedrock.invoke monkeypatched to canned Markdown (no real calls).
"""
import importlib
import re

import boto3
import pytest
from moto import mock_aws

BUCKET = "mablop-test"
TEXT_MODEL = "test-quality-korean"
_PH = re.compile(r"!\[img-(\d+)\]")


@pytest.fixture
def de(monkeypatch):
    """Fresh moto S3 + bucket + env; yield reloaded drafting_engine module."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_TEXT_MODEL", TEXT_MODEL)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import drafting_engine

        importlib.reload(drafting_engine)
        yield drafting_engine


def _canned(markdown):
    """Return a (captured, fake_invoke) pair; fake_invoke records model id + yields markdown."""
    captured = {}

    def fake_invoke(model_id, body):
        captured["model_id"] = model_id
        captured["body"] = body
        return {"outputText": markdown}

    return captured, fake_invoke


def _counts(markdown):
    """Map placeholder index -> occurrence count."""
    out = {}
    for m in _PH.finditer(markdown):
        out[int(m.group(1))] = out.get(int(m.group(1)), 0) + 1
    return out


def test_invokes_quality_model_and_stores_and_returns(de, monkeypatch):
    captured, fake = _canned("# 제목\n\n본문 ![img-1] 이어서 ![img-2]")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    out = de.generate_from_prompt("프롬프트", image_count=2)

    assert captured["model_id"] == TEXT_MODEL  # quality model invoked
    assert out["draftKey"] == "generated-posts/{}.md".format(out["draftId"])
    assert out["draftId"].startswith("draft-")
    # stored under generated-posts/ and matches returned markdown
    stored = boto3.client("s3").get_object(Bucket=BUCKET, Key=out["draftKey"])["Body"].read()
    assert stored.decode("utf-8") == out["markdown"]
    assert _counts(out["markdown"]) == {1: 1, 2: 1}


def test_appends_omitted_placeholders(de, monkeypatch):
    _, fake = _canned("# 제목\n\n본문만 있고 플레이스홀더 없음")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    out = de.generate_from_prompt("p", image_count=3)
    assert _counts(out["markdown"]) == {1: 1, 2: 1, 3: 1}


def test_dedupes_repeated_placeholders(de, monkeypatch):
    _, fake = _canned("![img-1] ... ![img-1] ... ![img-2] ... ![img-1]")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    out = de.generate_from_prompt("p", image_count=2)
    assert _counts(out["markdown"]) == {1: 1, 2: 1}


def test_removes_out_of_range_placeholders(de, monkeypatch):
    _, fake = _canned("![img-1] ![img-2] ![img-5] ![img-99]")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    out = de.generate_from_prompt("p", image_count=2)
    counts = _counts(out["markdown"])
    assert counts == {1: 1, 2: 1}
    assert all(n <= 2 for n in counts)  # none > K


def test_never_references_more_than_ten(de, monkeypatch):
    _, fake = _canned("no placeholders here")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    # image_count clamped to 10 even if more supplied
    out = de.generate_from_prompt("p", image_count=25)
    counts = _counts(out["markdown"])
    assert set(counts) == set(range(1, 11))
    assert len(counts) == 10
    assert all(v == 1 for v in counts.values())


def test_zero_images_yields_no_placeholders(de, monkeypatch):
    _, fake = _canned("![img-1] 본문 ![img-3]")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    out = de.generate_from_prompt("p", image_count=0)
    assert _counts(out["markdown"]) == {}


def test_delegates_to_real_modules():
    """Stubs delegate to retrieval/prompt_builder (signatures stable)."""
    import drafting_engine as mod

    assert mod.cosine_top_k.__module__ == "drafting_engine"  # wrapper exists
    # build_prompt wrapper forwards named inputs to prompt_builder
    prompt = mod.build_prompt({"tone": "warm"}, ["chunk a"],
                              {"title": "T", "outline": "O", "notes": "N"})
    assert "T" in prompt and "O" in prompt

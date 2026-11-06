"""Draft generation tests (Req 2.7-2.9, 3.6-3.7, 4.1-4.2; design steps 6-8).

moto S3 for storage; bedrock.invoke monkeypatched to canned Markdown (no real calls).
"""
import importlib
import json
import re

import boto3
import pytest
from moto import mock_aws

from conftest import ACTING_USER_ID

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
    assert out["draftKey"] == f"users/{ACTING_USER_ID}/generated-posts/{out['draftId']}.md"
    assert out["draftId"].startswith("draft-")
    # stored under the user's generated-posts/ and matches returned markdown
    stored = boto3.client("s3").get_object(Bucket=BUCKET, Key=out["draftKey"])["Body"].read()
    assert stored.decode("utf-8") == out["markdown"]
    assert _counts(out["markdown"]) == {1: 1, 2: 1}
    # no object under the legacy (unprefixed) key
    assert boto3.client("s3").list_objects_v2(Bucket=BUCKET, Prefix="generated-posts/").get("KeyCount", 0) == 0


def _index_entries():
    """Entries of the acting user's Draft_Index (users/{id}/metadata/drafts.json)."""
    body = boto3.client("s3").get_object(
        Bucket=BUCKET, Key=f"users/{ACTING_USER_ID}/metadata/drafts.json"
    )["Body"].read()
    return json.loads(body.decode("utf-8"))["drafts"]


def test_requested_title_is_recorded_in_draft_index(de, monkeypatch):
    _, fake = _canned("# 본문 제목\n\n본문")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    out = de.generate_from_prompt("p", image_count=0, title="  요청 제목  ")

    entries = _index_entries()
    assert [e["draftId"] for e in entries] == [out["draftId"]]
    assert entries[0]["title"] == "요청 제목"  # trimmed request title wins over the '#' line
    assert entries[0]["createdAt"].endswith("Z")


def test_title_falls_back_to_first_heading_without_request_title(de, monkeypatch):
    _, fake = _canned("# 본문 제목\n\n본문")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    de.generate_from_prompt("p", image_count=0)

    assert _index_entries()[0]["title"] == "본문 제목"


def test_index_failure_does_not_fail_generation_and_logs_class_name_only(de, monkeypatch, capsys):
    _, fake = _canned("# 제목\n\n비밀 본문")
    monkeypatch.setattr(de.bedrock, "invoke", fake)

    def broken_record(*args, **kwargs):
        raise RuntimeError("secret detail 비밀 본문")

    monkeypatch.setattr(de.draft_history, "record", broken_record)

    out = de.generate_from_prompt("p", image_count=0, title="제목")

    # draft is stored and returned despite the index failure (Req 22.4)
    stored = boto3.client("s3").get_object(Bucket=BUCKET, Key=out["draftKey"])["Body"].read()
    assert stored.decode("utf-8") == out["markdown"]
    logged = capsys.readouterr().out
    assert "RuntimeError" in logged
    assert "secret detail" not in logged and "비밀 본문" not in logged


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


def test_marker_shows_original_filename_without_user_prefix(de):
    prefix = f"users/{ACTING_USER_ID}/images/"
    keys = [prefix + "abc123.jpg", prefix + "def456.png", prefix + "ghi789.png"]
    de.s3store.put_json(prefix + "abc123.json", {"filename": "제주 일출.jpg", "caption": "일출"})
    de.s3store.put_json(prefix + "def456.json", {"filename": "../../evil(1).png"})  # hostile stored value
    # ghi789 has no metadata -> falls back to the object's file name only

    metas = de._load_image_meta(keys)
    names = de._image_names(keys, metas)
    assert names == {1: "제주 일출.jpg", 2: "evil1.png", 3: "ghi789.png"}
    assert de._captions_from_meta(metas) == {1: "일출"}

    md = de.normalize_placeholders("본문 ![img-1] ![img-2] ![img-3]", 3, image_names=names)
    assert "![img-1](제주 일출.jpg)" in md
    assert "![img-2](evil1.png)" in md
    assert "users/" not in md and "/images/" not in md and ".." not in md

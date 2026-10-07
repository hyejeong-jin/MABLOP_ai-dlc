"""End-to-end generate-draft + get-result flow (Req 2.1-2.9, 3.6-3.7, 4.1-4.2; Design Drafting Flow).

Integration-style: moto S3 backs storage; bedrock.embed + bedrock.invoke are
monkeypatched (no live Bedrock). Seeds a real index (index_store), a style
profile, and image caption JSON, then drives generate_draft/get_result and the
handler end-to-end.
"""
import importlib
import json
import re

import boto3
import numpy as np
import pytest
from moto import mock_aws

BUCKET = "mablop-test"
TEXT_MODEL = "test-quality-korean"
SECRET = "s3cr3t-token"
_PH = re.compile(r"!\[img-(\d+)\]")


@pytest.fixture
def env(monkeypatch):
    """Fresh moto S3 + bucket + env; reload storage/engine modules; yield reloaded drafting_engine."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_TEXT_MODEL", TEXT_MODEL)
    monkeypatch.setenv("MABLOP_EMBED_MODEL", "test-embed-model")
    monkeypatch.setenv("MABLOP_TOP_K", "2")
    monkeypatch.setenv("MABLOP_TOKEN", SECRET)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import s3store
        import retrieval
        import index_store
        import drafting_engine

        importlib.reload(s3store)
        importlib.reload(retrieval)
        importlib.reload(index_store)
        importlib.reload(drafting_engine)
        yield drafting_engine


def _seed_index():
    """Seed a 3-row float32 index with distinct rows + chunk texts."""
    import index_store

    vectors = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    meta = [
        {"chunkId": "c0", "postId": "p0", "offset": 0, "text": "스타일 예시 하나"},
        {"chunkId": "c1", "postId": "p0", "offset": 50, "text": "스타일 예시 둘"},
        {"chunkId": "c2", "postId": "p1", "offset": 0, "text": "스타일 예시 셋"},
    ]
    index_store.build_and_store_index(vectors, meta)


def _seed_profile():
    import s3store

    s3store.put_json("style-profile/profile.json", {"tone": "친근한 구어체"})


def _seed_caption(image_key, caption):
    import s3store

    s3store.put_json(image_key.rsplit(".", 1)[0] + ".json",
                     {"key": image_key, "caption": caption, "source": "user"})


def _counts(markdown):
    out = {}
    for m in _PH.finditer(markdown):
        out[int(m.group(1))] = out.get(int(m.group(1)), 0) + 1
    return out


def _patch_bedrock(de, monkeypatch, markdown):
    """Patch embed -> query vector aligned to row 0; invoke -> canned markdown. Capture the prompt."""
    captured = {}

    def fake_embed(text):
        captured["embed_input"] = text
        return [1.0, 0.0, 0.0]  # closest to seeded row 0

    def fake_invoke(model_id, body):
        captured["model_id"] = model_id
        captured["prompt"] = body.get("prompt")
        return {"outputText": markdown}

    monkeypatch.setattr(de.bedrock, "embed", fake_embed)
    monkeypatch.setattr(de.bedrock, "invoke", fake_invoke)
    return captured


def test_generate_draft_full_flow_stores_and_returns(env, monkeypatch):
    de = env
    _seed_index()
    _seed_profile()
    _seed_caption("images/img1.jpg", "카페 창가의 라떼")
    _seed_caption("images/img2.jpg", "성산일출봉 전경")

    captured = _patch_bedrock(de, monkeypatch, "# 제목\n\n본문 ![img-1] 이어서 ![img-2]")

    out = de.generate_draft({
        "title": "제주 카페 투어",
        "outline": "1. 성산 2. 애월",
        "notes": "라떼가 인상적",
        "imageKeys": ["images/img1.jpg", "images/img2.jpg"],
    })

    # quality model invoked; input text assembled from title/outline/notes
    assert captured["model_id"] == TEXT_MODEL
    assert "제주 카페 투어" in captured["embed_input"]
    assert "라떼가 인상적" in captured["embed_input"]
    # retrieved chunk texts + captions flow into the prompt
    assert "스타일 예시 하나" in captured["prompt"]
    assert "카페 창가의 라떼" in captured["prompt"]
    assert "친근한 구어체" in captured["prompt"]

    # placeholders well-formed: each supplied index exactly once (3.6, 3.7, 4.1)
    assert _counts(out["markdown"]) == {1: 1, 2: 1}
    # topK reflects retrieved chunk count (capped at MABLOP_TOP_K=2)
    assert out["topK"] == 2
    assert out["draftKey"] == "generated-posts/{}.md".format(out["draftId"])

    # stored under generated-posts/ matching the returned markdown (2.8, 4.2)
    stored = boto3.client("s3").get_object(Bucket=BUCKET, Key=out["draftKey"])["Body"].read()
    assert stored.decode("utf-8") == out["markdown"]


def test_get_result_returns_stored_draft(env, monkeypatch):
    de = env
    _seed_index()
    _patch_bedrock(de, monkeypatch, "# 제목\n\n본문")

    out = de.generate_draft({"title": "제목", "outline": "", "notes": "", "imageKeys": []})
    got = de.get_result({"kind": "draft", "id": out["draftId"]})

    assert got["status"] == "ready"
    assert got["markdown"] == out["markdown"]


def test_generate_draft_tolerates_missing_profile_and_captions(env, monkeypatch):
    de = env
    _seed_index()  # no profile, no caption json
    _patch_bedrock(de, monkeypatch, "# 제목\n\n본문 ![img-1]")

    out = de.generate_draft({"title": "제목", "imageKeys": ["images/x.jpg"]})
    assert _counts(out["markdown"]) == {1: 1}
    assert out["topK"] == 2


def test_get_result_without_id_errors(env):
    de = env
    with pytest.raises(ValueError):
        de.get_result({"kind": "draft"})


def test_handler_generate_draft_end_to_end(env, monkeypatch):
    """Handler path for action generate-draft with a valid token returns 200 + markdown."""
    de = env
    _seed_index()
    _seed_profile()
    _patch_bedrock(de, monkeypatch, "# 제목\n\n본문 ![img-1]")

    import access_controller
    import handler

    importlib.reload(access_controller)
    importlib.reload(handler)
    # handler imports its own drafting_engine ref; re-point embed/invoke on it
    monkeypatch.setattr(handler.drafting_engine.bedrock, "embed", lambda t: [1.0, 0.0, 0.0])
    monkeypatch.setattr(handler.drafting_engine.bedrock, "invoke",
                        lambda m, b: {"outputText": "# 제목\n\n본문 ![img-1]"})

    event = {
        "headers": {"X-Mablop-Token": SECRET},
        "body": json.dumps({
            "action": "generate-draft",
            "title": "제주 카페 투어",
            "outline": "성산",
            "notes": "라떼",
            "imageKeys": ["images/img1.jpg"],
        }),
    }
    resp = handler.handler(event)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert "![img-1]" in body["markdown"]
    assert body["draftId"].startswith("draft-")
    assert body["topK"] == 2

"""Vision captioning tests over moto S3 + stubbed bedrock.invoke (Req 3.3-3.5, 7.5; Properties 10, 11)."""
import importlib

import boto3
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

BUCKET = "mablop-test"


@pytest.fixture
def ia(monkeypatch):
    """Fresh moto S3 + bucket + env; stub bedrock.invoke (counts calls). Yield (module, calls)."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_VISION_MODEL", "test-vision")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import bedrock
        import image_analyzer
        import s3store

        importlib.reload(s3store)
        importlib.reload(bedrock)
        importlib.reload(image_analyzer)

        calls = {"n": 0}

        def fake_invoke(model_id, body):
            calls["n"] += 1
            return {"caption": "a generated caption"}

        monkeypatch.setattr(image_analyzer.bedrock, "invoke", fake_invoke)
        yield image_analyzer, calls


def _put_image(key, data=b"\xff\xd8\xff"):
    boto3.client("s3", region_name="us-east-1").put_object(Bucket=BUCKET, Key=key, Body=data)


def test_user_description_skips_vision_and_used_verbatim(ia):
    mod, calls = ia
    out = mod.analyze_images([{"key": "images/a.jpg", "description": "제주 성산일출봉 전경"}])
    assert calls["n"] == 0  # no model call (3.4)
    cap = out["captions"][0]
    assert cap == {"key": "images/a.jpg", "caption": "제주 성산일출봉 전경", "source": "user"}


def test_missing_description_calls_vision_once(ia):
    mod, calls = ia
    _put_image("images/b.jpg")
    out = mod.analyze_images([{"key": "images/b.jpg"}])
    assert calls["n"] == 1  # exactly one vision call (3.3)
    cap = out["captions"][0]
    assert cap["source"] == "vision"
    assert cap["caption"] == "a generated caption"


def test_metadata_stored_as_images_json(ia):
    mod, _ = ia
    _put_image("images/c.jpg")
    mod.analyze_images([{"key": "images/c.jpg", "contentType": "image/jpeg"}])
    meta = mod.s3store.get_json("images/c.json")
    assert meta["key"] == "images/c.jpg"
    assert meta["source"] == "vision"
    assert meta["contentType"] == "image/jpeg"
    assert meta["caption"] == "a generated caption"


def test_caption_truncated_to_max(ia, monkeypatch):
    mod, _ = ia
    monkeypatch.setenv("MABLOP_CAPTION_MAX_CHARS", "5")
    out = mod.analyze_images([{"key": "images/d.jpg", "description": "0123456789"}])
    assert out["captions"][0]["caption"] == "01234"


def test_mixed_batch_call_counts(ia):
    mod, calls = ia
    _put_image("images/e.jpg")
    _put_image("images/f.jpg")
    images = [
        {"key": "images/e.jpg"},
        {"key": "images/g.jpg", "description": "유저 설명"},
        {"key": "images/f.jpg"},
    ]
    out = mod.analyze_images(images)
    assert calls["n"] == 2  # one per description-less image, zero for the described one
    sources = [c["source"] for c in out["captions"]]
    assert sources == ["vision", "user", "vision"]


@settings(max_examples=100, deadline=None)
@given(
    flags=st.lists(st.booleans(), min_size=0, max_size=8),
    maxchars=st.integers(min_value=1, max_value=50),
)
def test_property_caption_counts_and_length_bound(flags, maxchars):
    """Feature: mablop-mvp, Properties 10 + 11 - vision called only when needed; captions bounded.

    Validates: Requirements 3.3, 3.4, 7.5
    """
    import os

    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        os.environ["MABLOP_BUCKET"] = BUCKET
        os.environ["MABLOP_VISION_MODEL"] = "test-vision"
        os.environ["MABLOP_CAPTION_MAX_CHARS"] = str(maxchars)
        import bedrock
        import image_analyzer
        import s3store

        importlib.reload(s3store)
        importlib.reload(bedrock)
        importlib.reload(image_analyzer)

        calls = {"n": 0}

        def fake_invoke(model_id, body):
            calls["n"] += 1
            return {"caption": "x" * 300}  # long caption to exercise truncation

        image_analyzer.bedrock.invoke = fake_invoke

        images = []
        want_vision = 0
        long_desc = "d" * 300
        for i, has_desc in enumerate(flags):
            key = f"images/p{i}.jpg"
            if has_desc:
                images.append({"key": key, "description": long_desc})
            else:
                boto3.client("s3", region_name="us-east-1").put_object(Bucket=BUCKET, Key=key, Body=b"\xff")
                images.append({"key": key})
                want_vision += 1

        out = image_analyzer.analyze_images(images)

        assert calls["n"] == want_vision  # Property 10: exactly one call per description-less image
        for cap in out["captions"]:
            assert len(cap["caption"]) <= maxchars  # Property 11: length bound

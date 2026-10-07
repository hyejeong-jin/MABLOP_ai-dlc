"""Presigned issuance tests over moto-mocked S3 (Req 3.1, 3.2; Property 12)."""
import importlib

import boto3
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from moto import mock_aws

BUCKET = "mablop-test"


@pytest.fixture
def ia(monkeypatch):
    """Fresh moto S3 + bucket + env; yield reloaded image_analyzer module."""
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import image_analyzer

        importlib.reload(image_analyzer)
        yield image_analyzer


def test_keys_scoped_under_images(ia):
    files = [{"filename": "a.jpg", "contentType": "image/jpeg", "size": 100}]
    out = ia.issue_presigned_urls(files)
    up = out["uploads"][0]
    assert up["key"].startswith("images/")
    assert up["key"].endswith(".jpg")
    assert up["method"] == "PUT"
    assert up["url"]


def test_conditions_include_contenttype_and_maxsize(ia):
    out = ia.issue_presigned_urls([{"filename": "a.png", "contentType": "image/png"}])
    cond = out["uploads"][0]["conditions"]
    assert cond["contentType"] == "image/png"
    assert cond["maxSize"] == 2 * 1024 * 1024


def test_expiry_within_configured_max(ia):
    out = ia.issue_presigned_urls([{"filename": "a.jpg", "contentType": "image/jpeg"}])
    assert out["uploads"][0]["expiresInSeconds"] <= ia._DEFAULT_EXPIRY


def test_expiry_env_capped_at_max(monkeypatch):
    monkeypatch.setenv("MABLOP_BUCKET", BUCKET)
    monkeypatch.setenv("MABLOP_PRESIGN_EXPIRY", "99999")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import image_analyzer

        importlib.reload(image_analyzer)
        out = image_analyzer.issue_presigned_urls([{"filename": "a.jpg", "contentType": "image/jpeg"}])
        assert out["uploads"][0]["expiresInSeconds"] <= image_analyzer._DEFAULT_EXPIRY


def test_ten_image_cap(ia):
    files = [{"filename": f"{i}.jpg", "contentType": "image/jpeg"} for i in range(25)]
    out = ia.issue_presigned_urls(files)
    assert len(out["uploads"]) == 10


def test_empty_files_yields_no_uploads(ia):
    assert ia.issue_presigned_urls([]) == {"uploads": []}


@settings(max_examples=100, deadline=None)
@given(
    n=st.integers(min_value=0, max_value=20),
    ct=st.sampled_from(["image/jpeg", "image/png", "image/webp", "image/gif"]),
)
def test_property_scoped_and_constrained(n, ct):
    """Feature: mablop-mvp, Property 12 - keys scoped, expiry<=max, type+size conditions.

    Validates: Requirements 3.2
    """
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        import os

        os.environ["MABLOP_BUCKET"] = BUCKET
        import image_analyzer

        importlib.reload(image_analyzer)
        files = [{"filename": f"f{i}.img", "contentType": ct} for i in range(n)]
        uploads = image_analyzer.issue_presigned_urls(files)["uploads"]
        assert len(uploads) == min(n, 10)  # 10-image cap never exceeded
        for up in uploads:
            assert up["key"].startswith("images/")  # scoped prefix
            assert up["expiresInSeconds"] <= image_analyzer._DEFAULT_EXPIRY  # bounded expiry
            assert up["conditions"]["contentType"] == ct  # content-type condition
            assert up["conditions"]["maxSize"] > 0  # max-size condition

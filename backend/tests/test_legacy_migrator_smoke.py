"""Smoke tests for legacy_migrator (task 10.1): copy, idempotence, divergence, already_bound."""
import json

import boto3

import legacy_migrator
import s3store

U1 = "u_" + "1" * 16
U2 = "u_" + "2" * 16
STATUS = {"processedPosts": 9, "chunks": 31}


def _seed(bucket):
    s3 = boto3.client("s3")
    s3.put_object(Bucket=bucket, Key="raw-posts/a.txt", Body=b"aaa")
    s3.put_object(Bucket=bucket, Key="raw-posts/ignored.png", Body=b"x")
    s3.put_object(Bucket=bucket, Key="generated-posts/d.md", Body=b"# draft")
    s3.put_object(Bucket=bucket, Key="embeddings/index.npy", Body=b"npy")
    s3.put_object(Bucket=bucket, Key="metadata/learning-status.json", Body=json.dumps(STATUS).encode())
    s3.put_object(Bucket=bucket, Key="images/i.png", Body=b"img")
    return s3


def _keys(s3, bucket, prefix):
    return sorted(o["Key"] for o in s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", []))


def test_copy_idempotent_and_original_unchanged(s3_bucket):
    s3 = _seed(s3_bucket)
    first = legacy_migrator.run(U1, now=1_700_000_000)
    assert first["status"] == "succeeded"
    assert first["copied"] == 4 and first["skipped"] == 0
    assert first["learningStatusEqual"] is True
    assert _keys(s3, s3_bucket, f"users/{U1}/") == sorted(
        [
            f"users/{U1}/raw-posts/a.txt",
            f"users/{U1}/generated-posts/d.md",
            f"users/{U1}/embeddings/index.npy",
            f"users/{U1}/metadata/learning-status.json",
            f"users/{U1}/metadata/migration.json",
        ]
    )
    second = legacy_migrator.run(U1, now=1_700_000_100)
    assert second["status"] == "succeeded"
    assert second["copied"] == 0 and second["skipped"] == 4
    assert s3.get_object(Bucket=s3_bucket, Key="raw-posts/a.txt")["Body"].read() == b"aaa"
    summary = legacy_migrator.summarize(second)
    assert summary["message"] == "기존 학습 데이터와 초안을 연결했습니다."


def test_diverged_destination_is_not_replaced(s3_bucket):
    s3 = _seed(s3_bucket)
    s3.put_object(Bucket=s3_bucket, Key=f"users/{U1}/raw-posts/a.txt", Body=b"newer-and-longer")
    result = legacy_migrator.run(U1)
    assert result["divergedKeys"] == ["raw-posts/a.txt"]
    assert result["status"] == "succeeded"
    body = s3.get_object(Bucket=s3_bucket, Key=f"users/{U1}/raw-posts/a.txt")["Body"].read()
    assert body == b"newer-and-longer"


def test_other_user_gets_already_bound_and_no_copy(s3_bucket):
    s3 = _seed(s3_bucket)
    legacy_migrator.run(U1)
    result = legacy_migrator.run(U2)
    assert result["status"] == "already_bound"
    assert _keys(s3, s3_bucket, f"users/{U2}/") == []
    summary = legacy_migrator.summarize(result)
    assert summary["message"] == "레거시 데이터는 이미 다른 사용자에게 연결되어 있습니다."
    assert summary["copied"] == 0 and summary["failedKeys"] == []

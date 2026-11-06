"""Property test: migration verification and failure reporting (Req 9.9, 9.10, 9.11, 9.19; Property 18).

A random legacy dataset is seeded into moto S3 and CopyObject is made to fail (s3_calls.fail_on) for a
random subset of the copied keys. The migration runs either directly (`legacy_migrator.run`) or through
`auth_service.admin_approve_user` for the Legacy_Owner. Results are compared with a reference model and
Korean messages with literal strings. Production code is not modified.

Feature: user-auth-admin, Property 18: Migration verification and failure reporting
"""
import json

import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
import legacy_migrator
import s3store
import style_learner
import user_scope
from conftest import TEST_BUCKET, TEST_REGION

NOW = 1_800_000_000  # epoch seconds
U1 = "u_" + "1" * 16
MIGRATION_REL = "metadata/migration.json"
STATUS_REL = "metadata/learning-status.json"
OPTIONAL_SINGLES = (
    "embeddings/index.npy",
    "embeddings/index-map.json",
    "vector-index/manifest.json",
    "style-profile/profile.json",
)
NOISE = {
    "raw-posts/ignored.png": b"x",
    "images/i.png": b"img",
    "metadata/ratelimit.json": b"{}",
    "checkpoints/run.json": b"{}",
}
MSG_SUCCEEDED = "기존 학습 데이터와 초안을 연결했습니다."
MSG_FAILED = (
    "기존 데이터 연결에 실패했습니다. 승인은 유지되었습니다. "
    "관리자 권한으로 데이터 연결을 다시 실행하세요."
)

_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# --- strategies -------------------------------------------------------------------------


@st.composite
def _scenarios(draw):
    """(files, fail_set, via, fail_migration_write): files maps relative path -> bytes."""
    body = st.binary(max_size=40)
    files = {}
    for i in draw(st.lists(st.integers(0, 5), max_size=4, unique=True)):
        files[f"raw-posts/p{i}.txt"] = draw(body)
    for i in draw(st.lists(st.integers(0, 5), max_size=3, unique=True)):
        files[f"generated-posts/d{i}.md"] = draw(body)
    for rel in draw(st.lists(st.sampled_from(OPTIONAL_SINGLES), max_size=4, unique=True)):
        files[rel] = draw(body)
    if draw(st.booleans()):
        status = {"processedPosts": draw(st.integers(0, 50)), "chunks": draw(st.integers(0, 200))}
        files[STATUS_REL] = json.dumps(status).encode()
    rels = sorted(files)
    fail_set = set(draw(st.lists(st.sampled_from(rels), unique=True))) if rels else set()
    via = draw(st.sampled_from(["run", "approve"]))
    return files, fail_set, via, draw(st.booleans())


# --- helpers ----------------------------------------------------------------------------


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _snapshot(predicate=lambda key: True) -> dict:
    client = boto3.client("s3", region_name=TEST_REGION)
    out = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            if predicate(obj["Key"]):
                out[obj["Key"]] = client.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
    return out


def _seed(files: dict) -> None:
    client = boto3.client("s3", region_name=TEST_REGION)
    for rel, data in {**NOISE, **files}.items():
        client.put_object(Bucket=TEST_BUCKET, Key=rel, Body=data)


def _is_original(key: str) -> bool:
    return not key.startswith("users/")


def _read_json(key: str):
    data = _snapshot(lambda k: k == key).get(key)
    return None if data is None else json.loads(data)


# --- the property ------------------------------------------------------------------------


@_SETTINGS
@given(scenario=_scenarios())
def test_migration_failure_is_reported_exactly_and_never_hurts_approval(
    s3_bucket, s3_calls, create_user, scenario
):
    """Validates: Requirements 9.9, 9.10, 9.11, 9.19"""
    files, fail_set, via, fail_migration_write = scenario
    _wipe_bucket()
    s3_calls.clear_failures()
    _seed(files)
    originals = _snapshot(_is_original)
    if via == "approve":
        record = create_user("jhe8888", status="pending")
        target = record["userId"]
    else:
        target = U1
    prefix = f"users/{target}/"
    for rel in fail_set:
        s3_calls.fail_on("CopyObject", key=prefix + rel)
    if fail_migration_write:
        s3_calls.fail_on("PutObject", key=prefix + MIGRATION_REL)
    s3_calls.clear()

    expected_failed = sorted(fail_set) + ([MIGRATION_REL] if fail_migration_write else [])
    expect_failure = bool(expected_failed)
    expected_status = "failed" if expect_failure else "succeeded"

    if via == "run":
        result = legacy_migrator.run(target, now=NOW)
        binding = None
    else:
        response = auth_service.admin_approve_user({"userId": target}, None, now=NOW)
        binding = response["legacyBinding"]
        result = None
        assert response["userId"] == target and response["status"] == "active"
        assert _read_json(prefix + "account.json")["status"] == "active"  # approval is never reverted

    stored = _read_json(prefix + MIGRATION_REL)
    if result is None:
        result = stored if not fail_migration_write else None
    if fail_migration_write:
        assert stored is None  # the failed write left no migration.json
    else:
        assert stored == result

    # status is failed iff some copy (or the migration.json write) failed; failedKeys is exact
    if via == "run":
        assert result["status"] == expected_status
        assert result["failedKeys"] == expected_failed
        assert result["divergedKeys"] == []
        assert result["copied"] == len(files) - len(fail_set)
        assert result["skipped"] == 0
        assert result["startedAt"] == result["finishedAt"] == auth_crypto.to_iso_ms(NOW * 1000)
        raw = [k for k in files if k.startswith("raw-posts/")]
        drafts = [k for k in files if k.startswith("generated-posts/")]
        assert result["rawPosts"] == {
            "source": len(raw),
            "destination": len([k for k in raw if k not in fail_set]),
        }
        assert result["generatedPosts"] == {
            "source": len(drafts),
            "destination": len([k for k in drafts if k not in fail_set]),
        }
        status_ok = STATUS_REL in files and STATUS_REL not in fail_set
        assert result["learningStatusEqual"] is (True if status_ok else None)
    else:
        assert binding == {
            "status": expected_status,
            "copied": len(files) - len(fail_set),
            "skipped": 0,
            "failedKeys": expected_failed,
            "divergedKeys": [],
            "message": MSG_FAILED if expect_failure else MSG_SUCCEEDED,
        }
    if stored is not None:
        assert stored["status"] == expected_status
        assert stored["failedKeys"] == expected_failed
        assert stored["copied"] == len(files) - len(fail_set)
        assert stored["skipped"] == 0 and stored["divergedKeys"] == []
        assert "T" in stored["startedAt"] and "T" in stored["finishedAt"]

    # destination: exactly the non-failed keys, byte-identical; nothing else leaves the legacy set
    destination = _snapshot(lambda k: k.startswith(prefix))
    copied_rels = {k[len(prefix):]: v for k, v in destination.items() if not k.endswith("account.json")}
    copied_rels.pop(MIGRATION_REL, None)
    assert copied_rels == {rel: data for rel, data in files.items() if rel not in fail_set}

    # originals are untouched and nothing was removed (Req 9.6)
    assert _snapshot(_is_original) == originals
    assert s3_calls.ops("DeleteObject", "DeleteObjects") == []
    # the binding record names the target even after a failure (a retry for the same user proceeds)
    assert _read_json(legacy_migrator.BINDING_KEY)["userId"] == target

    # a retry without failures copies exactly the missing keys and reports success
    s3_calls.clear_failures()
    retry = legacy_migrator.run(target, now=NOW + 60)
    assert retry["status"] == "succeeded" and retry["failedKeys"] == []
    assert retry["copied"] == len(fail_set)
    assert retry["skipped"] == len(files) - len(fail_set)
    assert retry["divergedKeys"] == []
    assert _snapshot(lambda k: k.startswith(prefix) and not k.endswith(("account.json", MIGRATION_REL))) == {
        prefix + rel: data for rel, data in files.items()
    }


# --- example tests -----------------------------------------------------------------------


def test_nine_posts_and_31_chunks_are_reported_after_a_successful_run(s3_bucket, create_user):
    """Validates: Requirements 9.10"""
    files = {f"raw-posts/post-{i}.txt": f"본문 {i}".encode() for i in range(9)}
    files[STATUS_REL] = json.dumps({"processedPosts": 9, "chunks": 31}).encode()
    _seed(files)
    record = create_user("jhe8888", status="pending")

    response = auth_service.admin_approve_user({"userId": record["userId"]}, None, now=NOW)

    assert response["legacyBinding"]["status"] == "succeeded"
    assert response["legacyBinding"]["message"] == MSG_SUCCEEDED
    with user_scope.bind(record["userId"]):
        status = style_learner.get_learning_status()
    assert status["processedPosts"] == 9
    assert status["chunks"] == 31
    migration = s3store.get_json(f"users/{record['userId']}/{MIGRATION_REL}")
    assert migration["rawPosts"] == {"source": 9, "destination": 9}
    assert migration["learningStatusEqual"] is True


def test_exception_in_migrator_becomes_failed_binding_without_exception_text(
    s3_bucket, create_user, monkeypatch
):
    """Validates: Requirements 9.19"""

    def boom(*_args, **_kwargs):
        raise RuntimeError("secret-internal-detail")

    monkeypatch.setattr(legacy_migrator, "run", boom)
    record = create_user("jhe8888", status="pending")

    response = auth_service.admin_approve_user({"userId": record["userId"]}, None, now=NOW)

    assert response["status"] == "active"
    assert response["legacyBinding"] == {
        "status": "failed",
        "copied": 0,
        "skipped": 0,
        "failedKeys": [],
        "divergedKeys": [],
        "message": MSG_FAILED,
    }
    assert "secret-internal-detail" not in json.dumps(response, ensure_ascii=False)
    assert s3store.get_json(f"users/{record['userId']}/account.json")["status"] == "active"


def test_failed_copy_example_reports_key_and_keeps_account_active(s3_bucket, s3_calls, create_user):
    """Validates: Requirements 9.9, 9.11"""
    _seed({"raw-posts/a.txt": b"aaa", "generated-posts/d.md": b"# d"})
    record = create_user("jhe8888", status="pending")
    s3_calls.fail_on("CopyObject", key=f"users/{record['userId']}/raw-posts/a.txt")

    response = auth_service.admin_approve_user({"userId": record["userId"]}, None, now=NOW)

    binding = response["legacyBinding"]
    assert binding["status"] == "failed"
    assert binding["failedKeys"] == ["raw-posts/a.txt"]
    assert binding["copied"] == 1
    assert binding["message"] == MSG_FAILED
    assert s3store.get_json(f"users/{record['userId']}/account.json")["status"] == "active"

"""Property test: the migration is copy-only and idempotent (Req 9.5, 9.6, 9.7, 9.8, 9.20; Property 17).

Random legacy worlds (Legacy_Keys, Legacy_Drafts, non-legacy noise, and destinations that are absent,
identical, same-size-but-different, or a different size) are written straight into moto S3. The S3
call recorder (``s3_calls``) shows that no delete request is made and that every write targets the
target user's prefix (or the binding record). Production code is not modified.

Feature: user-auth-admin, Property 17: Migration copies only Legacy_Keys and Legacy_Drafts, never
changes originals or divergent destinations, and is idempotent
"""
import json

import boto3
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import legacy_migrator
from conftest import TEST_BUCKET, TEST_REGION

TARGET = "u_" + "1" * 16
OTHER = "u_" + "2" * 16
USER_PREFIX = f"users/{TARGET}/"
MIGRATION_KEY = USER_PREFIX + legacy_migrator.MIGRATION_REL
BINDING_KEY = legacy_migrator.BINDING_KEY
LEARNING_STATUS = legacy_migrator.LEARNING_STATUS_REL

_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)

# Destination state before the run: nothing, identical object, same size with other bytes, other size.
_MODES = ("absent", "same", "same_size_other", "other_size")

_name = st.text(alphabet="abc012-_", min_size=1, max_size=4)
_legacy_rel = st.one_of(
    _name.map(lambda n: f"raw-posts/{n}.txt"),
    _name.map(lambda n: f"generated-posts/{n}.md"),
    st.sampled_from(legacy_migrator.LEGACY_SINGLE_KEYS),
)
_legacy = st.dictionaries(_legacy_rel, st.tuples(st.binary(min_size=1, max_size=12), st.sampled_from(_MODES)), max_size=10)

# Objects that are never Legacy_Keys or Legacy_Drafts: wrong suffix, images, checkpoint, rate limit.
_NOISE_FIXED = (
    "images/i.png",
    "metadata/ratelimit.json",
    "metadata/learn-checkpoint.json",
    "style-profile/other.json",
)
_noise_rel = st.one_of(
    _name.map(lambda n: f"raw-posts/{n}.png"),
    _name.map(lambda n: f"generated-posts/{n}.txt"),
    st.sampled_from(_NOISE_FIXED),
)
_noise = st.dictionaries(_noise_rel, st.binary(max_size=8), max_size=6)


def _wipe_bucket():
    boto3.resource("s3", region_name=TEST_REGION).Bucket(TEST_BUCKET).objects.all().delete()


def _snapshot():
    """{key: bytes} of every object in the bucket."""
    s3 = boto3.client("s3", region_name=TEST_REGION)
    out = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=TEST_BUCKET):
        for obj in page.get("Contents", []):
            out[obj["Key"]] = s3.get_object(Bucket=TEST_BUCKET, Key=obj["Key"])["Body"].read()
    return out


def _source_bytes(rel, data):
    """learning-status.json must be JSON for the migrator's equality check."""
    return json.dumps({"v": list(data)}).encode("utf-8") if rel == LEARNING_STATUS else data


def _destination_bytes(source, mode):
    if mode == "same":
        return source
    if mode == "same_size_other":
        return bytes([source[0] ^ 1]) + source[1:]
    if mode == "other_size":
        return source + b"x"
    return None


def _arrange(legacy, noise):
    """Write the world to S3; return ({rel: source bytes}, {rel: mode})."""
    s3 = boto3.client("s3", region_name=TEST_REGION)
    sources, modes = {}, {}
    for rel, (data, mode) in legacy.items():
        body = _source_bytes(rel, data)
        sources[rel] = body
        modes[rel] = mode
        s3.put_object(Bucket=TEST_BUCKET, Key=rel, Body=body)
        dest = _destination_bytes(body, mode)
        if dest is not None:
            s3.put_object(Bucket=TEST_BUCKET, Key=USER_PREFIX + rel, Body=dest)
    for rel, data in noise.items():
        s3.put_object(Bucket=TEST_BUCKET, Key=rel, Body=data)
    # Another user's data must stay as it is.
    s3.put_object(Bucket=TEST_BUCKET, Key=f"users/{OTHER}/raw-posts/a.txt", Body=b"other user")
    return sources, modes


def _write_keys(s3_calls):
    return {call["key"] for call in s3_calls.writes()}


@_SETTINGS
@given(legacy=_legacy, noise=_noise)
def test_migration_is_copy_only_and_idempotent(s3_bucket, s3_calls, legacy, noise):
    """Validates: Requirements 9.5, 9.6, 9.7, 9.8, 9.20"""
    _wipe_bucket()
    sources, modes = _arrange(legacy, noise)
    before = _snapshot()
    s3_calls.clear()

    first = legacy_migrator.run(TARGET, now=1_700_000_000)
    after_first = _snapshot()

    # No delete request of any kind; every write is a copy/put under the target prefix or the binding.
    assert not [op for op in s3_calls.ops() if op.startswith("Delete")]
    assert _write_keys(s3_calls) <= {BINDING_KEY, MIGRATION_KEY} | {USER_PREFIX + rel for rel in sources}
    for call in s3_calls.writes():
        if call["op"] == "CopyObject":
            assert call["source"] in sources  # only Legacy_Keys / Legacy_Drafts are copy sources
            assert call["key"] == USER_PREFIX + call["source"]

    # Existing objects (originals, divergent and equal-size destinations, other users) are unchanged.
    for key, body in before.items():
        assert after_first[key] == body

    # Only absent destinations were created, with the source bytes; noise was not copied.
    created = set(after_first) - set(before)
    expected_created = {USER_PREFIX + rel for rel, mode in modes.items() if mode == "absent"}
    assert created == expected_created | {BINDING_KEY, MIGRATION_KEY}
    for rel, mode in modes.items():
        if mode == "absent":
            assert after_first[USER_PREFIX + rel] == sources[rel]

    # Reported counts match the arranged destination states.
    diverged = sorted(rel for rel, mode in modes.items() if mode == "other_size")
    assert first["divergedKeys"] == diverged
    assert first["copied"] == sum(1 for mode in modes.values() if mode == "absent")
    assert first["skipped"] == sum(1 for mode in modes.values() if mode in ("same", "same_size_other"))

    # Second run: nothing copied, the stored objects (except migration.json) are exactly the same.
    s3_calls.clear()
    second = legacy_migrator.run(TARGET, now=1_700_000_100)
    after_second = _snapshot()

    assert "CopyObject" not in s3_calls.ops()
    assert not [op for op in s3_calls.ops() if op.startswith("Delete")]
    assert _write_keys(s3_calls) <= {BINDING_KEY, MIGRATION_KEY}
    assert {k: v for k, v in after_second.items() if k != MIGRATION_KEY} == {
        k: v for k, v in after_first.items() if k != MIGRATION_KEY
    }
    assert second["copied"] == 0
    assert second["skipped"] == first["copied"] + first["skipped"]
    assert second["divergedKeys"] == first["divergedKeys"]
    assert second["status"] == first["status"]
    assert second["failedKeys"] == first["failedKeys"]

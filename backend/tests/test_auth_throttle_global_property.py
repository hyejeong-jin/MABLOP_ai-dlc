"""Property-based tests for the global failure gate and registration limits (user-auth-admin Property 12).

Validates Req 5.6 (30 failures in 10 minutes block login and bootstrap-admin for 10 minutes),
5.7 (the sixth register request inside 60 minutes is rejected) and 5.8 (10 pending accounts close
registration). The gate and window rules run as pure functions of ``auth_throttle`` against a small
reference model; the persistence wrappers and the pending cap use a minimal moto bucket.
"""
import boto3
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_messages
import auth_service
import auth_throttle as at
from errors import MablopError

PROP_SETTINGS = settings(max_examples=100, deadline=None)
S3_PROP_SETTINGS = settings(
    max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture]
)

BASE_NOW = 1_700_000_000
LOGIN_IDS = [f"user{i:04d}" for i in range(40)] + ["", "ab", "BAD id!", None, 123]


# --- reference model (limits are the literals of Req 5.6 and 5.7, not the module constants) ---

def model_gate(events):
    """Yield (time, blocked_before, blocked_until_after) for failure times; 30 in 600 s block 600 s."""
    fails, until = [], 0
    for t in events:
        blocked = t < until
        if not blocked:
            fails = [f for f in fails if t - f < 600] + [t]
            if len(fails) >= 30:
                until = t + 600
        yield t, blocked, until


def model_register(times):
    """Yield True (admitted) or False (6th inside 3600 s) for each register time."""
    admitted = []
    for t in times:
        admitted = [a for a in admitted if t - a < 3600]
        ok = len(admitted) < 5
        if ok:
            admitted.append(t)
        yield ok


def test_constants_match_the_requirements():
    assert (at.GLOBAL_FAIL_LIMIT, at.GLOBAL_WINDOW_S, at.GLOBAL_BLOCK_S) == (30, 600, 600)
    assert (at.REGISTER_LIMIT, at.REGISTER_WINDOW_S, at.PENDING_CAP) == (5, 3600, 10)


# --- Req 5.6: global failure gate ---------------------------------------------------------------

@st.composite
def _failure_stream(draw):
    """(login_id, absolute time) pairs; a per-example gap keeps bursts dense enough to trigger blocks."""
    gap = draw(st.sampled_from([1, 4, 12, 25, 120]))
    n = draw(st.integers(0, 120))
    now, out = BASE_NOW, []
    for _ in range(n):
        jump = draw(st.integers(0, 99)) < 4
        now += draw(st.sampled_from([599, 600, 601, 1300])) if jump else draw(st.integers(0, gap))
        out.append((draw(st.sampled_from(LOGIN_IDS)), now))
    return out


@PROP_SETTINGS
@given(stream=_failure_stream())
def test_global_gate_follows_reference_model(stream):
    """**Validates: Requirements 5.6**"""
    state = at._empty_state()
    model = model_gate([t for _, t in stream])
    for (login_id, now), (_, was_blocked, until) in zip(stream, model):
        assert at.is_global_blocked(state, now) == was_blocked
        assert at.is_login_blocked(state, "user0001", now) == was_blocked
        state = at.apply_failure(state, login_id, now)
        assert at.is_global_blocked(state, now) == (now < until)
        assert state["globalBlockedUntil"] == (until if now < until else 0)
        if until:  # the block ends exactly at `until`, with no extension
            assert at.is_global_blocked(state, until - 1) == (now < until)
            assert not at.is_global_blocked(state, until)


@PROP_SETTINGS
@given(
    gaps=st.lists(st.integers(0, 19), min_size=29, max_size=29),
    extra=st.lists(st.integers(0, 599), max_size=15),
    login_id=st.sampled_from(LOGIN_IDS),
)
def test_thirtieth_failure_blocks_and_later_failures_do_not_extend_it(gaps, extra, login_id):
    """**Validates: Requirements 5.6**"""
    state, now = at._empty_state(), BASE_NOW
    state = at.apply_failure(state, login_id, now)
    for gap in gaps:  # 30 failures spread over at most 29 * 19 = 551 s
        assert not at.is_global_blocked(state, now)
        now += gap
        state = at.apply_failure(state, login_id, now)
    until = now + 600
    assert state["globalBlockedUntil"] == until
    for offset in sorted(extra):  # failures during the block change nothing
        t = now + offset
        assert at.is_global_blocked(state, t)
        state = at.apply_failure(state, login_id, t)
        assert state["globalBlockedUntil"] == until
    assert at.is_global_blocked(state, until - 1)
    assert not at.is_global_blocked(state, until)
    state = at.apply_failure(state, login_id, until)  # allowed again after release
    assert not at.is_global_blocked(state, until)
    assert state["globalBlockedUntil"] == 0
    assert state["globalFails"] == [until]


@PROP_SETTINGS
@given(gaps=st.lists(st.integers(0, 19), min_size=28, max_size=28), login_id=st.sampled_from(LOGIN_IDS))
def test_twenty_nine_failures_do_not_block(gaps, login_id):
    """**Validates: Requirements 5.6**"""
    state, now = at.apply_failure(at._empty_state(), login_id, BASE_NOW), BASE_NOW
    for gap in gaps:
        now += gap
        state = at.apply_failure(state, login_id, now)
    assert len(state["globalFails"]) == 29
    assert not at.is_global_blocked(state, now)


# --- Req 5.7: register window --------------------------------------------------------------------

@PROP_SETTINGS
@given(
    steps=st.lists(
        st.one_of(st.integers(0, 60), st.sampled_from([600, 1799, 3599, 3600, 3601, 7200])),
        max_size=40,
    )
)
def test_register_window_follows_reference_model(steps):
    """**Validates: Requirements 5.7**"""
    times, now = [], BASE_NOW
    for dt in steps:
        now += dt
        times.append(now)
    state = at._empty_state()
    for t, expected_ok in zip(times, model_register(times)):
        if expected_ok:
            state = at.apply_register(state, t)
            assert t in state["registers"]
        else:
            before = at.prune(state, t)
            with pytest.raises(MablopError) as err:
                at.apply_register(state, t)
            assert err.value.code == "rate_limited" and err.value.status == 429
            assert err.value.message == auth_messages.REGISTER_RATE
            assert at.prune(state, t) == before  # a rejected request records nothing
        assert len(state["registers"]) <= at.REGISTER_LIMIT


# --- wrappers on a moto bucket (Req 5.6, 5.7) ------------------------------------------------------

def test_guard_global_and_check_login_reject_during_block_and_allow_after(s3_bucket):
    """**Validates: Requirements 5.6**"""
    for i in range(29):
        at.record_failure("user0001" if i % 2 else "BAD id!", BASE_NOW + i)
    at.guard_global(BASE_NOW + 28)
    at.check_login("user0002", BASE_NOW + 28)
    at.record_failure(None, BASE_NOW + 29)  # the 30th failure, from any caller
    until = BASE_NOW + 29 + 600
    for check in (at.guard_global, lambda n: at.check_login("user0002", n)):
        for t in (BASE_NOW + 30, until - 1):
            with pytest.raises(MablopError) as err:
                check(t)
            assert err.value.status == 429 and err.value.message == auth_messages.LOGIN_BLOCKED
        check(until)
    at.record_failure("user0002", BASE_NOW + 100)  # during the block: no extension
    at.guard_global(until)


def test_admit_register_rejects_sixth_request_until_window_ends(s3_bucket):
    """**Validates: Requirements 5.7**"""
    for i in range(5):
        at.admit_register(BASE_NOW + i)
    for t in (BASE_NOW + 5, BASE_NOW + 3000, BASE_NOW + 3599):
        with pytest.raises(MablopError) as err:
            at.admit_register(t)
        assert err.value.status == 429 and err.value.message == auth_messages.REGISTER_RATE
    at.admit_register(BASE_NOW + 3600)  # the first admission aged out


# --- Req 5.7 + 5.8 through auth_service.register ---------------------------------------------------

@pytest.fixture
def fast_hash(monkeypatch):
    """Use 1000 PBKDF2 iterations for new accounts; the hash is not what these tests check."""
    original = auth_service.auth_crypto.hash_password
    monkeypatch.setattr(
        auth_service.auth_crypto, "hash_password", lambda pw, iterations=1000: original(pw, iterations)
    )


def _empty_bucket(bucket):
    s3 = boto3.client("s3", region_name="us-east-1")
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket):
        for obj in page.get("Contents", []):
            s3.delete_object(Bucket=bucket, Key=obj["Key"])


@S3_PROP_SETTINGS
@given(
    seed_pending=st.integers(0, 10),
    seed_other=st.integers(0, 2),
    steps=st.lists(st.sampled_from([0, 1, 30, 1800, 3599, 3600, 4000]), min_size=1, max_size=8),
)
def test_register_applies_window_then_pending_cap(
    s3_bucket, fast_hash, create_user, seed_pending, seed_other, steps
):
    """**Validates: Requirements 5.7, 5.8**"""
    _empty_bucket(s3_bucket)
    for i in range(seed_pending):
        create_user(f"pend{i:04d}", status="pending")
    for i in range(seed_other):
        create_user(f"done{i:04d}", status="active" if i % 2 else "disabled")
    times, now = [], BASE_NOW
    for dt in steps:
        now += dt
        times.append(now)

    pending = seed_pending
    for i, (t, in_window) in enumerate(zip(times, model_register(times))):
        body = {"loginId": f"newu{i:04d}", "name": "신규", "password": "correct-horse-battery"}
        if not in_window:
            expected = auth_messages.REGISTER_RATE
        elif pending >= 10:
            expected = auth_messages.REGISTER_CLOSED
        else:
            expected = None
        if expected is None:
            assert auth_service.register(body, None, t)["message"] == auth_messages.REGISTER_ACCEPTED
            pending += 1
        else:
            with pytest.raises(MablopError) as err:
                auth_service.register(body, None, t)
            assert err.value.status == 429 and err.value.message == expected

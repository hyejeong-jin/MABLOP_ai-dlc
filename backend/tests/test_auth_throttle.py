"""Unit tests for the persisted Login_Throttle wrappers (user-auth-admin Task 2.13; Req 5.2-5.10).

Concrete examples against moto S3 (``s3_bucket``). Time is injected through the ``now`` argument.
Expected messages are the Korean literals from the requirements, not the constants.
"""
import pytest

import auth_throttle as at
import s3store
from errors import MablopError

T0 = 1_700_000_000
BLOCKED_MSG = "로그인 시도가 너무 많습니다. 10분 후 다시 시도하세요."
REGISTER_RATE_MSG = "가입 신청이 너무 많습니다. 잠시 후 다시 시도하세요."

pytestmark = pytest.mark.usefixtures("s3_bucket")


def _state() -> dict:
    found = s3store.read_json_optional(s3store.auth_key("throttle.json"))
    assert found is not None, "throttle.json was not written"
    return found[0]


def _fail_n(login_id, count, start=T0, step=1):
    for i in range(count):
        at.record_failure(login_id, now=start + i * step)


def _assert_blocked(login_id, now):
    with pytest.raises(MablopError) as exc:
        at.check_login(login_id, now=now)
    assert exc.value.code == "rate_limited"
    assert exc.value.status == 429
    assert exc.value.message == BLOCKED_MSG


# --- per-loginId block (Req 5.2, 5.3) ------------------------------------------------------

def test_fresh_state_allows_login():
    at.check_login("alice01", now=T0)


def test_four_failures_do_not_block_and_fifth_does():
    _fail_n("alice01", 4)
    at.check_login("alice01", now=T0 + 10)
    at.record_failure("alice01", now=T0 + 4)
    _assert_blocked("alice01", now=T0 + 5)


def test_block_lasts_ten_minutes_from_fifth_failure():
    _fail_n("alice01", 5)  # fifth failure at T0 + 4
    _assert_blocked("alice01", now=T0 + 4 + 599)
    at.check_login("alice01", now=T0 + 4 + 600)


def test_block_is_per_login_id():
    _fail_n("alice01", 5)
    at.check_login("bobby02", now=T0 + 10)


def test_check_login_rejects_blocked_id_regardless_of_password():
    # check_login takes no password: a blocked loginId is refused before any verification.
    _fail_n("alice01", 5)
    _assert_blocked("alice01", now=T0 + 10)
    _assert_blocked("alice01", now=T0 + 20)


def test_failure_during_block_does_not_extend_it():
    _fail_n("alice01", 5)  # blocked until T0 + 4 + 600
    until = _state()["login"]["alice01"]["blockedUntil"]
    at.record_failure("alice01", now=T0 + 300)
    at.record_failure("alice01", now=T0 + 500)
    assert _state()["login"]["alice01"]["blockedUntil"] == until
    at.check_login("alice01", now=until)


def test_failures_outside_window_do_not_accumulate():
    _fail_n("alice01", 4)  # T0 .. T0+3
    at.record_failure("alice01", now=T0 + 603)  # the earlier four (last at T0 + 3) have expired
    at.check_login("alice01", now=T0 + 604)
    assert len(_state()["login"]["alice01"]["fails"]) == 1


# --- success and reset (Req 5.5) -------------------------------------------------------------

def test_record_success_resets_counter():
    _fail_n("alice01", 4)
    at.record_success("alice01", now=T0 + 10)
    assert "alice01" not in _state()["login"]
    _fail_n("alice01", 4, start=T0 + 20)
    at.check_login("alice01", now=T0 + 30)


def test_record_success_without_entry_does_not_write(s3_calls):
    at.record_success("alice01", now=T0)
    assert s3_calls.writes() == []


def test_record_success_with_invalid_login_id_does_not_write(s3_calls):
    at.record_success("BAD ID!", now=T0)
    assert s3_calls.writes() == []


def test_reset_login_clears_block():
    _fail_n("alice01", 5)
    _assert_blocked("alice01", now=T0 + 10)
    at.reset_login("alice01", now=T0 + 11)
    at.check_login("alice01", now=T0 + 12)
    assert "alice01" not in _state()["login"]


def test_reset_login_leaves_other_ids_untouched():
    _fail_n("alice01", 5)
    _fail_n("bobby02", 2)
    at.reset_login("alice01", now=T0 + 10)
    assert len(_state()["login"]["bobby02"]["fails"]) == 2


# --- malformed loginId counts only globally (Req 5.4) ---------------------------------------

@pytest.mark.parametrize("bad", ["", "ab", "UPPER", "bad id!", "x" * 33, None, 123])
def test_malformed_login_id_is_counted_globally_only(bad):
    for i in range(6):
        at.record_failure(bad, now=T0 + i)
    state = _state()
    assert state["login"] == {}
    assert len(state["globalFails"]) == 6
    at.check_login(bad, now=T0 + 10)


# --- global gate (Req 5.6) -----------------------------------------------------------------

def test_global_block_after_thirty_failures_across_ids():
    for i in range(29):
        at.record_failure(f"user{i:04d}", now=T0 + i)
    at.guard_global(now=T0 + 30)
    at.record_failure("user0029", now=T0 + 29)
    with pytest.raises(MablopError) as exc:
        at.guard_global(now=T0 + 30)
    assert exc.value.code == "rate_limited"
    assert exc.value.message == BLOCKED_MSG


def test_global_block_applies_to_login_ids_without_failures():
    for i in range(30):
        at.record_failure(f"user{i:04d}", now=T0 + i)
    _assert_blocked("neverfailed", now=T0 + 40)
    _assert_blocked(None, now=T0 + 40)


def test_global_block_expires_ten_minutes_after_thirtieth_failure():
    for i in range(30):
        at.record_failure(f"user{i:04d}", now=T0 + i)  # thirtieth at T0 + 29
    with pytest.raises(MablopError):
        at.guard_global(now=T0 + 29 + 599)
    at.guard_global(now=T0 + 29 + 600)
    at.check_login("neverfailed", now=T0 + 29 + 600)


def test_failure_during_global_block_does_not_extend_it():
    for i in range(30):
        at.record_failure(f"user{i:04d}", now=T0 + i)
    until = _state()["globalBlockedUntil"]
    at.record_failure("user0000", now=T0 + 100)
    state = _state()
    assert state["globalBlockedUntil"] == until
    assert len(state["globalFails"]) == 30


def test_guard_global_ignores_per_login_block():
    _fail_n("alice01", 5)
    at.guard_global(now=T0 + 10)


# --- registration limit (Req 5.7) ----------------------------------------------------------

def test_admit_register_allows_five_and_rejects_sixth():
    for i in range(5):
        at.admit_register(now=T0 + i)
    with pytest.raises(MablopError) as exc:
        at.admit_register(now=T0 + 10)
    assert exc.value.code == "rate_limited"
    assert exc.value.status == 429
    assert exc.value.message == REGISTER_RATE_MSG


def test_rejected_register_is_not_recorded():
    for i in range(5):
        at.admit_register(now=T0 + i)
    for _ in range(3):
        with pytest.raises(MablopError):
            at.admit_register(now=T0 + 10)
    assert len(_state()["registers"]) == 5


def test_register_allowed_again_after_sixty_minutes():
    for i in range(5):
        at.admit_register(now=T0 + i)  # oldest at T0, newest at T0 + 4
    with pytest.raises(MablopError):
        at.admit_register(now=T0 + 3599)
    at.admit_register(now=T0 + 3600)  # the T0 request has left the window
    with pytest.raises(MablopError):
        at.admit_register(now=T0 + 3600)  # window again holds 5


def test_register_limit_does_not_affect_login_gate():
    for i in range(5):
        at.admit_register(now=T0 + i)
    at.check_login("alice01", now=T0 + 10)
    at.guard_global(now=T0 + 10)


# --- persistence ---------------------------------------------------------------------------

def test_state_is_one_json_object_under_auth_prefix():
    at.record_failure("alice01", now=T0)
    found = s3store.read_json_optional("users/_auth/throttle.json")
    assert found is not None
    state = found[0]
    assert state["v"] == 1
    assert state["login"]["alice01"]["fails"] == [T0]
    assert state["login"]["alice01"]["last"] == T0
    assert state["globalFails"] == [T0]


def test_check_login_and_guard_global_are_read_only(s3_calls):
    at.check_login("alice01", now=T0)
    at.guard_global(now=T0)
    assert s3_calls.writes() == []


def test_expired_entries_are_removed_on_next_write():
    _fail_n("alice01", 2)
    at.record_failure("bobby02", now=T0 + 700)
    state = _state()
    assert "alice01" not in state["login"]
    assert list(state["login"]) == ["bobby02"]
    assert state["globalFails"] == [T0 + 700]

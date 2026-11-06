"""Property-based tests for the Login_Throttle pure rules (user-auth-admin Property 13, Req 5.9).

For any sequence of throttle writes the stored state holds no entry whose window has expired, at
most MAX_ENTRIES (200) loginId entries, and, when over the limit, evicts the entries with the
oldest ``last`` first. Only the pure rule functions of ``auth_throttle`` are exercised, so no S3
is involved.
"""
from hypothesis import given, settings
from hypothesis import strategies as st

import auth_throttle as at
from errors import MablopError

PROP_SETTINGS = settings(max_examples=100, deadline=None)

BASE_NOW = 1_700_000_000
ID_POOL = [f"user{i:04d}" for i in range(260)]
INVALID_IDS = ["", "ab", "UPPER", "bad id!", None, 123]


def _check_bounded_and_pruned(state: dict, now) -> None:
    """Assert the Property 13 invariants of a state written at `now`."""
    assert len(state["login"]) <= at.MAX_ENTRIES
    for login_id, entry in state["login"].items():
        assert all(now - t < at.LOGIN_WINDOW_S for t in entry["fails"])
        assert entry["blockedUntil"] == 0 or entry["blockedUntil"] > now
        # An entry that carries neither a live failure nor a live block must not be kept.
        assert entry["fails"] or entry["blockedUntil"] > now
    assert all(now - t < at.GLOBAL_WINDOW_S for t in state["globalFails"])
    assert state["globalBlockedUntil"] == 0 or state["globalBlockedUntil"] > now
    assert all(now - t < at.REGISTER_WINDOW_S for t in state["registers"])


# --- arbitrary stored state -> prune ----------------------------------------------------------

@st.composite
def _stored_entries(draw):
    """A list of loginId entries (possibly expired, possibly > 200) written around BASE_NOW."""
    count = draw(st.integers(min_value=0, max_value=260))
    lasts = draw(st.lists(
        st.integers(min_value=BASE_NOW - 2000, max_value=BASE_NOW),
        min_size=count, max_size=count,
    ))
    entries = {}
    for i, last in enumerate(lasts):
        fails = draw(st.lists(
            st.integers(min_value=BASE_NOW - 1500, max_value=BASE_NOW), max_size=5
        ))
        blocked = draw(st.sampled_from([0, BASE_NOW - 100, BASE_NOW, BASE_NOW + 1, BASE_NOW + 600]))
        entries[ID_POOL[i]] = {"fails": fails, "blockedUntil": blocked, "last": last}
    return entries


def _is_live(entry: dict, now) -> bool:
    return any(now - t < at.LOGIN_WINDOW_S for t in entry["fails"]) or entry["blockedUntil"] > now


@PROP_SETTINGS
@given(
    entries=_stored_entries(),
    global_fails=st.lists(st.integers(BASE_NOW - 1500, BASE_NOW), max_size=40),
    global_blocked=st.sampled_from([0, BASE_NOW - 1, BASE_NOW, BASE_NOW + 1, BASE_NOW + 600]),
    registers=st.lists(st.integers(BASE_NOW - 5000, BASE_NOW), max_size=10),
)
def test_prune_removes_expired_and_caps_with_oldest_last_evicted(
    entries, global_fails, global_blocked, registers
):
    """**Validates: Requirements 5.9**"""
    now = BASE_NOW
    state = {
        "v": 1, "login": entries, "globalFails": global_fails,
        "globalBlockedUntil": global_blocked, "registers": registers,
    }
    pruned = at.prune(state, now)
    _check_bounded_and_pruned(pruned, now)

    live = {k: v for k, v in entries.items() if _is_live(v, now)}
    kept = pruned["login"]
    assert set(kept) <= set(live)
    if len(live) <= at.MAX_ENTRIES:
        assert set(kept) == set(live)
    else:
        assert len(kept) == at.MAX_ENTRIES
        evicted = set(live) - set(kept)
        # Every kept entry is at least as recent as every evicted entry.
        assert min(live[k]["last"] for k in kept) >= max(live[k]["last"] for k in evicted)

    assert at.prune(pruned, now) == pruned  # idempotent


# --- sequences of throttle writes -------------------------------------------------------------

_login_ids = st.one_of(
    st.integers(0, 259).map(lambda i: ID_POOL[i]),
    st.sampled_from(INVALID_IDS),
)
_ops = st.lists(
    st.tuples(
        st.sampled_from(["fail", "fail", "fail", "register", "reset"]),
        _login_ids,
        st.sampled_from([0, 1, 1, 2, 5, 100, 700, 4000]),
    ),
    max_size=300,
)


@PROP_SETTINGS
@given(ops=_ops)
def test_state_stays_bounded_and_pruned_after_every_write(ops):
    """**Validates: Requirements 5.9**"""
    state = at._empty_state()
    now = BASE_NOW
    for kind, login_id, dt in ops:
        now += dt
        if kind == "fail":
            state = at.apply_failure(state, login_id, now)
        elif kind == "reset":
            state = at.apply_login_reset(state, login_id, now)
        else:
            try:
                state = at.apply_register(state, now)
            except MablopError:
                continue  # rejected request writes nothing
        _check_bounded_and_pruned(state, now)


@PROP_SETTINGS
@given(
    ids=st.lists(st.integers(0, 259), min_size=201, max_size=300),
    start=st.integers(0, 10_000),
)
def test_over_limit_failures_keep_the_200_most_recent_login_ids(ids, start):
    """**Validates: Requirements 5.9**

    One failure per second stays inside the 600 s window, so only the cap can remove entries; the
    survivors are the 200 loginIds with the latest ``last``.
    """
    distinct_by_recency = []
    for i in reversed(ids):
        if i not in distinct_by_recency:
            distinct_by_recency.append(i)
    state = at._empty_state()
    now = BASE_NOW + start
    for i in ids:
        now += 1
        state = at.apply_failure(state, ID_POOL[i], now)
        assert len(state["login"]) <= at.MAX_ENTRIES
    if len(distinct_by_recency) >= at.MAX_ENTRIES:
        expected = {ID_POOL[i] for i in distinct_by_recency[: at.MAX_ENTRIES]}
        assert set(state["login"]) == expected
    else:
        assert set(state["login"]) == {ID_POOL[i] for i in distinct_by_recency}
    _check_bounded_and_pruned(state, now)


@PROP_SETTINGS
@given(login_id=st.sampled_from(ID_POOL), gap=st.integers(0, 3000))
def test_entry_disappears_on_first_write_after_its_window_expires(login_id, gap):
    """**Validates: Requirements 5.9**"""
    state = at.apply_failure(at._empty_state(), login_id, BASE_NOW)
    later = BASE_NOW + gap
    pruned = at.prune(state, later)
    assert (login_id in pruned["login"]) == (gap < at.LOGIN_WINDOW_S)

"""Property test: the key builders accept exactly the safe inputs (Req 8.1, 8.2; Property 14).

The builders (user_key / auth_key / assert_own_image_keys) are pure string functions, so
no moto bucket is needed. An independent oracle (_valid_user_id / _safe_path) restates the
rules from design.md; the tests compare the real functions against it on generated inputs.

Feature: user-auth-admin, Property 14: Key builder accepts exactly the safe inputs
"""
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import auth_messages
import s3store
import user_scope
from errors import MablopError

_ID_RE = re.compile(r"u_[0-9a-f]{16}")


def _valid_user_id(value) -> bool:
    return isinstance(value, str) and _ID_RE.fullmatch(value) is not None


def _safe_path(rel) -> bool:
    """Oracle: non-empty, no leading '/', no '..', no backslash, no control chars."""
    if not isinstance(rel, str) or not rel or rel.startswith("/"):
        return False
    if ".." in rel or "\\" in rel:
        return False
    return not any(ord(ch) < 32 or ord(ch) == 127 for ch in rel)


# --- strategies -------------------------------------------------------------------------

_valid_ids = st.text(alphabet="0123456789abcdef", min_size=16, max_size=16).map(lambda h: "u_" + h)

_near_miss_ids = st.one_of(
    st.text(alphabet="0123456789abcdef", min_size=0, max_size=30).map(lambda h: "u_" + h),  # wrong length
    st.text(alphabet="0123456789ABCDEF", min_size=16, max_size=16).map(lambda h: "u_" + h),  # uppercase
    st.text(alphabet="0123456789abcdefg", min_size=16, max_size=16).map(lambda h: "u_" + h),  # non-hex
    _valid_ids.map(lambda i: i + "\n"),  # trailing newline must not slip through "$"
    _valid_ids.map(lambda i: " " + i),
    _valid_ids.map(lambda i: i[2:]),  # missing "u_"
    _valid_ids.map(lambda i: i.replace("u_", "U_", 1)),
    st.just("_auth"),
    st.just("u__auth"),
    st.just(""),
)
_invalid_ids = st.one_of(
    st.text(max_size=40),
    _near_miss_ids,
    st.integers(),
    st.binary(max_size=20),
).filter(lambda v: not _valid_user_id(v))

# Printable-ish text without surrogates, control characters or backslash.
_plain_alphabet = st.characters(blacklist_categories=("Cc", "Cs"), blacklist_characters="\\")
_safe_paths = st.text(alphabet=_plain_alphabet, min_size=1, max_size=40).filter(_safe_path)

_bad_tokens = st.one_of(
    st.just(".."),
    st.just("\\"),
    st.integers(min_value=0, max_value=31).map(chr),
    st.just("\x7f"),
)


@st.composite
def _unsafe_paths(draw):
    """A relative path that violates at least one rule."""
    kind = draw(st.sampled_from(["empty", "leading_slash", "token"]))
    if kind == "empty":
        return ""
    base = draw(st.text(alphabet=_plain_alphabet, max_size=20))
    if kind == "leading_slash":
        return "/" + base
    return draw(st.text(alphabet=_plain_alphabet, max_size=15)) + draw(_bad_tokens) + base


# Mixed pool, including boundary fragments, to compare against the oracle on both sides.
_fragments = st.sampled_from(["a", "b/", "..", ".", "/", "\\", "\x00", "\n", "\x7f", "한글", "x.md", "a..b", "a/./b"])
_mixed_paths = st.one_of(
    st.text(max_size=30),
    st.lists(_fragments, max_size=6).map("".join),
)

_SETTINGS = settings(max_examples=200, deadline=None)


def _raises(fn, code, message=None):
    with pytest.raises(MablopError) as info:
        fn()
    assert info.value.code == code
    if message is not None:
        assert info.value.message == message


# --- user_key ---------------------------------------------------------------------------

@_SETTINGS
@given(user_id=_valid_ids, rel=_safe_paths)
def test_user_key_returns_exact_key_for_safe_inputs(user_id, rel):
    """Validates: Requirements 8.1, 8.2"""
    assert s3store.user_key(rel, user_id) == f"users/{user_id}/{rel}"


@_SETTINGS
@given(user_id=_valid_ids, rel=_unsafe_paths())
def test_user_key_rejects_unsafe_path_with_bad_request(user_id, rel):
    """Validates: Requirements 8.1, 8.2"""
    _raises(lambda: s3store.user_key(rel, user_id), "bad_request", auth_messages.BAD_PATH)


@_SETTINGS
@given(user_id=_invalid_ids, rel=st.one_of(_safe_paths, _unsafe_paths()))
def test_user_key_rejects_malformed_user_id_with_internal_error(user_id, rel):
    """Validates: Requirements 8.1, 8.2"""
    _raises(lambda: s3store.user_key(rel, user_id), "internal_error")


@_SETTINGS
@given(user_id=st.one_of(_valid_ids, _invalid_ids), rel=_mixed_paths)
def test_user_key_matches_oracle_on_mixed_inputs(user_id, rel):
    """Validates: Requirements 8.1, 8.2"""
    if not _valid_user_id(user_id):
        _raises(lambda: s3store.user_key(rel, user_id), "internal_error")
    elif not _safe_path(rel):
        _raises(lambda: s3store.user_key(rel, user_id), "bad_request", auth_messages.BAD_PATH)
    else:
        assert s3store.user_key(rel, user_id) == f"users/{user_id}/{rel}"


@_SETTINGS
@given(user_id=_valid_ids, rel=_safe_paths)
def test_user_key_defaults_to_bound_user(user_id, rel):
    """Validates: Requirements 8.1"""
    with user_scope.bind(user_id):
        assert s3store.user_key(rel) == f"users/{user_id}/{rel}"


@_SETTINGS
@given(rel=_safe_paths)
def test_user_key_without_bound_user_fails_closed(rel):
    """Validates: Requirements 8.1"""
    _raises(lambda: s3store.user_key(rel), "internal_error")


# --- auth_key ---------------------------------------------------------------------------

@_SETTINGS
@given(rel=_mixed_paths)
def test_auth_key_applies_same_path_rules(rel):
    """Validates: Requirements 8.2"""
    if _safe_path(rel):
        assert s3store.auth_key(rel) == f"users/_auth/{rel}"
    else:
        _raises(lambda: s3store.auth_key(rel), "bad_request", auth_messages.BAD_PATH)


@_SETTINGS
@given(user_id=_valid_ids)
def test_user_id_pattern_never_matches_auth_namespace(user_id):
    """Validates: Requirements 8.1 (users/_auth/ cannot collide with a userId)"""
    assert user_id != "_auth"
    _raises(lambda: s3store.user_key("x", "_auth"), "internal_error")


# --- assert_own_image_keys --------------------------------------------------------------

@_SETTINGS
@given(user_id=_valid_ids, names=st.lists(_safe_paths, max_size=15))
def test_image_keys_accept_own_images(user_id, names):
    """Validates: Requirements 8.1, 8.2"""
    keys = [f"users/{user_id}/images/{n}" for n in names]
    with user_scope.bind(user_id):
        s3store.assert_own_image_keys(keys)  # does not raise


@st.composite
def _bad_image_key(draw, user_id):
    """A key that is not a valid image key of user_id, or is not a string."""
    kind = draw(st.sampled_from(["other_user", "no_prefix", "empty_name", "unsafe_name", "non_string", "legacy"]))
    name = draw(_safe_paths)
    if kind == "other_user":
        other = draw(_valid_ids.filter(lambda i: i != user_id))
        return f"users/{other}/images/{name}"
    if kind == "no_prefix":
        return draw(st.sampled_from([
            f"users/{user_id}/{name}",
            f"users/{user_id}/raw-posts/{name}",
            f"users/_auth/images/{name}",
            f"/users/{user_id}/images/{name}",
            f"users/{user_id}/images",  # no trailing slash, no name
        ]))
    if kind == "empty_name":
        return f"users/{user_id}/images/"
    if kind == "unsafe_name":
        token = draw(_bad_tokens)
        return f"users/{user_id}/images/{name}{token}{name}"
    if kind == "legacy":
        return f"images/{name}"
    return draw(st.one_of(st.none(), st.integers(), st.binary(max_size=10), st.lists(st.text(max_size=5), max_size=2)))


@_SETTINGS
@given(data=st.data(), user_id=_valid_ids, good=st.lists(_safe_paths, max_size=15), after=st.lists(_safe_paths, max_size=15))
def test_image_keys_reject_any_bad_key_anywhere(data, user_id, good, after):
    """Validates: Requirements 8.1, 8.2 (all keys are checked, not only the first 10)"""
    bad = data.draw(_bad_image_key(user_id))
    keys = [f"users/{user_id}/images/{n}" for n in good] + [bad] + [f"users/{user_id}/images/{n}" for n in after]
    with user_scope.bind(user_id):
        _raises(lambda: s3store.assert_own_image_keys(keys), "bad_request", auth_messages.BAD_IMAGE_PATH)


@_SETTINGS
@given(user_id=_valid_ids, other=_valid_ids, name=_safe_paths)
def test_image_keys_reject_other_users_images(user_id, other, name):
    """Validates: Requirements 8.1, 8.2"""
    if other == user_id:
        return
    with user_scope.bind(user_id):
        _raises(
            lambda: s3store.assert_own_image_keys([f"users/{other}/images/{name}"]),
            "bad_request",
            auth_messages.BAD_IMAGE_PATH,
        )


@_SETTINGS
@given(user_id=_valid_ids, value=st.one_of(st.none(), st.text(max_size=10), st.integers(), st.dictionaries(st.text(max_size=3), st.integers(), max_size=2)))
def test_image_keys_reject_non_list_input(user_id, value):
    """Validates: Requirements 8.2"""
    with user_scope.bind(user_id):
        _raises(lambda: s3store.assert_own_image_keys(value), "bad_request", auth_messages.BAD_IMAGE_PATH)

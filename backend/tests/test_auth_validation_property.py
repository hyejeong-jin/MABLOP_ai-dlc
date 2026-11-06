"""Property test: credential field validation matches the rules exactly (Req 1.4, 3.4, 12.4; Property 4).

The reference oracle below is written independently of auth_service: it uses explicit character sets,
code point ranges, and length arithmetic instead of the production regex or helper functions. The
expected Korean messages are literals copied from the requirements/design (not auth_messages constants).

The pure validators run without S3. For the register and change-password paths, a validation pass is
observed by a probe: the first call after validation (``admit_register`` / ``verify_password``) is
replaced with a function that raises a marker exception, so no S3, SSM, or PBKDF2 work happens.

Feature: user-auth-admin, Property 4: Credential field validation matches the rules exactly
"""
from types import SimpleNamespace
from unittest import mock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_service
import auth_throttle
from errors import MablopError

# --- literal messages (design "auth_service", requirements 1.4 / 3.4 / 12.4) ----------------------

MSG_LOGIN_ID = "아이디는 영문 소문자, 숫자, 밑줄(_), 하이픈(-)으로 4~32자여야 합니다."
MSG_NAME = "이름은 1~30자여야 합니다."
MSG_PASSWORD = "비밀번호는 10~128자여야 합니다."
MSG_SAME_PASSWORD = "새 비밀번호는 현재 비밀번호와 달라야 합니다."

# --- reference oracle -----------------------------------------------------------------------------

_LOGIN_ALPHABET = frozenset("abcdefghijklmnopqrstuvwxyz0123456789_-")


def oracle_login_id(value) -> bool:
    return isinstance(value, str) and 4 <= len(value) <= 32 and all(c in _LOGIN_ALPHABET for c in value)


def _is_control(ch: str) -> bool:
    cp = ord(ch)
    return cp <= 0x1F or 0x7F <= cp <= 0x9F or 0xD800 <= cp <= 0xDFFF


def oracle_name(value) -> bool:
    if not isinstance(value, str):
        return False
    trimmed = value.strip()
    return 1 <= len(trimmed) <= 30 and not any(_is_control(c) for c in trimmed)


def oracle_password(value) -> bool:
    return isinstance(value, str) and 10 <= len(value) <= 128


# --- strategies -----------------------------------------------------------------------------------

_SETTINGS = settings(max_examples=150, deadline=None)

_login_chars = st.sampled_from(sorted(_LOGIN_ALPHABET))
_near_login_chars = st.one_of(
    _login_chars,
    st.sampled_from(list("ABZ .@!/\\\n\t\u00e9\u0663\uff41한")),  # uppercase, space, newline, non-ASCII digit/letter
)

login_ids = st.one_of(
    st.text(alphabet=_login_chars, min_size=0, max_size=40),  # length around 4 and 32
    st.text(alphabet=_near_login_chars, min_size=0, max_size=40),
    st.integers(min_value=1, max_value=40).flatmap(
        lambda n: st.text(alphabet=_login_chars, min_size=n, max_size=n)
    ),
    st.text(max_size=40),
    st.builds(lambda s: s + "\n", st.text(alphabet=_login_chars, min_size=4, max_size=32)),
    st.sampled_from([None, 0, 1234, True, 4.5, [], ["abcd"], {}, b"abcd"]),
)

_name_chars = st.one_of(
    st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    st.sampled_from(list(" \t\n\r\x00\x07\x1b\x1f\x7f\x85\x9f\u00a0\u2003\u3000\u200b한글😀")),
)
names = st.one_of(
    st.text(alphabet=_name_chars, min_size=0, max_size=40),
    st.text(max_size=40),
    # whitespace padding around a body of a boundary length
    st.builds(
        lambda pad_l, body, pad_r: pad_l + body + pad_r,
        st.text(alphabet=" \t\n\u3000", max_size=6),
        st.text(alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E), min_size=0, max_size=32),
        st.text(alphabet=" \t\n\u3000", max_size=6),
    ),
    # a control character placed inside an otherwise valid name
    st.builds(
        lambda a, c, b: a + c + b,
        st.text(alphabet="가나다abc", min_size=1, max_size=10),
        st.sampled_from(["\x00", "\n", "\t", "\x1f", "\x7f", "\x85", "\x9f", "\ud800"]),
        st.text(alphabet="가나다abc", min_size=1, max_size=10),
    ),
    st.sampled_from([None, 0, 12, False, 1.5, [], ["이름"], {}, b"name"]),
)

_password_chars = st.one_of(
    st.characters(min_codepoint=0x20, max_codepoint=0x7E),
    st.sampled_from(list(" \t\n\x00\u3000한글😀")),
)
passwords = st.one_of(
    st.integers(min_value=0, max_value=140).flatmap(
        lambda n: st.text(alphabet=_password_chars, min_size=n, max_size=n)
    ),
    st.text(max_size=140),
    st.sampled_from(["a" * 9, "a" * 10, "a" * 128, "a" * 129, " " * 10, "한" * 10, "😀" * 128, "😀" * 129]),
    st.sampled_from([None, 0, 1234567890, True, 1.5, [], ["abcdefghij"], {}, b"abcdefghij"]),
)

valid_login_ids = st.text(alphabet=_login_chars, min_size=4, max_size=32)
valid_names = st.text(alphabet=st.characters(min_codepoint=0x21, max_codepoint=0x7E), min_size=1, max_size=30)
valid_passwords = st.text(alphabet=_password_chars, min_size=10, max_size=128)


# --- helpers --------------------------------------------------------------------------------------


def _bad_request_message(fn, *args) -> str:
    with pytest.raises(MablopError) as info:
        fn(*args)
    assert info.value.code == "bad_request"
    assert info.value.status == 400
    return info.value.message


class _Reached(Exception):
    """Raised by the probe that replaces the first step after validation."""


def _boom(*_args, **_kwargs):
    raise _Reached()


# --- pure validators ------------------------------------------------------------------------------


@_SETTINGS
@given(value=login_ids)
def test_validate_login_id_matches_rule(value):
    """Validates: Requirements 1.4"""
    if oracle_login_id(value):
        assert auth_service.validate_login_id(value) == value
    else:
        assert _bad_request_message(auth_service.validate_login_id, value) == MSG_LOGIN_ID


@_SETTINGS
@given(value=names)
def test_validate_name_matches_rule(value):
    """Validates: Requirements 1.4"""
    if oracle_name(value):
        assert auth_service.validate_name(value) == value.strip()
    else:
        assert _bad_request_message(auth_service.validate_name, value) == MSG_NAME


@_SETTINGS
@given(value=passwords)
def test_validate_password_matches_rule(value):
    """Validates: Requirements 3.4"""
    if oracle_password(value):
        assert auth_service.validate_password(value) == value
    else:
        assert _bad_request_message(auth_service.validate_password, value) == MSG_PASSWORD


@pytest.mark.parametrize(
    "value, ok",
    [("abc", False), ("abcd", True), ("a" * 32, True), ("a" * 33, False), ("ABCD", False),
     ("ab cd", False), ("abcd\n", False), ("ab.cd", False), ("a_b-1", True)],
)
def test_login_id_boundaries(value, ok):
    """Validates: Requirements 1.4"""
    assert oracle_login_id(value) is ok
    if ok:
        assert auth_service.validate_login_id(value) == value
    else:
        assert _bad_request_message(auth_service.validate_login_id, value) == MSG_LOGIN_ID


@pytest.mark.parametrize(
    "value, ok",
    [("", False), ("   ", False), (" a ", True), ("가" * 30, True), ("가" * 31, False),
     (" " + "가" * 30 + " ", True), ("a\nb", False), ("a\x00b", False), ("a\x7fb", False)],
)
def test_name_boundaries(value, ok):
    """Validates: Requirements 1.4"""
    assert oracle_name(value) is ok
    if ok:
        assert auth_service.validate_name(value) == value.strip()
    else:
        assert _bad_request_message(auth_service.validate_name, value) == MSG_NAME


@pytest.mark.parametrize(
    "value, ok",
    [("a" * 9, False), ("a" * 10, True), ("a" * 128, True), ("a" * 129, False),
     (" " * 10, True), ("한" * 10, True), ("1234567890", True)],
)
def test_password_boundaries_have_no_other_rule(value, ok):
    """Validates: Requirements 3.4"""
    assert oracle_password(value) is ok
    if ok:
        assert auth_service.validate_password(value) == value
    else:
        assert _bad_request_message(auth_service.validate_password, value) == MSG_PASSWORD


# --- register validation path ---------------------------------------------------------------------


@_SETTINGS
@given(login_id=login_ids, name=names, password=passwords)
def test_register_accepts_iff_all_fields_satisfy_the_rules(login_id, name, password):
    """Validates: Requirements 1.4, 3.4"""
    body = {"loginId": login_id, "name": name, "password": password}
    with mock.patch.object(auth_throttle, "admit_register", _boom):
        if oracle_login_id(login_id) and oracle_name(name) and oracle_password(password):
            with pytest.raises(_Reached):  # validation passed, the first later step was reached
                auth_service.register(body, None, 1000.0)
        else:
            if not oracle_login_id(login_id):
                expected = MSG_LOGIN_ID
            elif not oracle_name(name):
                expected = MSG_NAME
            else:
                expected = MSG_PASSWORD
            assert _bad_request_message(auth_service.register, body, None, 1000.0) == expected


@_SETTINGS
@given(field=st.sampled_from(["loginId", "name", "password"]), login_id=valid_login_ids,
       name=valid_names, password=valid_passwords)
def test_register_missing_field_is_bad_request_naming_that_field(field, login_id, name, password):
    """Validates: Requirements 1.4, 3.4"""
    body = {"loginId": login_id, "name": name, "password": password}
    del body[field]
    expected = {"loginId": MSG_LOGIN_ID, "name": MSG_NAME, "password": MSG_PASSWORD}[field]
    with mock.patch.object(auth_throttle, "admit_register", _boom):
        assert _bad_request_message(auth_service.register, body, None, 1000.0) == expected


@pytest.mark.parametrize("body", [None, [], "text", 7])
def test_register_non_dict_body_is_a_loginid_error(body):
    """Validates: Requirements 1.4"""
    with mock.patch.object(auth_throttle, "admit_register", _boom):
        assert _bad_request_message(auth_service.register, body, None, 1000.0) == MSG_LOGIN_ID


# --- change-password validation path --------------------------------------------------------------


def _change_ctx(login_id="tester"):
    user = {
        "userId": "u_0123456789abcdef",
        "loginId": login_id,
        "password": {"alg": "pbkdf2_sha256", "iterations": 1, "salt": "00", "hash": "00"},
    }
    return SimpleNamespace(user=user)


def _change(current, new):
    body = {"currentPassword": current, "newPassword": new}
    with mock.patch.object(auth_throttle, "check_login", lambda *a, **k: None), \
            mock.patch.object(auth_crypto, "verify_password", _boom):
        return auth_service.change_password(body, _change_ctx(), 1000.0)


@_SETTINGS
@given(current=passwords, new=passwords)
def test_change_password_policy_and_sameness(current, new):
    """Validates: Requirements 3.4, 12.4"""
    if not oracle_password(new):
        assert _bad_request_message(_change, current, new) == MSG_PASSWORD
    elif isinstance(current, str) and current == new:
        assert _bad_request_message(_change, current, new) == MSG_SAME_PASSWORD
    else:
        with pytest.raises(_Reached):  # new password accepted, the current-password check is next
            _change(current, new)


@_SETTINGS
@given(password=valid_passwords)
def test_change_password_equal_to_current_is_rejected_with_same_password_message(password):
    """Validates: Requirements 12.4"""
    assert oracle_password(password)
    assert _bad_request_message(_change, password, password) == MSG_SAME_PASSWORD


@_SETTINGS
@given(current=valid_passwords, new=valid_passwords)
def test_change_password_different_valid_new_password_passes_policy(current, new):
    """Validates: Requirements 12.4"""
    if current == new:
        return
    with pytest.raises(_Reached):
        _change(current, new)


@_SETTINGS
@given(current=valid_passwords, new=st.text(max_size=9))
def test_change_password_short_new_password_names_the_password_rule(current, new):
    """Validates: Requirements 12.4"""
    assert _bad_request_message(_change, current, new) == MSG_PASSWORD


def test_change_password_missing_new_password_is_a_password_error():
    """Validates: Requirements 12.4"""
    with mock.patch.object(auth_throttle, "check_login", lambda *a, **k: None), \
            mock.patch.object(auth_crypto, "verify_password", _boom):
        message = _bad_request_message(
            auth_service.change_password, {"currentPassword": "x" * 12}, _change_ctx(), 1000.0
        )
    assert message == MSG_PASSWORD

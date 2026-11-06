"""Property test: password hash round trip (Req 3.2; Property 3).

hash_password / verify_password are pure functions, so no moto bucket is needed. Small
iteration counts keep the PBKDF2 work fast; the production count is never used here.
An independent hashlib computation checks the stored fields rather than trusting
verify_password alone.

Feature: user-auth-admin, Property 3: Password hash round trip
"""
import hashlib
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

import auth_crypto
from errors import MablopError

_HEX_RE = re.compile(r"^[0-9a-f]+$")

# --- strategies -------------------------------------------------------------------------

_passwords = st.text(max_size=40)
# Includes lone surrogates, which can arrive through JSON and must not raise.
_passwords_with_surrogates = st.text(
    alphabet=st.characters(exclude_categories=()), max_size=20
)
_iterations = st.integers(min_value=1, max_value=50)

_SETTINGS = settings(max_examples=100, deadline=None)


def _raises_internal(fn):
    with pytest.raises(MablopError) as info:
        fn()
    assert info.value.code == "internal_error"


# --- round trip -------------------------------------------------------------------------

@_SETTINGS
@given(password=_passwords, iterations=_iterations)
def test_correct_password_verifies(password, iterations):
    """Validates: Requirements 3.2"""
    stored = auth_crypto.hash_password(password, iterations)
    assert auth_crypto.verify_password(password, stored) is True


@_SETTINGS
@given(password=_passwords_with_surrogates, iterations=_iterations)
def test_round_trip_holds_for_any_unicode_password(password, iterations):
    """Validates: Requirements 3.2"""
    stored = auth_crypto.hash_password(password, iterations)
    assert auth_crypto.verify_password(password, stored) is True


@_SETTINGS
@given(password=_passwords, other=_passwords, iterations=_iterations)
def test_different_password_is_rejected(password, other, iterations):
    """Validates: Requirements 3.2"""
    if other == password:
        return
    stored = auth_crypto.hash_password(password, iterations)
    assert auth_crypto.verify_password(other, stored) is False


# --- stored object ----------------------------------------------------------------------

@_SETTINGS
@given(password=_passwords, iterations=_iterations)
def test_stored_object_records_alg_iterations_salt_and_hash(password, iterations):
    """Validates: Requirements 3.1, 3.2"""
    stored = auth_crypto.hash_password(password, iterations)
    assert set(stored) == {"alg", "iterations", "salt", "hash"}
    assert stored["alg"] == "pbkdf2_sha256"
    assert stored["iterations"] == iterations
    assert isinstance(stored["salt"], str) and _HEX_RE.match(stored["salt"])
    assert len(stored["salt"]) == 32  # 16 bytes
    assert isinstance(stored["hash"], str) and _HEX_RE.match(stored["hash"])
    assert len(stored["hash"]) == 64  # SHA-256 output
    expected = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(stored["salt"]), iterations
    )
    assert stored["hash"] == expected.hex()


@_SETTINGS
@given(password=_passwords, iterations=_iterations)
def test_same_password_hashed_twice_has_different_salt_and_hash(password, iterations):
    """Validates: Requirements 3.1, 3.2"""
    first = auth_crypto.hash_password(password, iterations)
    second = auth_crypto.hash_password(password, iterations)
    assert first["salt"] != second["salt"]
    assert first["hash"] != second["hash"]
    assert auth_crypto.verify_password(password, first) is True
    assert auth_crypto.verify_password(password, second) is True


def test_default_iteration_count_is_the_production_value():
    """Validates: Requirements 3.1 (the default is never lowered by the test parameter)"""
    assert auth_crypto.PBKDF2_ITERATIONS == 600_000
    assert auth_crypto.hash_password.__defaults__ == (auth_crypto.PBKDF2_ITERATIONS,)


# --- verification uses the stored values ------------------------------------------------

@_SETTINGS
@given(password=_passwords, iterations=st.integers(min_value=1, max_value=49))
def test_verification_uses_the_stored_iteration_count(password, iterations):
    """Validates: Requirements 3.2"""
    stored = auth_crypto.hash_password(password, iterations)
    tampered = dict(stored, iterations=iterations + 1)
    assert auth_crypto.verify_password(password, tampered) is False


@_SETTINGS
@given(password=_passwords, iterations=_iterations)
def test_hashes_with_different_iteration_counts_coexist(password, iterations):
    """Validates: Requirements 3.2 (old records keep verifying after the default changes)"""
    low = auth_crypto.hash_password(password, iterations)
    high = auth_crypto.hash_password(password, iterations + 7)
    assert auth_crypto.verify_password(password, low) is True
    assert auth_crypto.verify_password(password, high) is True


# --- malformed stored objects -----------------------------------------------------------

@_SETTINGS
@given(
    password=_passwords,
    alg=st.one_of(st.text(max_size=20), st.none(), st.integers()).filter(
        lambda a: a != "pbkdf2_sha256"
    ),
)
def test_unknown_alg_is_internal_error(password, alg):
    """Validates: Requirements 3.2, 3.3"""
    stored = dict(auth_crypto.hash_password(password, 1), alg=alg)
    _raises_internal(lambda: auth_crypto.verify_password(password, stored))


@_SETTINGS
@given(
    password=_passwords,
    iterations=st.one_of(
        st.integers(max_value=0),
        st.integers(min_value=5_000_001),
        st.booleans(),
        st.none(),
        st.text(max_size=5),
        st.floats(allow_nan=True, allow_infinity=True),
    ),
)
def test_out_of_range_or_non_integer_iterations_is_internal_error(password, iterations):
    """Validates: Requirements 3.2, 3.3"""
    stored = dict(auth_crypto.hash_password(password, 1), iterations=iterations)
    _raises_internal(lambda: auth_crypto.verify_password(password, stored))


@_SETTINGS
@given(password=_passwords, field=st.sampled_from(["alg", "iterations", "salt", "hash"]))
def test_missing_field_is_internal_error(password, field):
    """Validates: Requirements 3.2, 3.3"""
    stored = auth_crypto.hash_password(password, 1)
    del stored[field]
    _raises_internal(lambda: auth_crypto.verify_password(password, stored))


@_SETTINGS
@given(
    password=_passwords,
    field=st.sampled_from(["salt", "hash"]),
    bad=st.one_of(st.just("zz"), st.just("abc"), st.none(), st.integers()),
)
def test_non_hex_salt_or_hash_is_internal_error(password, field, bad):
    """Validates: Requirements 3.2, 3.3"""
    stored = dict(auth_crypto.hash_password(password, 1), **{field: bad})
    _raises_internal(lambda: auth_crypto.verify_password(password, stored))


@_SETTINGS
@given(password=_passwords, stored=st.one_of(st.none(), st.text(max_size=5), st.integers(), st.lists(st.integers(), max_size=2)))
def test_non_dict_stored_value_is_internal_error(password, stored):
    """Validates: Requirements 3.2, 3.3"""
    _raises_internal(lambda: auth_crypto.verify_password(password, stored))

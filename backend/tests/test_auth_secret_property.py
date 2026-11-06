"""Property-based tests for the Signing_Secret loader (user-auth-admin Property 27, Req 7.3).

A Signing_Secret shorter than 32 characters, an unset path, a path equal to the Bootstrap_Token
path, or a failed SSM read makes every ``login`` and every Session_Token verification fail with
``internal_error`` and the generic INTERNAL message. No token is issued, the failure is not
cached, and a successful read of 32 or more characters is cached for the warm container.

Hypothesis + moto SSM/S3. The moto context lives for one test function, so each example resets
its own state (SSM parameter, ``auth_crypto.reset_cache()``) instead of relying on per-example
fixtures.
"""
import boto3
import pytest
from botocore.exceptions import ClientError
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

import auth_crypto
import auth_messages
import auth_service
from errors import MablopError

from conftest import (
    ACTING_USER_ID,
    BOOTSTRAP_TOKEN_PATH,
    SIGNING_SECRET_PATH,
    TEST_HASH_ITERATIONS,
    TEST_PASSWORD,
    TEST_REGION,
)

NOW_MS = 1_700_000_000_000
# Shape-valid token so that verify_token reaches the secret-loading step (D3 step 2).
SHAPED_TOKEN = "cGF5bG9hZA.c2lnbmF0dXJl"

PROP_SETTINGS = settings(
    max_examples=100,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)

_SSM_CHARS = st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00")
weak_secrets = st.text(alphabet=_SSM_CHARS, min_size=1, max_size=auth_crypto.SIGNING_SECRET_MIN_LEN - 1)
strong_secrets = st.text(
    alphabet=st.characters(min_codepoint=33, max_codepoint=126),
    min_size=auth_crypto.SIGNING_SECRET_MIN_LEN,
    max_size=64,
)


def _ssm():
    return boto3.client("ssm", region_name=TEST_REGION)


def _put_secret(value, path=SIGNING_SECRET_PATH):
    _ssm().put_parameter(Name=path, Value=value, Type="SecureString", Overwrite=True)


def _assert_generic_internal(err):
    assert err.code == "internal_error"
    assert err.status == 500
    assert err.message == auth_messages.INTERNAL


def _assert_all_paths_fail_closed(user_id=ACTING_USER_ID, login_body=None):
    """get_signing_secret, sign_token, verify_token (and login) fail with the generic internal_error."""
    with pytest.raises(MablopError) as exc:
        auth_crypto.get_signing_secret()
    _assert_generic_internal(exc.value)

    with pytest.raises(MablopError) as exc:
        auth_crypto.sign_token(user_id, NOW_MS)
    _assert_generic_internal(exc.value)

    with pytest.raises(MablopError) as exc:
        auth_crypto.verify_token(SHAPED_TOKEN, NOW_MS)
    _assert_generic_internal(exc.value)

    if login_body is not None:
        with pytest.raises(MablopError) as exc:
            auth_service.login(login_body, None, NOW_MS / 1000)
        _assert_generic_internal(exc.value)

    assert auth_crypto._cached_secret is None


@pytest.fixture
def secret_env(aws_mock, monkeypatch):
    """moto SSM only: signing path set, no parameter yet, cache reset around the test."""
    monkeypatch.setenv("MABLOP_SIGNING_SECRET_SSM_PATH", SIGNING_SECRET_PATH)
    monkeypatch.setenv("MABLOP_TOKEN_SSM_PATH", BOOTSTRAP_TOKEN_PATH)
    auth_crypto.reset_cache()
    yield
    auth_crypto.reset_cache()


@pytest.fixture
def login_env(secret_env, create_user):
    """Secret env plus an active user (cheap hash) that can log in; returns the login body."""
    create_user("tester", user_id=ACTING_USER_ID, iterations=TEST_HASH_ITERATIONS)
    return {"loginId": "tester", "password": TEST_PASSWORD}


# --- Property 27 -------------------------------------------------------------------------


@PROP_SETTINGS
@given(value=weak_secrets)
def test_property_weak_secret_fails_closed_everywhere(login_env, value):
    """Feature: user-auth-admin, Property 27 - a secret under 32 chars fails login and verification.

    **Validates: Requirements 7.3**
    """
    auth_crypto.reset_cache()
    _put_secret(value)
    _assert_all_paths_fail_closed(login_body=login_env)


@PROP_SETTINGS
@given(value=strong_secrets)
def test_property_unset_path_fails_closed_even_with_strong_parameter(login_env, monkeypatch, value):
    """Feature: user-auth-admin, Property 27 - an unset path never falls back to anything.

    **Validates: Requirements 7.3**
    """
    auth_crypto.reset_cache()
    _put_secret(value)
    monkeypatch.delenv("MABLOP_SIGNING_SECRET_SSM_PATH", raising=False)
    _assert_all_paths_fail_closed(login_body=login_env)
    monkeypatch.setenv("MABLOP_SIGNING_SECRET_SSM_PATH", "")
    _assert_all_paths_fail_closed(login_body=login_env)


@PROP_SETTINGS
@given(value=strong_secrets)
def test_property_path_equal_to_token_path_fails_closed(login_env, monkeypatch, value):
    """Feature: user-auth-admin, Property 27 - the signing path may not be the Bootstrap_Token path.

    **Validates: Requirements 7.3**
    """
    auth_crypto.reset_cache()
    _put_secret(value, path=BOOTSTRAP_TOKEN_PATH)
    monkeypatch.setenv("MABLOP_SIGNING_SECRET_SSM_PATH", BOOTSTRAP_TOKEN_PATH)
    _assert_all_paths_fail_closed(login_body=login_env)


@PROP_SETTINGS
@given(path_suffix=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-", min_size=1, max_size=20))
def test_property_missing_parameter_fails_closed(login_env, monkeypatch, path_suffix):
    """Feature: user-auth-admin, Property 27 - a parameter that does not exist fails closed.

    **Validates: Requirements 7.3**
    """
    auth_crypto.reset_cache()
    missing = f"/mablop/missing-{path_suffix}"
    monkeypatch.setenv("MABLOP_SIGNING_SECRET_SSM_PATH", missing)
    _assert_all_paths_fail_closed(login_body=login_env)


@pytest.mark.parametrize(
    "error",
    [
        ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}}, "GetParameter"),
        ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow down"}}, "GetParameter"),
        ClientError({"Error": {"Code": "InternalServerError", "Message": "boom"}}, "GetParameter"),
        RuntimeError("network down"),
    ],
)
def test_unreadable_parameter_fails_closed(login_env, secret_env, monkeypatch, error):
    """A failing SSM read (any exception) is the generic internal_error, never a token.

    **Validates: Requirements 7.3**
    """
    _put_secret("s" * 40)
    real_client = boto3.client

    class _FailingSsm:
        def get_parameter(self, **_kwargs):
            raise error

    def failing_client(service, *args, **kwargs):
        if service == "ssm":
            return _FailingSsm()
        return real_client(service, *args, **kwargs)

    monkeypatch.setattr(boto3, "client", failing_client)
    _assert_all_paths_fail_closed(login_body=login_env)


# --- failures are not cached, successes are ----------------------------------------------


@PROP_SETTINGS
@given(weak=weak_secrets, strong=strong_secrets)
def test_property_failure_is_not_cached_then_strong_secret_works(login_env, weak, strong):
    """Feature: user-auth-admin, Property 27 - a failed load is retried; a later strong value is used.

    **Validates: Requirements 7.3**
    """
    auth_crypto.reset_cache()
    _put_secret(weak)
    _assert_all_paths_fail_closed(login_body=login_env)

    _put_secret(strong)  # no reset_cache: nothing may have been cached by the failures
    assert auth_crypto.get_signing_secret() == strong
    result = auth_service.login(login_env, None, NOW_MS / 1000)
    assert isinstance(result["token"], str) and result["token"]
    assert auth_crypto.verify_token(result["token"], NOW_MS).uid == ACTING_USER_ID


@PROP_SETTINGS
@given(first=strong_secrets, second=strong_secrets)
def test_property_successful_load_is_cached_until_reset(secret_env, first, second):
    """Feature: user-auth-admin, Property 27 - a 32+ char secret is cached for the warm container.

    **Validates: Requirements 7.3**
    """
    auth_crypto.reset_cache()
    _put_secret(first)
    assert auth_crypto.get_signing_secret() == first

    _put_secret(second)  # SSM now differs, and even weak or deleted values must not matter
    assert auth_crypto.get_signing_secret() == first
    _ssm().delete_parameter(Name=SIGNING_SECRET_PATH)
    assert auth_crypto.get_signing_secret() == first
    token, _exp = auth_crypto.sign_token(ACTING_USER_ID, NOW_MS)
    assert auth_crypto.verify_token(token, NOW_MS).uid == ACTING_USER_ID

    auth_crypto.reset_cache()
    with pytest.raises(MablopError) as exc:
        auth_crypto.get_signing_secret()
    _assert_generic_internal(exc.value)


# --- boundary examples -------------------------------------------------------------------


def test_secret_length_boundary_31_fails_32_succeeds(login_env):
    """Exactly 31 characters is rejected; exactly 32 is accepted."""
    _put_secret("a" * 31)
    _assert_all_paths_fail_closed(login_body=login_env)

    _put_secret("a" * 32)
    assert auth_crypto.get_signing_secret() == "a" * 32
    assert auth_service.login(login_env, None, NOW_MS / 1000)["token"]

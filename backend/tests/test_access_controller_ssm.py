"""Token resolution from SSM when MABLOP_TOKEN env is absent (runtime path)."""
import importlib

import boto3
import pytest
from moto import mock_aws

from errors import MablopError

SECRET = "ssm-sourced-token"


@pytest.fixture
def ac(monkeypatch):
    """Reload access_controller with no MABLOP_TOKEN but an SSM path set."""
    monkeypatch.delenv("MABLOP_TOKEN", raising=False)
    monkeypatch.setenv("MABLOP_TOKEN_SSM_PATH", "/mablop/token")
    monkeypatch.setenv("MABLOP_BEDROCK_REGION", "us-east-1")
    with mock_aws():
        boto3.client("ssm", region_name="us-east-1").put_parameter(
            Name="/mablop/token", Value=SECRET, Type="SecureString"
        )
        import access_controller

        importlib.reload(access_controller)
        yield access_controller


def test_token_read_from_ssm(ac):
    assert ac.check_token({"X-Mablop-Token": SECRET}) is True


def test_wrong_token_rejected_with_ssm_source(ac):
    with pytest.raises(MablopError):
        ac.check_token({"X-Mablop-Token": "wrong"})


def test_env_token_takes_precedence(monkeypatch):
    monkeypatch.setenv("MABLOP_TOKEN", "env-token")
    monkeypatch.setenv("MABLOP_TOKEN_SSM_PATH", "/mablop/token")
    import access_controller

    importlib.reload(access_controller)
    assert access_controller.check_token({"X-Mablop-Token": "env-token"}) is True

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("RP_ENV", "test")
    monkeypatch.setenv("RP_CONFIG_SOURCE", "local")
    monkeypatch.setenv("RP_REPO_ROOT", str(REPO_ROOT))
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def ddb() -> Iterator[object]:
    from moto import mock_aws

    with mock_aws():
        import boto3
        from evidence_core.ddb import create_tables_for_tests

        resource = boto3.resource("dynamodb", region_name=os.environ["AWS_REGION"])
        create_tables_for_tests(resource)
        yield resource

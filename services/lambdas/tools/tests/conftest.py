from __future__ import annotations

from pathlib import Path

import boto3
import pytest
from evidence_core.env import Settings
from lambdas.tools._lib import stores as stores_mod
from lambdas.tools._lib.stores import s3_client
from lambdas.tools._lib.testing import BUCKET, World, seed_world
from moto import mock_aws

REPO = Path(__file__).resolve().parents[4]


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> World:
    monkeypatch.setenv("RP_ENV", "test")
    monkeypatch.setenv("RP_CONFIG_SOURCE", "local")
    monkeypatch.setenv("RP_REPO_ROOT", str(REPO))
    monkeypatch.setenv("RP_DATA_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    with mock_aws():
        w = seed_world(boto3.resource("dynamodb", region_name="us-east-1"),
                       s3_client("us-east-1"), Settings.from_env())
        stores_mod.use_stores(w.stores)
        yield w
        stores_mod.use_stores(None)

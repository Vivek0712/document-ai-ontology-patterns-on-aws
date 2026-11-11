from __future__ import annotations

import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
_LIB = REPO_ROOT / "scripts" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

BUCKET = "rp-data-test"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("RP_ENV", "test")
    monkeypatch.setenv("RP_CONFIG_SOURCE", "local")
    monkeypatch.setenv("RP_REPO_ROOT", str(REPO_ROOT))
    monkeypatch.setenv("RP_NO_PROFILE", "1")
    monkeypatch.setenv("RP_SSM_RP_DATA_BUCKET", BUCKET)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.delenv("RP_ACCESS_TOKEN", raising=False)


@pytest.fixture
def aws_env() -> Iterator[Any]:
    """moto: every contract table, a versioned data bucket and the repo config uploaded to config/."""
    from moto import mock_aws

    with mock_aws():
        import boto3
        from evidence_core.ddb import create_tables_for_tests
        from rp_ops.aws import Aws

        create_tables_for_tests(boto3.resource("dynamodb", region_name="us-east-1"))
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket=BUCKET)
        s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for path in sorted((REPO_ROOT / "config").rglob("*.yaml")):
            s3.put_object(Bucket=BUCKET, Key=f"config/{path.relative_to(REPO_ROOT / 'config')}", Body=path.read_bytes())
        for path in sorted((REPO_ROOT / "prompts").glob("*.txt")):
            s3.put_object(Bucket=BUCKET, Key=f"config/prompts/{path.name}", Body=path.read_bytes())
        yield Aws()

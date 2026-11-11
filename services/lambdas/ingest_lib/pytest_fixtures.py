"""Pytest fixtures shared by the ingestion tests. Each ``tests/conftest.py`` of B's directories does
``from lambdas.ingest_lib.pytest_fixtures import *  # noqa: F403``."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
VOCAB_FIXTURE = REPO_ROOT / "services" / "local_pipeline" / "fixtures" / "vocab_fixture_1.2.0.json"
BUCKET = "rp-data-test"


@pytest.fixture(autouse=True)
def ingest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {
        "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
        "AWS_REGION": "us-east-1", "RP_ENV": "test", "RP_CONFIG_SOURCE": "local", "RP_REPO_ROOT": str(REPO_ROOT),
        "RP_DATA_BUCKET": BUCKET, "RP_HEARTBEAT_SECONDS": "0", "RP_VOCAB_PATH": str(VOCAB_FIXTURE),
        "RP_CODE_SHA": "test-sha",
    }.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


@pytest.fixture
def vocab() -> Any:
    from lambdas.ingest_lib.vocab import load_vocab

    return load_vocab("1.2.0", path=VOCAB_FIXTURE)


@pytest.fixture
def aws() -> Iterator[Any]:
    """moto S3 + DynamoDB with the contract tables, a versioned data bucket, the demo package row,
    and fakes for Textract / Comprehend / Step Functions / AOSS / Neptune / embeddings / Docling."""
    from moto import mock_aws

    with mock_aws():
        import boto3
        from evidence_core.ddb import create_tables_for_tests, to_ddb
        from evidence_core.models.api import Package
        from evidence_core.models.status import iso
        from evidence_core.testing import SAMPLE_PACKAGE, SAMPLE_SUB

        from .aws import Services, use_services
        from .embedder import HashEmbedder
        from .testing import FakeAoss, FakeComprehend, FakeSfn, FakeSparql, FakeTextract, scanned_payslip_response

        region = os.environ["AWS_REGION"]
        ddb = boto3.resource("dynamodb", region_name=region)
        create_tables_for_tests(ddb)
        s3 = boto3.client("s3", region_name=region)
        s3.create_bucket(Bucket=BUCKET)
        s3.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        pkg = Package(package_id=SAMPLE_PACKAGE, name="Lending 01", owner_sub=SAMPLE_SUB, created_at=iso(),
                      allowed_principals=[f"user:{SAMPLE_SUB}", "group:lending-analyst"])
        ddb.Table("rp-packages").put_item(Item=to_ddb(pkg.dump()))
        svc = Services(
            clients={"s3": s3, "textract": FakeTextract(scanned_payslip_response()), "comprehend": FakeComprehend(),
                     "stepfunctions": FakeSfn()},
            embedder_override=HashEmbedder(), aoss_override=FakeAoss(), sparql_override=FakeSparql(),
        )
        with use_services(svc):
            yield svc


def put_bronze(svc: Any, file_name: str, data: bytes, *, package_id: str | None = None,
               sub: str | None = None) -> dict[str, Any]:
    """Upload to Bronze and return the IngestTrigger body EventBridge would build (pv filled by caller)."""
    from evidence_core import s3keys
    from evidence_core.testing import SAMPLE_PACKAGE, SAMPLE_SUB

    key = s3keys.bronze_key(sub or SAMPLE_SUB, package_id or SAMPLE_PACKAGE, file_name)
    resp = svc.s3.put_object(Bucket=BUCKET, Key=key, Body=data, ContentType="application/pdf")
    return {"bucket": BUCKET, "key": key, "versionId": resp.get("VersionId"), "size": len(data),
            "eventTime": "2026-11-16T17:00:00Z", "trigger": "s3"}

"""Shared helpers for the cross-builder integration tests (moto only, no AWS)."""

from __future__ import annotations

import boto3
from evidence_core.ddb import create_tables_for_tests, to_ddb
from evidence_core.models.api import Package
from evidence_core.models.status import iso


def seed_package(package_id: str, owner_sub: str, groups: list[str]) -> None:
    """rp-* tables in the active moto context with one package readable by its owner and ``groups``."""
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    create_tables_for_tests(ddb)
    pkg = Package(package_id=package_id, name="integration", owner_sub=owner_sub, created_at=iso(),
                  allowed_principals=[f"user:{owner_sub}", *(f"group:{g}" for g in groups)])
    ddb.Table("rp-packages").put_item(Item=to_ddb(pkg.dump()))

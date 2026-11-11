"""DynamoDB helpers: float<->Decimal conversion and the table key schema (CDK mirrors it)."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from .env import DEFAULT_TABLES


def to_ddb(value: Any) -> Any:
    """JSON-safe value -> DynamoDB-safe (floats become Decimal, None values dropped)."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    if isinstance(value, dict):
        return {k: to_ddb(v) for k, v in value.items() if v is not None}
    if isinstance(value, list | tuple):
        return [to_ddb(v) for v in value]
    return value


def from_ddb(value: Any) -> Any:
    """DynamoDB item -> JSON-safe (Decimal becomes int or float)."""
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: from_ddb(v) for k, v in value.items()}
    if isinstance(value, list):
        return [from_ddb(v) for v in value]
    if isinstance(value, set):
        return sorted(from_ddb(v) for v in value)
    return value


# Key schema for every table. infra/ creates exactly this; tests create it in moto.
# Each entry: (partition key, sort key or None, [GSIs (name, pk, sk or None)], ttl attr or None, stream)
TABLE_SCHEMA: dict[str, dict[str, Any]] = {
    "packages": {
        "name": DEFAULT_TABLES["packages"],
        "pk": "packageId",
        "sk": None,
        "gsis": [("byOwner", "ownerSub", "createdAt")],
        "ttl": None,
        "stream": False,
    },
    "documents": {
        "name": DEFAULT_TABLES["documents"],
        "pk": "packageId",
        "sk": "docId",
        "gsis": [("byDocId", "docId", None)],
        "ttl": None,
        "stream": False,
    },
    "doc_status": {
        "name": DEFAULT_TABLES["doc_status"],
        "pk": "pk",
        "sk": "sk",
        "gsis": [("byPackage", "gsi1pk", "gsi1sk")],
        "ttl": "expiresAt",
        "stream": True,  # NEW_IMAGE -> rp-status-stream (package rollup)
    },
    "unit_status": {
        "name": DEFAULT_TABLES["unit_status"],
        "pk": "unitId",
        "sk": None,
        "gsis": [("byDocLane", "docLane", "unitId"), ("byPackageLane", "packageLane", "unitId")],
        "ttl": None,
        "stream": False,
    },
    "doc_acl": {
        "name": DEFAULT_TABLES["doc_acl"],
        "pk": "docId",
        "sk": None,
        "gsis": [],
        "ttl": None,
        "stream": False,
    },
    "runs": {
        "name": DEFAULT_TABLES["runs"],
        "pk": "runId",
        "sk": None,
        "gsis": [("byPackage", "packageId", "createdAt")],
        "ttl": None,
        "stream": False,
    },
    "pipeline_versions": {
        "name": DEFAULT_TABLES["pipeline_versions"],
        "pk": "pipelineVersion",
        "sk": None,
        "gsis": [],
        "ttl": None,
        "stream": False,
    },
    "review_queue": {
        "name": DEFAULT_TABLES["review_queue"],
        "pk": "packageId",
        "sk": "reviewKey",  # {docId}#{pv}#{unitId}#{fieldId}
        "gsis": [],
        "ttl": None,
        "stream": False,
    },
    # one row per gateway tools/call decision (the REQUEST interceptor writes it; SC-7, W-13)
    "access_log": {
        "name": DEFAULT_TABLES["access_log"],
        "pk": "runId",
        "sk": "sk",  # {at}#{callId}
        "gsis": [("bySince", "day", "at")],
        "ttl": "ttl",  # 30 days
        "stream": False,
    },
}


def create_tables_for_tests(dynamodb_resource: Any) -> None:
    """Create every table with the contract schema (moto / DynamoDB Local)."""
    for spec in TABLE_SCHEMA.values():
        attrs = {spec["pk"]: "S"}
        keys = [{"AttributeName": spec["pk"], "KeyType": "HASH"}]
        if spec["sk"]:
            attrs[spec["sk"]] = "S"
            keys.append({"AttributeName": spec["sk"], "KeyType": "RANGE"})
        gsis = []
        for name, gpk, gsk in spec["gsis"]:
            attrs[gpk] = "S"
            gkeys = [{"AttributeName": gpk, "KeyType": "HASH"}]
            if gsk:
                attrs[gsk] = "S"
                gkeys.append({"AttributeName": gsk, "KeyType": "RANGE"})
            gsis.append({"IndexName": name, "KeySchema": gkeys, "Projection": {"ProjectionType": "ALL"}})
        kwargs: dict[str, Any] = {
            "TableName": spec["name"],
            "KeySchema": keys,
            "AttributeDefinitions": [{"AttributeName": k, "AttributeType": v} for k, v in attrs.items()],
            "BillingMode": "PAY_PER_REQUEST",
        }
        if gsis:
            kwargs["GlobalSecondaryIndexes"] = gsis
        if spec["stream"]:
            kwargs["StreamSpecification"] = {"StreamEnabled": True, "StreamViewType": "NEW_IMAGE"}
        dynamodb_resource.create_table(**kwargs)

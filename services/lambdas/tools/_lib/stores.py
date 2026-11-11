"""Data access shared by the tool Lambdas and get_region_http.

One ``Stores`` object per process holds the boto3 resources. Tests replace it with
``use_stores(Stores(...))`` pointing at moto.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from evidence_core.config import LoadedPipeline, load_pipeline
from evidence_core.ddb import from_ddb
from evidence_core.env import Settings
from evidence_core.ids import is_allowed
from evidence_core.models.api import DocAclItem, DocumentItem, Package
from evidence_core.models.units import UnitStatusItem

from .errors import Forbidden, NotFound

PACKAGE_FORBIDDEN_MESSAGE = "You don't have access to this package."


@dataclass
class Stores:
    settings: Settings
    dynamodb: Any = None  # boto3 DynamoDB resource
    s3: Any = None  # boto3 S3 client
    _tables: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_env(cls) -> Stores:
        import boto3

        s = Settings.from_env()
        return cls(settings=s, dynamodb=boto3.resource("dynamodb", region_name=s.region), s3=s3_client(s.region))

    @property
    def bucket(self) -> str:
        return self.settings.data_bucket

    def table(self, logical: str) -> Any:
        if logical not in self._tables:
            self._tables[logical] = self.dynamodb.Table(self.settings.table(logical))
        return self._tables[logical]

    # --- DynamoDB reads -----------------------------------------------------------------
    def get_package(self, package_id: str) -> Package | None:
        item = self.table("packages").get_item(Key={"packageId": package_id}).get("Item")
        return Package.load(from_ddb(item)) if item else None

    def get_doc_acl(self, doc_id: str) -> DocAclItem | None:
        item = self.table("doc_acl").get_item(Key={"docId": doc_id}).get("Item")
        return DocAclItem.load(from_ddb(item)) if item else None

    def get_unit_status(self, unit_id: str) -> UnitStatusItem | None:
        item = self.table("unit_status").get_item(Key={"unitId": unit_id}).get("Item")
        return UnitStatusItem.load(from_ddb(item)) if item else None

    def list_documents(self, package_id: str) -> list[DocumentItem]:
        return [DocumentItem.load(i) for i in self.query_all("documents", Key("packageId").eq(package_id))]

    def query_all(self, logical: str, key_condition: Any, index: str | None = None) -> list[dict[str, Any]]:
        kwargs: dict[str, Any] = {"KeyConditionExpression": key_condition}
        if index:
            kwargs["IndexName"] = index
        out: list[dict[str, Any]] = []
        while True:
            resp = self.table(logical).query(**kwargs)
            out.extend(from_ddb(i) for i in resp.get("Items", []))
            if "LastEvaluatedKey" not in resp:
                return out
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    # --- S3 reads -------------------------------------------------------------------------
    def read_json(self, key: str) -> Any | None:
        """JSON object at ``key`` in the data bucket, or None when it does not exist."""
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NoSuchBucket"):
                return None
            raise
        return json.loads(obj["Body"].read())

    def read_text(self, key: str) -> str | None:
        try:
            obj = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
                return None
            raise
        return obj["Body"].read().decode("utf-8")

    def presign_get(self, key: str, expires_in: int) -> str:
        return self.s3.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in
        )

    # --- access checks ----------------------------------------------------------------------
    def require_package(self, package_id: str, principals: list[str]) -> Package:
        """Package row if the caller can read it. Unknown and forbidden look the same to the
        caller (both 'forbidden'), so a probe cannot learn which package ids exist."""
        pkg = self.get_package(package_id)
        if pkg is None or not is_allowed(principals, pkg.allowed_principals):
            raise Forbidden(PACKAGE_FORBIDDEN_MESSAGE)
        return pkg

    def require_doc_acl(self, doc_id: str, principals: list[str], message: str) -> DocAclItem:
        acl = self.get_doc_acl(doc_id)
        if acl is None:
            raise NotFound(f"Unknown document {doc_id}.")
        if not is_allowed(principals, acl.allowed_principals):
            raise Forbidden(message)
        return acl


def s3_client(region: str) -> Any:
    """SigV4 S3 client: presigned GETs must be SigV4 for the SSE-KMS data bucket."""
    import boto3
    from botocore.config import Config

    return boto3.client("s3", region_name=region,
                        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"}))


_STORES: Stores | None = None


def stores() -> Stores:
    global _STORES
    if _STORES is None:
        _STORES = Stores.from_env()
    return _STORES


def use_stores(value: Stores | None) -> None:
    """Install (or clear) the process-wide Stores; used by tests."""
    global _STORES
    _STORES = value
    pipeline.cache_clear()


@lru_cache(maxsize=8)
def pipeline(pipeline_version: str) -> LoadedPipeline:
    """Current pipeline config for a version (config source from RP_CONFIG_SOURCE).

    Tools always use the current config object version: retrieval/generation blocks are
    identical across versions by construction (diff test), and K/weights come from here."""
    return load_pipeline(pipeline_version)

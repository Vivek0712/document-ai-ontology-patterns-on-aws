"""Package gate for the Runtime entrypoint: the same checks ``rp-api-ask`` makes, so the streaming
path (API Gateway HTTP proxy straight to the Runtime) cannot start runs on another user's package.

Unknown and forbidden packages get the same answer, so a probe cannot learn which ids exist.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from evidence_core.ddb import from_ddb
from evidence_core.ids import is_allowed, principals_for
from evidence_core.models.api import Package

PACKAGE_FORBIDDEN = "You don't have access to this package."


class PackageDenied(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PackageGate:
    load: Callable[[str], Package | None]

    @classmethod
    def from_settings(cls, settings: Any) -> PackageGate:
        import boto3

        table = boto3.resource("dynamodb", region_name=settings.region).Table(settings.table("packages"))

        def load(package_id: str) -> Package | None:
            item = table.get_item(Key={"packageId": package_id}).get("Item")
            return Package.load(from_ddb(item)) if item else None

        return cls(load=load)

    def check(self, package_id: str, pipeline_version: str, sub: str, groups: list[str]) -> Package:
        pkg = self.load(package_id)
        if pkg is None or not is_allowed(principals_for(sub, groups), pkg.allowed_principals):
            raise PackageDenied("forbidden", PACKAGE_FORBIDDEN)
        if pipeline_version not in pkg.pipeline_versions:
            raise PackageDenied("bad_request",
                                f"pipelineVersion {pipeline_version} is not enabled for this package")
        return pkg

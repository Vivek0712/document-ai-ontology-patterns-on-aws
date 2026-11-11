"""One overridable registry of AWS clients for the ingestion stages.

Lambdas use the module-level default (clients created lazily, reused across warm
invocations). Tests and ``local_pipeline`` swap pieces with ``use_services(...)``:
moto for S3/DynamoDB, stubs for Textract/Comprehend/Bedrock, a local Docling backend.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evidence_core.env import Settings

if TYPE_CHECKING:
    from .aoss import AossClient
    from .docling_client import DoclingBackend
    from .embedder import Embedder


@dataclass
class Services:
    settings: Settings = field(default_factory=Settings.from_env)
    clients: dict[str, Any] = field(default_factory=dict)
    embedder_override: Embedder | None = None
    aoss_override: AossClient | None = None
    docling_override: DoclingBackend | None = None
    sparql_override: Any = None
    tie_breaker_override: Any = None

    def client(self, name: str) -> Any:
        if name not in self.clients:
            import boto3
            from botocore.config import Config

            cfg = Config(retries={"max_attempts": 4, "mode": "adaptive"})
            self.clients[name] = boto3.client(name, region_name=self.settings.region, config=cfg)
        return self.clients[name]

    @property
    def s3(self) -> Any:
        return self.client("s3")

    @property
    def s3_presign(self) -> Any:
        """SigV4 S3 client for presigned URLs: the data bucket is SSE-KMS, and SigV4 signs the headers
        (``content-length``, ``content-type``) the upload must then send exactly."""
        key = "s3:sigv4"
        if key not in self.clients:
            import boto3
            from botocore.config import Config

            cfg = Config(signature_version="s3v4", s3={"addressing_style": "virtual"})
            self.clients[key] = boto3.client("s3", region_name=self.settings.region, config=cfg)
        return self.clients[key]

    @property
    def textract(self) -> Any:
        return self.client("textract")

    @property
    def comprehend(self) -> Any:
        return self.client("comprehend")

    @property
    def bedrock(self) -> Any:
        return self.client("bedrock-runtime")

    @property
    def sfn(self) -> Any:
        return self.client("stepfunctions")

    def table(self, logical: str) -> Any:
        key = f"ddb-table:{logical}"
        if key not in self.clients:
            import boto3

            self.clients[key] = boto3.resource("dynamodb", region_name=self.settings.region).Table(
                self.settings.table(logical)
            )
        return self.clients[key]

    def embedder(self, model_id: str, dimensions: int = 1024) -> Embedder:
        if self.embedder_override is not None:
            return self.embedder_override
        from .embedder import TitanEmbedder

        return TitanEmbedder(self.bedrock, model_id=model_id, dimensions=dimensions)

    def aoss(self) -> AossClient:
        if self.aoss_override is not None:
            return self.aoss_override
        key = "aoss"
        if key not in self.clients:
            from .aoss import AossClient

            if not self.settings.aoss_endpoint:
                raise RuntimeError("RP_AOSS_ENDPOINT is not set")
            self.clients[key] = AossClient.connect(self.settings.aoss_endpoint, self.settings.region)
        return self.clients[key]

    def docling(self) -> DoclingBackend:
        if self.docling_override is not None:
            return self.docling_override
        from .docling_client import DoclingServeBackend

        if not self.settings.docling_url:
            raise RuntimeError("RP_DOCLING_URL is not set")
        return DoclingServeBackend(self.settings.docling_url)

    def sparql(self) -> Any:
        if self.sparql_override is not None:
            return self.sparql_override
        from evidence_core.sparql import SparqlClient

        if not self.settings.neptune_endpoint:
            raise RuntimeError("RP_NEPTUNE_ENDPOINT is not set")
        return SparqlClient(self.settings.neptune_endpoint, region=self.settings.region)


_current: Services | None = None


def services() -> Services:
    global _current
    if _current is None:
        _current = Services()
    return _current


def reset_services() -> None:
    """Drop cached clients (tests that change env vars between cases)."""
    global _current
    _current = None


@contextlib.contextmanager
def use_services(svc: Services) -> Iterator[Services]:
    global _current
    prev = _current
    _current = svc
    try:
        yield svc
    finally:
        _current = prev

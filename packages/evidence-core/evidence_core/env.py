"""Environment variables every Lambda / the agent / scripts read."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

# Physical DynamoDB table names (CDK creates exactly these; env vars may override).
DEFAULT_TABLES: dict[str, str] = {
    "packages": "rp-packages",
    "documents": "rp-documents",
    "doc_status": "rp-doc-status",
    "unit_status": "rp-unit-status",
    "doc_acl": "rp-doc-acl",
    "runs": "rp-runs",
    "pipeline_versions": "rp-pipeline-versions",
    "review_queue": "rp-review-queue",
    "access_log": "rp-access-log",
}

TABLE_ENV: dict[str, str] = {
    "packages": "RP_TABLE_PACKAGES",
    "documents": "RP_TABLE_DOCUMENTS",
    "doc_status": "RP_TABLE_DOC_STATUS",
    "unit_status": "RP_TABLE_UNIT_STATUS",
    "doc_acl": "RP_TABLE_DOC_ACL",
    "runs": "RP_TABLE_RUNS",
    "pipeline_versions": "RP_TABLE_PIPELINE_VERSIONS",
    "review_queue": "RP_TABLE_REVIEW_QUEUE",
    "access_log": "RP_TABLE_ACCESS_LOG",
}


def _repo_root_default() -> Path:
    # packages/evidence-core/evidence_core/env.py -> repository root
    return Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Settings:
    env: Literal["demo", "local", "test"]
    region: str
    data_bucket: str
    config_source: Literal["s3", "local"]
    repo_root: Path
    tables: dict[str, str]
    aoss_endpoint: str | None
    neptune_endpoint: str | None
    docling_url: str | None
    docling_queue_url: str | None
    state_machine_arn: str | None
    user_pool_id: str | None
    user_pool_client_id: str | None
    agent_runtime_arn: str | None
    gateway_url: str | None
    heartbeat_seconds: float
    log_level: str
    code_sha: str | None

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ
        env = e.get("RP_ENV", "local")
        return cls(
            env=env,  # type: ignore[arg-type]
            region=e.get("AWS_REGION", e.get("AWS_DEFAULT_REGION", "us-east-1")),
            data_bucket=e.get("RP_DATA_BUCKET", "rp-data-local"),
            config_source=e.get("RP_CONFIG_SOURCE", "s3" if env == "demo" else "local"),  # type: ignore[arg-type]
            repo_root=Path(e.get("RP_REPO_ROOT", str(_repo_root_default()))),
            tables={k: e.get(TABLE_ENV[k], v) for k, v in DEFAULT_TABLES.items()},
            aoss_endpoint=e.get("RP_AOSS_ENDPOINT"),
            neptune_endpoint=e.get("RP_NEPTUNE_ENDPOINT"),
            docling_url=e.get("RP_DOCLING_URL"),
            docling_queue_url=e.get("RP_DOCLING_QUEUE_URL"),
            state_machine_arn=e.get("RP_STATE_MACHINE_ARN"),
            user_pool_id=e.get("RP_USER_POOL_ID"),
            user_pool_client_id=e.get("RP_USER_POOL_CLIENT_ID"),
            agent_runtime_arn=e.get("RP_AGENT_RUNTIME_ARN"),
            gateway_url=e.get("RP_GATEWAY_URL"),
            heartbeat_seconds=float(e.get("RP_HEARTBEAT_SECONDS", "5")),
            log_level=e.get("RP_LOG_LEVEL", "INFO"),
            code_sha=e.get("RP_CODE_SHA"),
        )

    def table(self, logical: str) -> str:
        return self.tables[logical]


# --- browser CORS (REST Lambdas: api, upload_url, get_region_http) ---------------------------

def allowed_origins() -> list[str]:
    """Origins allowed to call the REST API directly (``RP_CORS_ORIGINS``, comma separated).

    The deployed UI calls the API through CloudFront on its own origin, so this list only holds the dev
    origins. ``RP_CORS_ORIGIN`` (singular) is accepted as a legacy single value.
    """
    raw = os.environ.get("RP_CORS_ORIGINS") or os.environ.get("RP_CORS_ORIGIN") or ""
    return [o.strip() for o in raw.split(",") if o.strip()]


def cors_headers(request_headers: dict[str, str] | None) -> dict[str, str]:
    """Echo the request ``Origin`` only when it is configured; nothing otherwise (never ``*``)."""
    headers = {k.lower(): v for k, v in (request_headers or {}).items()}
    origin = headers.get("origin")
    if origin and origin in allowed_origins():
        return {"Access-Control-Allow-Origin": origin, "Vary": "Origin"}
    return {}

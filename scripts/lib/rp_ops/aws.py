"""Session, SSM, Lambda invoke and SigV4 HTTP helpers shared by the operator commands."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import cached_property
from typing import Any

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.exceptions import ClientError
from evidence_core.env import DEFAULT_TABLES

DEFAULT_REGION = "us-east-1"

# SSM parameter names written by CDK and deploy_agentcore.sh.
SSM = {
    "data_bucket": "/rp/data-bucket",
    "ui_bucket": "/rp/ui-bucket",
    "api_url": "/rp/api-url",
    "web_url": "/rp/web-url",
    "user_pool_id": "/rp/user-pool-id",
    "user_pool_client_id": "/rp/user-pool-client-id",
    "aoss_endpoint": "/rp/aoss-endpoint",
    "neptune_endpoint": "/rp/neptune-endpoint",
    "state_machine_arn": "/rp/state-machine-arn",
    "docling_queue_url": "/rp/docling-queue-url",
    "agent_runtime_arn": "/rp/agent/runtime-arn",
    "agent_runtime_url": "/rp/agent/runtime-url",
    "gateway_id": "/rp/agent/gateway-id",
    "gateway_url": "/rp/agent/gateway-url",
    "demo_password": "/rp/demo/password",  # SecureString, written by seed
}


def log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


@dataclass
class Aws:
    """One boto3 session for the operator's credentials (default chain, or ``profile``/AWS_PROFILE) and region."""

    profile: str | None = None
    region: str | None = None

    @cached_property
    def session(self) -> boto3.Session:
        profile = self.profile or os.environ.get("AWS_PROFILE") or None  # None = the default credential chain
        region = self.region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or DEFAULT_REGION
        if os.environ.get("RP_NO_PROFILE") == "1":  # tests / CI with env credentials
            return boto3.Session(region_name=region)
        return boto3.Session(profile_name=profile, region_name=region)

    @property
    def region_name(self) -> str:
        return self.session.region_name or DEFAULT_REGION

    def client(self, name: str) -> Any:
        return self.session.client(name)

    def resource(self, name: str) -> Any:
        return self.session.resource(name)

    # --- SSM ----------------------------------------------------------------------------------

    def ssm(self, logical_or_name: str, *, required: bool = True, decrypt: bool = False) -> str | None:
        name = SSM.get(logical_or_name, logical_or_name)
        env_override = os.environ.get("RP_SSM_" + name.strip("/").replace("/", "_").replace("-", "_").upper())
        if env_override:
            return env_override
        try:
            resp = self.client("ssm").get_parameter(Name=name, WithDecryption=decrypt)
            return str(resp["Parameter"]["Value"])
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ParameterNotFound" and not required:
                return None
            if exc.response["Error"]["Code"] == "ParameterNotFound":
                raise SystemExit(f"SSM parameter {name} not found: deploy the stack that writes it first") from exc
            raise

    def table(self, logical: str) -> Any:
        env = os.environ.get("RP_TABLE_" + logical.upper())
        return self.resource("dynamodb").Table(env or DEFAULT_TABLES[logical])

    # --- Lambda -------------------------------------------------------------------------------

    def invoke(self, function_name: str, payload: dict[str, Any]) -> Any:
        """Synchronous invoke; raises on a function error with its message."""
        resp = self.client("lambda").invoke(
            FunctionName=function_name,
            InvocationType="RequestResponse",
            Payload=json.dumps(payload).encode(),
        )
        body = resp["Payload"].read().decode() or "null"
        if resp.get("FunctionError"):
            raise RuntimeError(f"{function_name} failed: {body[:2000]}")
        return json.loads(body)

    # --- SigV4 HTTP (OpenSearch Serverless) --------------------------------------------------

    def signed_request(self, method: str, url: str, *, service: str, body: Any = None,
                       timeout: float = 30.0) -> tuple[int, Any]:
        data = None if body is None else (body if isinstance(body, bytes) else json.dumps(body).encode())
        headers = {"Content-Type": "application/json"}
        if service == "aoss":
            import hashlib

            headers["x-amz-content-sha256"] = hashlib.sha256(data or b"").hexdigest()
        req = AWSRequest(method=method, url=url, data=data, headers=headers)
        creds = self.session.get_credentials()
        if creds is None:
            raise SystemExit("no AWS credentials for the operator profile")
        SigV4Auth(creds.get_frozen_credentials(), service, self.region_name).add_auth(req)
        http_req = urllib.request.Request(  # noqa: S310 (https endpoint from SSM)
            url, data=data, method=method, headers=dict(req.headers.items()))
        try:
            with urllib.request.urlopen(http_req, timeout=timeout) as resp:  # noqa: S310 (https endpoint from SSM)
                raw = resp.read().decode() or "null"
                return resp.status, json.loads(raw)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode() or "null"
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, raw


def lambda_name(directory: str) -> str:
    """Lambda naming rule: ``rp-`` + directory name with ``_`` -> ``-``."""
    return "rp-" + directory.replace("_", "-")


def mask_account(text: str) -> str:
    """Never print a full 12-digit account ID: only its last four digits."""
    import re

    return re.sub(r"\b\d{8}(\d{4})\b", r"********\1", text)


def mask_arn(arn: str) -> str:
    """Mask the account and the principal's user or session name (often an e-mail with IAM Identity Center)."""
    import re

    return re.sub(r"(assumed-role/[^/]+/|:user/|federated-user/).+$", r"\1****", mask_account(arn))

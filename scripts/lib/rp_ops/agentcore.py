"""teardown: the AgentCore resources live outside CDK (scripts/deploy_agentcore.sh creates them), so
teardown removes them here before `cdk destroy`: gateway targets, gateway, runtime, SSM pointers."""

from __future__ import annotations

import time
from typing import Any

from botocore.exceptions import ClientError

from .aws import Aws, log

AGENT_SSM = ("/rp/agent/runtime-arn", "/rp/agent/runtime-url", "/rp/agent/gateway-id", "/rp/agent/gateway-url")
RUNTIME_NAME = "rp_agent"
GATEWAY_NAME = "rp-gateway"


def _ignore_missing(fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        return fn(*args, **kwargs)
    except ClientError as exc:
        if exc.response["Error"]["Code"] in ("ResourceNotFoundException", "ParameterNotFound"):
            return None
        raise


def find_gateway_ids(ctl: Any, ssm_id: str | None) -> list[str]:
    ids = [ssm_id] if ssm_id else []
    for page in ctl.get_paginator("list_gateways").paginate():
        ids += [g["gatewayId"] for g in page.get("items", []) if g.get("name") == GATEWAY_NAME]
    return sorted(set(ids))


def find_runtime_ids(ctl: Any, ssm_arn: str | None) -> list[str]:
    ids = [ssm_arn.rsplit("/", 1)[-1]] if ssm_arn else []
    for page in ctl.get_paginator("list_agent_runtimes").paginate():
        ids += [r["agentRuntimeId"] for r in page.get("agentRuntimes", []) if r.get("agentRuntimeName") == RUNTIME_NAME]
    return sorted(set(ids))


def delete_agentcore(aws: Aws, *, dry_run: bool = False) -> dict[str, list[str]]:
    ctl = aws.client("bedrock-agentcore-control")
    done: dict[str, list[str]] = {"targets": [], "gateways": [], "runtimes": [], "ssm": []}
    for gw in find_gateway_ids(ctl, aws.ssm("gateway_id", required=False)):
        targets = _ignore_missing(ctl.list_gateway_targets, gatewayIdentifier=gw) or {}
        for t in targets.get("items", []):
            done["targets"].append(f"{gw}/{t['targetId']}")
            if not dry_run:
                _ignore_missing(ctl.delete_gateway_target, gatewayIdentifier=gw, targetId=t["targetId"])
        if not dry_run and targets.get("items"):
            time.sleep(5)  # target deletion is asynchronous; the gateway refuses deletion while targets exist
        done["gateways"].append(gw)
        if not dry_run:
            _ignore_missing(ctl.delete_gateway, gatewayIdentifier=gw)
    for rt in find_runtime_ids(ctl, aws.ssm("agent_runtime_arn", required=False)):
        done["runtimes"].append(rt)
        if not dry_run:
            _ignore_missing(ctl.delete_agent_runtime, agentRuntimeId=rt)
    ssm = aws.client("ssm")
    for name in AGENT_SSM:
        done["ssm"].append(name)
        if not dry_run:
            _ignore_missing(ssm.delete_parameter, Name=name)
    for k, v in done.items():
        log(f"agentcore {k}: {v or 'none'}{' (dry run)' if dry_run else ''}")
    return done


def empty_bucket(aws: Aws, bucket: str, *, dry_run: bool = False) -> int:
    """Delete every object version and delete marker (buckets are versioned)."""
    s3 = aws.client("s3")
    n = 0
    try:
        pages = list(s3.get_paginator("list_object_versions").paginate(Bucket=bucket))
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "NoSuchBucket":
            return 0
        raise
    for page in pages:
        objs = [{"Key": v["Key"], "VersionId": v["VersionId"]}
                for v in page.get("Versions", []) + page.get("DeleteMarkers", [])]
        for i in range(0, len(objs), 1000):
            n += len(objs[i:i + 1000])
            if not dry_run:
                s3.delete_objects(Bucket=bucket, Delete={"Objects": objs[i:i + 1000], "Quiet": True})
    log(f"bucket {bucket}: {n} versions {'would be ' if dry_run else ''}deleted")
    return n

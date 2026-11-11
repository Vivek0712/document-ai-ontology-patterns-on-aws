"""prewarm / pause / resume: docling-serve on Fargate, Neptune cluster state, provisioned concurrency,
warm queries against both indexes and the graph, one call per model."""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import ClientError
from evidence_core.config import ModelSpec, parse_models
from evidence_core.env import Settings
from evidence_core.ids import new_run_id

from .aws import Aws, lambda_name, log

ECS_CLUSTER = "rp-cluster"
ECS_SERVICE = "rp-docling"
NEPTUNE_CLUSTER = "rp-graph"
DOCLING_MAX = 1  # docling-serve keeps async task state in process memory: one task only
DOCLING_FLOOR_PARAM = "/rp/docling/floor"  # owner of the scaling floor (services/lambdas/ingest_lib/docling_scaler.py)
# provisioned concurrency 1 on the tools on the Ask path and the chip-click route.
PC_FUNCTIONS: tuple[str, ...] = (
    lambda_name("search_units"), lambda_name("graph_hop"), lambda_name("get_region"), lambda_name("get_region_http"),
)
WARM_PRINCIPAL_SUB = "svc-prewarm"
WARM_GROUPS = ["lending-analyst"]


def pc_qualifier() -> str:
    """Alias that carries provisioned concurrency (CDK publishes it; CONTRACT GAP: alias name)."""
    return os.environ.get("RP_PC_QUALIFIER", "live")


# --- docling-serve -----------------------------------------------------------------------------

def docling_scale(aws: Aws, desired: int, *, wait: bool = True) -> dict[str, Any]:
    """Pin the scaling floor to ``desired`` (0 paused, 1 prewarmed) and set the desired count."""
    aas = aws.client("application-autoscaling")
    aas.register_scalable_target(
        ServiceNamespace="ecs", ResourceId=f"service/{ECS_CLUSTER}/{ECS_SERVICE}",
        ScalableDimension="ecs:service:DesiredCount", MinCapacity=desired, MaxCapacity=DOCLING_MAX,
    )
    ecs = aws.client("ecs")
    ecs.update_service(cluster=ECS_CLUSTER, service=ECS_SERVICE, desiredCount=desired)
    # prewarm owns the floor (the demand reaper leaves it alone); pause hands it back.
    aws.client("ssm").put_parameter(
        Name=DOCLING_FLOOR_PARAM, Type="String", Overwrite=True,
        Value=json.dumps({"owner": "prewarm" if desired > 0 else "none", "at": round(time.time(), 3)}),
    )
    log(f"{ECS_SERVICE}: desired {desired}, scaling min {desired} max {DOCLING_MAX}")
    if wait and desired > 0:
        log(f"{ECS_SERVICE}: waiting for a healthy task (image pull + model load take a few minutes)")
        ecs.get_waiter("services_stable").wait(cluster=ECS_CLUSTER, services=[ECS_SERVICE],
                                               WaiterConfig={"Delay": 15, "MaxAttempts": 60})
    svc = ecs.describe_services(cluster=ECS_CLUSTER, services=[ECS_SERVICE])["services"][0]
    return {"desired": svc["desiredCount"], "running": svc["runningCount"], "pending": svc["pendingCount"]}


# --- Neptune -----------------------------------------------------------------------------------

def neptune_status(aws: Aws) -> str | None:
    try:
        clusters = aws.client("neptune").describe_db_clusters(DBClusterIdentifier=NEPTUNE_CLUSTER)["DBClusters"]
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "DBClusterNotFoundFault":
            return None
        raise
    return str(clusters[0]["Status"]) if clusters else None


def neptune_set(aws: Aws, running: bool, *, wait: bool = True, timeout_s: int = 1500) -> str | None:
    status = neptune_status(aws)
    if status is None:
        log(f"{NEPTUNE_CLUSTER}: not deployed")
        return None
    nep = aws.client("neptune")
    if running and status == "stopped":
        nep.start_db_cluster(DBClusterIdentifier=NEPTUNE_CLUSTER)
        log(f"{NEPTUNE_CLUSTER}: starting (about 10 minutes)")
    elif not running and status == "available":
        nep.stop_db_cluster(DBClusterIdentifier=NEPTUNE_CLUSTER)
        log(f"{NEPTUNE_CLUSTER}: stopping")
    else:
        log(f"{NEPTUNE_CLUSTER}: {status}, no change")
    if not wait:
        return neptune_status(aws)
    target = "available" if running else "stopped"
    deadline = time.monotonic() + timeout_s
    while (status := neptune_status(aws)) != target:
        if time.monotonic() > deadline:
            raise SystemExit(f"{NEPTUNE_CLUSTER} still {status} after {timeout_s}s")
        time.sleep(20)
    log(f"{NEPTUNE_CLUSTER}: {status}")
    return status


# --- provisioned concurrency ---------------------------------------------------------------------

def set_provisioned_concurrency(aws: Aws, units: int) -> dict[str, str]:
    lam = aws.client("lambda")
    out: dict[str, str] = {}
    q = pc_qualifier()
    for fn in PC_FUNCTIONS:
        try:
            if units > 0:
                lam.put_provisioned_concurrency_config(FunctionName=fn, Qualifier=q,
                                                       ProvisionedConcurrentExecutions=units)
                out[fn] = f"{units} on :{q}"
            else:
                lam.delete_provisioned_concurrency_config(FunctionName=fn, Qualifier=q)
                out[fn] = "removed"
        except ClientError as exc:
            code = exc.response["Error"]["Code"]
            out[fn] = "none configured" if (units == 0 and code == "ResourceNotFoundException") else f"skipped ({code})"
        log(f"provisioned concurrency {fn}: {out[fn]}")
    return out


# --- warm calls ------------------------------------------------------------------------------------

def injected(package_id: str, pipeline_version: str) -> dict[str, Any]:
    """What the Gateway interceptor would inject (direct Lambda invoke bypasses the Gateway)."""
    return {"callerSub": WARM_PRINCIPAL_SUB, "groups": WARM_GROUPS, "pipelineVersion": pipeline_version,
            "packageId": package_id, "runId": new_run_id()}


def warm_search(aws: Aws, package_id: str, pipeline_versions: list[str]) -> dict[str, str]:
    out = {}
    for pv in pipeline_versions:
        t0 = time.monotonic()
        res = aws.invoke(lambda_name("search_units"),
                         {"query": "wages tips other compensation", "k": 1, **injected(package_id, pv)})
        units = unwrap_tool_result(res).get("units", [])
        out[pv] = f"{len(units)} unit(s) in {int((time.monotonic() - t0) * 1000)} ms"
        log(f"search_units {pv}: {out[pv]}")
    return out


def warm_graph(aws: Aws, package_id: str) -> str:
    t0 = time.monotonic()
    res = aws.invoke(lambda_name("graph_hop"), {"template": "concept_by_label_v1",
                                                "params": {"label": "Wages, tips, other compensation"},
                                                **injected(package_id, "2.3.1")})
    rows = unwrap_tool_result(res).get("rowCount")
    msg = f"{rows} row(s) in {int((time.monotonic() - t0) * 1000)} ms"
    log(f"graph_hop concept_by_label_v1: {msg}")
    return msg


def unwrap_tool_result(res: Any) -> dict[str, Any]:
    """Tool Lambdas may return the result object, an MCP ``{content:[{type:text,text}]}`` wrapper or a
    ``{body: "..."}`` proxy shape. Return the result object."""
    if isinstance(res, dict) and "content" in res and isinstance(res["content"], list):
        for part in res["content"]:
            if part.get("type") == "text":
                try:
                    return dict(json.loads(part["text"]))
                except (json.JSONDecodeError, TypeError):
                    break
    if isinstance(res, dict) and isinstance(res.get("body"), str):
        res = json.loads(res["body"])
    if isinstance(res, dict) and isinstance(res.get("error"), dict):
        err = res["error"]
        raise RuntimeError(f"tool error {err.get('code')}: {err.get('message')}")
    return res if isinstance(res, dict) else {}


# --- models ----------------------------------------------------------------------------------------

@dataclass(frozen=True)
class ModelPing:
    key: str
    model_id: str
    ok: bool
    ms: int
    detail: str


def load_models() -> dict[str, ModelSpec]:
    root = Settings.from_env().repo_root
    return parse_models((root / "config" / "models.yaml").read_text(encoding="utf-8")).models


def ping_model(aws: Aws, key: str, spec: ModelSpec) -> ModelPing:
    rt = aws.client("bedrock-runtime")
    t0 = time.monotonic()
    try:
        if spec.kind == "embedding":
            body: dict[str, Any] = {"inputText": "Wages, tips, other compensation"}
            if spec.dimensions:
                body["dimensions"] = spec.dimensions
            if spec.normalize is not None:
                body["normalize"] = spec.normalize
            resp = rt.invoke_model(modelId=spec.model_id, body=json.dumps(body), contentType="application/json",
                                   accept="application/json")
            vec = json.loads(resp["body"].read()).get("embedding", [])
            detail = f"{len(vec)}-d vector"
            ok = not spec.dimensions or len(vec) == spec.dimensions
        else:
            cfg: dict[str, Any] = {"maxTokens": 8}
            if spec.accepts_temperature:
                cfg["temperature"] = 0
            resp = rt.converse(modelId=spec.model_id, inferenceConfig=cfg,
                               messages=[{"role": "user", "content": [{"text": "Reply with the word ok."}]}])
            text = "".join(c.get("text", "") for c in resp["output"]["message"]["content"])
            detail = f"replied {text.strip()[:20]!r}, {resp.get('usage', {}).get('totalTokens', '?')} tokens"
            ok = True
    except ClientError as exc:
        ok, detail = False, f"{exc.response['Error']['Code']}: {exc.response['Error']['Message'][:160]}"
    return ModelPing(key=key, model_id=spec.model_id, ok=ok, ms=int((time.monotonic() - t0) * 1000), detail=detail)

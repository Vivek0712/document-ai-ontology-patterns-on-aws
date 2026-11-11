"""Clients and config the handlers share. One lazily built instance per Lambda container;
tests install their own with ``set_deps``."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from evidence_core.config import ConfigSource, LoadedPipeline, LocalConfigSource, S3ConfigSource, load_pipeline
from evidence_core.env import Settings

PIPELINE_CACHE_SECONDS = 60.0

# rp-api -> rp-graph-hop: the only templates the API may run, and the pattern of each parameter.
SHOWCASE_TEMPLATES: dict[str, dict[str, re.Pattern[str]]] = {
    "coupon_terms_v1": {"security": re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._/-]{0,31}$")},
    # API-only (models/tools.py API_GRAPH_TEMPLATES): a full IRI (rp-api expands CURIEs), depth "2" or absent
    "neighbourhood_v1": {"iri": re.compile(r"^(urn:rp:[A-Za-z0-9_:./#-]{1,200}|https?://[A-Za-z0-9_:./#%-]{1,200})$"),
                         "depth": re.compile(r"^2$")},
}
OPTIONAL_PARAMS: dict[str, set[str]] = {"neighbourhood_v1": {"depth"}}
GRAPH_HOP_SOURCE = "rp-api"


def check_showcase_template(template: str, params: dict[str, str]) -> None:
    """Raise ``ValueError`` unless ``template`` is allow-listed and ``params`` are exactly its parameters, each
    matching its pattern."""
    allowed = SHOWCASE_TEMPLATES.get(template)
    if allowed is None:
        raise ValueError(f"rp-api may not run graph_hop template {template!r}")
    required = set(allowed) - OPTIONAL_PARAMS.get(template, set())
    if not required <= set(params) <= set(allowed):
        raise ValueError(f"{template} takes parameters {sorted(allowed)} ({sorted(required)} required), "
                         f"got {sorted(params)}")
    bad = [k for k, v in params.items() if not isinstance(v, str) or not allowed[k].match(v)]
    if bad:
        raise ValueError(f"invalid {template} parameter(s): {', '.join(sorted(bad))}")


@dataclass
class Deps:
    settings: Settings
    ddb: Any  # boto3 DynamoDB service resource
    s3: Any  # boto3 S3 client
    sfn: Any = None  # boto3 Step Functions client
    lambda_client: Any = None
    ssm: Any = None
    config_source: ConfigSource | None = None
    aoss: Any = None  # aoss.AossClient | None
    graph_hop: Any = None  # callable(event) -> GraphHopResult JSON; default invokes rp-graph-hop:live (VPC)
    coa_control: Any = None  # callable(event) -> {"status", "body"}; default invokes rp-coa-control
    clock: Any = time.monotonic
    _pipelines: dict[str, tuple[float, LoadedPipeline]] = field(default_factory=dict, init=False)

    def table(self, logical: str) -> Any:
        return self.ddb.Table(self.settings.table(logical))

    @property
    def bucket(self) -> str:
        return self.settings.data_bucket

    def source(self) -> ConfigSource:
        if self.config_source is None:
            if self.settings.config_source == "s3":
                self.config_source = S3ConfigSource(bucket=self.settings.data_bucket, client=self.s3)
            else:
                self.config_source = LocalConfigSource(root=self.settings.repo_root)
        return self.config_source

    def invoke_graph_hop(self, template: str, params: dict[str, str], *, caller_sub: str, groups: list[str],
                         pipeline_version: str, package_id: str, run_id: str) -> dict[str, Any]:
        """Run one showcase template through ``rp-graph-hop`` (its ``live`` alias) with an API-built injected
        context. This is the one place besides the Gateway interceptor that asserts an identity to a tool
        ("rp-api → rp-graph-hop"), so it is narrow: only ``SHOWCASE_TEMPLATES``, each parameter
        checked against its pattern, the identity passed in by the route from the authorizer's claims, and the
        invocation marked ``rpSource: rp-api`` in the Lambda client context (the tool logs it). Tests and the
        mock-world export install ``graph_hop``."""
        check_showcase_template(template, params)
        event = {"template": template, "params": dict(params), "callerSub": caller_sub, "groups": list(groups),
                 "pipelineVersion": pipeline_version, "packageId": package_id, "runId": run_id}
        if self.graph_hop is not None:
            return dict(self.graph_hop(event))
        if self.lambda_client is None:
            raise RuntimeError("no Lambda client to invoke rp-graph-hop")
        import base64
        import json
        import os

        fn = os.environ.get("RP_GRAPH_HOP_FUNCTION", "rp-graph-hop:live")
        client_context = base64.b64encode(json.dumps({"custom": {"rpSource": GRAPH_HOP_SOURCE}}).encode()).decode()
        resp = self.lambda_client.invoke(FunctionName=fn, InvocationType="RequestResponse",
                                         ClientContext=client_context, Payload=json.dumps(event).encode())
        payload = json.loads(resp["Payload"].read() or b"{}")
        if resp.get("FunctionError"):
            raise RuntimeError(f"{fn} failed: {str(payload)[:300]}")
        return dict(payload)

    def pipeline(self, pipeline_version: str) -> LoadedPipeline:
        """Latest config for a version (cached 60 s per container)."""
        hit = self._pipelines.get(pipeline_version)
        now = self.clock()
        if hit and now - hit[0] < PIPELINE_CACHE_SECONDS:
            return hit[1]
        loaded = load_pipeline(pipeline_version, self.source())
        self._pipelines[pipeline_version] = (now, loaded)
        return loaded


_DEPS: Deps | None = None


def build_default() -> Deps:
    import boto3
    from botocore.config import Config

    settings = Settings.from_env()
    cfg = Config(retries={"max_attempts": 4, "mode": "standard"}, signature_version="s3v4")
    deps = Deps(
        settings=settings,
        ddb=boto3.resource("dynamodb", region_name=settings.region),
        s3=boto3.client("s3", region_name=settings.region, config=cfg),
        sfn=boto3.client("stepfunctions", region_name=settings.region),
        lambda_client=boto3.client("lambda", region_name=settings.region),
        ssm=boto3.client("ssm", region_name=settings.region),
    )
    if settings.aoss_endpoint:
        from .aoss import AossClient

        deps.aoss = AossClient(settings.aoss_endpoint, settings.region)
    return deps


def get_deps() -> Deps:
    global _DEPS
    if _DEPS is None:
        _DEPS = build_default()
    return _DEPS


def set_deps(deps: Deps | None) -> None:
    global _DEPS
    _DEPS = deps

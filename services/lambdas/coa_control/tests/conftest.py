"""Fixtures for rp-coa-control: moto DynamoDB for the state row and the access log, stub CodeBuild,
CloudWatch and CloudFormation clients, and a clock the test moves."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
T0 = datetime(2026, 11, 11, 9, 0, tzinfo=UTC)
ADMIN = ("7c41e2aa-0000-4000-8000-00000000000a", ("lending-analyst", "rp-admin"))
VIEWER = ("9b11c3dd-0000-4000-8000-00000000000b", ("markets-analyst",))


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for k, v in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
                 "AWS_REGION": "us-east-1", "RP_ENV": "test", "RP_CONFIG_SOURCE": "local",
                 "RP_REPO_ROOT": str(REPO_ROOT)}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("AWS_PROFILE", raising=False)


class Clock:
    def __init__(self, now: datetime = T0) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw: float) -> datetime:
        self.now = self.now + timedelta(**kw)
        return self.now


@dataclass
class FakeCodeBuild:
    """start_build / stop_build / batch_get_builds / list_builds_for_project over an in-memory build list, with
    the project's concurrency limit of 1: a stopped build keeps the slot for ``stop_ticks`` more reads."""

    builds: dict[str, dict[str, Any]] = field(default_factory=dict)
    started: list[dict[str, Any]] = field(default_factory=list)
    stopped: list[str] = field(default_factory=list)
    fail_starts: int = 0
    fail_with: str = "AccountLimitExceededException: concurrent build limit"
    tokens: dict[str, str] = field(default_factory=dict)
    enforce_concurrency: bool = False
    stop_ticks: int = 0  # reads of a stopped build before it reaches STOPPED
    lose_response: bool = False  # the next start creates the build but raises (the answer never arrives)
    _stopping: dict[str, int] = field(default_factory=dict)

    def _busy(self) -> bool:
        return any(b["buildStatus"] == "IN_PROGRESS" for b in self.builds.values())

    def start_build(self, **kw: Any) -> dict[str, Any]:
        if self.fail_starts > 0:
            self.fail_starts -= 1
            raise RuntimeError(self.fail_with)
        token = kw.get("idempotencyToken")
        if token in self.tokens:
            return {"build": self.builds[self.tokens[token]]}
        if self.enforce_concurrency and self._busy():
            raise RuntimeError("AccountLimitExceededException: Cannot have more than 1 builds in queue")
        bid = f"rp-coa-lifecycle:{len(self.builds) + 1:04d}"
        env_list = list(kw.get("environmentVariablesOverride") or [])
        env = {e["name"]: e["value"] for e in env_list}
        build = {"id": bid, "buildStatus": "IN_PROGRESS", "phases": [{"phaseType": "SUBMITTED"}],
                 "logs": {"deepLink": f"https://console.example/logs/{bid}"},
                 "environment": {"environmentVariables": env_list}}
        self.builds[bid] = build
        self.tokens[token] = bid
        self.started.append({"id": bid, **env})
        if self.lose_response:
            self.lose_response = False
            raise RuntimeError("ReadTimeoutError: the answer never arrived")
        return {"build": build}

    def stop_build(self, id: str) -> dict[str, Any]:  # noqa: A002 - boto3 parameter name
        self.stopped.append(id)
        if self.stop_ticks:
            self._stopping[id] = self.stop_ticks
        else:
            self.builds[id]["buildStatus"] = "STOPPED"
        return {"build": self.builds[id]}

    def settle(self) -> None:
        """Time passes for builds being stopped."""
        for bid in list(self._stopping):
            self._stopping[bid] -= 1
            if self._stopping[bid] <= 0:
                self.builds[bid]["buildStatus"] = "STOPPED"
                del self._stopping[bid]

    def batch_get_builds(self, ids: list[str]) -> dict[str, Any]:
        return {"builds": [self.builds[i] for i in ids if i in self.builds]}

    def list_builds_for_project(self, projectName: str, sortOrder: str = "DESCENDING") -> dict[str, Any]:  # noqa: N803
        return {"ids": sorted(self.builds, reverse=True)}

    # test helpers
    def finish(self, bid: str, status: str = "SUCCEEDED", result: str | None = None,
               failed_phase: str | None = None, at: datetime | None = None,
               exported: dict[str, str] | None = None) -> dict[str, Any]:
        b = self.builds[bid]
        when = at or T0
        phases = [{"phaseType": p, "phaseStatus": "SUCCEEDED", "startTime": when, "endTime": when}
                  for p in ("SUBMITTED", "QUEUED", "PROVISIONING", "DOWNLOAD_SOURCE", "INSTALL", "PRE_BUILD", "BUILD",
                            "POST_BUILD", "UPLOAD_ARTIFACTS", "FINALIZING")]
        if failed_phase:
            idx = next(i for i, p in enumerate(phases) if p["phaseType"] == failed_phase)
            phases[idx]["phaseStatus"] = "FAILED"
            phases[idx]["contexts"] = [{"statusCode": "COMMAND_EXECUTION_ERROR",
                                        "message": "Error while executing command: make deploy-dev. Reason: exit 2"}]
            phases = phases[: idx + 1] + [{"phaseType": "FINALIZING", "phaseStatus": "SUCCEEDED",
                                           "startTime": when, "endTime": when}]
        phases.append({"phaseType": "COMPLETED", "startTime": when})
        b.update(buildStatus=status, phases=phases, endTime=when)
        variables = {**({"RP_RESULT": result} if result else {}), **(exported or {})}
        if variables:
            b["exportedEnvironmentVariables"] = [{"name": k, "value": v} for k, v in variables.items()]
        return b

    def running(self, bid: str, through: str = "BUILD") -> None:
        order = ["SUBMITTED", "QUEUED", "PROVISIONING", "DOWNLOAD_SOURCE", "INSTALL", "PRE_BUILD", "BUILD"]
        upto = order[: order.index(through) + 1]
        self.builds[bid]["phases"] = [{"phaseType": p, "phaseStatus": "SUCCEEDED", "startTime": T0, "endTime": T0}
                                      for p in upto[:-1]] + [{"phaseType": upto[-1], "startTime": T0}]


def _runtime_metric(name: str) -> dict[str, Any]:
    arn = f"arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/{name}-x"
    return {"Namespace": "AWS/Bedrock-AgentCore", "MetricName": "Invocations", "Dimensions": [
        {"Name": "Resource", "Value": arn}, {"Name": "Operation", "Value": "InvokeAgentRuntime"},
        {"Name": "Name", "Value": f"{name}::DEFAULT"}]}


@dataclass
class FakeCloudWatch:
    """get_metric_data answers from `points`: "api_gateway" (Count), "api_4xx" (4XXError), "agentcore" ->
    [(timestamp, value)]; the API expression is Count - 4XXError per timestamp."""

    points: dict[str, list[tuple[datetime, float]]] = field(default_factory=dict)
    fail: bool = False
    agentcore_metrics: list[dict[str, Any]] = field(default_factory=lambda: [
        _runtime_metric("coa_dev_mcp_server"), _runtime_metric("other_agent"),
        {**_runtime_metric("coa_dev_context_manager"), "Dimensions": [
            *_runtime_metric("coa_dev_context_manager")["Dimensions"], {"Name": "ComputeType", "Value": "MicroVM"}]},
    ])
    queries: list[dict[str, Any]] = field(default_factory=list)
    list_calls: list[dict[str, Any]] = field(default_factory=list)

    def get_paginator(self, name: str) -> Any:
        assert name == "list_metrics"
        metrics = self.agentcore_metrics
        calls = self.list_calls

        class _P:
            def paginate(self, **kw: Any) -> list[dict[str, Any]]:
                calls.append(kw)
                return [{"Metrics": metrics}]

        return _P()

    def _series(self, key: str) -> list[tuple[datetime, float]]:
        return sorted(self.points.get(key, []), reverse=True)

    def get_metric_data(self, **kw: Any) -> dict[str, Any]:
        if self.fail:
            raise RuntimeError("throttled")
        self.queries.append(kw)
        out = []
        for q in kw["MetricDataQueries"]:
            if not q.get("ReturnData", True):
                continue
            if "Expression" in q:
                errors = dict(self._series("api_4xx"))
                pts = [(ts, v - errors.get(ts, 0.0)) for ts, v in self._series("api_gateway")]
            else:
                pts = self._series("agentcore")
            out.append({"Id": q["Id"], "Timestamps": [p[0] for p in pts], "Values": [p[1] for p in pts]})
        return {"MetricDataResults": out}


@dataclass
class FakeCloudFormation:
    network_status: str | None = None
    console_url: str = "https://d111111abcdef8.cloudfront.net"
    rest: str = "https://abc123.execute-api.us-east-1.amazonaws.com/prod/"

    def describe_stacks(self, StackName: str) -> dict[str, Any]:  # noqa: N803 - boto3 parameter name
        from botocore.exceptions import ClientError

        if StackName == "coa-dev-network":
            if self.network_status is None:
                raise ClientError({"Error": {"Code": "ValidationError",
                                             "Message": "Stack with id coa-dev-network does not exist"}},
                                  "DescribeStacks")
            return {"Stacks": [{"StackStatus": self.network_status}]}
        if self.network_status is None:
            raise ClientError({"Error": {"Code": "ValidationError", "Message": "does not exist"}}, "DescribeStacks")
        outputs = {"coa-dev-web": [{"OutputKey": "PublicUIWebsiteURLB708A0CB", "OutputValue": self.console_url}],
                   "coa-dev-api": [{"OutputKey": "ApiEndpoint", "OutputValue": self.rest}]}
        return {"Stacks": [{"StackStatus": "CREATE_COMPLETE", "Outputs": outputs.get(StackName, [])}]}


@dataclass
class Env:
    svc: Any
    clock: Clock
    codebuild: FakeCodeBuild
    cloudwatch: FakeCloudWatch
    cfn: FakeCloudFormation
    table: Any
    audit_table: Any
    emitted: list[str] = field(default_factory=list)

    def item(self) -> Any:
        return self.svc.store.get("coa-dev")

    def audit_rows(self) -> list[dict[str, Any]]:
        return sorted(self.audit_table.scan()["Items"], key=lambda r: r["sk"])

    def api(self, op: str, body: dict[str, Any] | None = None, who: tuple[str, tuple[str, ...]] = ADMIN
            ) -> tuple[int, dict[str, Any]]:
        from lambdas.coa_control.service import Caller

        return self.svc.api(op, Caller(sub=who[0], groups=who[1]), body or {})


@pytest.fixture
def env() -> Iterator[Env]:
    from moto import mock_aws

    with mock_aws():
        import boto3
        from evidence_core.config import LocalConfigSource
        from evidence_core.ddb import create_tables_for_tests

        from lambdas.coa_control.handler import load_config
        from lambdas.coa_control.service import Activity, Audit, Executor, Outputs, Service, Store

        ddb = boto3.resource("dynamodb", region_name="us-east-1")
        create_tables_for_tests(ddb)
        table = ddb.create_table(TableName="rp-coa-env", BillingMode="PAY_PER_REQUEST",
                                 AttributeDefinitions=[{"AttributeName": "env", "AttributeType": "S"}],
                                 KeySchema=[{"AttributeName": "env", "KeyType": "HASH"}])
        audit_table = ddb.Table("rp-access-log")
        cfg = load_config(LocalConfigSource(root=REPO_ROOT))
        clock, cb, cw, cfn = Clock(), FakeCodeBuild(), FakeCloudWatch(), FakeCloudFormation()
        emitted: list[str] = []
        svc = Service(cfg=cfg, store=Store(table), executor=Executor(cb, cfg.executor.project),
                      activity=Activity(cw, cfg), outputs=Outputs(cfn, cfg), audit=Audit(audit_table), clock=clock,
                      sleep=lambda _s: None, emit=emitted.append)
        yield Env(svc=svc, clock=clock, codebuild=cb, cloudwatch=cw, cfn=cfn, table=table, audit_table=audit_table,
                  emitted=emitted)


def event_for(build_id: str, project: str = "rp-coa-lifecycle", kind: str = "CodeBuild Build State Change"
              ) -> dict[str, Any]:
    return {"source": "aws.codebuild", "detail-type": kind,
            "detail": {"project-name": project, "build-id": build_id, "build-status": "IN_PROGRESS"}}

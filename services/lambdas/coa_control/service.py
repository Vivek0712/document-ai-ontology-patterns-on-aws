"""I/O around ``machine``: the state row (DynamoDB, optimistic locking), the executor (CodeBuild ``rp-coa-lifecycle``),
activity (CloudWatch), COA's stack outputs (CloudFormation) and the audit rows (``rp-access-log``).

Three callers reach it through ``handler``:

- ``rp-api`` (``op`` get / up / down / settings / keep_alive / touch) with the caller's sub and groups from the
  authorizer. Every mutation needs the admin group; allowed and denied attempts are both audited.
- EventBridge, on every ``rp-coa-lifecycle`` state or phase change: the build is read back with ``BatchGetBuilds``
  and folded into the row (so a missed or duplicate event changes nothing).
- The idle watcher, every ``tickMinutes``: reconciles a build whose events were missed (and adopts a build whose
  start was never bound), retries a destroy while AgentCore holds ENIs, retries a failed tear-down once, checks
  CloudFormation while off (COA deployed by hand is watched too), reads activity and starts an automatic tear-down
  when idle ≥ the threshold or the session hits its cap. It also emits the ``Provenant/CoaEnv`` metrics (EMF) the
  needs-attention alarm watches.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from evidence_core.ddb import from_ddb, to_ddb
from evidence_core.ids import short
from evidence_core.logs import get_logger, log_kv
from evidence_core.models.coa_env import (
    ADMIN_GROUP,
    BACKGROUND_FIELDS,
    CoaControlConfig,
    CoaDownRequest,
    CoaEndpoints,
    CoaEniRetry,
    CoaEnvironmentView,
    CoaEnvItem,
    CoaKeepAliveRequest,
    CoaLifecycleInfo,
    CoaSettingsRequest,
    CoaUpRequest,
    CoaViewer,
)
from evidence_core.models.governance import AccessDecision, AccessInjected
from evidence_core.models.status import iso
from pydantic.alias_generators import to_camel

from . import machine as m

log = get_logger("rp-coa-control")
AUDIT_RUN_ID = "coa-environment"
AUDIT_TTL_DAYS = 30
ENDPOINTS_MAX_AGE = timedelta(minutes=30)
PENDING_RECOVERY_AFTER = timedelta(minutes=2)
HISTORY_IN_VIEW = 10
METRIC_NAMESPACE = "Provenant/CoaEnv"
# StartBuild errors that mean "not now" (the one concurrency slot is still held, throttling), not "never"
RETRYABLE_START = ("AccountLimitExceeded", "ResourceLimitExceeded", "LimitExceeded", "Throttl", "concurrent")


class Conflict(Exception):  # noqa: N818
    """The conditional write lost: someone changed the row since it was read."""


# --- adapters ------------------------------------------------------------------------------------------------

@dataclass
class Store:
    table: Any  # boto3 DynamoDB Table rp-coa-env

    def get(self, env: str) -> CoaEnvItem | None:
        row = self.table.get_item(Key={"env": env}, ConsistentRead=True).get("Item")
        return CoaEnvItem.load(from_ddb(row)) if row else None

    def put(self, item: CoaEnvItem, expected_version: int | None) -> CoaEnvItem:
        """Write ``item`` with ``version + 1`` if the stored version is still ``expected_version`` (``None``: the
        row must not exist yet). An update writes every state-machine attribute and none of ``BACKGROUND_FIELDS``,
        so a background write that landed after ``item`` was read is never overwritten by a stale copy."""
        from botocore.exceptions import ClientError

        nxt = item.model_copy(update={"version": (expected_version or 0) + 1})
        dumped = nxt.dump()
        try:
            if expected_version is None:
                self.table.put_item(Item=to_ddb(dumped), ConditionExpression="attribute_not_exists(#e)",
                                    ExpressionAttributeNames={"#e": "env"})
                return nxt
            skip = {"env", *(to_camel(f) for f in BACKGROUND_FIELDS)}
            sets, removes, names, values = [], [], {"#v": "version"}, {":v": expected_version}
            for i, name in enumerate(to_camel(f) for f in CoaEnvItem.model_fields):
                if name in skip:
                    continue
                names[f"#f{i}"] = name
                if name in dumped:
                    sets.append(f"#f{i} = :f{i}")
                    values[f":f{i}"] = to_ddb(dumped[name])
                else:
                    removes.append(f"#f{i}")
            expr = "SET " + ", ".join(sets) + (" REMOVE " + ", ".join(removes) if removes else "")
            self.table.update_item(Key={"env": item.env}, UpdateExpression=expr, ConditionExpression="#v = :v",
                                   ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
                raise Conflict(str(exc)) from exc
            raise
        return nxt

    def set_fields(self, env: str, **fields: Any) -> None:
        """Informational fields (``checked_at``, ``endpoints``, the activity error) without a version bump, so a
        background write never turns a reader's next action into a 409. ``None`` removes the attribute."""
        sets = {k: v for k, v in fields.items() if v is not None}
        drops = [k for k, v in fields.items() if v is None]
        names = {f"#{k}": to_camel(k) for k in fields}
        values = {f":{k}": to_ddb(v.dump() if hasattr(v, "dump") else v) for k, v in sets.items()}
        expr = ("SET " + ", ".join(f"#{k} = :{k}" for k in sets) if sets else "") + \
               (" REMOVE " + ", ".join(f"#{k}" for k in drops) if drops else "")
        kw: dict[str, Any] = {"ExpressionAttributeValues": values} if values else {}
        self.table.update_item(Key={"env": env}, UpdateExpression=expr.strip(),
                               ConditionExpression="attribute_exists(#env)",
                               ExpressionAttributeNames={**names, "#env": "env"}, **kw)

    def set_seen(self, env: str, source: str, at: str, count: float | None = None) -> None:
        """One background activity source's latest time (``seen.<source>``, and its request count), without a
        version bump. Only ever moves the time forward."""
        from botocore.exceptions import ClientError

        names = {"#a": "seen", "#s": source, "#env": "env"}
        values: dict[str, Any] = {":at": at}
        expr = "SET #a.#s = :at"
        if count is not None:
            names["#c"] = "seenCount"
            values[":n"] = to_ddb(count)
            expr += ", #c.#s = :n"
        cond = "attribute_exists(#env) AND (attribute_not_exists(#a.#s) OR #a.#s < :at)"
        try:
            self.table.update_item(Key={"env": env}, UpdateExpression=expr, ConditionExpression=cond,
                                   ExpressionAttributeNames=names, ExpressionAttributeValues=values)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code == "ConditionalCheckFailedException":
                return  # a later time is already there
            if code != "ValidationException":
                raise
            # a row written before the map existed: create both maps, then retry once
            self.table.update_item(Key={"env": env}, UpdateExpression="SET #a = if_not_exists(#a, :e), "
                                   "#c = if_not_exists(#c, :e)", ExpressionAttributeNames={"#a": "seen",
                                                                                          "#c": "seenCount"},
                                   ExpressionAttributeValues={":e": {}})
            self.table.update_item(Key={"env": env}, UpdateExpression=expr, ConditionExpression=cond,
                                   ExpressionAttributeNames=names, ExpressionAttributeValues=values)


@dataclass
class Executor:
    client: Any  # boto3 CodeBuild client
    project: str

    def start(self, *, operation: str, op_id: str, reason: str, by: str) -> dict[str, Any]:
        env = [{"name": "RP_OPERATION", "value": operation, "type": "PLAINTEXT"},
               {"name": "RP_OP_ID", "value": op_id, "type": "PLAINTEXT"},
               {"name": "RP_REASON", "value": reason[:200], "type": "PLAINTEXT"},
               {"name": "RP_REQUESTED_BY", "value": by, "type": "PLAINTEXT"}]
        resp = self.client.start_build(projectName=self.project, idempotencyToken=op_id,
                                       environmentVariablesOverride=env)
        return dict(resp["build"])

    def stop(self, build_id: str) -> None:
        self.client.stop_build(id=build_id)

    def get(self, build_id: str) -> dict[str, Any] | None:
        builds = self.client.batch_get_builds(ids=[build_id]).get("builds") or []
        return dict(builds[0]) if builds else None

    def find_by_op(self, op_id: str, newest: int = 20) -> dict[str, Any] | None:
        """The build started for claim ``op_id`` (its ``RP_OP_ID``), among the project's newest builds. CodeBuild
        honours an idempotency token for 5 minutes only, so a claim recovered later looks here before starting."""
        ids = (self.client.list_builds_for_project(projectName=self.project, sortOrder="DESCENDING").get("ids")
               or [])[:newest]
        if not ids:
            return None
        for build in self.client.batch_get_builds(ids=ids).get("builds") or []:
            if m.build_op_id(build) == op_id:
                return dict(build)
        return None


@dataclass
class Activity:
    """Activity on COA itself: COA REST API requests (API Gateway ``Count``) and AgentCore runtime invocations."""

    cloudwatch: Any
    cfg: CoaControlConfig

    BATCH = 400  # GetMetricData takes up to 500 queries per call

    def observe(self, now: datetime, lookback_minutes: int) -> dict[str, tuple[str, float]]:
        """Per source, the end of the newest 5-minute period with activity and the count in it. COA REST requests
        are ``Count - 4XXError`` (unauthenticated calls and scanners are not activity) and need at least
        ``minRequestsPerPeriod``; AgentCore counts every invocation of COA's own runtimes."""
        start = now - timedelta(minutes=lookback_minutes)
        api = self.cfg.idle.activity.api_gateway
        dims = [{"Name": "ApiName", "Value": api.api_name}]
        api_queries = [
            {"Id": "apic", "ReturnData": False, "MetricStat": {"Metric": {
                "Namespace": api.namespace, "MetricName": api.metric, "Dimensions": dims}, "Period": 300,
                "Stat": "Sum"}},
            {"Id": "apie", "ReturnData": False, "MetricStat": {"Metric": {
                "Namespace": api.namespace, "MetricName": api.error_metric, "Dimensions": dims}, "Period": 300,
                "Stat": "Sum"}},
            {"Id": "api", "ReturnData": True, "Expression": "FILL(apic, 0) - FILL(apie, 0)", "Period": 300},
        ]
        runtime = [{"Id": f"rt{i}", "ReturnData": True, "MetricStat": {"Metric": metric, "Period": 300,
                                                                          "Stat": "Sum"}}
                   for i, metric in enumerate(self._agentcore_metrics())]
        found: dict[str, tuple[str, float]] = {}
        batches = [api_queries] + [runtime[i:i + self.BATCH] for i in range(0, len(runtime), self.BATCH)]
        for batch in batches:
            for res in self._query(batch, start, now):
                rid = str(res["Id"])
                source = "api_gateway" if rid == "api" else "agentcore"
                floor = api.min_requests_per_period if source == "api_gateway" else 1
                for ts, value in zip(res.get("Timestamps") or [], res.get("Values") or [], strict=False):
                    if value is not None and value >= floor:
                        at = iso(min(ts.astimezone(UTC) + timedelta(seconds=300), now))
                        if source not in found or m.latest([found[source][0], at]) != found[source][0]:
                            found[source] = (at, float(value))
                        break
        return found

    def _query(self, queries: list[dict[str, Any]], start: datetime, end: datetime) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        token = None
        while True:
            kw: dict[str, Any] = {"MetricDataQueries": queries, "StartTime": start, "EndTime": end,
                                  "ScanBy": "TimestampDescending"}
            if token:
                kw["NextToken"] = token
            resp = self.cloudwatch.get_metric_data(**kw)
            out.extend(resp.get("MetricDataResults") or [])
            token = resp.get("NextToken")
            if not token:
                return out

    def _agentcore_metrics(self) -> list[dict[str, Any]]:
        """COA's runtimes with data in the last ``recentlyActive`` (ListMetrics otherwise returns two weeks of
        metrics, including runtimes of earlier deploys)."""
        a = self.cfg.idle.activity.agentcore
        out: list[dict[str, Any]] = []
        for page in self.cloudwatch.get_paginator("list_metrics").paginate(Namespace=a.namespace,
                                                                           MetricName=a.metric,
                                                                           RecentlyActive=a.recently_active):
            for metric in page.get("Metrics") or []:
                dims = {d["Name"]: d["Value"] for d in metric.get("Dimensions") or []}
                if set(dims) == {"Name", "Operation", "Resource"} and dims["Name"].startswith(a.name_prefix):
                    out.append({"Namespace": a.namespace, "MetricName": a.metric,
                                "Dimensions": metric["Dimensions"]})
        return out


@dataclass
class Outputs:
    cloudformation: Any
    cfg: CoaControlConfig

    def _output(self, stack: str, prefix: str) -> str | None:
        from botocore.exceptions import ClientError

        try:
            stacks = self.cloudformation.describe_stacks(StackName=stack).get("Stacks") or []
        except ClientError:
            return None
        for out in (stacks[0].get("Outputs") or []) if stacks else []:
            if str(out.get("OutputKey", "")).startswith(prefix):
                return str(out.get("OutputValue"))
        return None

    def read(self, now: datetime) -> CoaEndpoints:
        o = self.cfg.outputs
        return CoaEndpoints(console_url=self._output(o.console_url.stack, o.console_url.output_prefix),
                            rest_endpoint=self._output(o.rest_endpoint.stack, o.rest_endpoint.output_prefix),
                            read_at=iso(now))

    def probe(self) -> str | None:
        """The status of COA's network stack (``None``: it does not exist)."""
        from botocore.exceptions import ClientError

        try:
            stacks = self.cloudformation.describe_stacks(StackName=self.cfg.outputs.probe_stack).get("Stacks") or []
        except ClientError as exc:
            if "does not exist" in str(exc):
                return None
            raise
        return str(stacks[0].get("StackStatus")) if stacks else None


@dataclass
class Audit:
    table: Any | None  # rp-access-log (put only)

    def write(self, *, at: datetime, op: str, op_id: str, sub: str, groups: list[str], allowed: bool,
              reason: str) -> None:
        if self.table is None:
            return
        ts = iso(at)
        row = AccessDecision(at=ts, run_id=AUDIT_RUN_ID, call_id=op_id, tool=f"coa_environment.{op}",
                             target="rp-coa-control", caller_sub6=short(sub), groups=list(groups),
                             injected=AccessInjected(pipeline_version="", package_id=""),
                             injection_point="coa-control", decision="allow" if allowed else "deny",
                             reason=reason[:300], day=ts[:10],
                             ttl=int(at.timestamp()) + AUDIT_TTL_DAYS * 86400)
        try:
            self.table.put_item(Item=to_ddb(row.dump() | {"sk": f"{ts}#{op_id}"}))
        except Exception:  # noqa: BLE001 - the audit write never blocks the operation; it is logged
            log.exception("audit write failed", extra={"rp": {"op": op, "opId": op_id}})


# --- the service --------------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Caller:
    sub: str
    groups: tuple[str, ...]
    operator: str | None = None  # CLI only: the caller's own ARN suffix as the CLI reported it (audit text)

    @property
    def is_admin(self) -> bool:
        return ADMIN_GROUP in self.groups

    @property
    def tag(self) -> str:
        return "cli" if self.sub == "cli" else short(self.sub)


SYSTEM = Caller(sub=m.IDLE_WATCHER, groups=())




@dataclass
class Service:
    cfg: CoaControlConfig
    store: Store
    executor: Executor
    activity: Activity | None
    outputs: Outputs | None
    audit: Audit
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    sleep: Callable[[float], None] = time.sleep
    emit: Callable[[str], None] = print  # EMF lines (stdout -> CloudWatch Logs -> metrics)

    @property
    def env(self) -> str:
        return self.cfg.coa.stack_prefix

    # ---- reading ----

    def load(self) -> CoaEnvItem:
        """The row, created on first use from what CloudFormation says is deployed."""
        item = self.store.get(self.env)
        if item is not None:
            return item
        now = self.clock()
        status = self.outputs.probe() if self.outputs else None
        if status is None or status == "DELETE_COMPLETE":
            first = m.new_item(self.cfg, now, state="off", detected=True, reason="no COA stacks found")
        elif status in ("DELETE_IN_PROGRESS", "DELETE_FAILED"):
            first = m.new_item(self.cfg, now, state="partially_down", detected=True,
                               reason=f"{self.cfg.outputs.probe_stack} is {status}")
        else:
            first = m.new_item(self.cfg, now, state="running", detected=True,
                               reason=f"COA found deployed ({self.cfg.outputs.probe_stack} {status})")
            if self.outputs:
                first.endpoints = self.outputs.read(now)
        first.probed_at = iso(now)
        try:
            return self.store.put(first, None)
        except Conflict:
            return self.store.get(self.env) or first

    def view(self, item: CoaEnvItem, caller: Caller, *, outcome: str | None = None) -> CoaEnvironmentView:
        now = self.clock()
        eni = None
        if item.state == "partially_down" or item.eni_retries:
            first = m.parse(item.eni_first_at)
            eni = CoaEniRetry(retries=item.eni_retries, max_retries=self.cfg.lifecycle.eni_max_retries,
                              next_retry_at=item.next_retry_at,
                              window_ends_at=iso(first + timedelta(hours=self.cfg.lifecycle.eni_window_hours))
                              if first else None)
        lc = self.cfg.lifecycle
        show_endpoints = item.state in ("running", "starting") or (item.state == "failed"
                                                                   and item.failed_operation == "up")
        return CoaEnvironmentView(
            deployed=True, server_time=iso(now), state=item.state, failed_operation=item.failed_operation,
            version=item.version, state_since=item.state_since, running_since=item.running_since,
            session_started_at=item.session_started_at, stopped_at=item.stopped_at,
            reason=item.reason, build=item.build, settings=m.settings_of(item, self.cfg),
            idle=m.idle_view(item, self.cfg), cost=m.cost_of(item, self.cfg, now) if caller.is_admin else None,
            endpoints=item.endpoints if show_endpoints else None,
            error=item.error, eni=eni, history=list(reversed(item.history))[:HISTORY_IN_VIEW],
            viewer=CoaViewer(can_operate=caller.is_admin),
            lifecycle=CoaLifecycleInfo(up_minutes=lc.up_minutes, down_minutes=lc.down_minutes,
                                       eni_window_hours=lc.eni_window_hours, coa_tag=self.cfg.coa.tag,
                                       stack_prefix=self.cfg.coa.stack_prefix, project=self.cfg.executor.project),
            detected=item.detected, outcome=outcome,  # type: ignore[arg-type]
            note=None if caller.is_admin else f"Read only: changing the environment needs the {ADMIN_GROUP} group.",
            notice=item.notice)

    # ---- the API ----

    def api(self, op: str, caller: Caller, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        op_id = uuid.uuid4().hex[:16]
        now = self.clock()
        try:
            if op == "get":
                item = self.load()
                if m.probe_due(item, self.cfg, now):
                    item = self._probe(item, now)
                item = self._refresh_endpoints(item, now)
                return 200, self.view(item, caller).dump()
            if op == "touch":  # a Provenant call that reached live COA: activity, no admin needed
                item = self.load()
                self.store.set_seen(self.env, "provenant", iso(now))
                return 200, self.view(m.record_seen(item, "provenant", iso(now)), caller).dump()
            if op not in ("up", "down", "settings", "keep_alive"):
                return 404, _error("not_found", f"no operation {op!r}")
            if not caller.is_admin:
                self._audit(now, op, op_id, caller, False, f"not in {ADMIN_GROUP}")
                return 403, _error("forbidden", f"Changing the COA environment needs the {ADMIN_GROUP} group.")
            status, view = self._mutate(op, op_id, caller, body)
            return status, view.dump()
        except m.Refusal as exc:
            if op in ("up", "down", "settings", "keep_alive"):
                self._audit(now, op, op_id, caller, False, f"refused: {exc.message}")
            return exc.status, _error(exc.code, exc.message)
        except Conflict:
            return 409, _error("conflict", "The environment changed while this request ran; reload and try again.")
        except ValueError as exc:  # pydantic validation of the body
            return 400, _error("bad_request", str(exc).splitlines()[0][:300])

    def _mutate(self, op: str, op_id: str, caller: Caller, body: dict[str, Any]) -> tuple[int, CoaEnvironmentView]:
        now = self.clock()
        item = self.load()
        if op == "settings":
            req = CoaSettingsRequest.model_validate(body)
            m.check_version(item, req.expected_version)
            saved = self.store.put(m.update_settings(item, self.cfg, auto_teardown=req.auto_teardown,
                                                     idle_minutes=req.idle_minutes), item.version)
            self._audit(now, op, op_id, caller, True,
                        f"auto tear-down {'on' if req.auto_teardown else 'off'}, idle {req.idle_minutes} min")
            return 200, self.view(saved, caller, outcome="updated")
        if op == "keep_alive":
            req_k = CoaKeepAliveRequest.model_validate(body)
            m.check_version(item, req_k.expected_version)
            saved = self.store.put(m.keep_alive(item, self.cfg, now), item.version)
            self._audit(now, op, op_id, caller, True, f"keep alive until {saved.keep_alive_until}")
            return 200, self.view(saved, caller, outcome="updated")
        if op == "up":
            req_u = CoaUpRequest.model_validate(body)
            m.check_version(item, req_u.expected_version)
            plan = m.request_up(item, self.cfg, now, by=caller.tag, op_id=op_id,
                                idempotency_key=req_u.idempotency_key)
        else:
            req_d = CoaDownRequest.model_validate(body)
            m.check_version(item, req_d.expected_version)
            plan = m.request_down(item, self.cfg, now, by=caller.tag, op_id=op_id, force=req_d.force,
                                  idempotency_key=req_d.idempotency_key)
        if not plan.starts:
            self._audit(now, op, op_id, caller, True, f"no-op ({item.state})")
            return 200, self.view(item, caller, outcome="noop")
        self._audit(now, op, op_id, caller, True, plan.item.reason or op)
        saved = self._launch(plan, item.version, op_id)
        return 202, self.view(saved, caller, outcome=plan.outcome)

    def _audit(self, now: datetime, op: str, op_id: str, caller: Caller, allowed: bool, reason: str) -> None:
        if caller.operator:
            reason = f"{reason} (cli: {caller.operator})"
        self.audit.write(at=now, op=op, op_id=op_id, sub=caller.sub, groups=list(caller.groups), allowed=allowed,
                         reason=reason)

    # ---- starting a build ----

    def _launch(self, plan: m.Plan, expected_version: int, op_id: str) -> CoaEnvItem:
        claimed = self.store.put(plan.item, expected_version)  # the lock: one claim per version
        if plan.stop_build:
            try:
                self.executor.stop(plan.stop_build)
            except Exception:  # noqa: BLE001 - it may have just finished; the start below decides
                log.exception("stop_build failed", extra={"rp": {"buildId": plan.stop_build}})
        return self._start_claimed(claimed, op_id, retries=6 if plan.stop_build else 1)

    def _start_claimed(self, item: CoaEnvItem, op_id: str, *, retries: int = 1, defer: bool = True) -> CoaEnvItem:
        """StartBuild for a claimed row. A retryable refusal (the concurrency slot is still held by a stopped
        build, throttling) leaves the claim pending for the idle watcher, which keeps trying until
        ``PENDING_START_GIVE_UP``; any other refusal fails the operation now."""
        assert item.build is not None
        claim_id = item.build.id
        last_error: Exception | None = None
        build: dict[str, Any] | None = None
        for attempt in range(retries):
            try:
                build = self.executor.start(operation=item.build.operation, op_id=op_id, reason=item.build.reason,
                                            by=item.build.requested_by)
                break
            except Exception as exc:  # noqa: BLE001 - classified below
                last_error = exc
                if not _retryable(exc):
                    break
                if attempt + 1 < retries:
                    self.sleep(3.0)
        if build is None:  # the answer may be lost while the build was created: look for it before deciding
            build = self._find(op_id)
        if build is None:
            message = str(last_error)[:300]
            if defer and _retryable(last_error):
                log_kv(log, "start deferred", opId=op_id, error=message[:200])
                return item
            return self._write(lambda it, msg=message: m.start_failed(it, self.clock(), msg)
                               if it.build and it.build.id == claim_id else it)
        return self._bind(claim_id, build, op_id)

    def _find(self, op_id: str) -> dict[str, Any] | None:
        try:
            return self.executor.find_by_op(op_id)
        except Exception:  # noqa: BLE001 - logged; treated as not found
            log.exception("find_by_op failed")
            return None

    def _bind(self, claim_id: str, build: dict[str, Any], op_id: str) -> CoaEnvItem:
        """Swap the claim for the real build id. Losing every write race is not an error: the build runs, and its
        first event (or the next tick, by ``RP_OP_ID``) binds it."""

        def bind(it: CoaEnvItem) -> CoaEnvItem:
            if not it.build or it.build.id != claim_id:
                return it
            nxt = it.model_copy(deep=True)
            nxt.build.id = str(build["id"])  # type: ignore[union-attr]
            nxt.build.status = str(build.get("buildStatus") or "IN_PROGRESS")  # type: ignore[union-attr]
            nxt.build.log_url = (build.get("logs") or {}).get("deepLink")  # type: ignore[union-attr]
            return nxt

        try:
            bound = self._write(bind, attempts=5)
        except Conflict:
            log_kv(log, "bind deferred", buildId=str(build["id"]), opId=op_id)
            return self.load()
        log_kv(log, "build started", buildId=str(build["id"]), opId=op_id)
        return bound

    def _write(self, change: Callable[[CoaEnvItem], CoaEnvItem], attempts: int = 3) -> CoaEnvItem:
        """Read, change, conditionally write; re-read on a lost race."""
        for _ in range(attempts):
            item = self.load()
            nxt = change(item)
            if nxt is item or nxt == item:
                return item
            try:
                return self.store.put(nxt, item.version)
            except Conflict:
                continue
        raise Conflict("too many concurrent writers")

    def _refresh_endpoints(self, item: CoaEnvItem, now: datetime) -> CoaEnvItem:
        if self.outputs is None or item.state != "running":
            return item
        read = m.parse(item.endpoints.read_at) if item.endpoints else None
        if read is not None and now - read < ENDPOINTS_MAX_AGE and item.endpoints and item.endpoints.rest_endpoint:
            return item
        endpoints = self.outputs.read(now)
        self.store.set_fields(self.env, endpoints=endpoints)
        return item.model_copy(update={"endpoints": endpoints})

    def _probe(self, item: CoaEnvItem, now: datetime) -> CoaEnvItem:
        """Off (or failed after a tear-down): what does CloudFormation say? COA deployed outside this control
        becomes running, so the idle watcher applies to it; a failed tear-down with nothing left becomes off."""
        if self.outputs is None:
            return item
        try:
            status = self.outputs.probe()
        except Exception:  # noqa: BLE001 - logged; the next probe tries again
            log.exception("probe failed")
            return item
        self.store.set_fields(self.env, probed_at=iso(now))
        item = item.model_copy(update={"probed_at": iso(now)})
        deployed = status is not None and status not in ("DELETE_COMPLETE", "DELETE_IN_PROGRESS", "DELETE_FAILED")

        def change(it: CoaEnvItem) -> CoaEnvItem:
            if it.state != item.state or it.version != item.version:
                return it  # something else moved it meanwhile
            if it.state == "off" and deployed:
                nxt = m.found_deployed(it, now, f"{self.cfg.outputs.probe_stack} {status}")
                if self.outputs:
                    nxt.endpoints = self.outputs.read(now)
                return nxt
            if it.state == "off" and status == "DELETE_FAILED":
                return m.found_delete_failed(it, self.cfg, now, status)
            if it.state == "failed" and status is None:
                return m.found_gone(it, now)
            return it

        saved = self._write(change)
        if saved.version != item.version:
            self.audit.write(at=now, op="detect", op_id=uuid.uuid4().hex[:16], sub=SYSTEM.sub, groups=[],
                             allowed=True, reason=f"{item.state} -> {saved.state}: {saved.reason}")
        return saved

    # ---- the executor's events ----

    def on_build_event(self, event: dict[str, Any]) -> CoaEnvItem | None:
        detail = event.get("detail") or {}
        if detail.get("project-name") != self.cfg.executor.project:
            return None
        build_id = str(detail.get("build-id") or "")
        build = self.executor.get(build_id) if build_id else None
        if not build:
            return None
        return self._fold(build)

    def _fold(self, build: dict[str, Any]) -> CoaEnvItem | None:
        changed: list[CoaEnvItem] = []

        def apply(it: CoaEnvItem) -> CoaEnvItem:
            nxt = m.apply_build(it, self.cfg, self.clock(), build)
            if nxt is None:
                return it
            if nxt.state == "running" and it.state != "running" and self.outputs:
                nxt.endpoints = self.outputs.read(self.clock())
            changed.append(nxt)
            return nxt

        item = self._write(apply)
        if changed:
            log_kv(log, "build folded", buildId=build.get("id"), status=build.get("buildStatus"), state=item.state)
        return item if changed else None

    # ---- the idle watcher ----

    def tick(self) -> dict[str, Any]:
        now = self.clock()
        item = self.load()
        action = "none"
        if item.state in ("starting", "stopping") and item.build:
            if item.build.id.startswith(m.PENDING_PREFIX):
                action = self._recover_pending(item, now)
            else:  # never act while a build runs; only reconcile a finished build whose event was missed
                build = self.executor.get(item.build.id)
                if build and build.get("buildStatus") in m.TERMINAL and self._fold(build):
                    action = "reconciled"
        elif m.eni_retry_due(item, now):
            plan = m.request_down(item, self.cfg, now, by=m.IDLE_WATCHER, op_id=uuid.uuid4().hex[:16],
                                  reason=f"retry {item.eni_retries}: waiting for AgentCore ENIs")
            op_id = plan.item.build.id.removeprefix(m.PENDING_PREFIX)  # type: ignore[union-attr]
            self.audit.write(at=now, op="down", op_id=op_id, sub=SYSTEM.sub, groups=[], allowed=True,
                             reason=plan.item.reason or "eni retry")
            self._launch(plan, item.version, op_id)
            action = "eni_retry"
        elif m.failed_down_retry_due(item, self.cfg, now):
            op_id = uuid.uuid4().hex[:16]
            plan = m.request_failed_down_retry(item, self.cfg, now, op_id=op_id)
            self.audit.write(at=now, op="down", op_id=op_id, sub=SYSTEM.sub, groups=[], allowed=True,
                             reason=plan.item.reason or "failed tear-down retry")
            self._launch(plan, item.version, op_id)
            action = "failed_down_retry"
        elif m.probe_due(item, self.cfg, now):
            before = item.state
            item = self._probe(item, now)
            if item.state != before:
                action = f"detected {item.state}"
        elif m.auto_teardown_applies(item):
            item, observed = self._observe(item, now)
            due = m.idle_due(item, self.cfg, now) if observed else None
            if due is not None:
                op_id = uuid.uuid4().hex[:16]
                plan = m.request_down(item, self.cfg, now, by=m.IDLE_WATCHER, op_id=op_id, reason=due)
                self.audit.write(at=now, op="down", op_id=op_id, sub=SYSTEM.sub, groups=[], allowed=True,
                                 reason=f"automatic tear-down: {due}"
                                        + (f" ≥ {item.idle_minutes} min" if due.startswith("idle") else ""))
                self._launch(plan, item.version, op_id)
                action = f"teardown {due}"
        self._stamp(now)
        self._metrics(now)
        log_kv(log, "tick", state=item.state, action=action)
        return {"state": item.state, "action": action}

    def _recover_pending(self, item: CoaEnvItem, now: datetime) -> str:
        """A claim whose StartBuild never bound: adopt the build it did start (looked up by ``RP_OP_ID``: the
        idempotency token lasts only 5 minutes), else start it again while under ``PENDING_START_GIVE_UP``, then
        give up to failed."""
        assert item.build is not None
        claim_id = item.build.id
        op_id = claim_id.removeprefix(m.PENDING_PREFIX)
        started = m.parse(item.build.started_at) or now
        if now - started < PENDING_RECOVERY_AFTER:
            return "none"  # the API call may still be starting it
        found = self._find(op_id)
        if found is not None:
            self._bind(claim_id, found, op_id)
            if found.get("buildStatus") in m.TERMINAL:
                self._fold(found)
            return "adopted_pending"
        if m.pending_start_stale(item, now):
            self._write(lambda it: m.start_failed(it, now, f"no build started within "
                                                            f"{int(m.PENDING_START_GIVE_UP.total_seconds() // 60)} "
                                                            "minutes")
                        if it.build and it.build.id == claim_id else it)
            return "gave_up_pending_start"
        after = self._start_claimed(item, op_id, retries=1, defer=True)
        return "started_pending" if after.build and after.build.id != claim_id else "pending_deferred"

    def _observe(self, item: CoaEnvItem, now: datetime) -> tuple[CoaEnvItem, bool]:
        """Read COA's own activity into the row. While CloudWatch cannot be read no tear-down starts on that
        account; the error is recorded and shown, and once it has lasted longer than the idle threshold the
        watcher decides on the activity it last saw rather than wait forever."""
        if self.activity is None:
            return item, True
        try:
            seen = self.activity.observe(now, min(item.idle_minutes + 10, self.cfg.idle.max_minutes + 10))
        except Exception as exc:  # noqa: BLE001 - recorded; the next tick reads again
            log.exception("activity read failed")
            since = item.activity_error_since or iso(now)
            message = f"CloudWatch could not be read: {type(exc).__name__}: {str(exc)[:160]}"
            self.store.set_fields(self.env, activity_error=message, activity_error_since=since)
            item = item.model_copy(update={"activity_error": message, "activity_error_since": since})
            failing = now - (m.parse(since) or now)
            return item, failing >= timedelta(minutes=item.idle_minutes)
        out = item
        if item.activity_error:
            self.store.set_fields(self.env, activity_error=None, activity_error_since=None)
            out = item.model_copy(update={"activity_error": None, "activity_error_since": None})
        for source, (at, count) in seen.items():
            nxt = m.record_seen(out, source, at, count)
            if nxt is not out:
                self.store.set_seen(self.env, source, nxt.seen[source], count)
                out = nxt
        return out, True

    def _stamp(self, now: datetime) -> None:
        from botocore.exceptions import ClientError

        try:
            self.store.set_fields(self.env, checked_at=iso(now))
        except ClientError:
            log.exception("checked_at write failed")

    def _metrics(self, now: datetime) -> None:
        """``Provenant/CoaEnv`` NeedsAttention (alarm rp-coa-env-needs-attention) and Billable, as one EMF line."""
        item = self.store.get(self.env)
        if item is None:
            return
        record = {
            "_aws": {"Timestamp": int(now.timestamp() * 1000), "CloudWatchMetrics": [{
                "Namespace": METRIC_NAMESPACE, "Dimensions": [["Environment"]],
                "Metrics": [{"Name": "NeedsAttention", "Unit": "Count"}, {"Name": "Billable", "Unit": "Count"}]}]},
            "Environment": self.env, "State": item.state,
            "NeedsAttention": 1 if m.needs_attention(item, now) else 0,
            "Billable": 0 if item.state == "off" else 1,
        }
        self.emit(json.dumps(record))


def _retryable(exc: Exception | None) -> bool:
    text = f"{type(exc).__name__}: {exc}" if exc else ""
    return any(k.lower() in text.lower() for k in RETRYABLE_START)


def _error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}

"""GET /access/decisions?runId=|since= (SC-7, W-13): what the gateway injected into each tool call.

- ``runId``: the run's decisions, after the package ACL of the run's package (``runs.load_run``). Rows come from
  ``rp-access-log`` (the REQUEST interceptor writes one per ``tools/call``). A run with no rows there (logged before
  the table existed, or asked through the local gateway) is answered from its RunRecord's tool calls, which logged
  the same injected values; those rows say ``source: run-record`` and carry the tool's decision from the call's
  logged result (``deny`` for a refusal, ``unknown`` for another error).
- ``since`` (ISO time): the caller's own decisions from that time (GSI ``bySince``, a day per query, at most 7
  days), newest first. A caller never sees another user's rows here.

No argument values leave the route: only the names of the arguments the model supplied, and the identity keys it
supplied that the gateway removed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError
from evidence_core.ids import short
from evidence_core.models.answer import RunRecord, ToolCallRecord
from evidence_core.models.governance import AccessDecision, AccessDecisionPage, AccessInjected, Decision
from evidence_core.models.tools import INJECTED_FIELDS

from .. import store
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request
from . import ACCESS_DECISIONS, router
from .runs import load_run

MAX_SINCE_DAYS = 7
# Gateway target names (the CDK/AgentCore target ids) of the v1 tools
TARGETS = {"search_units": "search-units", "graph_hop": "graph-hop", "get_region": "get-region",
           "doc_status": "doc-status"}


def record_decision(call: ToolCallRecord) -> tuple[Decision, str | None]:
    """What the tool decided, read from the call's logged result: a refusal (``status: denied`` or an error coded
    ``forbidden``) is ``deny``; a result is ``allow``; any other error never reached a decision we can read, so it
    is ``unknown``. The gateway's own decision is not in a RunRecord (it is only in ``rp-access-log``)."""
    error = (call.output or {}).get("error") if isinstance(call.output, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if call.status == "denied" or code == "forbidden":
        return "deny", call.error or (error.get("message") if isinstance(error, dict) else None)
    if call.status == "ok":
        return "allow", None
    return "unknown", call.error


def decisions_from_record(run: RunRecord) -> list[AccessDecision]:
    """One row per logged tool call, from the injected values the RunRecord kept (``ToolCallRecord.injected``) and
    the tool's logged result (``record_decision``): a call the tool refused is a ``deny`` row, never ``allow``."""
    out = []
    for c in run.tool_calls:
        inj = c.injected or {}
        model_keys = sorted(c.input or {})
        decision, reason = record_decision(c)
        out.append(AccessDecision(
            at=c.started_at, run_id=run.run_id, call_id=c.call_id, tool=c.tool, target=TARGETS.get(c.tool, c.tool),
            caller_sub6=short(str(inj.get("callerSub") or run.caller_sub)),
            groups=list(inj.get("groups") or run.groups),
            injected=AccessInjected(pipeline_version=str(inj.get("pipelineVersion") or run.pipeline_version),
                                     package_id=str(inj.get("packageId") or run.package_id),
                                     run_id=inj.get("runId") or run.run_id),
            injection_point=_point(str(inj.get("via") or "")), decision=decision, reason=reason,
            stripped_model_keys=[k for k in model_keys if k in INJECTED_FIELDS],
            model_args=[k for k in model_keys if k not in INJECTED_FIELDS], source="run-record"))
    return out


def _point(via: str) -> Any:
    return via if via in ("interceptor", "agent_hook", "local-gateway") else "interceptor"


def _table(deps: Deps) -> Any | None:
    try:
        return deps.table("access_log")
    except KeyError:  # an older Settings without the table
        return None


def _query(table: Any, **kwargs: Any) -> list[dict[str, Any]] | None:
    """Rows, or None when the table does not exist (an older stack)."""
    try:
        return list(store.query_all(table, **kwargs))
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise


RECORD_NOTE = ("No rp-access-log rows for this run; rows rebuilt from the run record's logged tool calls. The "
               "decision is the tool's (its logged result); the gateway's own decision is only in rp-access-log.")


def access_rows(deps: Deps, run: RunRecord) -> list[AccessDecision]:
    """The run's decisions: ``rp-access-log`` rows when there are any, else rebuilt from the RunRecord."""
    table = _table(deps)
    rows = _query(table, KeyConditionExpression=Key("runId").eq(run.run_id)) if table is not None else None
    if rows:
        return sorted((AccessDecision.load(r) for r in rows), key=lambda d: (d.at, d.call_id))
    return decisions_from_record(run)


@router.route("GET", ACCESS_DECISIONS)
def access_decisions(req: Request) -> Reply:
    run_id, since = req.query.get("runId"), req.query.get("since")
    if bool(run_id) == bool(since):
        raise bad_request("pass exactly one of runId or since")
    deps = get_deps()
    table = _table(deps)
    if run_id:
        run = load_run(deps, req.caller, run_id)
        items = access_rows(deps, run)
        note = (RECORD_NOTE if items and all(d.source == "run-record" for d in items) else None)
        return Reply(AccessDecisionPage(items=items, run_id=run.run_id, note=note))
    try:
        start = datetime.fromisoformat(str(since).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError as exc:
        raise bad_request("since must be an ISO time") from exc
    now = datetime.now(UTC)
    if start < now - timedelta(days=MAX_SINCE_DAYS):
        raise bad_request(f"since may be at most {MAX_SINCE_DAYS} days ago")
    me = short(req.caller.sub)
    items: list[AccessDecision] = []
    day = start.date()
    while table is not None and day <= now.date():
        rows = _query(table, IndexName="bySince",
                      KeyConditionExpression=Key("day").eq(day.isoformat()) & Key("at").gte(start.isoformat())) or []
        items += [d for d in (AccessDecision.load(r) for r in rows) if d.caller_sub6 == me]
        day += timedelta(days=1)
    items.sort(key=lambda d: d.at, reverse=True)
    return Reply(AccessDecisionPage(items=items[:200], since=start.isoformat()))

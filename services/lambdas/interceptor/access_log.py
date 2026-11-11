"""One ``rp-access-log`` row per ``tools/call`` the interceptor decides (SC-7, W-13).

The row says who called (sub, 6 characters, and groups), what was injected, the injection point, and the decision
with its reason. It never holds argument values: only the names the model supplied and the identity keys it supplied
that the interceptor removed. Writing is best effort: a failed write is logged and never blocks or alters the call
(the decision is already made). The table is optional: without ``RP_TABLE_ACCESS_LOG`` nothing is written.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from typing import Any

from evidence_core.ddb import to_ddb
from evidence_core.ids import short
from evidence_core.logs import get_logger
from evidence_core.models.governance import AccessDecision, AccessInjected
from evidence_core.models.tools import INJECTED_FIELDS

log = get_logger("rp.interceptor.access")
TTL_DAYS = 30
TOOL_NAME_DELIMITER = "___"  # AgentCore Gateway target___tool

_TABLE: Any = None


def use_table(table: Any) -> None:
    global _TABLE
    _TABLE = table


def table() -> Any | None:
    global _TABLE
    if _TABLE is None:
        name = os.environ.get("RP_TABLE_ACCESS_LOG")
        if not name:
            return None
        import boto3

        _TABLE = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "us-east-1")).Table(name)
    return _TABLE


def decision_row(*, gateway_tool: str | None, call_id: Any, sub: str | None, groups: list[str],
                 values: dict[str, Any] | None, model_args: dict[str, Any] | None, decision: str,
                 reason: str | None = None, now: datetime | None = None) -> AccessDecision:
    at = (now or datetime.now(UTC)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    target, _, tool = (gateway_tool or "").partition(TOOL_NAME_DELIMITER)
    supplied = sorted(model_args or {})
    v = values or {}
    return AccessDecision(
        at=at, run_id=str(v.get("runId") or "no-run"), call_id=str(call_id if call_id is not None else "-"),
        tool=tool or target or "unknown", target=target or "unknown", caller_sub6=short(sub or ""),
        groups=list(groups), injected=AccessInjected(pipeline_version=str(v.get("pipelineVersion") or ""),
                                                     package_id=str(v.get("packageId") or ""), run_id=v.get("runId")),
        injection_point="interceptor", decision=decision, reason=reason,  # type: ignore[arg-type]
        stripped_model_keys=[k for k in supplied if k in INJECTED_FIELDS],
        model_args=[k for k in supplied if k not in INJECTED_FIELDS], source="access-log", day=at[:10],
        ttl=int(time.time()) + TTL_DAYS * 86400)


def write(row: AccessDecision) -> None:
    t = table()
    if t is None:
        return
    try:
        t.put_item(Item=to_ddb(row.dump() | {"sk": f"{row.at}#{row.call_id}"}))
    except Exception:  # noqa: BLE001 - best effort; the decision stands
        log.exception("access log write failed", extra={"rp": {"runId": row.run_id, "tool": row.tool}})

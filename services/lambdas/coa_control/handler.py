"""Lambda ``rp-coa-control`` (stack ``rp-coa-control``, context ``rp:coaControl``).

One function, three event shapes:

- ``{"source": "rp-api", "op": …, "caller": {"sub", "groups"}, "body": {…}}``: a ``/coa/environment`` route. Only
  ``rp-api`` may invoke it (IAM); the caller identity is the authorizer's, copied by ``rp-api``.
- ``{"source": "aws.codebuild", …}``: a state or phase change of ``rp-coa-lifecycle`` (EventBridge rule).
- ``{"op": "tick"}``: the idle watcher (EventBridge schedule, every ``idle.tickMinutes``).
- ``{"source": "cli", "op": …, "operator": "<arn suffix>"}``: ``scripts/coa_env.sh``. IAM already decided
  (``lambda:InvokeFunction`` on this function), so the caller is an admin, audited as ``cli`` with the operator the
  script reported (from ``sts get-caller-identity``; self-reported, so it is audit text, not an identity check).

Who may invoke the function is the trust boundary for the ``rp-api`` and ``cli`` shapes: only ``rp-api``'s role is
granted it in this app (infra test), and any principal that holds ``lambda:InvokeFunction`` on it (an account admin)
could equally start the CodeBuild project directly. Decision 0006, "Residual breadth".
"""

from __future__ import annotations

import os
from typing import Any

from evidence_core.config import ConfigSource, default_source
from evidence_core.logs import get_logger
from evidence_core.models.coa_env import ADMIN_GROUP, COA_ENV_TABLE, COA_ENV_TABLE_ENV, CONFIG_KEY, parse_config
from evidence_core.prices import PRICES_KEY

from .service import Activity, Audit, Caller, Executor, Outputs, Service, Store

log = get_logger("rp-coa-control")
_SERVICE: Service | None = None


def load_config(source: ConfigSource | None = None) -> Any:
    src = source or default_source()
    coa_text, _ = src.read_text(CONFIG_KEY)
    prices_text, _ = src.read_text(PRICES_KEY)
    return parse_config(coa_text, prices_text)


def build_service() -> Service:
    import boto3

    region = os.environ.get("AWS_REGION", "us-east-1")
    cfg = load_config()
    ddb = boto3.resource("dynamodb", region_name=region)
    access_log = os.environ.get("RP_TABLE_ACCESS_LOG")
    return Service(
        cfg=cfg,
        store=Store(ddb.Table(os.environ.get(COA_ENV_TABLE_ENV, COA_ENV_TABLE["name"]))),
        executor=Executor(boto3.client("codebuild", region_name=region),
                          os.environ.get("RP_COA_PROJECT", cfg.executor.project)),
        activity=Activity(boto3.client("cloudwatch", region_name=region), cfg),
        outputs=Outputs(boto3.client("cloudformation", region_name=region), cfg),
        audit=Audit(ddb.Table(access_log) if access_log else None),
    )


def get_service() -> Service:
    global _SERVICE
    if _SERVICE is None:
        _SERVICE = build_service()
    return _SERVICE


def set_service(service: Service | None) -> None:
    global _SERVICE
    _SERVICE = service


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    svc = get_service()
    if event.get("source") == "rp-api":
        caller = event.get("caller") or {}
        status, body = svc.api(str(event.get("op") or ""),
                               Caller(sub=str(caller.get("sub") or ""), groups=tuple(caller.get("groups") or ())),
                               dict(event.get("body") or {}))
        return {"status": status, "body": body}
    if event.get("source") == "cli":
        operator = str(event.get("operator") or "unknown")[-64:]
        status, body = svc.api(str(event.get("op") or ""),
                               Caller(sub="cli", groups=(ADMIN_GROUP,), operator=operator),
                               dict(event.get("body") or {}))
        return {"status": status, "body": body}
    if event.get("source") == "aws.codebuild":
        item = svc.on_build_event(event)
        return {"state": item.state if item else None}
    if event.get("op") == "tick":
        return svc.tick()
    log.warning("ignored event", extra={"rp": {"keys": sorted(event)[:10]}})
    return {"ignored": True}

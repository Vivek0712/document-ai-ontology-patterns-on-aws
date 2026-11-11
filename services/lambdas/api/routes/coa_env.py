"""COA environment lifecycle (Settings → COA environment, decision record 0006).

``rp-api`` owns the routes (one REST API, one authorizer) and forwards each request to ``rp-coa-control`` with
the caller's sub and groups from the authorizer claims, never from the body: the same narrow trust edge as
``rp-api → rp-graph-hop``. ``rp-coa-control`` enforces the admin group, audits, and owns the state row, the
executor and the idle watcher. Viewing (GET) is never activity.

Without the opt-in stack (``RP_COA_CONTROL`` off, or the function missing) GET answers ``deployed: false`` with the
CLI fallback, and every action answers 503.
"""

from __future__ import annotations

import json
import os
from typing import Any

from evidence_core.models.coa_env import (
    ADMIN_GROUP,
    COA_CONTROL_FUNCTION,
    CoaDownRequest,
    CoaEnvironmentView,
    CoaKeepAliveRequest,
    CoaSettingsRequest,
    CoaUpRequest,
    CoaViewer,
)

from ..deps import get_deps
from ..http import ApiException, Reply, Request, not_ready
from . import COA_ENV, COA_ENV_DOWN, COA_ENV_KEEP_ALIVE, COA_ENV_SETTINGS, COA_ENV_UP, router

NOT_DEPLOYED = ("Environment control isn't deployed in this stack (CDK context rp:coaControl). Use the CLI: "
                "make coa-status, make coa-up, make coa-down.")


def _enabled() -> bool:
    return os.environ.get("RP_COA_CONTROL", "off") == "on"


def _not_deployed(req: Request) -> Reply:
    return Reply(CoaEnvironmentView(deployed=False, viewer=CoaViewer(can_operate=ADMIN_GROUP in req.caller.groups),
                                    note=NOT_DEPLOYED))


def _invoke(req: Request, op: str, body: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """``{"status", "body"}`` from rp-coa-control, or ``None`` when it is not deployed."""
    event = {"source": "rp-api", "op": op, "requestId": req.request_id,
             "caller": {"sub": req.caller.sub, "groups": list(req.caller.groups)}, "body": body or {}}
    deps = get_deps()
    if deps.coa_control is not None:
        return dict(deps.coa_control(event))
    if not _enabled() or deps.lambda_client is None:
        return None
    from botocore.exceptions import ClientError

    fn = os.environ.get("RP_COA_CONTROL_FUNCTION", COA_CONTROL_FUNCTION)
    try:
        resp = deps.lambda_client.invoke(FunctionName=fn, InvocationType="RequestResponse",
                                         Payload=json.dumps(event).encode())
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ResourceNotFoundException":
            return None
        raise
    payload = json.loads(resp["Payload"].read() or b"{}")
    if resp.get("FunctionError"):
        raise RuntimeError(f"{fn} failed: {str(payload)[:300]}")
    return dict(payload)


def _reply(result: dict[str, Any]) -> Reply:
    status = int(result.get("status") or 500)
    body = result.get("body") or {}
    if status >= 400:
        err = body.get("error") or {}
        raise ApiException(status, str(err.get("code") or "internal"), str(err.get("message") or "COA control failed"))
    return Reply(CoaEnvironmentView.load(body), status=status)


def _act(req: Request, op: str, model: Any) -> Reply:
    body = req.parse(model).dump()  # validated here as well, so a malformed body never reaches rp-coa-control
    result = _invoke(req, op, body)
    if result is None:
        raise not_ready(NOT_DEPLOYED)
    return _reply(result)


@router.route("GET", COA_ENV)
def coa_environment(req: Request) -> Reply:
    result = _invoke(req, "get")
    return _not_deployed(req) if result is None else _reply(result)


@router.route("POST", COA_ENV_UP)
def coa_up(req: Request) -> Reply:
    return _act(req, "up", CoaUpRequest)


@router.route("POST", COA_ENV_DOWN)
def coa_down(req: Request) -> Reply:
    return _act(req, "down", CoaDownRequest)


@router.route("PUT", COA_ENV_SETTINGS)
def coa_settings(req: Request) -> Reply:
    return _act(req, "settings", CoaSettingsRequest)


@router.route("POST", COA_ENV_KEEP_ALIVE)
def coa_keep_alive(req: Request) -> Reply:
    return _act(req, "keep_alive", CoaKeepAliveRequest)

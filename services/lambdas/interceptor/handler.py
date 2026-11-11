"""AgentCore Gateway REQUEST interceptor (``rp-interceptor``, ``passRequestHeaders: true``).

Input (MCP target):  ``{"interceptorInputVersion": "1.0", "mcp": {"gatewayRequest": {"headers",
"body": {jsonrpc, id, method, params}}}}``.
Output: ``{"interceptorOutputVersion": "1.0", "mcp": {"transformedGatewayRequest": {"body"}}}``,
or ``transformedGatewayResponse`` (the gateway answers immediately) to refuse a call.

For ``tools/call`` it re-validates the bearer token against the ``rp-users`` JWKS, then
deletes and sets ``params.arguments.{callerSub, groups, pipelineVersion, packageId, runId}``
from the token and the ``X-Rp-*`` headers the agent sets. Idempotent: running it twice on
the same request produces the same body. Other MCP methods pass through unchanged (the
gateway's CUSTOM_JWT authorizer already validated the token for them).
"""

from __future__ import annotations

import copy
import os
from typing import Any

from evidence_core import PIPELINE_VERSIONS
from evidence_core.ids import PACKAGE_ID_RE, RUN_ID_RE, short
from evidence_core.logs import get_logger
from evidence_core.models.tools import HEADER_PACKAGE, HEADER_PIPELINE_VERSION, HEADER_RUN, INJECTED_FIELDS

from lambdas.interceptor import access_log
from lambdas.interceptor.tokens import AccessTokenVerifier, Caller, TokenError, bearer

log = get_logger("rp.interceptor")

OUTPUT_VERSION = "1.0"
ERR_UNAUTHORIZED = -32001
ERR_BAD_CONTEXT = -32002

_VERIFIER: AccessTokenVerifier | None = None


def verifier() -> AccessTokenVerifier:
    global _VERIFIER
    if _VERIFIER is None:
        pool = os.environ.get("RP_USER_POOL_ID")
        client = os.environ.get("RP_USER_POOL_CLIENT_ID")
        if not pool or not client:
            raise RuntimeError("RP_USER_POOL_ID and RP_USER_POOL_CLIENT_ID must be set")
        _VERIFIER = AccessTokenVerifier(region=os.environ.get("AWS_REGION", "us-east-1"), user_pool_id=pool,
                                        client_id=client)
    return _VERIFIER


def use_verifier(v: AccessTokenVerifier | None) -> None:
    global _VERIFIER
    _VERIFIER = v


def header(headers: dict[str, Any], name: str) -> str | None:
    lname = name.lower()
    for k, v in (headers or {}).items():
        if k.lower() == lname:
            return str(v) if v is not None else None
    return None


def pass_through(body: Any) -> dict[str, Any]:
    return {"interceptorOutputVersion": OUTPUT_VERSION, "mcp": {"transformedGatewayRequest": {"body": body}}}


def refuse(body: dict[str, Any], code: int, message: str) -> dict[str, Any]:
    return {
        "interceptorOutputVersion": OUTPUT_VERSION,
        "mcp": {
            "transformedGatewayResponse": {
                "statusCode": 200,
                "body": {"jsonrpc": "2.0", "id": body.get("id"), "error": {"code": code, "message": message}},
            }
        },
    }


def injected_values(caller: Caller, headers: dict[str, Any]) -> dict[str, Any]:
    """Injected argument values from a verified caller and the agent's X-Rp-* headers.
    Raises ValueError with a user-safe message when the context headers are unusable."""
    pv = header(headers, HEADER_PIPELINE_VERSION)
    pkg = header(headers, HEADER_PACKAGE)
    run = header(headers, HEADER_RUN)
    if not pkg or not PACKAGE_ID_RE.match(pkg):
        raise ValueError(f"missing or invalid {HEADER_PACKAGE} header")
    if pv not in PIPELINE_VERSIONS:
        raise ValueError(f"missing or unknown {HEADER_PIPELINE_VERSION} header")
    if run is not None and not RUN_ID_RE.match(run):
        raise ValueError(f"invalid {HEADER_RUN} header")
    values: dict[str, Any] = {
        "callerSub": caller.sub,
        "groups": list(caller.groups),
        "pipelineVersion": pv,
        "packageId": pkg,
    }
    if run:
        values["runId"] = run
    return values


def inject(arguments: dict[str, Any] | None, values: dict[str, Any]) -> dict[str, Any]:
    """Delete every injected key the model may have supplied, then set the caller's values."""
    out = {k: v for k, v in (arguments or {}).items() if k not in INJECTED_FIELDS}
    out.update(values)
    return out


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    mcp = (event or {}).get("mcp") or {}
    request = mcp.get("gatewayRequest") or {}
    body = copy.deepcopy(request.get("body") or {})
    if not isinstance(body, dict) or body.get("method") != "tools/call":
        return pass_through(body)
    headers = request.get("headers") or {}
    tool = (body.get("params") or {}).get("name")
    model_args = (body.get("params") or {}).get("arguments") or {}
    run_header = header(headers, HEADER_RUN)
    try:
        caller = verifier().verify(bearer(header(headers, "Authorization")))
    except TokenError as e:
        log.warning("refused: token", extra={"rp": {"tool": tool, "reason": str(e)}})
        access_log.write(access_log.decision_row(
            gateway_tool=tool, call_id=body.get("id"), sub=None, groups=[],
            values={"runId": run_header if run_header and RUN_ID_RE.match(run_header) else None},
            model_args=model_args, decision="deny", reason="token"))
        return refuse(body, ERR_UNAUTHORIZED, "Unauthorized: the access token is not valid for this app.")
    try:
        values = injected_values(caller, headers)
    except ValueError as e:
        log.warning("refused: context", extra={"rp": {"tool": tool, "reason": str(e), "callerSub": short(caller.sub)}})
        access_log.write(access_log.decision_row(
            gateway_tool=tool, call_id=body.get("id"), sub=caller.sub, groups=caller.groups,
            values={"runId": run_header if run_header and RUN_ID_RE.match(run_header) else None},
            model_args=model_args, decision="deny", reason=str(e)))
        return refuse(body, ERR_BAD_CONTEXT, f"Refused: {e}.")
    params = dict(body.get("params") or {})
    supplied = sorted(k for k in (params.get("arguments") or {}) if k in INJECTED_FIELDS)
    params["arguments"] = inject(params.get("arguments"), values)
    body["params"] = params
    log.info("injected", extra={"rp": {"tool": tool, "callerSub": short(caller.sub), "groups": caller.groups,
                                       "pipelineVersion": values["pipelineVersion"], "packageId": values["packageId"],
                                       "runId": values.get("runId"), "strippedModelKeys": supplied}})
    access_log.write(access_log.decision_row(gateway_tool=tool, call_id=body.get("id"), sub=caller.sub,
                                             groups=caller.groups, values=values, model_args=model_args,
                                             decision="allow"))
    return pass_through(body)

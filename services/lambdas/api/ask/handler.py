"""Lambda ``rp-api-ask`` behind ``POST /ask``.

The preferred integration is an API Gateway HTTP proxy straight to the AgentCore Runtime invocations endpoint
with response streaming; the CDK app wires that when the Runtime ARN is known at synth time
(context ``agentRuntimeArn``). Until then, this Lambda is the integration:

1. validates ``AskRequest`` and the package ACL (the agent re-checks through the tools); a refusal here (no access
   to the package, or a pipeline version the package does not enable) is written to ``rp-access-log`` as an
   ask-gate row (``evidence_core.access_gate``) before the error is returned: no run starts, no model is called;
2. forwards the caller's own ``Authorization: Bearer <access token>`` to the Runtime, whose
   ``customJWTAuthorizer`` validates it (SigV4 is not accepted by a JWT-configured Runtime);
3. returns the Runtime's SSE text as ``text/event-stream``. The Python runtime cannot stream a Lambda
   response, so API Gateway delivers the frames at once; the UI parser handles both (answer.py).

Upstream failures are returned as an SSE ``error`` frame so the Ask screen renders them in place.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any
from urllib.parse import quote

import urllib3
from evidence_core import access_gate
from evidence_core.logs import get_logger, log_kv
from evidence_core.models.answer import AskRequest, ErrorEvent, sse

from ..acl import require_package
from ..deps import Deps, get_deps
from ..http import ApiException, Request, bad_request, cors_headers, error_response, not_ready

log = get_logger("rp-api-ask")
SESSION_HEADER = "X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"
RUNTIME_ARN_PARAM = "/rp/agent/runtime-arn"
ASK_TIMEOUT_DEFAULT_S = 25.0  # CDK sets RP_ASK_TIMEOUT_SECONDS from the API Gateway integration timeout
_http = urllib3.PoolManager()
_runtime_arn_cache: dict[str, tuple[float, str]] = {}


def runtime_arn(deps: Deps) -> str:
    """Env ``RP_AGENT_RUNTIME_ARN``, else SSM ``/rp/agent/runtime-arn`` (written by deploy_agentcore.sh)."""
    if deps.settings.agent_runtime_arn:
        return deps.settings.agent_runtime_arn
    hit = _runtime_arn_cache.get(RUNTIME_ARN_PARAM)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    if deps.ssm is None:
        raise not_ready("The agent runtime is not deployed yet.")
    try:
        value = deps.ssm.get_parameter(Name=RUNTIME_ARN_PARAM)["Parameter"]["Value"]
    except Exception as exc:  # ParameterNotFound and friends
        raise not_ready("The agent runtime is not deployed yet (run scripts/deploy_agentcore.sh).") from exc
    _runtime_arn_cache[RUNTIME_ARN_PARAM] = (time.monotonic(), value)
    return value


def invocation_url(arn: str, region: str, qualifier: str = "DEFAULT") -> str:
    return (f"https://bedrock-agentcore.{region}.amazonaws.com/runtimes/{quote(arn, safe='')}/invocations"
            f"?qualifier={quote(qualifier)}")


def session_id_for(body: AskRequest) -> str:
    if body.session_id and len(body.session_id) >= 33:
        return body.session_id
    return f"rp-ask-{uuid.uuid4().hex}"  # 39 chars; Runtime requires >= 33


def _sse_error(code: str, message: str, headers: dict[str, str]) -> dict[str, Any]:
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/event-stream", "Cache-Control": "no-store", **cors_headers(headers)},
        "body": sse(ErrorEvent(code=code, message=message)),
    }


def read_timeout(context: Any, started: float) -> float:
    """Seconds the Runtime may take: under API Gateway's integration timeout (whose clock also covers the
    cold start, SSM and DynamoDB reads before this point) and under the Lambda's own remaining time, so
    the caller gets the SSE ``timeout`` frame instead of a bare 504."""
    budget = float(os.environ.get("RP_ASK_TIMEOUT_SECONDS", ASK_TIMEOUT_DEFAULT_S)) - (time.monotonic() - started)
    if context is not None and hasattr(context, "get_remaining_time_in_millis"):
        budget = min(budget, context.get_remaining_time_in_millis() / 1000.0 - 1.0)
    return max(1.0, budget)


def log_refusal(deps: Deps, req: Request, body: AskRequest, refused: ApiException, request_id: str) -> None:
    """One ask-gate row in ``rp-access-log`` for a refused ask (best effort: the refusal is returned either way)."""
    try:
        table = deps.table("access_log")
    except KeyError:  # an older Settings without the table
        table = None
    row = access_gate.refusal_row(
        path="api", request_id=request_id, sub=req.caller.sub, groups=list(req.caller.groups),
        package_id=body.package_id, pipeline_version=body.pipeline_version, question=body.question,
        code=refused.code, reason=refused.message)
    written = access_gate.write(table, row)
    log_kv(log, "ask refused", packageId=body.package_id, pv=body.pipeline_version,
           code=refused.code, requestId=request_id, logged=written)


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    entered = time.monotonic()
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items() if v is not None}
    request_id = str((event.get("requestContext") or {}).get("requestId") or "")
    try:
        req = Request.from_event(event)
        if not req.caller.authenticated:
            raise ApiException(401, "unauthorized", "Sign in first.")
        body: AskRequest = req.parse(AskRequest)
        deps = get_deps()
        try:
            pkg = require_package(deps, req.caller, body.package_id)
            if body.pipeline_version not in pkg.pipeline_versions:
                raise bad_request(f"pipelineVersion {body.pipeline_version} is not enabled for this package")
        except ApiException as refused:
            log_refusal(deps, req, body, refused, request_id)
            raise
        auth = headers.get("authorization")
        if not auth:
            raise ApiException(401, "unauthorized", "Missing access token.")
        arn = runtime_arn(deps)
    except ApiException as exc:
        return error_response(exc, request_id, headers)

    session_id = session_id_for(body)
    url = invocation_url(arn, deps.settings.region)
    timeout = read_timeout(context, entered)
    started = time.monotonic()
    try:
        resp = _http.request(
            "POST", url,
            body=body.dump_json().encode(),
            headers={"Authorization": auth if auth.lower().startswith("bearer ") else f"Bearer {auth}",
                     "Content-Type": "application/json", "Accept": "text/event-stream", SESSION_HEADER: session_id},
            timeout=urllib3.Timeout(connect=5.0, read=timeout),
            retries=False,
        )
    except urllib3.exceptions.TimeoutError:
        log_kv(log, "ask timeout", packageId=body.package_id, pv=body.pipeline_version, requestId=request_id)
        return _sse_error("timeout", f"The agent did not answer within {int(round(timeout))} s.", headers)
    except urllib3.exceptions.HTTPError as exc:
        log_kv(log, "ask transport error", error=str(exc), requestId=request_id)
        return _sse_error("agent_error", "Could not reach the agent runtime.", headers)

    ms = int((time.monotonic() - started) * 1000)
    text = resp.data.decode("utf-8", errors="replace")
    log_kv(log, "ask", packageId=body.package_id, pv=body.pipeline_version, status=resp.status, ms=ms,
           bytes=len(text), sessionId=session_id, requestId=request_id)
    if resp.status in (401, 403):
        return _sse_error("forbidden", "The agent runtime rejected the access token.", headers)
    if resp.status >= 400:
        detail = _upstream_message(text)
        return _sse_error("agent_error", f"Agent runtime error {resp.status}: {detail}", headers)
    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" not in ctype and text and not text.lstrip().startswith("data:"):
        # A non-streaming entrypoint returns one JSON document; wrap it so the UI sees SSE frames.
        text = "".join(f"data: {json.dumps(e, separators=(',', ':'))}\n\n" for e in _as_events(text))
    return {
        "statusCode": 200,
        "headers": {"Content-Type": "text/event-stream", "Cache-Control": "no-store",
                    SESSION_HEADER: session_id, **cors_headers(headers)},
        "body": text,
    }


def _upstream_message(text: str) -> str:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:200]
    return str(data.get("message") or data.get("Message") or data)[:200]


def _as_events(text: str) -> list[dict[str, Any]]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return [{"type": "error", "code": "agent_error", "message": "Agent returned a non-JSON response."}]
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict) and isinstance(data.get("events"), list):
        return data["events"]
    return [data] if isinstance(data, dict) else []

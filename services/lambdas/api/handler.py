"""Lambda ``rp-api``: every REST route owned by D except ``POST /ask`` (see ``lambdas.api.ask``)."""

from __future__ import annotations

from typing import Any

from evidence_core.logs import get_logger, log_kv

from .http import ApiException, Request, error_response, json_response
from .routes import (  # noqa: F401 - registers the routes
    access,
    artifacts,
    build,
    coa_env,
    cost,
    documents,
    evaluate,
    governance,
    inspect,
    knowledge,
    overlay,
    packages,
    pipelines,
    router,
    runs,
    showcases,
    status,
    units,
)

log = get_logger("rp-api")


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items() if v is not None}
    request_id = str((event.get("requestContext") or {}).get("requestId") or "")
    try:
        req = Request.from_event(event)
        if not req.caller.authenticated:
            raise ApiException(401, "unauthorized", "Sign in first.")
        route = router.resolve(req.method, req.resource)
        if route is None:
            raise ApiException(404, "not_found", f"No route {req.method} {req.resource}.")
        reply = route(req)
        log_kv(log, "request", method=req.method, resource=req.resource, status=reply.status,
               sub=req.caller.sub[:6], requestId=request_id)
        return json_response(reply.status, reply.body, headers, reply.headers)
    except ApiException as exc:
        log_kv(log, "request", method=event.get("httpMethod"), resource=event.get("resource"), status=exc.status,
               code=exc.code, error=exc.message, requestId=request_id)
        return error_response(exc, request_id, headers)
    except Exception:  # noqa: BLE001 - last-resort 500 with the request id, details only in logs
        log.exception("unhandled error", extra={"rp": {"requestId": request_id}})
        return error_response(ApiException(500, "internal", "Internal error."), request_id, headers)

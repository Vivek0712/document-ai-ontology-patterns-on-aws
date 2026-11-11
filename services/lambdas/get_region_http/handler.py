"""GET /region/{unitId}?field={fieldId} (API Gateway REST, Lambda proxy, Cognito authorizer).

Identity comes from ``requestContext.authorizer.claims`` (``sub``, ``cognito:groups``) that
API Gateway validated; the AgentCore Gateway is not on this path. DocAcl is checked before
anything in Gold is read or presigned: User B asking for a User A unit gets 403
``You don't have access to this region.``
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote

from evidence_core.env import cors_headers
from evidence_core.ids import parse_groups_claim, principals_for, short
from evidence_core.logs import get_logger
from evidence_core.models.api import REGION_URL_TTL_SECONDS

from lambdas.tools._lib.errors import BadRequest, ToolError
from lambdas.tools._lib.region import resolve_region
from lambdas.tools._lib.stores import stores

log = get_logger("rp.get_region_http")

_CODES = {400: "bad_request", 401: "unauthorized", 403: "forbidden", 404: "not_found", 503: "not_ready",
          500: "internal"}


def _cors_headers(event: dict[str, Any]) -> dict[str, str]:
    return {"Content-Type": "application/json", "Cache-Control": "no-store"} | cors_headers(event.get("headers"))


def _response(event: dict[str, Any], status: int, body: dict[str, Any]) -> dict[str, Any]:
    return {"statusCode": status, "headers": _cors_headers(event), "body": json.dumps(body)}


def _error(event: dict[str, Any], status: int, message: str) -> dict[str, Any]:
    request_id = (event.get("requestContext") or {}).get("requestId")
    body = {"error": {"code": _CODES.get(status, "internal"), "message": message}}
    if request_id:
        body["error"]["requestId"] = request_id
    return _response(event, status, body)


def caller(event: dict[str, Any]) -> tuple[str, list[str]] | None:
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    sub = claims.get("sub")
    if not sub:
        return None
    return sub, parse_groups_claim(claims.get("cognito:groups"))


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    who = caller(event)
    if who is None:
        return _error(event, 401, "Sign in to view regions.")
    sub, groups = who
    params = event.get("pathParameters") or {}
    query = event.get("queryStringParameters") or {}
    unit_id = unquote(params.get("unitId") or "")
    field_id = query.get("field") or None
    if field_id:
        field_id = unquote(field_id)
    principals = principals_for(sub, groups)
    try:
        if not unit_id:
            raise BadRequest("unitId is required")
        st = stores()
        region = resolve_region(st, unit_id, field_id, principals)
        region.url = st.presign_get(region.page_image_key, REGION_URL_TTL_SECONDS)
        region.expires_in = REGION_URL_TTL_SECONDS
    except ToolError as e:
        log.info("region refused", extra={"rp": {"unitId": unit_id, "fieldId": field_id, "callerSub": short(sub),
                                                 "code": e.code}})
        return _error(event, e.http_status, e.message)
    except Exception:
        log.exception("region failed", extra={"rp": {"unitId": unit_id, "fieldId": field_id}})
        return _error(event, 500, "Could not resolve the region.")
    log.info("region ok", extra={"rp": {"unitId": unit_id, "fieldId": field_id, "callerSub": short(sub),
                                        "page": region.page}})
    return _response(event, 200, region.dump())

"""API Gateway (REST, Lambda proxy, Cognito authorizer) helpers for ``upload_url``.

D owns the REST handlers and their shared helpers; this is the minimum B needs for its one route,
with the same error body (``ApiError``) and principal rules."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from evidence_core.env import cors_headers
from evidence_core.ids import parse_groups_claim, principals_for
from evidence_core.models.api import ApiError, ApiErrorBody


class HttpError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


@dataclass
class Caller:
    sub: str
    groups: list[str]

    @property
    def principals(self) -> list[str]:
        return principals_for(self.sub, self.groups)


def caller_from_event(event: dict[str, Any]) -> Caller:
    claims = ((event.get("requestContext") or {}).get("authorizer") or {}).get("claims") or {}
    sub = claims.get("sub")
    if not sub:
        raise HttpError(401, "unauthorized", "Missing caller identity.")
    return Caller(sub=str(sub), groups=parse_groups_claim(claims.get("cognito:groups")))


def _headers(event: dict[str, Any] | None) -> dict[str, str]:
    return {"Content-Type": "application/json", "Cache-Control": "no-store"} | cors_headers(
        (event or {}).get("headers"))


def respond(status: int, body: Any, event: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = body.dump() if hasattr(body, "dump") else body
    return {"statusCode": status, "headers": _headers(event), "body": json.dumps(payload)}


def error(exc: HttpError, request_id: str | None = None, event: dict[str, Any] | None = None) -> dict[str, Any]:
    body = ApiError(error=ApiErrorBody(code=exc.code, message=exc.message, request_id=request_id))  # type: ignore[arg-type]
    return respond(exc.status, body, event)


def json_body(event: dict[str, Any]) -> dict[str, Any]:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode("utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HttpError(400, "bad_request", "Body is not valid JSON.") from e
    if not isinstance(data, dict):
        raise HttpError(400, "bad_request", "Body must be a JSON object.")
    return data

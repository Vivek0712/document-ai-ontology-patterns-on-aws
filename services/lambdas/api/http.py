"""API Gateway REST proxy plumbing: request parsing, JSON/CORS responses, typed errors, routing."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote

from evidence_core import env as _env
from evidence_core.base import RpModel
from pydantic import ValidationError

from .auth import Caller

ErrorCode = str  # ApiErrorBody.code literal


class ApiException(Exception):  # noqa: N818 - public name used across handlers
    """Raised by handlers; rendered as ``ApiError {error: {code, message, requestId}}``."""

    def __init__(self, status: int, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def bad_request(message: str) -> ApiException:
    return ApiException(400, "bad_request", message)


def forbidden(message: str = "You don't have access to this resource.") -> ApiException:
    return ApiException(403, "forbidden", message)


def not_found(message: str = "Not found.") -> ApiException:
    return ApiException(404, "not_found", message)


def conflict(message: str) -> ApiException:
    return ApiException(409, "conflict", message)


def not_ready(message: str) -> ApiException:
    return ApiException(503, "not_ready", message)


@dataclass
class Request:
    method: str
    resource: str  # API Gateway resource template, e.g. "/packages/{packageId}"
    path: str
    path_params: dict[str, str]
    query: dict[str, str]
    headers: dict[str, str]  # lower-cased names
    body: str | None
    caller: Caller
    request_id: str
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_event(cls, event: dict[str, Any]) -> Request:
        body = event.get("body")
        if body is not None and event.get("isBase64Encoded"):
            body = base64.b64decode(body).decode("utf-8")
        ctx = event.get("requestContext") or {}
        return cls(
            method=(event.get("httpMethod") or "GET").upper(),
            resource=event.get("resource") or event.get("path") or "/",
            path=event.get("path") or "/",
            path_params={k: unquote(v) for k, v in (event.get("pathParameters") or {}).items() if v is not None},
            query={k: v for k, v in (event.get("queryStringParameters") or {}).items() if v is not None},
            headers={k.lower(): v for k, v in (event.get("headers") or {}).items() if v is not None},
            body=body,
            caller=Caller.from_event(event),
            request_id=str(ctx.get("requestId") or ""),
            raw=event,
        )

    def param(self, name: str) -> str:
        value = self.path_params.get(name)
        if not value:
            raise bad_request(f"missing path parameter {name}")
        return value

    def json_body(self) -> dict[str, Any]:
        if not self.body:
            return {}
        try:
            data = json.loads(self.body)
        except json.JSONDecodeError as exc:
            raise bad_request(f"body is not JSON: {exc.msg}") from exc
        if not isinstance(data, dict):
            raise bad_request("body must be a JSON object")
        return data

    def parse(self, model: type[RpModel]) -> Any:
        try:
            return model.model_validate(self.json_body())
        except ValidationError as exc:
            first = exc.errors()[0]
            loc = ".".join(str(p) for p in first.get("loc", ()))
            raise bad_request(f"{loc}: {first.get('msg')}" if loc else str(first.get("msg"))) from exc

    def int_query(self, name: str, default: int, *, lo: int = 1, hi: int = 500) -> int:
        raw = self.query.get(name)
        if raw is None or raw == "":
            return default
        try:
            value = int(raw)
        except ValueError as exc:
            raise bad_request(f"{name} must be an integer") from exc
        return max(lo, min(hi, value))


# --- responses -----------------------------------------------------------------------------

allowed_origins = _env.allowed_origins
cors_headers = _env.cors_headers  # the configured web origins only


def json_response(status: int, payload: Any, request_headers: dict[str, str] | None = None,
                  extra_headers: dict[str, str] | None = None) -> dict[str, Any]:
    if isinstance(payload, RpModel):
        body = payload.dump_json()
    else:
        body = json.dumps(payload, separators=(",", ":"), default=str)
    headers = {"Content-Type": "application/json", "Cache-Control": "no-store"}
    headers.update(cors_headers(request_headers))
    headers.update(extra_headers or {})
    return {"statusCode": status, "headers": headers, "body": body}


def error_response(exc: ApiException, request_id: str, request_headers: dict[str, str] | None) -> dict[str, Any]:
    payload = {"error": {"code": exc.code, "message": exc.message, "requestId": request_id or None}}
    return json_response(exc.status, payload, request_headers)


# --- routing --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Reply:
    """What a route returns: model or dict plus the status code."""

    body: Any
    status: int = 200
    headers: dict[str, str] | None = None


Handler = Callable[[Request], Reply]


class Router:
    """Routes on (httpMethod, API Gateway resource template); the infra uses the same templates."""

    def __init__(self) -> None:
        self._routes: dict[tuple[str, str], Handler] = {}

    def route(self, method: str, resource: str) -> Callable[[Handler], Handler]:
        def deco(fn: Handler) -> Handler:
            key = (method.upper(), resource)
            if key in self._routes:
                raise ValueError(f"duplicate route {key}")
            self._routes[key] = fn
            return fn

        return deco

    def resolve(self, method: str, resource: str) -> Handler | None:
        return self._routes.get((method.upper(), resource))

    @property
    def routes(self) -> list[tuple[str, str]]:
        return sorted(self._routes)

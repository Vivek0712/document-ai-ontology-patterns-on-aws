"""Typed tool errors.

A tool Lambda never raises to AgentCore Gateway for an expected failure: it returns
``{"error": {"code", "message"}}`` so the agent can show ``denied`` / ``error`` in the
trace strip. ``get_region_http`` maps the same errors to HTTP status codes.
"""

from __future__ import annotations

from typing import Any, Literal

ErrorCode = Literal["bad_request", "forbidden", "not_found", "not_ready", "internal"]

HTTP_STATUS: dict[str, int] = {
    "bad_request": 400,
    "forbidden": 403,
    "not_found": 404,
    "not_ready": 503,
    "internal": 500,
}


class ToolError(Exception):
    code: ErrorCode = "internal"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    @property
    def http_status(self) -> int:
        return HTTP_STATUS[self.code]

    def payload(self) -> dict[str, Any]:
        return {"error": {"code": self.code, "message": self.message}}


class BadRequest(ToolError):
    code = "bad_request"


class Forbidden(ToolError):
    code = "forbidden"


class NotFound(ToolError):
    code = "not_found"


class NotReady(ToolError):
    code = "not_ready"


REGION_FORBIDDEN_MESSAGE = "You don't have access to this region."

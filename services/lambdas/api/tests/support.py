"""Shared constants and the API Gateway proxy event builder for the API tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
BUCKET = "rp-data-test"
SUB_A = "7c41e2aa-0000-4000-8000-00000000000a"
SUB_B = "9b11c3dd-0000-4000-8000-00000000000b"


def make_event(method: str, resource: str, *, path_params: dict[str, str] | None = None,
               query: dict[str, str] | None = None, body: Any = None, sub: str = SUB_A,
               groups: list[str] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    path = resource
    for k, v in (path_params or {}).items():
        path = path.replace("{" + k + "}", v)
    claims: dict[str, Any] = {}
    if sub:
        claims = {"sub": sub, "username": sub, "client_id": "rp-web",
                  "cognito:groups": "[" + " ".join(groups if groups is not None else ["lending-analyst"]) + "]",
                  "scope": "aws.cognito.signin.user.admin", "token_use": "access"}
    return {
        "resource": resource,
        "path": path,
        "httpMethod": method,
        "headers": {"Origin": "https://web.example.com", "Authorization": "Bearer test-token", **(headers or {})},
        "pathParameters": path_params,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None and not isinstance(body, str) else body,
        "isBase64Encoded": False,
        "requestContext": {"requestId": "req-1", "authorizer": {"claims": claims}},
    }

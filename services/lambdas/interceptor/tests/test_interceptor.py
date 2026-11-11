from __future__ import annotations

import copy
import time
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from lambdas.interceptor import handler as mod
from lambdas.interceptor.tokens import AccessTokenVerifier

POOL = "us-east-1_TESTPOOL"
CLIENT = "webclient123"
ISS = f"https://cognito-idp.us-east-1.amazonaws.com/{POOL}"
SUB = "7c41e2aa-0000-4000-8000-00000000000a"

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(key: Any = KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims = {"sub": SUB, "iss": ISS, "client_id": CLIENT, "token_use": "access", "iat": now, "exp": now + 600,
              "cognito:groups": ["lending-analyst"], "username": "user-a"}
    claims.update(overrides)
    return jwt.encode({k: v for k, v in claims.items() if v is not None}, key, algorithm="RS256",
                      headers={"kid": "k1"})


@pytest.fixture(autouse=True)
def verifier():
    mod.use_verifier(AccessTokenVerifier(region="us-east-1", user_pool_id=POOL, client_id=CLIENT,
                                         key_resolver=lambda _t: KEY.public_key()))
    yield
    mod.use_verifier(None)


def event(arguments: dict[str, Any] | None = None, *, method: str = "tools/call", tok: str | None = None,
          headers: dict[str, str] | None = None) -> dict[str, Any]:
    h = {"authorization": f"Bearer {tok or token()}", "X-Rp-Pipeline-Version": "2.3.1",
         "x-rp-package": "pkg-lending-01", "X-Rp-Run": "r-0123456789ab"}
    if headers is not None:
        h = headers
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 7, "method": method}
    if method == "tools/call":
        body["params"] = {"name": "search-units___search_units", "arguments": arguments or {"query": "wages"}}
    return {"interceptorInputVersion": "1.0",
            "mcp": {"rawGatewayRequest": {"body": "..."}, "gatewayRequest": {"path": "/mcp", "httpMethod": "POST",
                                                                             "headers": h, "body": body}}}


def args_of(out: dict[str, Any]) -> dict[str, Any]:
    assert out["interceptorOutputVersion"] == "1.0"
    return out["mcp"]["transformedGatewayRequest"]["body"]["params"]["arguments"]


def test_injects_identity_and_strips_model_values():
    forged = {"query": "wages", "callerSub": "someone-else", "groups": ["admin"], "packageId": "pkg-other-01",
              "pipelineVersion": "1.0"}
    args = args_of(mod.handler(event(forged)))
    assert args == {"query": "wages", "callerSub": SUB, "groups": ["lending-analyst"], "pipelineVersion": "2.3.1",
                    "packageId": "pkg-lending-01", "runId": "r-0123456789ab"}


def test_idempotent():
    first = mod.handler(event())
    again = copy.deepcopy(event())
    again["mcp"]["gatewayRequest"]["body"] = first["mcp"]["transformedGatewayRequest"]["body"]
    assert mod.handler(again) == first


def test_pass_through_for_other_methods():
    out = mod.handler(event(method="tools/list", headers={}))
    assert out["mcp"]["transformedGatewayRequest"]["body"] == {"jsonrpc": "2.0", "id": 7, "method": "tools/list"}


@pytest.mark.parametrize("tok", [
    token(OTHER_KEY),
    token(token_use="id"),
    token(client_id="another-client"),
    token(iss="https://cognito-idp.us-east-1.amazonaws.com/other"),
    token(exp=int(time.time()) - 3600, iat=int(time.time()) - 7200),
])
def test_refuses_bad_tokens(tok):
    out = mod.handler(event(tok=tok))
    resp = out["mcp"]["transformedGatewayResponse"]
    assert resp["body"]["error"]["code"] == mod.ERR_UNAUTHORIZED and resp["body"]["id"] == 7
    assert "transformedGatewayRequest" not in out["mcp"]


def test_refuses_missing_headers():
    out = mod.handler(event(headers={"Authorization": f"Bearer {token()}"}))
    assert out["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == mod.ERR_UNAUTHORIZED - 1
    out = mod.handler(event(headers={"Authorization": f"Bearer {token()}", "X-Rp-Package": "pkg-lending-01",
                                     "X-Rp-Pipeline-Version": "9.9"}))
    assert "Pipeline-Version" in out["mcp"]["transformedGatewayResponse"]["body"]["error"]["message"]
    out = mod.handler(event(headers={}))
    assert out["mcp"]["transformedGatewayResponse"]["body"]["error"]["code"] == mod.ERR_UNAUTHORIZED


def test_groups_absent_from_token():
    args = args_of(mod.handler(event(tok=token(**{"cognito:groups": None}))))
    assert args["groups"] == []

"""/coa/environment routes on rp-api: not deployed → CLI fallback; deployed → forwarded to rp-coa-control with the
authorizer's identity (never the body's), errors passed through, bodies validated before forwarding."""

from __future__ import annotations

from typing import Any

import pytest

from lambdas.api.tests.support import SUB_A


def test_not_deployed_answers_with_the_cli(call: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RP_COA_CONTROL", raising=False)
    status, body = call("GET", "/coa/environment")
    assert status == 200
    assert body["deployed"] is False and body["cli"] == ["make coa-status", "make coa-up", "make coa-down"]
    assert "isn't deployed" in body["note"] and body["viewer"]["canOperate"] is False
    status, body = call("GET", "/coa/environment", groups=["lending-analyst", "rp-admin"])
    assert body["viewer"]["canOperate"] is True
    for method, path, payload in (("POST", "/coa/environment/up", {}), ("POST", "/coa/environment/down", {}),
                                  ("PUT", "/coa/environment/settings", {"autoTeardown": True, "idleMinutes": 60}),
                                  ("POST", "/coa/environment/keep-alive", {})):
        status, body = call(method, path, body=payload, groups=["rp-admin"])
        assert (status, body["error"]["code"]) == (503, "not_ready"), path


def _view(state: str = "off", **extra: Any) -> dict[str, Any]:
    return {"deployed": True, "state": state, "version": 3, "viewer": {"canOperate": True, "adminGroup": "rp-admin"},
            **extra}


def test_forwards_identity_from_the_authorizer(env: Any, call: Any) -> None:
    seen: list[dict[str, Any]] = []

    def fake(event: dict[str, Any]) -> dict[str, Any]:
        seen.append(event)
        if event["op"] == "up":
            return {"status": 202, "body": _view("starting", outcome="started")}
        return {"status": 200, "body": _view()}

    env.deps.coa_control = fake
    status, body = call("GET", "/coa/environment", groups=["lending-analyst", "rp-admin"])
    assert status == 200 and body["state"] == "off"
    status, body = call("POST", "/coa/environment/up", body={"idempotencyKey": "k1", "sub": "forged"},
                        groups=["rp-admin"])
    assert status == 202 and body["outcome"] == "started"
    assert seen[0] == {"source": "rp-api", "op": "get", "requestId": "req-1",
                       "caller": {"sub": SUB_A, "groups": ["lending-analyst", "rp-admin"]}, "body": {}}
    assert seen[1]["caller"]["sub"] == SUB_A and seen[1]["body"] == {"idempotencyKey": "k1"}


def test_errors_pass_through(env: Any, call: Any) -> None:
    env.deps.coa_control = lambda e: {"status": 403, "body": {"error": {"code": "forbidden", "message": "admins"}}}
    status, body = call("POST", "/coa/environment/down", body={"force": True})
    assert (status, body["error"]["code"], body["error"]["message"]) == (403, "forbidden", "admins")
    env.deps.coa_control = lambda e: {"status": 409, "body": {"error": {"code": "conflict", "message": "busy"}}}
    assert call("POST", "/coa/environment/up", body={})[0] == 409


def test_bodies_are_validated_before_forwarding(env: Any, call: Any) -> None:
    env.deps.coa_control = lambda e: pytest.fail("must not be forwarded")
    assert call("PUT", "/coa/environment/settings", body={"autoTeardown": True})[0] == 400
    assert call("POST", "/coa/environment/up", body={"idempotencyKey": "x" * 65})[0] == 400
    assert call("POST", "/coa/environment/down", body={"force": "maybe"})[0] == 400


def test_function_missing_is_not_deployed(env: Any, call: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    class Missing:
        def invoke(self, **kw: Any) -> Any:
            raise ClientError({"Error": {"Code": "ResourceNotFoundException", "Message": "no"}}, "Invoke")

    monkeypatch.setenv("RP_COA_CONTROL", "on")
    env.deps.lambda_client = Missing()
    status, body = call("GET", "/coa/environment")
    assert status == 200 and body["deployed"] is False


def test_invokes_rp_coa_control_when_on(env: Any, call: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    import io
    import json

    calls: list[dict[str, Any]] = []

    class Lam:
        def invoke(self, **kw: Any) -> Any:
            calls.append(kw)
            return {"Payload": io.BytesIO(json.dumps({"status": 200, "body": _view("running")}).encode())}

    monkeypatch.setenv("RP_COA_CONTROL", "on")
    env.deps.lambda_client = Lam()
    status, body = call("GET", "/coa/environment")
    assert status == 200 and body["state"] == "running"
    assert calls[0]["FunctionName"] == "rp-coa-control" and calls[0]["InvocationType"] == "RequestResponse"

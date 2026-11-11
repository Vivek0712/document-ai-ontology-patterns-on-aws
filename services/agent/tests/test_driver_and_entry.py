from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import main as entry
from lambdas.interceptor.tokens import Caller, TokenError
from rp_agent.driver import (
    DriverSpec,
    StrandsDriver,
    ToolFinished,
    ToolStarted,
    bare_tool_name,
    classify_payload,
    payload_from_tool_result,
)


def spec(inject: dict[str, Any] | None = None, max_calls: int = 2) -> DriverSpec:
    return DriverSpec(model_id="m", temperature=0, max_tokens=700, system_prompt="p", tools=["search_units"],
                      gateway_url="https://gw", headers={}, max_tool_calls=max_calls, inject=inject)


def bare_driver(s: DriverSpec) -> StrandsDriver:
    d = StrandsDriver.__new__(StrandsDriver)
    d.spec, d._pending, d._started, d._calls = s, [], {}, 0
    return d


def test_tool_result_parsing():
    ok = {"status": "success", "content": [{"text": json.dumps({"units": []})}]}
    assert payload_from_tool_result(ok) == ({"units": []}, None)
    structured = {"status": "success", "content": [], "structuredContent": {"rows": []}}
    assert payload_from_tool_result(structured) == ({"rows": []}, None)
    denied = {"status": "success", "content": [{"text": json.dumps({"error": {"code": "forbidden", "message": "no"}})}]}
    payload, err = payload_from_tool_result(denied)
    assert classify_payload(payload, err) == ("denied", "no")
    rpc = {"status": "error", "content": [{"text": "Unauthorized: the access token is not valid"}]}
    payload, err = payload_from_tool_result(rpc)
    assert payload is None and classify_payload(payload, err)[0] == "denied"
    assert classify_payload(None, "boom")[0] == "error"
    assert bare_tool_name("search-units___search_units") == "search_units"


def test_before_hook_strips_model_identity_and_caps_calls():
    d = bare_driver(spec())
    forged = {"query": "x", "callerSub": "evil", "packageId": "pkg-other-01"}
    ev = SimpleNamespace(tool_use={"toolUseId": "t1", "name": "search_units", "input": dict(forged)},
                         cancel_tool=False)
    d._before(ev)
    assert ev.tool_use["input"] == {"query": "x"} and ev.cancel_tool is False
    (started,) = d._drain()
    assert isinstance(started, ToolStarted) and started.input == {"query": "x"}
    for i in range(2):
        e = SimpleNamespace(tool_use={"toolUseId": f"t{i + 2}", "name": "search_units", "input": {}},
                            cancel_tool=False)
        d._before(e)
    assert e.cancel_tool and "limit" in e.cancel_tool


def test_before_hook_fallback_injects_caller_values():
    values = {"callerSub": "sub-a", "groups": ["lending-analyst"], "pipelineVersion": "2.3.1",
              "packageId": "pkg-lending-01", "runId": "r-0123456789ab"}
    d = bare_driver(spec(inject=values))
    ev = SimpleNamespace(tool_use={"toolUseId": "t1", "name": "search_units",
                                   "input": {"query": "x", "callerSub": "evil"}}, cancel_tool=False)
    d._before(ev)
    assert ev.tool_use["input"] == {"query": "x"} | values


def test_after_hook_emits_finished():
    d = bare_driver(spec())
    d._started["t1"] = 0.0
    ev = SimpleNamespace(tool_use={"toolUseId": "t1", "name": "search_units"}, cancel_message=None,
                         result={"status": "success", "content": [{"text": json.dumps({"units": []})}]})
    d._after(ev)
    (fin,) = d._drain()
    assert isinstance(fin, ToolFinished) and fin.status == "ok" and fin.payload == {"units": []}


def test_entrypoint_rejects_bad_token_and_bad_payload(monkeypatch):
    class Verifier:
        def verify(self, token: str) -> Caller:
            if token != "good":
                raise TokenError("bad")
            return Caller(sub="sub-a", groups=["lending-analyst"])

    seen: dict[str, Any] = {}

    class Runner:
        async def run(self, request, caller, session_id=None):
            seen.update(request=request, caller=caller, session_id=session_id)
            yield {"type": "run_start"}

    from evidence_core.models.api import Package
    from rp_agent.access import PackageGate

    packages = {"pkg-lending-01": Package(package_id="pkg-lending-01", name="Lending", owner_sub="sub-a",
                                          created_at="2026-11-03T00:00:00Z",
                                          allowed_principals=["user:sub-a", "group:lending-analyst"],
                                          pipeline_versions=["1.0", "2.3.1"]),
                "pkg-other-01": Package(package_id="pkg-other-01", name="Other", owner_sub="sub-b",
                                        created_at="2026-11-03T00:00:00Z", allowed_principals=["user:sub-b"])}
    monkeypatch.setattr(entry, "verifier", lambda: Verifier())
    monkeypatch.setattr(entry, "runner", lambda: Runner())
    monkeypatch.setattr(entry, "package_gate", lambda: PackageGate(load=packages.get))

    def collect(payload: dict[str, Any], auth: str | None) -> list[dict[str, Any]]:
        ctx = SimpleNamespace(request_headers={"Authorization": auth} if auth else {}, session_id="s" * 36)

        async def go() -> list[dict[str, Any]]:
            return [e async for e in entry.invoke(payload, ctx)]

        return asyncio.run(go())

    good = {"question": "q?", "packageId": "pkg-lending-01", "pipelineVersion": "2.3.1"}
    assert collect(good, "Bearer bad")[0]["code"] == "forbidden"
    assert collect(good, None)[0]["code"] == "forbidden"
    assert collect({"question": ""}, "Bearer good")[0]["code"] == "bad_request"
    assert collect(good, "Bearer good") == [{"type": "run_start"}]
    assert seen["caller"].sub == "sub-a" and seen["caller"].authorization == "Bearer good"
    # The streaming path skips rp-api-ask, so the Runtime enforces the package ACL and pipeline version.
    seen.clear()
    for pkg in ("pkg-other-01", "pkg-missing-01"):
        denied = collect(good | {"packageId": pkg}, "Bearer good")
        assert denied[0]["code"] == "forbidden" and "access to this package" in denied[0]["message"]
    assert collect(good | {"pipelineVersion": "9.9"}, "Bearer good")[0]["code"] == "bad_request"
    assert not seen  # the runner never ran: no run record, no model tokens

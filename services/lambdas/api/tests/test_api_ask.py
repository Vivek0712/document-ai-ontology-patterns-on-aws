from __future__ import annotations

import json
from typing import Any

import pytest

from lambdas.api.ask import handler as ask_mod
from lambdas.api.tests.support import SUB_B, make_event

ARN = "arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/rp_agent-abc"


class _Resp:
    def __init__(self, status: int, body: str, ctype: str = "text/event-stream") -> None:
        self.status = status
        self.data = body.encode()
        self.headers = {"Content-Type": ctype}


@pytest.fixture
def upstream(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    frames = 'data: {"type":"run_start","runId":"r-1"}\n\ndata: {"type":"answer","answer":"ok"}\n\n'

    def fake_request(method: str, url: str, **kw: Any) -> _Resp:
        calls.append({"method": method, "url": url, **kw})
        return _Resp(200, frames)

    monkeypatch.setattr(ask_mod._http, "request", fake_request)
    monkeypatch.setenv("RP_AGENT_RUNTIME_ARN", ARN)
    return calls


def _ask(body: dict[str, Any], **kw: Any) -> dict[str, Any]:
    from lambdas.api.routes import router  # noqa: F401 - ensure routes import cleanly alongside ask

    return ask_mod.handler(make_event("POST", "/ask", body=body, **kw), None)


def test_ask_proxies_with_user_token(env, seeded, upstream) -> None:
    env.deps.settings = type(env.deps.settings).from_env()
    resp = _ask({"question": "What is the total income?", "packageId": seeded.package_id, "pipelineVersion": "2.3.1"})
    assert resp["statusCode"] == 200 and resp["headers"]["Content-Type"] == "text/event-stream"
    assert '"type":"answer"' in resp["body"]
    call = upstream[0]
    assert call["url"].startswith("https://bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3A")
    assert call["url"].endswith("/invocations?qualifier=DEFAULT")
    assert call["headers"]["Authorization"] == "Bearer test-token"
    assert len(call["headers"]["X-Amzn-Bedrock-AgentCore-Runtime-Session-Id"]) >= 33
    assert json.loads(call["body"])["pipelineVersion"] == "2.3.1"


def test_ask_acl_and_validation(env, seeded, upstream) -> None:
    env.deps.settings = type(env.deps.settings).from_env()
    resp = _ask({"question": "q", "packageId": seeded.package_id, "pipelineVersion": "2.3.1"}, sub=SUB_B,
                groups=["markets-analyst"])
    assert resp["statusCode"] == 403
    resp = _ask({"question": "", "packageId": seeded.package_id, "pipelineVersion": "2.3.1"})
    assert resp["statusCode"] == 400
    resp = _ask({"question": "q", "packageId": seeded.package_id, "pipelineVersion": "3.0"})
    assert resp["statusCode"] == 400
    assert upstream == []


def test_ask_gate_refusals_are_written_to_the_access_log(env, seeded, upstream) -> None:
    """A refused ask starts no run, so the gate itself logs the refusal (SC-7's evidence for the other user)."""
    from boto3.dynamodb.conditions import Key
    from evidence_core.access_gate import gate_pk, question_sha
    from evidence_core.ddb import from_ddb

    env.deps.settings = type(env.deps.settings).from_env()
    question = "What is the total income across this package?"
    resp = _ask({"question": question, "packageId": seeded.package_id, "pipelineVersion": "2.3.1"}, sub=SUB_B,
                groups=["markets-analyst"])
    assert resp["statusCode"] == 403 and upstream == []
    _ask({"question": question, "packageId": seeded.package_id, "pipelineVersion": "3.0"})  # owner, bad lane
    _ask({"question": question, "packageId": seeded.package_id, "pipelineVersion": "2.3.1"})  # allowed: no row
    items = [from_ddb(i) for i in env.deps.table("access_log").query(
        KeyConditionExpression=Key("runId").eq(gate_pk(seeded.package_id)))["Items"]]
    assert len(items) == 2
    b = next(i for i in items if i["callerSub6"] == SUB_B[:6])
    assert b["decision"] == "deny" and b["injectionPoint"] == "ask-gate" and b["tool"] == "ask"
    assert b["target"] == "rp-api-ask" and b["groups"] == ["markets-analyst"] and b["callId"] == "req-1"
    assert b["gate"] == {"path": "api", "requestId": "req-1", "code": "forbidden",
                         "questionSha": question_sha(question)}
    assert b["injected"] == {"pipelineVersion": "2.3.1", "packageId": seeded.package_id}
    assert b["reason"] == "You don't have access to this package." and b["day"] == b["at"][:10] and b["ttl"] > 0
    assert question not in json.dumps(items)  # the question text is never logged, only its hash
    assert SUB_B not in json.dumps(items)  # nor the full sub
    owner = next(i for i in items if i["callerSub6"] != SUB_B[:6])
    assert owner["gate"]["code"] == "bad_request" and owner["injected"]["pipelineVersion"] == "3.0"


def test_ask_not_ready_without_runtime(env, seeded) -> None:
    resp = _ask({"question": "q", "packageId": seeded.package_id, "pipelineVersion": "1.0"})
    assert resp["statusCode"] == 503 and '"not_ready"' in resp["body"]


def test_ask_runtime_arn_from_ssm(env, seeded, monkeypatch) -> None:
    env.deps.ssm.put_parameter(Name="/rp/agent/runtime-arn", Value=ARN, Type="String")
    ask_mod._runtime_arn_cache.clear()
    assert ask_mod.runtime_arn(env.deps) == ARN


def test_ask_upstream_error_becomes_sse_error(env, seeded, monkeypatch) -> None:
    monkeypatch.setenv("RP_AGENT_RUNTIME_ARN", ARN)
    env.deps.settings = type(env.deps.settings).from_env()
    monkeypatch.setattr(ask_mod._http, "request", lambda *a, **k: _Resp(500, '{"message":"boom"}', "application/json"))
    resp = _ask({"question": "q", "packageId": seeded.package_id, "pipelineVersion": "2.3.1"})
    assert resp["statusCode"] == 200
    frame = json.loads(resp["body"].removeprefix("data: ").strip())
    assert frame == {"type": "error", "code": "agent_error", "message": "Agent runtime error 500: boom"}


def test_ask_wraps_plain_json_entrypoint(env, seeded, monkeypatch) -> None:
    monkeypatch.setenv("RP_AGENT_RUNTIME_ARN", ARN)
    env.deps.settings = type(env.deps.settings).from_env()
    events = [{"type": "run_start", "runId": "r-1"}, {"type": "answer", "answer": "x"}]
    monkeypatch.setattr(ask_mod._http, "request", lambda *a, **k: _Resp(200, json.dumps(events), "application/json"))
    resp = _ask({"question": "q", "packageId": seeded.package_id, "pipelineVersion": "2.3.1"})
    assert resp["body"].count("data: ") == 2


def test_ask_read_timeout_stays_under_api_gateway(monkeypatch) -> None:
    """The upstream read budget ends before API Gateway's 29 s clock, so the UI gets an SSE timeout frame."""
    import time

    from lambdas.api.ask import handler as ask

    class Ctx:
        def __init__(self, ms: int) -> None:
            self.ms = ms

        def get_remaining_time_in_millis(self) -> int:
            return self.ms

    monkeypatch.delenv("RP_ASK_TIMEOUT_SECONDS", raising=False)
    now = time.monotonic()
    assert ask.read_timeout(None, now) == pytest.approx(25.0, abs=0.1)
    monkeypatch.setenv("RP_ASK_TIMEOUT_SECONDS", "25")
    assert ask.read_timeout(Ctx(60_000), now - 3) == pytest.approx(22.0, abs=0.1)  # 3 s spent before the call
    assert ask.read_timeout(Ctx(10_000), now) == pytest.approx(9.0, abs=0.1)  # Lambda remaining time wins
    assert ask.read_timeout(Ctx(60_000), now - 40) == 1.0

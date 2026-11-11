"""The agent loop behind a small interface, so the runner is testable without Bedrock.

``StrandsDriver`` runs a Strands ``Agent`` (BedrockModel, temperature 0) with the Gateway's
MCP tools and turns hook callbacks + stream events into ``DriverEvent``s. Tool names arrive
from the Gateway as ``{target}___{tool}``; the agent registers them under the bare name so
the prompt, the trace and the run record all say ``search_units``.
"""

from __future__ import annotations

import contextlib
import json
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from evidence_core.models.tools import INJECTED_FIELDS

TOOL_NAME_DELIMITER = "___"


# --- events -----------------------------------------------------------------------------------

@dataclass
class TextDelta:
    text: str


@dataclass
class ToolStarted:
    call_id: str
    tool: str
    input: dict[str, Any]  # model-visible arguments only


@dataclass
class ToolFinished:
    call_id: str
    tool: str
    ms: int
    status: Literal["ok", "error", "denied"]
    payload: dict[str, Any] | None
    error: str | None = None


@dataclass
class Completed:
    text: str
    usage: dict[str, int] = field(default_factory=dict)


DriverEvent = TextDelta | ToolStarted | ToolFinished | Completed


@dataclass(frozen=True)
class DriverSpec:
    model_id: str
    temperature: float | None
    max_tokens: int
    system_prompt: str
    tools: list[str]  # bare tool names enabled for this run
    gateway_url: str
    headers: dict[str, str]  # Authorization + X-Rp-* for the Gateway
    max_tool_calls: int
    inject: dict[str, Any] | None = None  # fallback identity injection (agent hook) values
    region: str = "us-east-1"


class Driver(Protocol):
    def run(self, prompt: str) -> AsyncIterator[DriverEvent]: ...

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[DriverEvent]: ...

    def close(self) -> None: ...


# --- helpers shared with tests ---------------------------------------------------------------------

def bare_tool_name(name: str) -> str:
    return name.split(TOOL_NAME_DELIMITER, 1)[-1]


def model_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
    return {k: v for k, v in (arguments or {}).items() if k not in INJECTED_FIELDS}


def payload_from_tool_result(result: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Strands ToolResult (MCP) -> (tool JSON payload, error text)."""
    if not isinstance(result, dict):
        return None, "tool returned no result"
    structured = result.get("structuredContent")
    texts = [c.get("text") for c in result.get("content") or [] if isinstance(c, dict) and c.get("text")]
    if isinstance(structured, dict):
        payload: Any = structured
    else:
        payload = None
        for t in texts:
            try:
                payload = json.loads(t)
                break
            except (TypeError, json.JSONDecodeError):
                continue
    if result.get("status") == "error" or result.get("isError"):
        if isinstance(payload, dict) and "error" in payload:
            return payload, None
        return None, (texts[0] if texts else "tool error")[:500]
    if not isinstance(payload, dict):
        return None, "tool result is not JSON"
    return payload, None


def classify_payload(payload: dict[str, Any] | None, error: str | None) -> tuple[str, str | None]:
    """-> (status, error message) using the tool Lambdas' ``{"error": {code, message}}`` shape."""
    if payload is not None and isinstance(payload.get("error"), dict):
        err = payload["error"]
        return ("denied" if err.get("code") == "forbidden" else "error"), str(err.get("message", ""))
    if error is not None:
        lowered = error.lower()
        return ("denied" if "unauthorized" in lowered or "refused" in lowered else "error"), error
    return "ok", None


# --- Strands implementation ------------------------------------------------------------------------

class StrandsDriver:
    """One Strands Agent per ask (conversation kept across the citation retry)."""

    def __init__(self, spec: DriverSpec) -> None:
        from strands import Agent
        from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
        from strands.models.bedrock import BedrockModel
        from strands.tools.mcp import MCPClient
        from strands.tools.mcp.mcp_agent_tool import MCPAgentTool

        self.spec = spec
        self._pending: list[DriverEvent] = []
        self._started: dict[str, float] = {}
        self._calls = 0
        self.mcp = MCPClient(url=spec.gateway_url, headers=spec.headers)
        self.mcp.start()
        tools = []
        for t in self._list_all():
            name = bare_tool_name(t.tool_name)
            if name in spec.tools:
                tools.append(MCPAgentTool(t.mcp_tool, self.mcp, name_override=name))
        self.tool_names = [t.tool_name for t in tools]
        model_kwargs: dict[str, Any] = {"model_id": spec.model_id, "max_tokens": spec.max_tokens,
                                        "region_name": spec.region}
        if spec.temperature is not None:
            model_kwargs["temperature"] = spec.temperature
        self.agent = Agent(model=BedrockModel(**model_kwargs), tools=tools, system_prompt=spec.system_prompt,
                           callback_handler=None)
        self.agent.hooks.add_callback(BeforeToolCallEvent, self._before)
        self.agent.hooks.add_callback(AfterToolCallEvent, self._after)

    def _list_all(self) -> list[Any]:
        out: list[Any] = []
        token = None
        while True:
            page = self.mcp.list_tools_sync(pagination_token=token)
            out.extend(page)
            token = getattr(page, "pagination_token", None)
            if not token:
                return out

    # hooks ------------------------------------------------------------------------------------
    def _before(self, event: Any) -> None:
        use = event.tool_use
        call_id = use.get("toolUseId") or uuid.uuid4().hex
        visible = model_args(use.get("input"))
        self._calls += 1
        if self._calls > self.spec.max_tool_calls:
            event.cancel_tool = f"tool call limit ({self.spec.max_tool_calls}) reached; answer with what you have"
        # Never forward model-supplied identity; in fallback mode write the caller's own values.
        use["input"] = visible | (self.spec.inject or {})
        self._started[call_id] = time.monotonic()
        self._pending.append(ToolStarted(call_id=call_id, tool=bare_tool_name(use.get("name", "")), input=visible))

    def _after(self, event: Any) -> None:
        use = event.tool_use
        call_id = use.get("toolUseId", "")
        ms = int((time.monotonic() - self._started.pop(call_id, time.monotonic())) * 1000)
        if getattr(event, "cancel_message", None):
            self._pending.append(ToolFinished(call_id, bare_tool_name(use.get("name", "")), ms, "error", None,
                                              str(event.cancel_message)))
            return
        payload, error = payload_from_tool_result(event.result)
        status, message = classify_payload(payload, error)
        self._pending.append(ToolFinished(call_id, bare_tool_name(use.get("name", "")), ms, status,  # type: ignore[arg-type]
                                          payload if status == "ok" else payload, message))

    def _drain(self) -> list[DriverEvent]:
        out, self._pending = self._pending, []
        return out

    # loop ---------------------------------------------------------------------------------------
    async def run(self, prompt: str) -> AsyncIterator[DriverEvent]:  # type: ignore[override]
        final_text = ""
        usage: dict[str, int] = {}
        async for ev in self.agent.stream_async(prompt):
            for pending in self._drain():
                yield pending
            if isinstance(ev, dict) and isinstance(ev.get("data"), str):
                yield TextDelta(ev["data"])
            if isinstance(ev, dict) and "result" in ev:
                result = ev["result"]
                final_text = str(result).strip()
                acc = getattr(getattr(result, "metrics", None), "accumulated_usage", None) or {}
                usage = {k: int(acc.get(k, 0)) for k in ("inputTokens", "outputTokens", "totalTokens")}
        for pending in self._drain():
            yield pending
        yield Completed(text=final_text, usage=usage)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[DriverEvent]:  # type: ignore[override]
        """Direct tool call made by agent code (not the model), e.g. get_region for chip bboxes."""
        call_id = f"agent-{uuid.uuid4().hex[:12]}"
        visible = model_args(arguments)
        yield ToolStarted(call_id=call_id, tool=tool, input=visible)
        t0 = time.monotonic()
        gateway_name = next((n for n in self._gateway_names() if bare_tool_name(n) == tool), tool)
        result = await self.mcp.call_tool_async(tool_use_id=call_id, name=gateway_name,
                                                arguments=visible | (self.spec.inject or {}))
        payload, error = payload_from_tool_result(result)
        status, message = classify_payload(payload, error)
        yield ToolFinished(call_id, tool, int((time.monotonic() - t0) * 1000), status, payload, message)  # type: ignore[arg-type]

    def _gateway_names(self) -> list[str]:
        return [t.mcp_tool.name for t in self.agent.tool_registry.registry.values() if hasattr(t, "mcp_tool")]

    def close(self) -> None:
        with contextlib.suppress(Exception):  # best effort shutdown
            self.mcp.stop(None, None, None)

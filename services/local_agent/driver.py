"""The v1 Strands driver with its tools served by the ``LocalGateway`` instead of the AgentCore Gateway.

``LocalStrandsDriver`` subclasses ``rp_agent.driver.StrandsDriver`` and keeps everything that shapes the
answer: the same Strands ``Agent``, ``BedrockModel`` (Converse, the lane's model id, temperature and max
tokens), system prompt, tool-call limit and the before/after hooks that time each call and build the
trace. Only the transport differs: tool specs are the definitions the deploy script registers on the
Gateway (``deploy.tool_definitions`` over ``contracts/generated/mcp_tools.json``), and a call runs the
tool Lambda handler in-process.
"""

from __future__ import annotations

import json
import time
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from evidence_core.models.tools import HEADER_PACKAGE, HEADER_PIPELINE_VERSION, HEADER_RUN
from rp_agent.driver import (
    DriverEvent,
    DriverSpec,
    StrandsDriver,
    ToolFinished,
    ToolStarted,
    classify_payload,
    model_args,
)

from .gateway import Caller, LocalGateway

REPO = Path(__file__).resolve().parents[2]
MCP_TOOLS = REPO / "contracts" / "generated" / "mcp_tools.json"


def gateway_tool_specs(path: Path = MCP_TOOLS) -> dict[str, dict[str, Any]]:
    """Tool name -> Strands ToolSpec, built exactly as ``deploy_agentcore.sh`` builds the Gateway targets."""
    raw = _deploy_module().tool_definitions(json.loads(path.read_text()))
    return {name: {"name": name, "description": t["definition"]["description"],
                   "inputSchema": {"json": t["definition"]["inputSchema"]}} for name, t in raw.items()}


def _deploy_module() -> Any:
    """``services/agent/deploy/deploy.py`` by path (``deploy`` is too generic a top-level name to import)."""
    import importlib.util
    import sys

    name = "rp_agent_deploy_script"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, REPO / "services" / "agent" / "deploy" / "deploy.py")
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def _tool_result(tool_use_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """The ToolResult the model sees: the Lambda's JSON as text content, as the Gateway returns it."""
    return {"toolUseId": tool_use_id, "status": "success", "content": [{"text": json.dumps(payload)}]}


def local_tool(spec: dict[str, Any], gateway: LocalGateway, caller: Caller, headers: dict[str, str]) -> Any:
    from strands.types._events import ToolResultEvent
    from strands.types.tools import AgentTool

    class LocalGatewayTool(AgentTool):
        @property
        def tool_name(self) -> str:
            return spec["name"]

        @property
        def tool_spec(self) -> Any:
            return spec

        @property
        def tool_type(self) -> str:
            return "local-gateway"

        async def stream(self, tool_use: Any, invocation_state: dict[str, Any], **kwargs: Any) -> Any:
            payload, _ms = gateway.invoke(spec["name"], tool_use.get("input") or {}, caller,
                                          pipeline_version=headers[HEADER_PIPELINE_VERSION],
                                          package_id=headers[HEADER_PACKAGE], run_id=headers[HEADER_RUN])
            yield ToolResultEvent(_tool_result(tool_use["toolUseId"], payload))

    return LocalGatewayTool()


class LocalStrandsDriver(StrandsDriver):
    """``StrandsDriver`` with in-process tools. ``boto_session`` carries the Bedrock credentials."""

    def __init__(self, spec: DriverSpec, *, gateway: LocalGateway, caller: Caller, boto_session: Any,
                 tool_specs: dict[str, dict[str, Any]] | None = None) -> None:
        from strands import Agent
        from strands.hooks import AfterToolCallEvent, BeforeToolCallEvent
        from strands.models.bedrock import BedrockModel

        self.spec = spec
        self.gateway = gateway
        self.caller = caller
        self._pending: list[DriverEvent] = []
        self._started: dict[str, float] = {}
        self._calls = 0
        specs = tool_specs or gateway_tool_specs()
        tools = [local_tool(specs[name], gateway, caller, spec.headers) for name in spec.tools if name in specs]
        self.tool_names = [t.tool_name for t in tools]
        model_kwargs: dict[str, Any] = {"model_id": spec.model_id, "max_tokens": spec.max_tokens,
                                        "boto_session": boto_session}
        if spec.temperature is not None:
            model_kwargs["temperature"] = spec.temperature
        self.agent = Agent(model=BedrockModel(**model_kwargs), tools=tools, system_prompt=spec.system_prompt,
                           callback_handler=None)
        self.agent.hooks.add_callback(BeforeToolCallEvent, self._before)
        self.agent.hooks.add_callback(AfterToolCallEvent, self._after)

    async def call_tool(self, tool: str, arguments: dict[str, Any]) -> AsyncIterator[DriverEvent]:  # type: ignore[override]
        """Direct tool call made by agent code (``get_region`` for claim regions), as in ``StrandsDriver``."""
        call_id = f"agent-{uuid.uuid4().hex[:12]}"
        visible = model_args(arguments)
        yield ToolStarted(call_id=call_id, tool=tool, input=visible)
        t0 = time.monotonic()
        h = self.spec.headers
        payload, _ = self.gateway.invoke(tool, visible, self.caller, pipeline_version=h[HEADER_PIPELINE_VERSION],
                                         package_id=h[HEADER_PACKAGE], run_id=h[HEADER_RUN])
        status, message = classify_payload(payload, None)
        yield ToolFinished(call_id, tool, int((time.monotonic() - t0) * 1000), status, payload, message)  # type: ignore[arg-type]

    def close(self) -> None:  # no MCP session to stop
        return None

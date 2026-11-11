"""Entry wrapper for AgentCore Gateway Lambda targets.

Gateway invokes the function with ``event`` = the tool arguments (after the REQUEST
interceptor injected ``callerSub, groups, pipelineVersion, packageId, runId``) and
``context.client_context.custom['bedrockAgentCoreToolName']`` = ``{target}___{tool}``.
The wrapper splits model arguments from injected context, validates the model arguments
against the contract model, runs the tool and returns the result as camelCase JSON.
Model-supplied values for injected keys never reach the tool function.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from evidence_core.base import RpModel
from evidence_core.ids import PIPELINE_VERSION_RE, principals_for, short
from evidence_core.logs import get_logger
from evidence_core.models.tools import API_TOOL_ARG_MODELS, TOOL_ARG_MODELS, InjectedContext, split_arguments
from pydantic import ValidationError

from .errors import BadRequest, Forbidden, ToolError
from .stores import Stores, stores

A = TypeVar("A", bound=RpModel)

TOOL_NAME_DELIMITER = "___"


@dataclass(frozen=True)
class ToolCall:
    """Everything a tool function may rely on. ``ctx`` comes from the interceptor only."""

    tool: str
    ctx: InjectedContext
    principals: list[str]
    stores: Stores
    gateway_tool_name: str | None = None


def gateway_tool_name(context: Any) -> str | None:
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    return custom.get("bedrockAgentCoreToolName")


API_SOURCE = "rp-api"  # rpSource of rp-api's invocations (deps.GRAPH_HOP_SOURCE)


def invocation_source(context: Any, gateway_name: str | None) -> str:
    """Who invoked the tool: ``gateway`` (an MCP tool call), the ``rpSource`` an internal caller sets in the Lambda
    client context (``rp-api`` for the showcase composers), else ``direct``. Logged on every call."""
    if gateway_name:
        return "gateway"
    custom = getattr(getattr(context, "client_context", None), "custom", None) or {}
    return str(custom.get("rpSource") or "direct")


def bare_tool_name(name: str) -> str:
    return name.split(TOOL_NAME_DELIMITER, 1)[-1]


def tool_handler(tool: str) -> Callable[[Callable[[Any, ToolCall], RpModel]], Callable[[dict[str, Any], Any],
                                                                                     dict[str, Any]]]:
    """Wrap ``fn(args, call) -> ResultModel`` as a Lambda handler for one MCP tool."""
    args_model = TOOL_ARG_MODELS[tool]
    log = get_logger(f"rp.tools.{tool}")

    def deco(fn: Callable[[Any, ToolCall], RpModel]) -> Callable[[dict[str, Any], Any], dict[str, Any]]:
        def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
            t0 = time.monotonic()
            gw_name = gateway_tool_name(context)
            if gw_name and bare_tool_name(gw_name) != tool:
                return BadRequest(f"this function serves {tool}, not {bare_tool_name(gw_name)}").payload()
            try:
                model_args, ctx = split_arguments(event or {})
            except PermissionError as e:
                log.warning("missing injected context", extra={"rp": {"tool": tool, "detail": str(e)}})
                return Forbidden("Caller identity was not injected; the request was refused.").payload()
            if not PIPELINE_VERSION_RE.match(ctx.pipeline_version):
                return BadRequest(f"invalid pipelineVersion {ctx.pipeline_version!r}").payload()
            principals = principals_for(ctx.caller_sub, ctx.groups)
            log_ctx = {"tool": tool, "source": invocation_source(context, gw_name),
                       "callerSub": short(ctx.caller_sub), "groups": ctx.groups,
                       "pipelineVersion": ctx.pipeline_version, "packageId": ctx.package_id, "runId": ctx.run_id}
            model = API_TOOL_ARG_MODELS.get(tool, args_model) if log_ctx["source"] == API_SOURCE else args_model
            try:
                args = model.model_validate(model_args)
            except ValidationError as e:
                log.info("bad arguments", extra={"rp": log_ctx | {"errors": e.error_count()}})
                return BadRequest(_validation_message(e)).payload()
            call = ToolCall(tool=tool, ctx=ctx, principals=principals, stores=stores(), gateway_tool_name=gw_name)
            try:
                result = fn(args, call)
            except ToolError as e:
                log.info("tool error", extra={"rp": log_ctx | {"code": e.code, "message": e.message}})
                return e.payload()
            except Exception:
                log.exception("tool failed", extra={"rp": log_ctx})
                return {"error": {"code": "internal", "message": f"{tool} failed; see the function logs."}}
            log.info("tool ok", extra={"rp": log_ctx | {"ms": int((time.monotonic() - t0) * 1000)}})
            return result.dump()

        handler.__wrapped__ = fn  # type: ignore[attr-defined]
        return handler

    return deco


def _validation_message(e: ValidationError) -> str:
    parts = []
    for err in e.errors()[:3]:
        loc = ".".join(str(p) for p in err.get("loc", ()))
        parts.append(f"{loc}: {err.get('msg')}")
    return "invalid arguments: " + "; ".join(parts)


logging.getLogger("botocore").setLevel(logging.WARNING)

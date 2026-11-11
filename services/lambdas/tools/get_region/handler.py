"""get_region@1: page + bbox for a unit or one of its fields (no presigned URL in the tool).

The unit must belong to the injected package and pipeline version; the citation validator
in the agent attaches page/bbox to claims from this result, never from model text.
"""

from __future__ import annotations

from evidence_core.models.tools import GetRegionArgs, RegionResult

from lambdas.tools._lib.region import resolve_region
from lambdas.tools._lib.runtime import ToolCall, tool_handler


@tool_handler("get_region")
def get_region(args: GetRegionArgs, call: ToolCall) -> RegionResult:
    call.stores.require_package(call.ctx.package_id, call.principals)
    return resolve_region(
        call.stores,
        args.unit_id,
        args.field_id,
        call.principals,
        package_id=call.ctx.package_id,
        pipeline_version=call.ctx.pipeline_version,
    )


handler = get_region

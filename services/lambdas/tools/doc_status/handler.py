"""doc_status@1: ingestion status of the package's documents for the injected pipeline version.

One query on the DocStatus GSI ``byPackage`` (gsi1pk = packageId, gsi1sk begins with
``{pv}#``) returns every stage and lane record of the lane; file names and docTypes come
from the Documents table; ``ready`` comes from the package rollup when present.
"""

from __future__ import annotations

from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core.enums import LaneState, StageState
from evidence_core.models.status import LaneRecord
from evidence_core.models.tools import DocStatusArgs, DocStatusDoc, DocStatusResult

from lambdas.tools._lib.errors import NotFound
from lambdas.tools._lib.runtime import ToolCall, tool_handler


def _lane_ready(lanes: dict[str, LaneRecord]) -> bool:
    active = [ln for ln in lanes.values() if ln.state is not LaneState.SKIPPED]
    return bool(active) and all(ln.state is LaneState.SUCCEEDED for ln in active)


@tool_handler("doc_status")
def doc_status(args: DocStatusArgs, call: ToolCall) -> DocStatusResult:
    pkg = call.stores.require_package(call.ctx.package_id, call.principals)
    pv = call.ctx.pipeline_version
    prefix = f"{pv}#{args.doc_id}#" if args.doc_id else f"{pv}#"
    items = call.stores.query_all(
        "doc_status", Key("gsi1pk").eq(pkg.package_id) & Key("gsi1sk").begins_with(prefix), index="byPackage"
    )
    docs_meta = {d.doc_id: d for d in call.stores.list_documents(pkg.package_id)}
    lanes: dict[str, LaneRecord] = {}
    stages: dict[str, dict[str, StageState]] = {}
    for item in items:
        kind = item.get("recordType")
        doc_id = item.get("docId") or item.get("pk")
        if kind == "LANE":
            lanes[doc_id] = LaneRecord.load(item)
        elif kind == "STAGE":
            stages.setdefault(doc_id, {})[item["stageId"]] = StageState(item["state"])
    doc_ids = sorted(set(lanes) | set(stages))
    if args.doc_id and not doc_ids:
        raise NotFound(f"No status for document {args.doc_id} in pipeline version {pv}.")
    out: list[DocStatusDoc] = []
    for doc_id in doc_ids:
        lane = lanes.get(doc_id)
        meta = docs_meta.get(doc_id)
        out.append(
            DocStatusDoc(
                doc_id=doc_id,
                file_name=meta.file_name if meta else doc_id,
                doc_type=meta.doc_type if meta else None,
                lane_state=lane.state if lane else LaneState.PENDING,
                current_stage=lane.current_stage.value if lane and lane.current_stage else None,
                stages=stages.get(doc_id, {}),
                units=lane.units if lane else None,
                held=lane.held if lane else None,
                indexed=lane.indexed if lane else None,
            )
        )
    rollup: Any = pkg.lanes.get(pv)
    ready = rollup.ready if rollup is not None else _lane_ready(lanes)
    return DocStatusResult(package_id=pkg.package_id, pipeline_version=pv, ready=ready, docs=out)


handler = doc_status

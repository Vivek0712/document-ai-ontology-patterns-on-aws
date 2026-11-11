"""GET /packages/{packageId}/status: the stage board, polled every second.

One GSI query (``byPackage``) returns every stage and lane record of the package; documents come
from ``rp-documents``. Lanes not started yet are rendered from the stage registry (PENDING, or
SKIPPED with the reason), so the board always has every column and row.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from evidence_core.enums import LaneState, StageState
from evidence_core.models.api import (
    Counters,
    DocStatusView,
    DocumentItem,
    LaneDocCounts,
    LaneRollup,
    LaneView,
    Package,
    PackageStatus,
    StageCell,
)
from evidence_core.models.status import LaneRecord, StageRecord, iso
from evidence_core.stages import resolve_stages

from .. import store
from ..acl import require_package
from ..deps import Deps, get_deps
from ..http import Reply, Request
from . import PACKAGE_STATUS, router

_CELL_FIELDS = set(StageCell.model_fields)


def stage_cell(rec: StageRecord) -> StageCell:
    return StageCell.model_validate({k: getattr(rec, k) for k in _CELL_FIELDS if hasattr(rec, k)})


def default_cells(deps: Deps, pv: str) -> list[StageCell]:
    """Cells for a lane with no records yet: PENDING for enabled stages, SKIPPED with the reason."""
    stages = resolve_stages(deps.pipeline(pv).config)
    return [
        StageCell(stage_id=s.id, group=s.group, order=s.order, title=s.title,
                  state=StageState.PENDING if s.enabled else StageState.SKIPPED, skip_reason=s.skip_reason)
        for s in stages
    ]


def compute_rollup(lanes: list[LaneRecord | None], *, doc_count: int) -> LaneRollup:
    counts = LaneDocCounts(total=doc_count)
    indexed = expected = 0
    for lane in lanes:
        state = lane.state if lane else LaneState.PENDING
        if state is LaneState.SUCCEEDED:
            counts.succeeded += 1
        elif state is LaneState.FAILED:
            counts.failed += 1
        elif state is LaneState.RUNNING:
            counts.running += 1
        elif state is LaneState.SKIPPED:
            counts.skipped += 1
        else:
            counts.pending += 1
        if lane:
            indexed += lane.indexed or 0
            expected += max((lane.units or 0) - (lane.held or 0), 0)
    if counts.total == 0:
        state_s = "EMPTY"
    elif counts.running:
        state_s = "RUNNING"
    elif counts.pending:
        state_s = "RUNNING" if counts.succeeded + counts.failed else "PENDING"
    elif counts.failed:
        state_s = "PARTIAL" if counts.succeeded else "FAILED"
    else:
        state_s = "SUCCEEDED"
    active = counts.total - counts.skipped
    ready = counts.total > 0 and active == counts.succeeded and indexed == expected
    return LaneRollup(state=state_s, ready=ready, indexed=indexed, expected=expected, docs=counts)


def compute_counters(lanes: list[LaneRecord | None]) -> Counters:
    c = Counters()
    for lane in lanes:
        if lane:
            c.units += lane.units or 0
            c.held += lane.held or 0
            c.indexed += lane.indexed or 0
            c.triples += lane.triples or 0
    return c


def group_records(records: list[dict[str, Any]]) -> tuple[dict[tuple[str, str], LaneRecord],
                                                          dict[tuple[str, str], list[StageRecord]]]:
    lanes: dict[tuple[str, str], LaneRecord] = {}
    stages: dict[tuple[str, str], list[StageRecord]] = defaultdict(list)
    for r in records:
        kind = r.get("recordType")
        if kind == "LANE":
            lane = LaneRecord.load(r)
            lanes[(lane.doc_id, lane.pipeline_version)] = lane
        elif kind == "STAGE":
            rec = StageRecord.load(r)
            stages[(rec.doc_id, rec.pipeline_version)].append(rec)
    for recs in stages.values():
        recs.sort(key=lambda s: s.order)
    return lanes, stages


def build_package_status(deps: Deps, pkg: Package) -> PackageStatus:
    docs: list[DocumentItem] = store.query_documents(deps, pkg.package_id)
    lanes, stages = group_records(store.package_status_records(deps, pkg.package_id))
    views: list[DocStatusView] = []
    for d in docs:
        doc_lanes: dict[str, LaneView] = {}
        for pv in pkg.pipeline_versions:
            lane = lanes.get((d.doc_id, pv))
            recs = stages.get((d.doc_id, pv))
            cells = [stage_cell(r) for r in recs] if recs else default_cells(deps, pv)
            doc_lanes[pv] = LaneView(
                state=lane.state if lane else LaneState.PENDING,
                current_stage=lane.current_stage if lane else None,
                execution_arn=lane.execution_arn if lane else None,
                started_at=lane.started_at if lane else None,
                ended_at=lane.ended_at if lane else None,
                duration_ms=lane.duration_ms if lane else None,
                config_sha=lane.config_sha if lane else None,
                stages=cells,
            )
        views.append(DocStatusView(
            doc_id=d.doc_id, file_name=d.file_name, size_bytes=d.size_bytes, page_count=d.page_count,
            doc_type=d.doc_type, upload_state=d.upload_state, bronze_key=d.bronze_key, in_bronze_at=d.in_bronze_at,
            lanes=doc_lanes,
        ))

    rollups: dict[str, LaneRollup] = {}
    counters: dict[str, Counters] = {}
    for pv in pkg.pipeline_versions:
        pv_lanes = [lanes.get((d.doc_id, pv)) for d in docs]
        # The status-stream rollup on the package row is authoritative when present; the local
        # computation covers the window before the first stream record lands.
        rollups[pv] = pkg.lanes.get(pv) or compute_rollup(pv_lanes, doc_count=len(docs))
        counters[pv] = pkg.counters.get(pv) or compute_counters(pv_lanes)

    now = iso()
    return PackageStatus(
        package_id=pkg.package_id,
        updated_at=pkg.updated_at or pkg.created_at,
        server_time=now,
        lanes=rollups,
        counters=counters,
        objects_in_bronze=sum(1 for d in docs if d.upload_state == "IN_BRONZE"),
        ask_ready=bool(pkg.pipeline_versions) and all(rollups[pv].ready for pv in pkg.pipeline_versions),
        docs=views,
    )


@router.route("GET", PACKAGE_STATUS)
def get_package_status(req: Request) -> Reply:
    deps = get_deps()
    pkg = require_package(deps, req.caller, req.param("packageId"))
    return Reply(build_package_status(deps, pkg))

"""Document and stage inspection: detail, one stage, events, units, reprocess."""

from __future__ import annotations

import os
import re
import time
from collections import defaultdict

from evidence_core import s3keys
from evidence_core.enums import LaneState, PiiFlag, StageId, StageState
from evidence_core.ids import PIPELINE_VERSION_RE, pv_slug
from evidence_core.models.api import (
    DocumentDetail,
    LaneDetail,
    ReprocessRequest,
    ReprocessResponse,
    StageDetail,
    StageEventPage,
    UnitList,
    UnitListItem,
)
from evidence_core.models.status import IngestTrigger, LaneRecord, StageRecord
from evidence_core.stages import STAGE_IDS, resolve_stages

from .. import store
from ..acl import DocAccess, require_doc
from ..deps import Deps, get_deps
from ..http import ApiException, Reply, Request, bad_request, conflict, forbidden, not_found
from . import DOC_EVENTS, DOC_REPROCESS, DOC_STAGE, DOC_UNITS, DOCUMENT, router


def _pv(req: Request) -> str:
    pv = req.param("pv")
    if not PIPELINE_VERSION_RE.match(pv):
        raise bad_request(f"invalid pipeline version {pv!r}")
    return pv


def _stage_id(req: Request) -> StageId:
    raw = req.param("stageId")
    try:
        return StageId(raw)
    except ValueError as exc:
        raise bad_request(f"unknown stage {raw!r}; one of {', '.join(s.value for s in STAGE_IDS)}") from exc


def load_document(deps: Deps, access: DocAccess, doc_id: str):  # noqa: ANN201 - DocumentItem
    doc = store.get_document(deps, access.package_id, doc_id)
    if doc is None:
        raise not_found(f"Document {doc_id} not found.")
    return doc


@router.route("GET", DOCUMENT)
def get_document(req: Request) -> Reply:
    deps = get_deps()
    doc_id = req.param("docId")
    access = require_doc(deps, req.caller, doc_id)
    doc = load_document(deps, access, doc_id)
    lanes: dict[str, LaneRecord] = {}
    stages: dict[str, list[StageRecord]] = defaultdict(list)
    for r in store.doc_records(deps, doc_id, "LANE#") + store.doc_records(deps, doc_id, "STAGE#"):
        if r.get("recordType") == "LANE" or r["sk"].startswith("LANE#"):
            lane = LaneRecord.load(r)
            lanes[lane.pipeline_version] = lane
        else:
            rec = StageRecord.load(r)
            stages[rec.pipeline_version].append(rec)
    pkg = store.get_package(deps, access.package_id)
    versions = list(pkg.pipeline_versions) if pkg else []
    versions += [pv for pv in sorted(set(lanes) | set(stages)) if pv not in versions]
    detail: dict[str, LaneDetail] = {}
    for pv in versions:
        lane = lanes.get(pv) or LaneRecord(doc_id=doc_id, package_id=access.package_id, pipeline_version=pv,
                                           state=LaneState.PENDING)
        recs = sorted(stages.get(pv, []), key=lambda s: s.order) or _placeholder_records(deps, doc_id,
                                                                                         access.package_id, pv)
        detail[pv] = LaneDetail(lane=lane, stages=recs)
    return Reply(DocumentDetail(document=doc, lanes=detail, can_write=access.is_owner))


def _placeholder_records(deps: Deps, doc_id: str, package_id: str, pv: str) -> list[StageRecord]:
    return [
        StageRecord(doc_id=doc_id, package_id=package_id, pipeline_version=pv, stage_id=s.id, order=s.order,
                    group=s.group, title=s.title, state=StageState.PENDING if s.enabled else StageState.SKIPPED,
                    services=s.services, function_name=s.functions[0] if s.functions else None,
                    skip_reason=s.skip_reason)
        for s in resolve_stages(deps.pipeline(pv).config)
    ]


@router.route("GET", DOC_STAGE)
def get_stage(req: Request) -> Reply:
    deps = get_deps()
    doc_id, pv, stage_id = req.param("docId"), _pv(req), _stage_id(req)
    access = require_doc(deps, req.caller, doc_id)
    definitions = resolve_stages(deps.pipeline(pv).config)
    definition = next(s for s in definitions if s.id == stage_id)
    rec = next((r for r in store.stage_records(deps, doc_id, pv) if r.stage_id == stage_id), None)
    if rec is None:
        rec = next(r for r in _placeholder_records(deps, doc_id, access.package_id, pv) if r.stage_id == stage_id)
    events = store.all_stage_events(deps, doc_id, pv, stage_id.value, execution_arn=rec.execution_arn)
    history = store.stage_history(deps, doc_id, pv, stage_id.value)
    idx = [s.id for s in definitions].index(stage_id)
    prev_stage = definitions[idx - 1].id if idx > 0 else None
    next_stage = definitions[idx + 1].id if idx + 1 < len(definitions) else None
    return Reply(StageDetail(record=rec, definition=definition, events=events, previous_stage=prev_stage,
                             next_stage=next_stage, history=history))


@router.route("GET", DOC_EVENTS)
def get_events(req: Request) -> Reply:
    deps = get_deps()
    doc_id, pv = req.param("docId"), _pv(req)
    require_doc(deps, req.caller, doc_id)
    stage = req.query.get("stageId")
    if stage and stage not in {s.value for s in STAGE_IDS}:
        raise bad_request(f"unknown stage {stage!r}")
    items, lek = store.stage_events(deps, doc_id, pv, limit=req.int_query("limit", 200),
                                    cursor=store.decode_cursor(req.query.get("cursor")), stage_id=stage)
    return Reply(StageEventPage(items=items, next_cursor=store.encode_cursor(lek)))


@router.route("GET", DOC_UNITS)
def list_units(req: Request) -> Reply:
    """UnitStatus rows joined with the Silver units file (all units, held ones included)."""
    deps = get_deps()
    doc_id, pv = req.param("docId"), _pv(req)
    access = require_doc(deps, req.caller, doc_id)
    statuses = {s.unit_id: s for s in store.unit_status_for_lane(deps, doc_id, pv)}
    silver_key = s3keys.silver_units_key(access.package_id, doc_id, pv)
    units = {u.unit_id: u for u in store.load_units_jsonl(deps, silver_key)}
    items: list[UnitListItem] = []
    for uid in sorted(set(statuses) | set(units)):
        st, u = statuses.get(uid), units.get(uid)
        pii = (st.pii_flag if st else None) or (u.pii_flag if u else PiiFlag.NONE)
        held = (st.held if st else False) or pii is PiiFlag.HELD
        items.append(UnitListItem(
            unit_id=uid,
            label=(st.label if st else None) or (u.label if u else uid),
            unit_type=(st.unit_type if st else None) or u.unit_type,  # type: ignore[union-attr]
            pages=u.pages if u else [],
            pii_flag=pii,
            held=held,
            indexed=bool(st and (st.aoss_id or st.indexed_at)),
            aoss_id=st.aoss_id if st else None,
            field_count=len(u.fields) if u else 0,
            chars=u.chars if u else 0,
            silver_key=(st.silver_key if st else None) or silver_key,
            gold_key=(st.gold_key if st else None)
            or (None if held or u is None else s3keys.gold_unit_key(access.package_id, uid)),
            review_items=st.review_items if st else 0,
        ))
    return Reply(UnitList(items=items))


_EXEC_NAME_RE = re.compile(r"[^A-Za-z0-9-_]")


def execution_name(doc_id: str, pv: str) -> str:
    """Unique, <= 80 chars, only characters Step Functions accepts."""
    base = _EXEC_NAME_RE.sub("-", f"rp-{doc_id[:44]}-{pv_slug(pv)}-re")
    return f"{base}-{int(time.time() * 1000)}"[:80]


@router.route("POST", DOC_REPROCESS)
def reprocess(req: Request) -> Reply:
    deps = get_deps()
    doc_id = req.param("docId")
    access = require_doc(deps, req.caller, doc_id)
    if not access.is_owner:
        raise forbidden("Only the document owner can reprocess it.")
    body: ReprocessRequest = req.parse(ReprocessRequest)
    pkg = store.get_package(deps, access.package_id)
    if pkg is None or body.pipeline_version not in pkg.pipeline_versions:
        raise bad_request(f"pipelineVersion {body.pipeline_version} is not enabled for this package")
    doc = load_document(deps, access, doc_id)
    if doc.upload_state != "IN_BRONZE":
        raise conflict(f"Document is {doc.upload_state}; only documents in Bronze can be reprocessed.")
    lane = store.lane_record(deps, doc_id, body.pipeline_version)
    if lane and lane.state is LaneState.RUNNING:
        raise conflict("This lane is already running.")
    arn = os.environ.get("RP_STATE_MACHINE_ARN") or deps.settings.state_machine_arn
    if not arn or deps.sfn is None:
        raise ApiException(503, "not_ready", "Ingest state machine is not configured.")
    trigger = IngestTrigger(pipeline_version=body.pipeline_version, bucket=deps.bucket, key=doc.bronze_key,
                            version_id=doc.source_version_id, size=doc.size_bytes, trigger="reprocess",
                            requested_by=req.caller.sub)
    resp = deps.sfn.start_execution(stateMachineArn=arn, name=execution_name(doc_id, body.pipeline_version),
                                    input=trigger.dump_json())
    return Reply(ReprocessResponse(doc_id=doc_id, pipeline_version=body.pipeline_version,
                                   execution_arn=resp["executionArn"]), status=202)

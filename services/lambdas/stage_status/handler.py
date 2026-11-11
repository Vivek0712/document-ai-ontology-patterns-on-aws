"""rp-stage-status: status writes for states that are not a stage Lambda.

ops (``event.op``):
- ``start``          ``{stageId, engine?, ...envelope}``: STARTED with config slice + versions (ExtractOpen)
- ``close_extract``  ``{...envelope, extractParts}``: merge the Parallel branches into the extract
                     ``StageResult`` (ExtractOutput + every artifact) and write SUCCEEDED (ExtractClose)
- ``lane_failed``    ``{ctx, error}``: current stage + lane FAILED from the Catch output (LaneFailed)
- ``execution_event`` ``{detail}``: EventBridge backstop for executions that FAILED / TIMED_OUT /
                     ABORTED without reaching LaneFailed
- ``docling_reap``   EventBridge schedule: lower the docling-serve scaling floor after 20 idle minutes
                     (``ingest_lib.docling_scaler``). ExtractOpen raises it when a lane has digital pages.
"""

from __future__ import annotations

import json
from typing import Any

from evidence_core import s3keys
from evidence_core.config import load_pipeline
from evidence_core.enums import Engine, LaneState, StageId, StageState
from evidence_core.ids import doc_id_for
from evidence_core.logs import get_logger, log_kv
from evidence_core.models.stage_outputs import ExtractOutput
from evidence_core.models.status import (
    ArtifactRef,
    ExtractPart,
    IngestTrigger,
    StageEnvelope,
    StageError,
    StageResult,
    StageVersions,
)
from evidence_core.stages import stage_keys
from evidence_core.status import StatusWriter, config_snapshot, error_from_sfn

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.docling_scaler import scaler_from_env
from lambdas.ingest_lib.stage_io import source_ref

log = get_logger("rp-stage-status")


def _writer(env: StageEnvelope, stage: StageId) -> StatusWriter:
    svc = services()
    c = env.ctx
    return StatusWriter(svc.table("doc_status"), c.doc_id, c.package_id, c.pipeline_version, stage,
                        svc.settings.heartbeat_seconds, execution_arn=env.execution_arn or c.execution_arn)


def op_start(event: dict[str, Any]) -> dict[str, Any]:
    env = StageEnvelope.load(event)
    stage = StageId(event["stageId"])
    pipeline = load_pipeline(env.ctx.pipeline_version, version_id=env.ctx.config_version_id)
    w = _writer(env, stage)
    inputs = [source_ref(env.ctx)]
    route = env.result(StageId.ROUTE)
    if route is not None:
        inputs += route.artifacts
    w.start(engine=Engine(event["engine"]) if event.get("engine") else None, inputs=inputs,
            input_summary={"digitalPages": (route.output.get("digitalPages") if route else None),
                           "scanPages": (route.output.get("scanPages") if route else None)},
            config=config_snapshot(pipeline, stage),
            versions=StageVersions(pipeline_version=env.ctx.pipeline_version, config_sha=env.ctx.config_sha,
                                   config_version_id=env.ctx.config_version_id,
                                   ontology_version=env.ctx.ontology_version, prompt_sha=env.ctx.prompt_sha,
                                   code_sha=services().settings.code_sha),
            sfn_state=env.state_name, execution_arn=env.execution_arn, function_name="rp-stage-status")
    if stage is StageId.EXTRACT and route is not None and route.output.get("digitalPages"):
        _docling_demand(w)
    return StageResult(stage_id=stage, state=StageState.RUNNING).dump()


def _docling_demand(w: StatusWriter) -> None:
    """Start docling-serve now instead of waiting for the queue alarm. Best effort: never fails the stage."""
    svc = services()
    if svc.settings.env != "demo":
        return
    try:
        res = scaler_from_env(svc, svc.settings.docling_queue_url).ensure_capacity()
    except Exception as exc:  # noqa: BLE001 - the queue alarm still scales out, only later
        log_kv(log, "docling demand failed", error=type(exc).__name__, detail=str(exc)[:300])
        return
    if res["action"] == "scale_out":
        w.heartbeat(None, "starting docling-serve on Fargate (cold start, usually a few minutes)", force=True)


def op_docling_reap(event: dict[str, Any]) -> dict[str, Any]:
    svc = services()
    return scaler_from_env(svc, svc.settings.docling_queue_url).reap()


def _flatten_parts(raw: Any) -> list[ExtractPart]:
    out: list[ExtractPart] = []
    stack = list(raw) if isinstance(raw, list) else [raw]
    while stack:
        item = stack.pop(0)
        if isinstance(item, list):
            stack = item + stack
        elif isinstance(item, dict) and item.get("engine"):
            out.append(ExtractPart.load(item))
    return out


def merge_extract(env: StageEnvelope) -> StageResult:
    parts = _flatten_parts(env.extract_parts or [])
    route = env.result(StageId.ROUTE)
    expected_digital = set(route.output.get("digitalPages", [])) if route else set()
    expected_scan = set(route.output.get("scanPages", [])) if route else set()
    pages_by_engine: dict[str, list[int]] = {}
    artifacts: list[ArtifactRef] = []
    blocks = 0
    metrics: dict[str, float | int] = {}
    for p in parts:
        pages_by_engine.setdefault(p.engine.value, []).extend(p.pages)
        artifacts.extend(p.artifacts)
        blocks += int(p.metrics.get("items") or p.metrics.get("layoutBlocks") or 0)
        key = f"{p.engine.value}Ms"
        metrics[key] = max(int(metrics.get(key, 0)), int(p.duration_ms or 0))
    got_digital = set(pages_by_engine.get("docling", []))
    got_scan = set(pages_by_engine.get("textract", []))
    missing = sorted((expected_digital - got_digital) | (expected_scan - got_scan))
    if missing:
        raise RuntimeError(f"extract produced no layout for pages {missing}")
    engines = [e for e in ("docling", "textract") if e in pages_by_engine]
    out = ExtractOutput(engines=engines, pages_by_engine={k: sorted(v) for k, v in pages_by_engine.items()},
                        blocks=blocks)
    metrics |= {"blocks": blocks, "pages": sum(len(v) for v in pages_by_engine.values())}

    def mmss(ms: float | int) -> str:
        s = int(ms) // 1000
        return f"{s // 60:02d}:{s % 60:02d}"

    headline = " · ".join(f"{e.capitalize()} {mmss(metrics.get(f'{e}Ms', 0))}" for e in engines)
    engine = Engine(engines[0]) if len(engines) == 1 else None
    return StageResult(stage_id=StageId.EXTRACT, state=StageState.SUCCEEDED, engine=engine, headline=headline,
                       artifacts=artifacts, metrics=metrics, output=out.dump())


WORKER_FUNCTION = {"docling": "rp-docling-call", "textract": "rp-textract-page"}


def op_close_extract(event: dict[str, Any]) -> dict[str, Any]:
    env = StageEnvelope.load(event)
    w = _writer(env, StageId.EXTRACT)
    try:
        result = merge_extract(env)
    except Exception as exc:
        from evidence_core.status import error_from_exception

        w.fail(error_from_exception(exc))
        raise
    done = w.succeed(result)
    k = stage_keys(w.doc_id, w.package_id, w.pipeline_version, StageId.EXTRACT)
    # The functions that did the work (the inspector shows them instead of the open/close shim).
    workers = [WORKER_FUNCTION[e] for e in (result.output or {}).get("engines", []) if e in WORKER_FUNCTION]
    if workers:
        w.table.update_item(Key={"pk": k.pk, "sk": k.sk}, UpdateExpression="SET #f = :f",
                            ExpressionAttributeNames={"#f": "functionName"},
                            ExpressionAttributeValues={":f": " + ".join(workers)})
    engines = sorted({a.note for a in result.artifacts if a.note})
    if engines:  # engine versions are only known once the branches report back
        w.table.update_item(Key={"pk": k.pk, "sk": k.sk}, UpdateExpression="SET #v.#e = :e",
                            ConditionExpression="attribute_exists(#v)",
                            ExpressionAttributeNames={"#v": "versions", "#e": "engineVersion"},
                            ExpressionAttributeValues={":e": " + ".join(engines)})
    return done.dump()


def op_lane_failed(event: dict[str, Any]) -> dict[str, Any]:
    env = StageEnvelope.load(event)
    err = error_from_sfn(env.error or {})
    w = _writer(env, StageId.REGISTER)
    w.lane_failed(err)
    return {"laneState": LaneState.FAILED.value, "error": err.dump()}


def op_execution_event(event: dict[str, Any]) -> dict[str, Any]:
    detail = event.get("detail") or {}
    raw_input = detail.get("input")
    trig = IngestTrigger.load(json.loads(raw_input) if isinstance(raw_input, str) else raw_input or {})
    bk = s3keys.parse_bronze_key(trig.key)
    doc_id = doc_id_for(bk.package_id, bk.file_name)
    svc = services()
    w = StatusWriter(svc.table("doc_status"), doc_id, bk.package_id, trig.pipeline_version, StageId.REGISTER,
                     execution_arn=detail.get("executionArn"))
    lane = w.get_lane()
    arn = detail.get("executionArn")
    if lane is None or lane.state in (LaneState.SUCCEEDED, LaneState.FAILED, LaneState.SKIPPED):
        return {"changed": False, "reason": "lane already terminal or absent"}
    if lane.execution_arn and arn and lane.execution_arn != arn:
        return {"changed": False, "reason": "a newer execution owns the lane"}
    status = str(detail.get("status", "FAILED"))
    err = StageError(type=f"States.{status.title().replace('_', '')}",
                     message=str(detail.get("cause") or detail.get("error") or f"execution {status}")[:1000],
                     cause=str(detail.get("cause") or "")[:4096] or None)
    w.lane_failed(err)
    return {"changed": True, "status": status}


OPS = {"start": op_start, "close_extract": op_close_extract, "lane_failed": op_lane_failed,
       "execution_event": op_execution_event, "docling_reap": op_docling_reap}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    op = event.get("op")
    if op not in OPS:
        raise ValueError(f"unknown op {op!r}")
    return OPS[op](event)

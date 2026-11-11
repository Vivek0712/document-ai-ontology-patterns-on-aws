from __future__ import annotations

from typing import Any

import pytest
from boto3.dynamodb.conditions import Key
from evidence_core.config import LocalConfigSource, load_pipeline
from evidence_core.ddb import from_ddb
from evidence_core.enums import ArtifactKind, Engine, LaneState, StageId, StageState
from evidence_core.models.status import ArtifactRef, LaneRecord, StageProgress, StageResult, iso
from evidence_core.stages import resolve_stages
from evidence_core.status import StatusWriter, error_from_sfn, stage_handler
from evidence_core.testing import make_ctx, make_envelope


class FakeClock:
    def __init__(self) -> None:
        self.t = 100.0

    def __call__(self) -> float:
        return self.t


def _table(ddb: Any) -> Any:
    return ddb.Table("rp-doc-status")


def _init(ddb: Any, repo_root, pv: str = "2.3.1") -> tuple[StatusWriter, Any]:
    ctx = make_ctx(pv)
    lp = load_pipeline(pv, LocalConfigSource(repo_root))
    w = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, pv, StageId.REGISTER, clock=FakeClock())
    w.init_lane(resolve_stages(lp.config), LaneRecord(doc_id=ctx.doc_id, package_id=ctx.package_id,
                                                      pipeline_version=pv, state=LaneState.RUNNING,
                                                      execution_arn=ctx.execution_arn, started_at=iso()))
    return w, ctx


def _items(ddb: Any, doc_id: str, prefix: str) -> list[dict[str, Any]]:
    resp = _table(ddb).query(KeyConditionExpression=Key("pk").eq(doc_id) & Key("sk").begins_with(prefix))
    return [from_ddb(i) for i in resp["Items"]]


def test_init_lane_writes_pending_and_skipped(ddb: Any, repo_root) -> None:
    w, ctx = _init(ddb, repo_root, "1.0")
    stages = _items(ddb, ctx.doc_id, "STAGE#1.0#")
    assert [s["stageId"] for s in stages] == [s.value for s in StageId]
    states = {s["stageId"]: s["state"] for s in stages}
    assert states["graph"] == "SKIPPED" and states["route"] == "SKIPPED" and states["units"] == "PENDING"
    assert _items(ddb, ctx.doc_id, "LANE#1.0")[0]["state"] == "RUNNING"
    by_pkg = _table(ddb).query(IndexName="byPackage", KeyConditionExpression=Key("gsi1pk").eq(ctx.package_id))
    assert len(by_pkg["Items"]) == len(StageId) + 1  # stages + lane, no events


def test_stage_lifecycle_and_heartbeat_throttle(ddb: Any, repo_root) -> None:
    w0, ctx = _init(ddb, repo_root)
    clock = FakeClock()
    w = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.EXTRACT, heartbeat_seconds=5,
                     clock=clock)
    w.queued(Engine.DOCLING, "queued on rp-docling")
    w.start(engine=Engine.DOCLING, inputs=[ArtifactRef(key=ctx.bronze_key, kind=ArtifactKind.SOURCE_PDF,
                                                       content_type="application/pdf")])
    clock.t += 1
    assert w.heartbeat(StageProgress(done=0, total=1)) is False  # throttled
    clock.t += 5
    assert w.heartbeat(StageProgress(done=1, total=1), "converting") is True
    clock.t += 2.5
    w.succeed(StageResult(stage_id=StageId.EXTRACT, state=StageState.SUCCEEDED, engine=Engine.DOCLING,
                          headline="Docling 00:08", metrics={"pages": 1},
                          artifacts=[ArtifactRef(key="silver/x/docling.json", kind=ArtifactKind.DOCLING_JSON)]))
    rec = w.get()
    assert rec is not None and rec.state is StageState.SUCCEEDED
    assert rec.attempt == 1 and rec.heartbeat_count == 1 and rec.duration_ms == 8500
    assert rec.artifacts[0].kind is ArtifactKind.DOCLING_JSON and rec.queued_at is not None
    events = [e["event"] for e in _items(ddb, ctx.doc_id, "EVENT#2.3.1#") if e["stageId"] == "extract"]
    assert events == ["QUEUED", "STARTED", "HEARTBEAT", "SUCCEEDED"]
    lane = w.get_lane()
    assert lane is not None and lane.current_stage is StageId.EXTRACT


def test_retry_increments_attempt_and_clears_error(ddb: Any, repo_root) -> None:
    _, ctx = _init(ddb, repo_root)
    w = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.UNITS, clock=FakeClock())
    w.start(engine=Engine.EU_BUILDER)
    w.fail(error_from_sfn({"Error": "Lambda.Unknown", "Cause": "boom"}))
    assert w.get().state is StageState.FAILED and w.get_lane().state is LaneState.FAILED
    w.start(engine=Engine.EU_BUILDER)
    rec = w.get()
    assert rec.attempt == 2 and rec.error is None and rec.state is StageState.RUNNING


def test_lane_failed_closes_running_stage(ddb: Any, repo_root) -> None:
    _, ctx = _init(ddb, repo_root)
    w = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.PII_GATE, clock=FakeClock())
    w.start(engine=Engine.COMPREHEND)
    lane_writer = w.for_stage(StageId.READY)
    lane_writer.lane_failed(error_from_sfn({"Error": "States.Timeout", "Cause": "Task timed out"}))
    assert w.get().state is StageState.FAILED
    lane = w.get_lane()
    assert lane.state is LaneState.FAILED and lane.error.type == "States.Timeout"


def test_stage_handler_success_and_failure(ddb: Any, repo_root) -> None:
    _, ctx = _init(ddb, repo_root)
    env = make_envelope(ctx)

    @stage_handler(StageId.UNITS, engine=Engine.EU_BUILDER, table=_table(ddb),
                   input_summary=lambda e: {"docId": e.ctx.doc_id})
    def build(sctx):
        assert sctx.pipeline.config.mode == "evidence_units"
        return StageResult(stage_id=StageId.UNITS, state=StageState.SUCCEEDED, engine=Engine.EU_BUILDER,
                           headline="4 units", metrics={"units": 4})

    out = build(env.dump(), None)
    assert out["stageId"] == "units" and out["headline"] == "4 units"
    rec = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.UNITS).get()
    assert rec.state is StageState.SUCCEEDED
    assert rec.config is not None and "preprocessing.units" in rec.config.values
    assert rec.versions is not None and rec.versions.config_sha == ctx.config_sha
    assert rec.input == {"docId": ctx.doc_id}

    @stage_handler(StageId.PII_GATE, table=_table(ddb))
    def broken(sctx):
        raise RuntimeError("comprehend unavailable")

    with pytest.raises(RuntimeError):
        broken(env.dump(), None)
    rec = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.PII_GATE).get()
    assert rec.state is StageState.FAILED and rec.error.type == "RuntimeError"
    assert "comprehend unavailable" in rec.error.message


def test_missing_upstream_artifact_fails_the_stage_that_needed_it(ddb: Any, repo_root) -> None:
    """inputs() raising (e.g. ResultPath drift) must leave an error on THIS stage, not a PENDING record."""
    _, ctx = _init(ddb, repo_root)
    env = make_envelope(ctx)

    def need_promote(e: Any) -> list[ArtifactRef]:
        raise RuntimeError("stage promote produced no gold_units_jsonl artifact")

    @stage_handler(StageId.INDEX, engine=Engine.TITAN_AOSS, table=_table(ddb), inputs=need_promote)
    def index(sctx):  # pragma: no cover - never reached
        raise AssertionError("must not run")

    with pytest.raises(RuntimeError):
        index(env.dump(), None)
    rec = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.INDEX).get()
    assert rec.state is StageState.FAILED and "no gold_units_jsonl" in rec.error.message
    assert rec.versions is not None and rec.config is not None


def test_stage_records_the_model_that_actually_ran(ddb: Any, repo_root) -> None:
    _, ctx = _init(ddb, repo_root)
    env = make_envelope(ctx)

    @stage_handler(StageId.CLASSIFY, engine=Engine.SKOS, table=_table(ddb), model_key="MODEL_COMPACT")
    def classify_by_margin(sctx):
        sctx.record_model(None)  # the SKOS margin decided; Nova was never called
        return StageResult(stage_id=StageId.CLASSIFY, state=StageState.SUCCEEDED, engine=Engine.SKOS)

    classify_by_margin(env.dump(), None)
    rec = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.CLASSIFY).get()
    assert rec.versions is not None and rec.versions.model_id is None

    @stage_handler(StageId.INDEX, engine=Engine.TITAN_AOSS, table=_table(ddb), model_key="EMBED_MODEL")
    def index_local(sctx):
        sctx.record_model("local-hash-embedder")
        return StageResult(stage_id=StageId.INDEX, state=StageState.SUCCEEDED, engine=Engine.TITAN_AOSS)

    index_local(env.dump(), None)
    rec = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.INDEX).get()
    assert rec.versions is not None and rec.versions.model_id == "local-hash-embedder"


def test_new_execution_archives_the_previous_run(ddb: Any, repo_root) -> None:
    """Reprocess: the first run's records move to HIST#, events carry their executionArn."""
    w, ctx = _init(ddb, repo_root)
    units = w.for_stage(StageId.UNITS)
    units.start(engine=Engine.EU_BUILDER, execution_arn=ctx.execution_arn)
    units.fail(error_from_sfn({"Error": "RuntimeError", "Cause": "first run broke"}))
    lp = load_pipeline("2.3.1", LocalConfigSource(repo_root))
    second_arn = ctx.execution_arn + "-second"
    w2 = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.REGISTER, clock=FakeClock())
    w2.init_lane(resolve_stages(lp.config), LaneRecord(doc_id=ctx.doc_id, package_id=ctx.package_id,
                                                       pipeline_version="2.3.1", state=LaneState.RUNNING,
                                                       execution_arn=second_arn, started_at=iso()))
    hist = _items(ddb, ctx.doc_id, "HIST#2.3.1#")
    archived = [h for h in hist if h["recordType"] == "STAGE_HIST" and h["stageId"] == "units"]
    assert len(archived) == 1 and archived[0]["state"] == "FAILED"
    assert archived[0]["error"]["message"] and archived[0]["executionArn"] == ctx.execution_arn
    assert any(h["recordType"] == "LANE_HIST" for h in hist)
    current = StatusWriter(_table(ddb), ctx.doc_id, ctx.package_id, "2.3.1", StageId.UNITS).get()
    assert current.state is StageState.PENDING and current.execution_arn == second_arn
    events = _items(ddb, ctx.doc_id, "EVENT#2.3.1#")
    first = [e for e in events if e["stageId"] == "units"]
    assert first and all(e["executionArn"] == ctx.execution_arn for e in first)
    # the same execution calling init_lane again (duplicate skip) archives nothing new
    w2.init_lane(resolve_stages(lp.config), LaneRecord(doc_id=ctx.doc_id, package_id=ctx.package_id,
                                                       pipeline_version="2.3.1", state=LaneState.RUNNING,
                                                       execution_arn=second_arn, started_at=iso()))
    assert len(_items(ddb, ctx.doc_id, "HIST#2.3.1#")) == len(hist)

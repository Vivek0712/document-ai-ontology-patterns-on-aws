"""rp-mark-ready (both lanes): wait until the AOSS count for (docId, pv) equals the number of units
indexed (poll ``readiness.pollSeconds``, cap ``readiness.maxWaitSeconds``), then mark the lane
SUCCEEDED with its unit/held/indexed/triple counts. A count that never converges fails the stage."""

from __future__ import annotations

import time

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState
from evidence_core.ids import index_name
from evidence_core.models.stage_outputs import ReadyOutput
from evidence_core.models.status import StageEnvelope, StageProgress, StageResult
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.stage_io import artifacts, store_for


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return artifacts(env, StageId.INDEX, ArtifactKind.INDEX_RECEIPT) + artifacts(env, StageId.GRAPH,
                                                                                  ArtifactKind.GRAPH_RECEIPT)


class NotReady(RuntimeError):
    pass


@stage_handler(StageId.READY, engine=Engine.LAMBDA, inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    env = sctx.envelope
    pv = ctx.pipeline_version
    readiness = sctx.pipeline.config.retrieval.readiness
    index = index_name(pv)

    def out_of(stage: StageId, key: str) -> int:
        r = env.result(stage)
        return int((r.output.get(key) if r else 0) or 0)

    expected = out_of(StageId.INDEX, "indexed")
    aoss = services().aoss()
    t0 = time.monotonic()
    count = aoss.count(index, ctx.doc_id, pv) if expected else 0
    while count != expected:
        waited = time.monotonic() - t0
        if waited >= readiness.max_wait_seconds:
            raise NotReady(f"{index} holds {count} documents for {ctx.doc_id}, expected {expected} "
                           f"after {int(waited)} s")
        sctx.heartbeat(StageProgress(done=count, total=expected, unit="units"),
                       f"waiting for index refresh ({count}/{expected})")
        time.sleep(readiness.poll_seconds)
        count = aoss.count(index, ctx.doc_id, pv)
    waited_ms = int((time.monotonic() - t0) * 1000)
    out = ReadyOutput(index=index, expected=expected, indexed=count, waited_ms=waited_ms)
    ref = store_for(ctx).put_json(s3keys.ready_receipt_key(ctx.package_id, ctx.doc_id, pv), out,
                                  kind=ArtifactKind.READY_RECEIPT)
    sctx.writer.lane_succeeded(units=out_of(StageId.UNITS, "units"), held=out_of(StageId.PII_GATE, "held"),
                               indexed=count, triples=out_of(StageId.GRAPH, "triples") or None)
    return StageResult(stage_id=StageId.READY, state=StageState.SUCCEEDED, engine=Engine.LAMBDA,
                       headline=f"ready · {count}/{expected} searchable", artifacts=[ref], output=out.dump(),
                       metrics={"expected": expected, "indexed": count, "waitedMs": waited_ms})


handler = run

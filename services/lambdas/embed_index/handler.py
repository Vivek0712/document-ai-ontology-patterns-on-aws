"""rp-embed-index (both lanes; no Step Functions retry): Gold units -> Titan Text Embeddings V2
(breadcrumb + text, 1024-d, normalised) -> bulk index with auto ``_id`` into ``units-v<pv>``.

Idempotent per (docId, pv): first deletes every AOSS ``_id`` stored in UnitStatus for this doc lane,
then indexes and stores the new ids. Held units are never indexed.
"""

from __future__ import annotations

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, PiiFlag, StageId, StageState
from evidence_core.ids import index_name
from evidence_core.models.stage_outputs import IndexOutput
from evidence_core.models.status import StageEnvelope, StageProgress, StageResult, iso
from evidence_core.models.units import IndexDocument
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.stage_io import require_artifact, store_for
from lambdas.ingest_lib.units_io import delete_unit_statuses, read_units, unit_statuses_for_lane, update_unit_status

BATCH = 16


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return [require_artifact(env, StageId.PROMOTE, ArtifactKind.GOLD_UNITS_JSONL)]


@stage_handler(StageId.INDEX, engine=Engine.TITAN_AOSS, engine_version="titan-embed-text-v2 + aoss bulk",
               model_key="EMBED_MODEL", inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    cfg = sctx.pipeline.config.retrieval
    pv = ctx.pipeline_version
    svc = services()
    store = store_for(ctx)
    units = [u for u in read_units(store, require_artifact(sctx.envelope, StageId.PROMOTE,
                                                           ArtifactKind.GOLD_UNITS_JSONL).key)
             if u.pii_flag is not PiiFlag.HELD]
    index = index_name(pv)
    aoss = svc.aoss()
    created = aoss.ensure_index(index, cfg.embed_dimensions)

    tbl = svc.table("unit_status")
    previous = unit_statuses_for_lane(tbl, ctx.doc_id, pv)
    old_ids = [s.aoss_id for s in previous if s.aoss_id]
    deleted = aoss.delete_ids(index, old_ids)
    pii = sctx.envelope.result(StageId.PII_GATE)
    held_ids = set(pii.output.get("heldUnitIds", [])) if pii else set()
    current = {u.unit_id for u in units} | held_ids
    for s in previous:
        if s.aoss_id and s.unit_id in current:
            update_unit_status(tbl, s.unit_id, remove=["aossId", "indexedAt"])
    delete_unit_statuses(tbl, [s.unit_id for s in previous if s.unit_id not in current])

    embedder = svc.embedder(cfg.embed_model, cfg.embed_dimensions)
    sctx.record_model(embedder.model_id)  # the embedder that ran (local runs use a hash embedder)
    receipt: list[dict[str, str]] = []
    indexed_at = iso()
    for start in range(0, len(units), BATCH):
        batch = units[start:start + BATCH]
        vectors = embedder.embed([u.embedding_text() for u in batch])
        docs = [IndexDocument.from_unit(u, embedding=v, config_sha=ctx.config_sha, indexed_at=indexed_at).dump()
                for u, v in zip(batch, vectors, strict=True)]
        ids = aoss.bulk_index(index, docs)
        for u, aoss_id in zip(batch, ids, strict=True):
            update_unit_status(tbl, u.unit_id, aossId=aoss_id, indexedAt=indexed_at)
            receipt.append({"unitId": u.unit_id, "aossId": aoss_id})
        sctx.heartbeat(StageProgress(done=min(start + BATCH, len(units)), total=len(units), unit="units"),
                       f"{len(receipt)}/{len(units)} indexed")
    out = IndexOutput(index=index, indexed=len(receipt), deleted_previous=deleted, embed_model=embedder.model_id,
                      dimensions=cfg.embed_dimensions)
    ref = store.put_json(s3keys.index_receipt_key(ctx.package_id, ctx.doc_id, pv),
                         out.dump() | {"items": receipt, "indexCreated": created, "indexedAt": indexed_at},
                         kind=ArtifactKind.INDEX_RECEIPT, count=len(receipt))
    return StageResult(stage_id=StageId.INDEX, state=StageState.SUCCEEDED, engine=Engine.TITAN_AOSS,
                       headline=f"{len(receipt)} indexed", artifacts=[ref], output=out.dump(),
                       metrics={"indexed": len(receipt), "deletedPrevious": deleted,
                                "embedCalls": len(units)})


handler = run

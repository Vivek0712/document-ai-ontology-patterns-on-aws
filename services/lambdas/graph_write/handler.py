"""rp-graph-write (2.3.1, in the VPC): Gold units -> typed facts in the package's ABox named graph
on Amazon Neptune (SPARQL UPDATE, SigV4). Deletes the document's previous statements first."""

from __future__ import annotations

from evidence_core import iris, s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState
from evidence_core.models.stage_outputs import GraphOutput
from evidence_core.models.status import StageEnvelope, StageProgress, StageResult
from evidence_core.sparql import bindings_to_rows
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.rdf import build_abox, count_doc_triples_query, delete_doc_update, insert_batches
from lambdas.ingest_lib.stage_io import require_artifact, store_for
from lambdas.ingest_lib.units_io import read_units, update_unit_status


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return [require_artifact(env, StageId.PROMOTE, ArtifactKind.GOLD_UNITS_JSONL)]


@stage_handler(StageId.GRAPH, engine=Engine.NEPTUNE, engine_version="neptune SPARQL 1.1 UPDATE", inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    env = sctx.envelope
    pv = ctx.pipeline_version
    store = store_for(ctx)
    units = read_units(store, require_artifact(env, StageId.PROMOTE, ArtifactKind.GOLD_UNITS_JSONL).key)
    cls = env.result(StageId.CLASSIFY)
    doc_type = cls.output.get("docType") if cls else None
    graph = iris.abox_graph(ctx.package_id, pv)
    quads, facts = build_abox(units, package_id=ctx.package_id, pipeline_version=pv, doc_id=ctx.doc_id,
                              file_name=ctx.file_name, source_sha=ctx.source_sha256, doc_type=doc_type,
                              stage_activity=iris.activity_iri(ctx.doc_id, pv, StageId.UNITS.value),
                              source_key=ctx.bronze_key, stage_id=StageId.UNITS.value)
    sparql = services().sparql()
    _, rows = bindings_to_rows(sparql.query(count_doc_triples_query(graph, ctx.doc_id)))
    previous = int(rows[0]["n"]) if rows else 0
    sparql.update(delete_doc_update(graph, ctx.doc_id))
    batches = insert_batches(quads)
    for i, q in enumerate(batches, start=1):
        sparql.update(q)
        sctx.heartbeat(StageProgress(done=i, total=len(batches), unit="batches"), f"{i}/{len(batches)} inserts")
    nq_ref = store.put_bytes(s3keys.abox_key(ctx.package_id, ctx.doc_id, pv), quads.nquads().encode("utf-8"),
                             kind=ArtifactKind.ABOX_NQUADS, content_type="application/n-quads",
                             count=len(quads.lines), note=graph)
    out = GraphOutput(graph=graph, triples=len(quads.lines), facts=facts, deleted_previous=previous)
    receipt = store.put_json(s3keys.graph_receipt_key(ctx.package_id, ctx.doc_id, pv),
                             out.dump() | {"batches": len(batches), "units": [u.unit_id for u in units]},
                             kind=ArtifactKind.GRAPH_RECEIPT)
    tbl = services().table("unit_status")
    per_unit = {u.unit_id: sum(1 for line in quads.lines if iris.unit_iri(u.unit_id) in line) for u in units}
    for uid, n in per_unit.items():
        update_unit_status(tbl, uid, graphTriples=n)
    return StageResult(stage_id=StageId.GRAPH, state=StageState.SUCCEEDED, engine=Engine.NEPTUNE,
                       headline=f"{len(quads.lines)} triples · {facts} facts" + (
                           f" · {quads.step_events} step event{'s' * (quads.step_events != 1)}"
                           if quads.step_events else ""),
                       artifacts=[nq_ref, receipt], output=out.dump(),
                       metrics={"triples": len(quads.lines), "facts": facts, "deletedPrevious": previous,
                                "batches": len(batches)}
                       | ({"stepEvents": quads.step_events} if quads.step_events else {})
                       | ({"stepEventParseFailed": quads.step_event_failures} if quads.step_event_failures else {}))


handler = run

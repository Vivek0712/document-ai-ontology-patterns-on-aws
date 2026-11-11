"""rp-classify (2.3.1): DocumentType from SKOS indicator phrases in the vocab snapshot; Amazon Nova 2
Lite (``preprocessing.classify.tieBreakModel``) breaks ties below ``minMargin``. Writes
classification.json and ``docType`` on the DocumentItem."""

from __future__ import annotations

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState
from evidence_core.models.stage_outputs import ClassifyOutput
from evidence_core.models.status import StageEnvelope, StageResult
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.classifier import classify
from lambdas.ingest_lib.stage_io import artifacts, load_layout, store_for
from lambdas.ingest_lib.vocab import load_vocab


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return artifacts(env, StageId.EXTRACT, ArtifactKind.DOCLING_JSON, ArtifactKind.TEXTRACT_JSON)


@stage_handler(StageId.CLASSIFY, engine=Engine.SKOS, engine_version="skos-indicators v1", model_key="MODEL_COMPACT",
               inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    pre = sctx.pipeline.config.preprocessing
    if pre.classify == "none" or not ctx.ontology_version:
        raise RuntimeError("classify ran for a pipeline without classify/ontology")
    vocab = load_vocab(ctx.ontology_version)
    layout, _, _ = load_layout(sctx.envelope, vocab)
    svc = services()
    client = svc.tie_breaker_override if svc.tie_breaker_override is not None else svc.bedrock
    result = classify(layout.full_text(), vocab.doc_types, min_margin=pre.classify.min_margin, client=client,
                      model_id=pre.classify.tie_break_model)
    sctx.record_model(result.tie_break_model if result.tie_break_used else None)  # SKOS margin decided: no model
    out = ClassifyOutput(doc_type=result.doc_type, label=result.label, scores=result.scores, margin=result.margin,
                         tie_break_used=result.tie_break_used, tie_break_model=result.tie_break_model)
    body = out.dump() | {"hits": result.hits, "ontologyVersion": vocab.ontology_version,
                         "tieBreakReply": result.tie_break_reply}
    ref = store_for(ctx).put_json(s3keys.classification_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version), body,
                                  kind=ArtifactKind.CLASSIFICATION)
    svc.table("documents").update_item(Key={"packageId": ctx.package_id, "docId": ctx.doc_id},
                                       UpdateExpression="SET docType = :t",
                                       ExpressionAttributeValues={":t": result.doc_type})
    engine = Engine.NOVA if result.tie_break_used else Engine.SKOS
    headline = f"{result.label}" + (" · Nova tie-break" if result.tie_break_used else f" · margin {result.margin:.2f}")
    return StageResult(stage_id=StageId.CLASSIFY, state=StageState.SUCCEEDED, engine=engine, headline=headline,
                       artifacts=[ref], output=out.dump(),
                       metrics={"margin": result.margin, "tieBreak": int(result.tie_break_used),
                                "candidates": len(result.scores)})


handler = run

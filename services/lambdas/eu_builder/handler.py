"""rp-eu-builder (2.3.1): canonical layout -> evidence units (builder.py), written to
silver units.jsonl + build_report.json; UnitStatus rows; low-confidence fields to ReviewQueue."""

from __future__ import annotations

from collections import Counter

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState
from evidence_core.models.stage_outputs import UnitsOutput
from evidence_core.models.status import StageEnvelope, StageProgress, StageResult
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.glyphs import GlyphLocator
from lambdas.ingest_lib.stage_io import artifacts, load_layout, read_source, store_for
from lambdas.ingest_lib.units_io import (
    delete_unit_statuses,
    put_unit_statuses,
    replace_review_items,
    unit_statuses_for_lane,
)
from lambdas.ingest_lib.vocab import load_vocab

from .builder import BuildInput, build_units

ENGINE_VERSION = "eu-builder 1.0 (phases A/B/C, marker hard rule)"


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return (artifacts(env, StageId.EXTRACT, ArtifactKind.DOCLING_JSON, ArtifactKind.TEXTRACT_JSON)
            + artifacts(env, StageId.CLASSIFY, ArtifactKind.CLASSIFICATION))


@stage_handler(StageId.UNITS, engine=Engine.EU_BUILDER, engine_version=ENGINE_VERSION, model_key="EMBED_MODEL",
               inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    cfg = sctx.pipeline.config
    pre = cfg.preprocessing
    if pre.units == "none" or not ctx.ontology_version:
        raise RuntimeError("eu_builder ran for a pipeline without units/ontology config")
    vocab = load_vocab(ctx.ontology_version)
    layout, engine_versions, keys = load_layout(sctx.envelope, vocab)
    sctx.heartbeat(StageProgress(done=0, total=len(layout.blocks), unit="blocks"), "layout normalised")
    cls = sctx.envelope.result(StageId.CLASSIFY)
    doc_type = cls.output.get("docType") if cls else None
    svc = services()
    embedder = svc.embedder(cfg.retrieval.embed_model, cfg.retrieval.embed_dimensions)
    sctx.record_model(embedder.model_id)  # Phase B allocation embeddings
    digital = set((sctx.envelope.result(StageId.EXTRACT).output.get("pagesByEngine") or {}).get("docling", [])) \
        if sctx.envelope.result(StageId.EXTRACT) else set()
    glyphs = GlyphLocator(read_source(ctx)) if digital else None
    try:
        out = build_units(BuildInput(
            doc_id=ctx.doc_id, package_id=ctx.package_id, pipeline_version=ctx.pipeline_version,
            file_name=ctx.file_name, allowed_principals=ctx.allowed_principals, source_key=ctx.bronze_key,
            source_version_id=ctx.source_version_id, source_sha=ctx.source_sha256, config_sha=ctx.config_sha,
            ontology_version=ctx.ontology_version, layout=layout, vocab=vocab, units_cfg=pre.units,
            enrichment=pre.enrichment, embedder=embedder, doc_type=doc_type, engine_versions=engine_versions,
            extract_artifacts=keys, glyphs=glyphs,
        ))
    finally:
        if glyphs is not None:
            glyphs.close()
    store = store_for(ctx)
    pv = ctx.pipeline_version
    units_key = s3keys.silver_units_key(ctx.package_id, ctx.doc_id, pv)
    units_ref = store.put_jsonl(units_key, out.units, kind=ArtifactKind.UNITS_JSONL)
    report_ref = store.put_json(s3keys.build_report_key(ctx.package_id, ctx.doc_id, pv), out.report,
                                kind=ArtifactKind.BUILD_REPORT, count=len(out.report.get("allocations", [])),
                                note=f"embeddings: {embedder.model_id}")
    status_tbl = svc.table("unit_status")
    current = {u.unit_id for u in out.units}
    previous = unit_statuses_for_lane(status_tbl, ctx.doc_id, pv)
    stale = [s.unit_id for s in previous
             if s.unit_id not in current and not s.aoss_id]  # rows with an aossId are cleaned by embed_index
    delete_unit_statuses(status_tbl, stale)
    reviews_by_unit = Counter(r.unit_id for r in out.review_items)
    put_unit_statuses(status_tbl, out.units, silver_key=units_key, review_counts=dict(reviews_by_unit),
                      previous=previous)
    replace_review_items(svc.table("review_queue"), ctx.package_id, ctx.doc_id, pv, out.review_items)

    types = Counter(u.unit_type.value for u in out.units)
    nfields = sum(len(u.fields) for u in out.units)
    mean_chars = round(sum(u.chars for u in out.units) / len(out.units), 1) if out.units else 0.0
    result = UnitsOutput(units=len(out.units), unit_types=dict(types), fields=nfields,
                         review_items=len(out.review_items), phase_counts=out.report["phaseCounts"],
                         mean_chars=mean_chars)
    headline = f"{len(out.units)} unit{'s' if len(out.units) != 1 else ''} · {nfields} fields"
    return StageResult(stage_id=StageId.UNITS, state=StageState.SUCCEEDED, engine=Engine.EU_BUILDER,
                       headline=headline, artifacts=[units_ref, report_ref], output=result.dump(),
                       metrics={"units": len(out.units), "fields": nfields, "reviewItems": len(out.review_items),
                                "blocks": out.report["blocks"], "meanChars": mean_chars,
                                "unknownLabels": sum(layout.unknown_labels.values())})


handler = run

"""rp-naive-chunk (1.0): fixed-size chunks (recursive splitter, ``sizeTokens`` / ``overlapPct``,
whitespace tokens) over the text layer. No bbox, no breadcrumb, no ontology."""

from __future__ import annotations

from typing import Any

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState, UnitType
from evidence_core.ids import chunk_label, chunk_local, unit_id
from evidence_core.models.stage_outputs import UnitsOutput
from evidence_core.models.status import DocCtx, StageEnvelope, StageResult, iso
from evidence_core.models.units import BlockRef, Member, Unit, UnitProvenance
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.chunker import split_pages
from lambdas.ingest_lib.stage_io import require_artifact, store_for
from lambdas.ingest_lib.units_io import delete_unit_statuses, put_unit_statuses, unit_statuses_for_lane

ENGINE_VERSION = "fixed-chunker 1.0 (recursive splitter, whitespace tokens)"


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return [require_artifact(env, StageId.EXTRACT, ArtifactKind.TEXT_LAYER)]


def chunk_units(ctx: DocCtx, text_layer: dict[str, Any], *, size: int, overlap_pct: int, text_layer_key: str,
                config_sha: str) -> list[Unit]:
    overlap = size * overlap_pct // 100
    pages = [(p["page"], p["text"]) for p in text_layer["pages"]]
    units = []
    for n, ch in enumerate(split_pages(pages, size=size, overlap=overlap), start=1):
        units.append(Unit(
            unit_id=unit_id(ctx.doc_id, ctx.pipeline_version, chunk_local(n)), doc_id=ctx.doc_id,
            package_id=ctx.package_id, pipeline_version=ctx.pipeline_version, unit_type=UnitType.CHUNK,
            label=chunk_label(n), chunk_label=chunk_label(n), file_name=ctx.file_name, text=ch.text,
            pages=ch.pages, members=[Member(member_id="m-01", role="text", text=None, block_ref=BlockRef(
                engine=Engine.TEXT_LAYER, artifact_key=text_layer_key,
                pointer=",".join(f"#/pages/{p - 1}" for p in ch.pages), engine_label="page text"))],
            allowed_principals=ctx.allowed_principals, chars=len(ch.text), tokens=ch.tokens,
            provenance=UnitProvenance(engine=Engine.TEXT_LAYER, engine_version=text_layer.get("engineVersion"),
                                      source_key=ctx.bronze_key, source_version_id=ctx.source_version_id,
                                      source_sha=ctx.source_sha256, extract_artifacts=[text_layer_key],
                                      config_sha=config_sha,
                                      build={"sizeTokens": size, "overlapTokens": overlap, "chunk": n}),
        ))
    return units


@stage_handler(StageId.UNITS, engine=Engine.FIXED_CHUNKER, engine_version=ENGINE_VERSION, inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    chunker = sctx.pipeline.config.preprocessing.chunker
    size, overlap_pct = chunker.size_tokens or 300, chunker.overlap_pct or 0
    ref = require_artifact(sctx.envelope, StageId.EXTRACT, ArtifactKind.TEXT_LAYER)
    store = store_for(ctx)
    text_layer = store.get_json(ref.key, ref.version_id)
    units = chunk_units(ctx, text_layer, size=size, overlap_pct=overlap_pct, text_layer_key=ref.key,
                        config_sha=ctx.config_sha)
    pv = ctx.pipeline_version
    units_key = s3keys.silver_units_key(ctx.package_id, ctx.doc_id, pv)
    units_ref = store.put_jsonl(units_key, units, kind=ArtifactKind.UNITS_JSONL)
    empty = [p["page"] for p in text_layer["pages"] if not p["text"].strip()]
    report = {"docId": ctx.doc_id, "pipelineVersion": pv, "builtAt": iso(), "chunker": "recursive",
              "sizeTokens": size, "overlapTokens": size * overlap_pct // 100, "tokenizer": chunker.tokenizer,
              "emptyPages": empty, "chunks": [{"unitId": u.unit_id, "pages": u.pages, "tokens": u.tokens}
                                              for u in units]}
    report_ref = store.put_json(s3keys.build_report_key(ctx.package_id, ctx.doc_id, pv), report,
                                kind=ArtifactKind.BUILD_REPORT, count=len(units))
    tbl = services().table("unit_status")
    current = {u.unit_id for u in units}
    previous = unit_statuses_for_lane(tbl, ctx.doc_id, pv)
    delete_unit_statuses(tbl, [s.unit_id for s in previous if s.unit_id not in current and not s.aoss_id])
    put_unit_statuses(tbl, units, silver_key=units_key, previous=previous)
    mean = round(sum(u.chars for u in units) / len(units), 1) if units else 0.0
    out = UnitsOutput(units=len(units), unit_types={"chunk": len(units)} if units else {}, fields=0,
                      mean_chars=mean)
    headline = f"{len(units)} chunk{'s' if len(units) != 1 else ''}" + (" · no text layer" if not units else "")
    return StageResult(stage_id=StageId.UNITS, state=StageState.SUCCEEDED, engine=Engine.FIXED_CHUNKER,
                       headline=headline, artifacts=[units_ref, report_ref], output=out.dump(),
                       metrics={"units": len(units), "meanChars": mean, "emptyPages": len(empty)})


handler = run

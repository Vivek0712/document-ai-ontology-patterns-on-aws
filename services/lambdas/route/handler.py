"""rp-route (2.3.1): per page, text-layer characters > ``scanTextCharsMax`` -> digital (Docling),
else scan (Textract). Writes route.json and marks ``extract`` QUEUED."""

from __future__ import annotations

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState
from evidence_core.models.stage_outputs import PageInfo, RouteOutput
from evidence_core.models.status import StageEnvelope, StageResult
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.pdf import inspect_pdf, pypdf_version
from lambdas.ingest_lib.stage_io import read_source, source_ref, store_for


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return [source_ref(env.ctx)]


@stage_handler(StageId.ROUTE, engine=Engine.PYPDF, engine_version=pypdf_version(), inputs=_inputs,
               input_summary=lambda env: {"pages": env.ctx.page_count})
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    router = sctx.pipeline.config.preprocessing.extraction.router
    if router is None:
        raise RuntimeError("route ran for a pipeline without a router")
    data = read_source(ctx)
    insp = inspect_pdf(data, max_bytes=len(data) + 1)
    pages: list[PageInfo] = []
    digital, scan = [], []
    for p in insp.pages:
        is_digital = p.text_chars > router.scan_text_chars_max
        pages.append(p.model_copy(update={"route": "digital" if is_digital else "scan"}))
        (digital if is_digital else scan).append(p.page)
    out = RouteOutput(page_count=len(pages), digital_pages=digital, scan_pages=scan,
                      threshold_chars=router.scan_text_chars_max, pages=pages)
    ref = store_for(ctx).put_json(s3keys.route_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version), out,
                                  kind=ArtifactKind.ROUTE, count=len(pages))
    engines = [e for e, ps in ((Engine.DOCLING, digital), (Engine.TEXTRACT, scan)) if ps]
    sctx.writer.for_stage(StageId.EXTRACT).queued(
        engine=engines[0] if len(engines) == 1 else None,
        message=f"{len(digital)} digital → Docling · {len(scan)} scan → Textract")
    return StageResult(stage_id=StageId.ROUTE, state=StageState.SUCCEEDED, engine=Engine.PYPDF,
                       headline=f"{len(digital)} digital · {len(scan)} scan", artifacts=[ref], output=out.dump(),
                       metrics={"digitalPages": len(digital), "scanPages": len(scan)})


handler = run

"""rp-text-layer (1.0): pypdf ``extract_text()`` in its default stream-order mode.
Scanned pages have no text layer and yield nothing; they are listed as ``emptyPages``."""

from __future__ import annotations

from evidence_core import s3keys
from evidence_core.enums import ArtifactKind, Engine, StageId, StageState
from evidence_core.models.stage_outputs import ExtractOutput
from evidence_core.models.status import StageEnvelope, StageProgress, StageResult
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.pdf import page_texts, pypdf_version
from lambdas.ingest_lib.stage_io import read_source, source_ref, store_for


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return [source_ref(env.ctx)]


def extract_text_layer(data: bytes, library: str | None) -> dict:
    pages = []
    for page, text, w, h in page_texts(data):
        pages.append({"page": page, "width": round(w, 2), "height": round(h, 2), "chars": len(text.strip()),
                      "text": text})
    return {"engine": Engine.TEXT_LAYER.value, "library": library or pypdf_version(), "mode": "default",
            "engineVersion": pypdf_version(), "pages": pages}


@stage_handler(StageId.EXTRACT, engine=Engine.TEXT_LAYER, engine_version=pypdf_version(), inputs=_inputs,
               input_summary=lambda env: {"pages": env.ctx.page_count})
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    lib = sctx.pipeline.config.preprocessing.extraction.library
    doc = extract_text_layer(read_source(ctx), lib)
    sctx.heartbeat(StageProgress(done=len(doc["pages"]), total=ctx.page_count), "text layer extracted")
    empty = [p["page"] for p in doc["pages"] if p["chars"] == 0]
    ref = store_for(ctx).put_json(s3keys.text_layer_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version), doc,
                                  kind=ArtifactKind.TEXT_LAYER, count=len(doc["pages"]), note=pypdf_version())
    out = ExtractOutput(engines=["text-layer"], pages_by_engine={"text-layer": [p["page"] for p in doc["pages"]]},
                        blocks=len(doc["pages"]) - len(empty), empty_pages=empty)
    chars = sum(p["chars"] for p in doc["pages"])
    headline = f"text layer · {chars:,} chars" + (f" · {len(empty)} empty" if empty else "")
    return StageResult(stage_id=StageId.EXTRACT, state=StageState.SUCCEEDED, engine=Engine.TEXT_LAYER,
                       headline=headline, artifacts=[ref], output=out.dump(),
                       metrics={"pages": len(doc["pages"]), "emptyPages": len(empty), "chars": chars})


handler = run

"""rp-promote (both lanes): passed units (piiFlag none | masked) to Gold (units.jsonl + one JSON
per unit); every page rendered to PNG at ``render.dpi`` with held regions filled black."""

from __future__ import annotations

from evidence_core import s3keys
from evidence_core.bbox import BBox
from evidence_core.enums import ArtifactKind, Engine, PiiFlag, StageId, StageState
from evidence_core.models.stage_outputs import PromoteOutput
from evidence_core.models.status import StageEnvelope, StageProgress, StageResult
from evidence_core.models.units import PiiDecisions
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.render import pdfium_version, render_pages
from lambdas.ingest_lib.stage_io import read_source, require_artifact, source_ref, store_for
from lambdas.ingest_lib.units_io import read_units, update_unit_status


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return [require_artifact(env, StageId.UNITS, ArtifactKind.UNITS_JSONL),
            require_artifact(env, StageId.PII_GATE, ArtifactKind.PII_DECISIONS), source_ref(env.ctx)]


@stage_handler(StageId.PROMOTE, engine=Engine.PDFIUM, engine_version=pdfium_version(), inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    env = sctx.envelope
    pv = ctx.pipeline_version
    store = store_for(ctx)
    units = read_units(store, require_artifact(env, StageId.UNITS, ArtifactKind.UNITS_JSONL).key)
    dref = require_artifact(env, StageId.PII_GATE, ArtifactKind.PII_DECISIONS)
    decisions = {d.unit_id: d for d in PiiDecisions.load(store.get_json(dref.key, dref.version_id)).decisions}
    passed = []
    held: dict[int, list[BBox]] = {}
    for u in units:
        d = decisions.get(u.unit_id)
        flag = d.decision if d else PiiFlag.NONE
        if flag is PiiFlag.HELD:
            for b in d.held_bboxes:  # type: ignore[union-attr]
                held.setdefault(b.page, []).append(b)
            continue
        passed.append(u.model_copy(update={"pii_flag": flag}))
    artifacts = [store.put_jsonl(s3keys.gold_units_key(ctx.package_id, ctx.doc_id, pv), passed,
                                 kind=ArtifactKind.GOLD_UNITS_JSONL)]
    tbl = services().table("unit_status")
    for u in passed:
        key = s3keys.gold_unit_key(ctx.package_id, u.unit_id)
        store.put_json(key, u, kind=ArtifactKind.GOLD_UNIT)
        update_unit_status(tbl, u.unit_id, goldKey=key)
    if passed:
        artifacts.append(store.ref(s3keys.gold_unit_key(ctx.package_id, passed[0].unit_id), ArtifactKind.GOLD_UNIT,
                                   count=len(passed), note=f"{len(passed)} unit files under units/"))
    dpi = sctx.pipeline.config.preprocessing.render.dpi
    pages = render_pages(read_source(ctx), dpi=dpi, held=held)
    for i, page in enumerate(pages, start=1):
        artifacts.append(store.put_bytes(s3keys.page_image_key(ctx.package_id, ctx.doc_id, pv, page.page), page.png,
                                         kind=ArtifactKind.PAGE_IMAGE, content_type="image/png", page=page.page,
                                         note=f"{dpi} dpi · {page.width_px}x{page.height_px}px"
                                              + (f" · {page.held_regions} held region(s) filled" if page.held_regions
                                                 else "")))
        sctx.heartbeat(StageProgress(done=i, total=len(pages)), f"page {page.page} rendered")
    held_doc = {"pages": {str(p): [b.dump() for b in boxes] for p, boxes in sorted(held.items())}, "dpi": dpi}
    store.put_json(s3keys.held_regions_key(ctx.package_id, ctx.doc_id, pv), held_doc, kind=ArtifactKind.PAGE_IMAGE,
                   count=sum(len(v) for v in held.values()))
    nheld = sum(len(v) for v in held.values())
    out = PromoteOutput(gold_units=len(passed), pages_rendered=len(pages), held_regions=nheld)
    return StageResult(stage_id=StageId.PROMOTE, state=StageState.SUCCEEDED, engine=Engine.PDFIUM,
                       headline=f"{len(passed)} to Gold · {len(pages)} page{'s' if len(pages) != 1 else ''}",
                       artifacts=artifacts, output=out.dump(),
                       metrics={"goldUnits": len(passed), "pages": len(pages), "heldRegions": nheld, "dpi": dpi})


handler = run

"""rp-pii-gate (both lanes): Amazon Comprehend ``DetectPiiEntities`` per unit, rule ``rp-pii-v1``
. Held units stay in Silver and are never indexed. Held regions for the Gold page images are
the unit bboxes plus the printed glyph boxes of each unmasked span (located on the PDF text layer;
this also covers v1.0 chunks, which carry no bbox). Values are never logged or written."""

from __future__ import annotations

from collections import Counter

from evidence_core import s3keys
from evidence_core.bbox import BBox
from evidence_core.enums import ArtifactKind, Engine, PiiFlag, StageId, StageState
from evidence_core.models.stage_outputs import PiiGateOutput
from evidence_core.models.status import StageEnvelope, StageProgress, StageResult
from evidence_core.models.units import PiiDecision, PiiDecisions, Unit
from evidence_core.status import StageContext, stage_handler

from lambdas.ingest_lib.aws import services
from lambdas.ingest_lib.glyphs import GlyphLocator
from lambdas.ingest_lib.pii import decide, detect_entities
from lambdas.ingest_lib.stage_io import read_source, require_artifact, store_for
from lambdas.ingest_lib.units_io import read_units, update_unit_status


def _inputs(env: StageEnvelope):  # noqa: ANN202
    return [require_artifact(env, StageId.UNITS, ArtifactKind.UNITS_JSONL)]


def locate_spans(glyphs: GlyphLocator, unit: Unit, decision: PiiDecision) -> list[BBox]:
    """Glyph boxes of every unmasked span on the unit's pages (the span text stays in memory)."""
    boxes: list[BBox] = []
    for hit in decision.entities:
        if hit.masked:
            continue
        span = unit.text[hit.begin:hit.end]
        for page in unit.pages:
            for candidate in {span, span.strip(), " ".join(span.split())}:
                box = glyphs.find(page, candidate)
                if box is not None:
                    boxes.append(box)
                    break
    return boxes


@stage_handler(StageId.PII_GATE, engine=Engine.COMPREHEND, engine_version="comprehend DetectPiiEntities",
               inputs=_inputs)
def run(sctx: StageContext) -> StageResult:
    ctx = sctx.ctx
    cfg = sctx.pipeline.config.preprocessing.pii_gate
    ref = require_artifact(sctx.envelope, StageId.UNITS, ArtifactKind.UNITS_JSONL)
    store = store_for(ctx)
    units = read_units(store, ref.key)
    svc = services()
    decisions: list[PiiDecision] = []
    glyphs: GlyphLocator | None = None
    try:
        for i, u in enumerate(units, start=1):
            entities = detect_entities(svc.comprehend, u.text, cfg.language_code)
            d = decide(u, u.text, entities, cfg)
            if d.decision is PiiFlag.HELD:
                glyphs = glyphs or GlyphLocator(read_source(ctx))
                d.held_bboxes = d.held_bboxes + locate_spans(glyphs, u, d)
            decisions.append(d)
            sctx.heartbeat(StageProgress(done=i, total=len(units), unit="units"), f"{i}/{len(units)} units checked")
    finally:
        if glyphs is not None:
            glyphs.close()
    doc = PiiDecisions(doc_id=ctx.doc_id, pipeline_version=ctx.pipeline_version, rule=cfg.rule, decisions=decisions)
    dref = store.put_json(s3keys.pii_decisions_key(ctx.package_id, ctx.doc_id, ctx.pipeline_version), doc,
                          kind=ArtifactKind.PII_DECISIONS, count=len(decisions))
    tbl = svc.table("unit_status")
    for d in decisions:
        update_unit_status(tbl, d.unit_id, piiFlag=d.decision.value, held=d.decision is PiiFlag.HELD)
    counts = Counter(d.decision for d in decisions)
    held_ids = [d.unit_id for d in decisions if d.decision is PiiFlag.HELD]
    out = PiiGateOutput(units=len(units), passed=len(units) - len(held_ids), masked=counts[PiiFlag.MASKED],
                        held=len(held_ids), held_unit_ids=held_ids)
    types = Counter(h.type for d in decisions for h in d.entities)
    headline = f"{len(held_ids)} held · {len(units) - len(held_ids)} passed"
    return StageResult(stage_id=StageId.PII_GATE, state=StageState.SUCCEEDED, engine=Engine.COMPREHEND,
                       headline=headline, artifacts=[dref], output=out.dump(),
                       metrics={"units": len(units), "held": len(held_ids), "masked": counts[PiiFlag.MASKED],
                                "entities": sum(types.values())} | {f"entity_{k}": v for k, v in types.items()})


handler = run

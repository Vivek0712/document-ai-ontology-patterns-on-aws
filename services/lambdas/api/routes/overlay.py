"""GET /documents/{docId}/lanes/{pv}/stages/{stageId}/overlay?page= : the stage's boxes on one page.

The stage inspector draws what each stage saw or produced on the page image:

- ``extract``: every engine block (Docling item or Textract layout block) with its engine label and canonical role;
- ``units``: evidence-unit outlines, their members by role and phase, and field boxes;
- ``pii_gate``: unit outlines with their PII decision, held regions;
- ``promote`` / ``index`` / ``graph`` / ``ready``: Gold unit outlines and the blacked-out held regions.

Boxes are computed here from the whole Silver/Gold artifact, because an artifact preview is truncated at 64 KB and
digit-masked, which breaks coordinates. No document text is returned (Silver holds unmasked PII).
"""

from __future__ import annotations

from typing import Any

from evidence_core.bbox import BBox, from_docling, from_textract
from evidence_core.enums import ArtifactKind, StageId, StageState
from evidence_core.models.api import REGION_URL_TTL_SECONDS, OverlayBox, StageOverlay
from evidence_core.models.status import ArtifactRef, StageRecord
from evidence_core.models.units import Unit

from .. import store
from ..acl import require_doc
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request
from . import DOC_OVERLAY, router
from .documents import _pv, _stage_id, load_document

# Docling item labels -> canonical roles (display only; eu_builder uses the vocab for the real mapping).
DOCLING_ROLE = {"title": "title", "section_header": "section_header", "text": "text", "paragraph": "text",
                "list_item": "list_item", "table": "table", "caption": "caption", "footnote": "footnote",
                "picture": "picture", "chart": "picture", "formula": "formula", "page_header": "page_header",
                "page_footer": "page_footer", "key_value_region": "key_value", "form": "key_value",
                "document_index": "table", "code": "text", "checkbox_selected": "text",
                "checkbox_unselected": "text"}
TEXTRACT_ROLE = {"LAYOUT_TITLE": "title", "LAYOUT_SECTION_HEADER": "section_header", "LAYOUT_TEXT": "text",
                 "LAYOUT_LIST": "list_item", "LAYOUT_TABLE": "table", "LAYOUT_FIGURE": "picture",
                 "LAYOUT_KEY_VALUE": "key_value", "LAYOUT_HEADER": "page_header", "LAYOUT_FOOTER": "page_footer",
                 "LAYOUT_PAGE_NUMBER": "page_footer", "TABLE": "table", "KEY_VALUE_SET": "key_value"}
_TEXTRACT_SKIP = {"PAGE", "WORD", "LINE", "CELL", "MERGED_CELL", "SELECTION_ELEMENT", "TABLE_TITLE", "TABLE_FOOTER",
                  "QUERY", "QUERY_RESULT", "SIGNATURE"}
UNIT_STAGES = {StageId.UNITS, StageId.PII_GATE}
GOLD_STAGES = {StageId.PROMOTE, StageId.INDEX, StageId.GRAPH, StageId.READY}


def _artifacts(rec: StageRecord | None, *kinds: ArtifactKind) -> list[ArtifactRef]:
    return [a for a in (rec.artifacts if rec else []) if a.kind in kinds]


def _page_sizes(register: StageRecord | None) -> dict[int, tuple[float, float]]:
    pages = (register.output or {}).get("pages", []) if register else []
    return {int(p["page"]): (float(p["width"]), float(p["height"])) for p in pages if "page" in p}


def docling_boxes(doc: dict[str, Any], page: int, page_map: dict[int, int] | None) -> list[OverlayBox]:
    sizes = {int(k): float(v["size"]["height"]) for k, v in (doc.get("pages") or {}).items()}
    out: list[OverlayBox] = []
    for group in ("texts", "tables", "pictures", "key_value_items", "form_items"):
        for item in doc.get(group) or []:
            label = str(item.get("label") or group.rstrip("s"))
            for prov in item.get("prov") or []:
                inner = int(prov.get("page_no", 1))
                src = (page_map or {}).get(inner, inner)
                if src != page or "bbox" not in prov:
                    continue
                box = from_docling(prov["bbox"], page=page, page_height=sizes.get(inner, 792.0))
                out.append(OverlayBox(kind="block", bbox=box, role=DOCLING_ROLE.get(label, "other"), label=label,
                                      ref=item.get("self_ref")))
    return out


def textract_boxes(resp: dict[str, Any], page: int, width: float, height: float) -> list[OverlayBox]:
    out: list[OverlayBox] = []
    for b in resp.get("Blocks") or []:
        kind = str(b.get("BlockType", ""))
        geo = (b.get("Geometry") or {}).get("BoundingBox")
        if kind in _TEXTRACT_SKIP or not geo:
            continue
        if kind == "KEY_VALUE_SET" and "KEY" not in (b.get("EntityTypes") or []):
            continue
        box = from_textract(geo, page=page, page_width=width, page_height=height)
        out.append(OverlayBox(kind="block", bbox=box, role=TEXTRACT_ROLE.get(kind, "other"), label=kind,
                              ref=b.get("Id")))
    return out


def unit_boxes(units: list[Unit], page: int, *, members: bool, fields: bool,
               flags: dict[str, str] | None = None) -> list[OverlayBox]:
    out: list[OverlayBox] = []
    for u in units:
        flag = (flags or {}).get(u.unit_id, u.pii_flag.value if u.pii_flag else None)
        for b in u.bboxes:
            if b.page == page:
                out.append(OverlayBox(kind="unit", bbox=b, label=u.unit_type.value, unit_id=u.unit_id,
                                      ref=u.unit_id.rsplit(":", 1)[-1], pii_flag=flag))
        if members:
            for m in u.members:
                if m.bbox is not None and m.bbox.page == page:
                    out.append(OverlayBox(kind="member", bbox=m.bbox, role=m.role.value,
                                          label=m.block_ref.engine_label if m.block_ref else None, ref=m.member_id,
                                          unit_id=u.unit_id, phase=m.phase, alloc_score=m.alloc_score))
        if fields:
            for f in u.fields:
                if f.bbox is not None and f.bbox.page == page:
                    out.append(OverlayBox(kind="field", bbox=f.bbox, label=f.label, ref=f.field_id,
                                          unit_id=u.unit_id))
    return out


def build_overlay(deps: Deps, doc_id: str, pv: str, stage: StageId, page: int, page_count: int,
                  records: dict[StageId, StageRecord]) -> StageOverlay:
    rec = records.get(stage)
    sizes = _page_sizes(records.get(StageId.REGISTER))
    width, height = sizes.get(page, (None, None))
    ov = StageOverlay(doc_id=doc_id, pipeline_version=pv, stage_id=stage, page=page, page_count=page_count,
                      page_width=width, page_height=height)
    promote = records.get(StageId.PROMOTE)
    image = next((a for a in _artifacts(promote, ArtifactKind.PAGE_IMAGE) if a.page == page), None)
    if image is not None:
        ov.page_image_url = store.presign_get(deps, image.key, REGION_URL_TTL_SECONDS)
    if rec is None or rec.state is StageState.SKIPPED:
        ov.note = rec.skip_reason if rec and rec.skip_reason else "this stage does not run in this pipeline version"
        return ov
    if rec.state is not StageState.SUCCEEDED and stage not in (StageId.EXTRACT, StageId.UNITS):
        ov.note = f"nothing to draw until this stage succeeds ({rec.state.value})"

    if stage is StageId.EXTRACT:
        refs = _artifacts(rec, ArtifactKind.DOCLING_JSON, ArtifactKind.TEXTRACT_JSON)
        if not refs:
            ov.note = ("the text layer carries no regions: 1.0 extracts characters only" if pv == "1.0"
                       else ov.note or "no engine output yet")
        for ref in refs:
            if ref.kind is ArtifactKind.TEXTRACT_JSON:
                if (ref.page or 1) != page:
                    continue
                data = store.read_json(deps, ref.key) or {}
                ov.boxes += textract_boxes(data, page, width or 612.0, height or 792.0)
            else:
                data = store.read_json(deps, ref.key) or {}
                pm_raw = store.read_json(deps, ref.key.replace("docling.json", "docling_pages.json")) or {}
                page_map = {int(k): int(v) for k, v in (pm_raw.get("pageMap") or {}).items()} or None
                ov.boxes += docling_boxes(data, page, page_map)
            ov.sources.append(ref.key)
        return ov

    if stage in UNIT_STAGES:
        units_ref = next(iter(_artifacts(records.get(StageId.UNITS), ArtifactKind.UNITS_JSONL)), None)
        if units_ref is None:
            ov.note = ov.note or "no units yet"
            return ov
        units = store.load_units_jsonl(deps, units_ref.key)
        ov.sources.append(units_ref.key)
        flags: dict[str, str] = {}
        held: list[BBox] = []
        dec_ref = next(iter(_artifacts(records.get(StageId.PII_GATE), ArtifactKind.PII_DECISIONS)), None)
        if stage is StageId.PII_GATE and dec_ref is not None:
            decisions = (store.read_json(deps, dec_ref.key) or {}).get("decisions", [])
            flags = {d["unitId"]: d["decision"] for d in decisions}
            held = [BBox.load(b) for d in decisions for b in d.get("heldBboxes", [])]
            ov.sources.append(dec_ref.key)
        ov.boxes += unit_boxes(units, page, members=stage is StageId.UNITS, fields=stage is StageId.UNITS,
                               flags=flags)
        ov.boxes += [OverlayBox(kind="held", bbox=b) for b in held if b.page == page]
        if pv == "1.0" and not ov.boxes:
            ov.note = "1.0 chunks carry no bbox: there is no page region to draw"
        return ov

    # Gold stages: what was promoted, and what was blacked out.
    gold_ref = next(iter(_artifacts(promote, ArtifactKind.GOLD_UNITS_JSONL)), None)
    if gold_ref is not None:
        ov.boxes += unit_boxes(store.load_units_jsonl(deps, gold_ref.key), page, members=False, fields=False)
        ov.sources.append(gold_ref.key)
    held_key = gold_ref.key.replace("/units.jsonl", "/pages/held_regions.json") if gold_ref else None
    held_doc = store.read_json(deps, held_key) if held_key else None
    if held_doc:
        ov.boxes += [OverlayBox(kind="held", bbox=BBox.load(b)) for b in held_doc.get("pages", {}).get(str(page), [])]
        ov.sources.append(held_key)  # type: ignore[arg-type]
    if pv == "1.0" and not ov.boxes:
        ov.note = "1.0 chunks carry no bbox: there is no page region to draw"
    return ov


@router.route("GET", DOC_OVERLAY)
def get_overlay(req: Request) -> Reply:
    deps = get_deps()
    doc_id = req.param("docId")
    pv = _pv(req)
    stage = _stage_id(req)
    access = require_doc(deps, req.caller, doc_id)
    doc = load_document(deps, access, doc_id)
    page_count = int(doc.page_count or 1)
    page = req.int_query("page", 1, lo=1, hi=10_000)
    if page > page_count:
        raise bad_request(f"page {page} is beyond the document's {page_count} pages")
    records = {r.stage_id: r for r in store.stage_records(deps, doc_id, pv)}
    return Reply(build_overlay(deps, doc_id, pv, stage, page, page_count, records))

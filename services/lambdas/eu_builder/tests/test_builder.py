"""Evidence-unit builder on synthetic layout JSON and on recorded Docling/Textract output."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from evidence_core.bbox import contains
from evidence_core.config import EnrichmentConfig, UnitsConfig
from evidence_core.enums import CanonicalRole, UnitType
from lambdas.eu_builder.builder import BuildInput, build_units
from lambdas.ingest_lib.docling_layout import docling_to_layout
from lambdas.ingest_lib.embedder import HashEmbedder
from lambdas.ingest_lib.glyphs import GlyphLocator
from lambdas.ingest_lib.layout import DocLayout
from lambdas.ingest_lib.testing import scanned_payslip_response
from lambdas.ingest_lib.textract_layout import textract_to_layout
from local_pipeline import synthetic

FIX = Path(__file__).resolve().parents[2] / "ingest_lib" / "tests" / "fixtures"
ENRICH = EnrichmentConfig(breadcrumb=True, doc_type="ontology", field_concept="ontology", period=True, bbox=True)


class TopicEmbedder:
    """Vectors by topic keyword so Phase B decisions are exact in tests."""

    model_id = "topic-test-embedder"
    dimensions = 3
    topics = ("revenue", "weather", "coupon")

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        out = []
        for t in texts:
            v = [1.0 if k in t.lower() else 0.0 for k in self.topics]
            out.append(v if any(v) else [0.0, 0.0, 0.0])
        return out


def _bl(left: float, top: float, right: float, bottom: float, h: float = 792.0) -> dict[str, Any]:
    """Top-left numbers -> a Docling BOTTOMLEFT bbox."""
    return {"l": left, "t": h - top, "r": right, "b": h - bottom, "coord_origin": "BOTTOMLEFT"}


def make_doc(items: list[dict[str, Any]], pages: int = 1) -> dict[str, Any]:
    """Minimal DoclingDocument: items = {kind: texts|tables|pictures, label, text, page, box, ...}."""
    doc: dict[str, Any] = {"schema_name": "DoclingDocument", "body": {"self_ref": "#/body", "children": []},
                           "furniture": {"self_ref": "#/furniture", "children": []}, "groups": [], "texts": [],
                           "tables": [], "pictures": [], "key_value_items": [],
                           "pages": {str(p): {"size": {"width": 612.0, "height": 792.0}, "page_no": p}
                                     for p in range(1, pages + 1)}}
    for it in items:
        kind = it.get("kind", "texts")
        idx = len(doc[kind])
        ref = f"#/{kind}/{idx}"
        node: dict[str, Any] = {"self_ref": ref, "label": it["label"], "text": it.get("text", ""),
                                "prov": [{"page_no": it.get("page", 1), "bbox": _bl(*it["box"])}]}
        if kind == "tables":
            rows = it["rows"]
            cells = []
            for r, row in enumerate(rows):
                for c, text in enumerate(row):
                    x0 = it["box"][0] + c * 100
                    y0 = it["box"][1] + r * 20
                    cells.append({"bbox": {"l": x0, "t": y0, "r": x0 + 90, "b": y0 + 15, "coord_origin": "TOPLEFT"},
                                  "row_span": 1, "col_span": 1, "start_row_offset_idx": r, "end_row_offset_idx": r + 1,
                                  "start_col_offset_idx": c, "end_col_offset_idx": c + 1, "text": text,
                                  "column_header": r == 0 and it.get("header", False)})
            node["data"] = {"table_cells": cells, "num_rows": len(rows), "num_cols": len(rows[0])}
        doc[kind].append(node)
        target = "furniture" if it["label"] in ("page_header", "page_footer") else "body"
        doc[target]["children"].append({"$ref": ref})
    return doc


def _build(layout: DocLayout, vocab: Any, *, doc_type: str | None = None, embedder: Any = None,
           units_cfg: UnitsConfig | None = None, glyphs: GlyphLocator | None = None, file_name: str = "d.pdf"):
    return build_units(BuildInput(
        doc_id="doc-abc123", package_id="pkg-lending-01", pipeline_version="2.3.1", file_name=file_name,
        allowed_principals=["user:u", "group:lending-analyst"], source_key="bronze/u/pkg-lending-01/d.pdf",
        source_version_id="v1", source_sha="0" * 64, config_sha="cfg", ontology_version="1.2.0", layout=layout,
        vocab=vocab, units_cfg=units_cfg or UnitsConfig(), enrichment=ENRICH, embedder=embedder or TopicEmbedder(),
        doc_type=doc_type, glyphs=glyphs))


@pytest.fixture
def report_doc() -> dict[str, Any]:
    return make_doc([
        {"label": "page_header", "text": "NewAge Global Fintech · Q3 FY2025", "box": (50, 20, 400, 30)},
        {"label": "section_header", "text": "3. Results", "box": (50, 60, 300, 75)},
        {"label": "text", "text": "Revenue grew modestly in H2 as revenue from payments rose.",
         "box": (50, 90, 550, 120)},
        {"label": "caption", "text": "Table 3 · Total revenue", "box": (50, 140, 300, 152)},
        {"label": "text", "text": "USD millions", "box": (450, 140, 550, 152)},
        {"kind": "tables", "label": "table", "box": (50, 160, 450, 220), "header": True,
         "rows": [["Quarter", "Revenue"], ["Q1", "4,120"], ["Q3", "4,890"], ["Q4", "5,010¹"]]},
        {"label": "text", "text": "Weather in the region was mild this quarter.", "box": (50, 300, 550, 320)},
        {"label": "section_header", "text": "4. Outlook", "box": (50, 400, 300, 415)},
        {"label": "text", "text": "We expect weather to stay mild.", "box": (50, 430, 550, 450)},
        {"label": "text", "text": "¹ Q4 figures are preliminary.", "box": (50, 720, 300, 732), "page": 2},
        {"label": "page_footer", "text": "Fictitious document", "box": (50, 760, 300, 770)},
    ], pages=2)


def test_phases_a_b_c_and_marker_link(report_doc: dict[str, Any], vocab: Any) -> None:
    layout = docling_to_layout(report_doc, artifact_key="silver/k/docling.json", vocab=vocab)
    out = _build(layout, vocab)
    panel = next(u for u in out.units if u.unit_type is UnitType.TABLE_PANEL)
    roles = {m.role for m in panel.members}
    assert {CanonicalRole.TABLE, CanonicalRole.CAPTION, CanonicalRole.UNIT_LABEL, CanonicalRole.FOOTNOTE} <= roles
    phases = {m.role: m.phase for m in panel.members}
    assert phases[CanonicalRole.CAPTION] == "A" and phases[CanonicalRole.UNIT_LABEL] == "A"
    assert phases[CanonicalRole.FOOTNOTE] == "marker"  # cross-page footnote via the marker hard rule
    assert panel.pages == [1, 2] and len(panel.bboxes) == 2
    # Phase B: the revenue paragraph joins the panel (cosine 1.0 >= tau); weather does not (0.0)
    texts = [m.text for m in panel.members]
    assert any("Revenue grew" in (t or "") for t in texts)
    b_member = next(m for m in panel.members if m.phase == "B")
    assert b_member.alloc_score == pytest.approx(1.0)
    # Phase C: residual section_text units per section, headers included, page furniture excluded
    residual = [u for u in out.units if u.unit_type is UnitType.SECTION_TEXT]
    assert any("Weather in the region" in u.text for u in residual)
    assert any(u.text.startswith("4. Outlook") for u in residual)
    assert all("Fictitious document" not in u.text and "Q3 FY2025" not in u.text for u in out.units)
    assert out.report["phaseCounts"] == {"A": 1, "B": 1, "C": len(residual), "marker": 1}
    assert out.report["derivedRoles"]["unit_label"] == 1
    allocs = [a for a in out.report["allocations"] if a["phase"] == "B"]
    assert {a["allocated"] for a in allocs} == {True, False}
    assert all(a["unit"] for a in out.report["allocations"])
    # header-row table: amounts pair with row label / column header
    labels = {f.label: f.value for f in panel.fields}
    assert labels["Q3 · Revenue"] == "4,890"
    assert [u.unit_id for u in out.units] == [f"doc-abc123:2.3.1:eu-{i:02d}" for i in range(1, len(out.units) + 1)]
    assert out.report["invariants"]["I1"].startswith("deferred")


def test_marker_link_off_leaves_footnote_to_residual(report_doc: dict[str, Any], vocab: Any) -> None:
    layout = docling_to_layout(report_doc, artifact_key="k", vocab=vocab)
    out = _build(layout, vocab, units_cfg=UnitsConfig(marker_link="off"))
    panel = next(u for u in out.units if u.unit_type is UnitType.TABLE_PANEL)
    assert CanonicalRole.FOOTNOTE not in {m.role for m in panel.members}


def test_cross_page_table_merge(vocab: Any) -> None:
    doc = make_doc([
        {"kind": "tables", "label": "table", "box": (50, 700, 450, 740), "rows": [["a", "1.00"], ["b", "2.00"]]},
        {"kind": "tables", "label": "table", "box": (50, 40, 450, 80), "rows": [["c", "3.00"], ["d", "4.00"]],
         "page": 2},
    ], pages=2)
    layout = docling_to_layout(doc, artifact_key="k", vocab=vocab)
    out = _build(layout, vocab)
    panels = [u for u in out.units if u.unit_type is UnitType.TABLE_PANEL]
    assert len(panels) == 1 and panels[0].pages == [1, 2]
    assert panels[0].provenance.build["mergedTables"] == 1


def test_residual_split_on_token_budget(vocab: Any) -> None:
    para = " ".join(["word"] * 150)
    doc = make_doc([{"label": "section_header", "text": "Notes", "box": (50, 40, 200, 50)}]
                   + [{"label": "text", "text": para, "box": (50, 60 + i * 40, 550, 90 + i * 40)} for i in range(4)])
    layout = docling_to_layout(doc, artifact_key="k", vocab=vocab)
    out = _build(layout, vocab, units_cfg=UnitsConfig(max_residual_tokens=400))
    assert len(out.units) == 2
    assert all((u.tokens or 0) <= 401 for u in out.units)


def test_residual_heading_runs_belong_to_the_following_text(vocab: Any) -> None:
    """'Part II' + '2. Results' introduce the Beta text; they must not be glued to the Overview unit."""
    doc = make_doc([
        {"label": "section_header", "text": "1. Overview", "box": (50, 40, 300, 52)},
        {"label": "text", "text": "Alpha body text about overview.", "box": (50, 60, 550, 80)},
        {"label": "section_header", "text": "Part II", "box": (50, 100, 300, 112)},
        {"label": "section_header", "text": "2. Results", "box": (50, 120, 300, 132)},
        {"label": "text", "text": "Beta body text about results.", "box": (50, 140, 550, 160)},
    ])
    layout = docling_to_layout(doc, artifact_key="k", vocab=vocab)
    out = _build(layout, vocab)
    residual = [u.text.splitlines() for u in out.units if u.unit_type is UnitType.SECTION_TEXT]
    assert ["1. Overview", "Alpha body text about overview."] in residual
    assert ["Part II", "2. Results", "Beta body text about results."] in residual


def test_w2_from_recorded_docling_has_box_fields_with_tight_bboxes(vocab: Any) -> None:
    doc = json.loads((FIX / "w2_2025_maya_fictus.docling.json").read_text())
    layout = docling_to_layout(doc, artifact_key="silver/k/docling.json", vocab=vocab)
    glyphs = GlyphLocator(synthetic.w2_pdf())
    try:
        out = _build(layout, vocab, doc_type="doc:W2", embedder=HashEmbedder(), glyphs=glyphs,
                     file_name="w2_2025_maya_fictus.pdf")
    finally:
        glyphs.close()
    panel = next(u for u in out.units if u.unit_type is UnitType.TABLE_PANEL)
    fields = {f.field_id: f for f in panel.fields}
    assert fields["f-box1"].value == "98,400.00" and fields["f-box1"].value_number == "98400.00"
    assert fields["f-box1"].concept == "fin:W2WagesBox1" and fields["f-box1"].period == "TY2025"
    assert fields["f-box3"].value == "105,000.00"
    assert fields["f-box12a"].value_number == "6600.00"
    assert panel.breadcrumb == ["W-2 2025", "Maya Fictus", "Wages (Boxes 1–6)"]
    assert panel.label == "W-2 · Maya Fictus · Wages (Boxes 1–6)"
    box1 = fields["f-box1"].bbox
    assert box1 is not None and contains(panel.bboxes[0], box1, tolerance=6)
    assert box1.x1 < 310  # left column only: label + its value, not the neighbouring box 2
    assert fields["f-box1"].block_refs[0].pointer.startswith("#/tables/0/data/table_cells/")


def test_payslip_text_fields_and_period(vocab: Any) -> None:
    doc = json.loads((FIX / "payslip_2026-09_maya_fictus.docling.json").read_text())
    layout = docling_to_layout(doc, artifact_key="k", vocab=vocab)
    out = _build(layout, vocab, doc_type="doc:Payslip", embedder=HashEmbedder(),
                 glyphs=GlyphLocator(synthetic.payslip_pdf()))
    fields = {f.concept: f for u in out.units for f in u.fields if f.concept}
    assert fields["fin:PayslipGrossPay"].value == "9,000.00"
    assert fields["fin:PayslipYtdGross"].value_number == "81000.00"
    assert fields["fin:PayslipYtdGross"].period == "2026-09"
    assert fields["fin:PayslipGrossPay"].bbox is not None


def test_textract_scan_field_group_and_review_item(vocab: Any) -> None:
    layout = textract_to_layout(scanned_payslip_response(), page=1, page_width=612, page_height=792,
                                artifact_key="silver/k/textract/p001.json", vocab=vocab)
    out = _build(layout, vocab, doc_type="doc:Payslip", embedder=HashEmbedder())
    group = next(u for u in out.units if u.unit_type is UnitType.FIELD_GROUP)
    concepts = {f.concept: f for f in group.fields}
    assert concepts["fin:PayslipGrossPay"].confidence == pytest.approx(0.96)
    assert concepts["fin:PayslipGrossPay"].bbox is not None
    assert group.confidence == pytest.approx(0.72)
    assert [r.field_id for r in out.review_items] == [concepts["fin:PayslipYtdGross"].field_id]
    assert out.review_items[0].threshold == 0.80
    assert group.provenance.engine.value == "textract"
    assert group.breadcrumb[0] == "Payslip Sep 2026"


def test_form_boxes_split_into_loose_text_items_pair_by_geometry(vocab: Any) -> None:
    """Docling often emits each W-2 box label and value as its own text item; the builder pairs them
    from the text layer and makes one field_group (values drawn in overlay order in the PDF)."""
    from lambdas.ingest_lib.glyphs import text_runs

    pdf = synthetic.w2_pdf()
    glyphs = GlyphLocator(pdf)
    items = [{"label": "page_footer" if t.startswith("Fictitious") else "text", "text": t,
              "box": (b.x0, b.y0, b.x1, b.y1)} for t, b in text_runs(glyphs, 1)]
    layout = docling_to_layout(make_doc(items), artifact_key="silver/k/docling.json", vocab=vocab)
    try:
        out = _build(layout, vocab, doc_type="doc:W2", embedder=HashEmbedder(), glyphs=glyphs)
    finally:
        glyphs.close()
    group = next(u for u in out.units if u.unit_type is UnitType.FIELD_GROUP)
    values = {f.field_id: f.value for f in group.fields}
    assert values["f-box1"] == "98,400.00" and values["f-box2"] == "13,776.00"
    assert values["f-box3"] == "105,000.00" and values["f-box6"] == "1,522.50"
    assert out.report["derivedRoles"]["form_field_group"] == 1
    box1 = next(f for f in group.fields if f.field_id == "f-box1")
    assert len(box1.block_refs) == 2 and box1.bbox is not None and box1.bbox.x1 < 310

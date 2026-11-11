"""Raised footnote markers (glyph geometry), the fields they follow, and the D2 step event they lead to.

Uses the generated samples (``data/samples``, generator seeds 2025/2031/305): the trade confirmation prints
``Coupon 4.25%`` with a raised ``3`` and opens page 2 with the raised ``3`` of its footnote; the quarterly report
puts a raised ``1`` after Q4 in Table 3 and a raised ``2`` after "Corporate" in Table 4; the lending W-2 has none.
Expected boxes are the generator's gold regions (``gold.json``).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evidence_core.bbox import BBox
from evidence_core.enums import CanonicalRole, Engine, UnitType
from evidence_core.models.units import FieldValue, Member, Unit, UnitProvenance
from lambdas.eu_builder.fields import FieldBuilder, RawField, table_fields
from lambdas.ingest_lib.glyphs import GlyphLocator, raised_glyphs
from lambdas.ingest_lib.layout import LayoutBlock, TableCell, TableGrid
from lambdas.ingest_lib.rdf import build_abox, parse_step_event

SAMPLES = Path(__file__).resolve().parents[4] / "data" / "samples"
TC = SAMPLES / "tradeconf" / "trade_confirmation_xs2031.pdf"
QR = SAMPLES / "quarterly" / "quarterly_report_q3_fy2025.pdf"
W2 = SAMPLES / "lending" / "w2_2025_maya_fictus.pdf"
pytestmark = pytest.mark.skipif(not TC.exists(), reason="data/samples missing: run `make data`")


def _gold(pkg: str, region: str) -> BBox:
    for doc in json.loads((SAMPLES / pkg / "gold.json").read_text())["documents"]:
        for r in doc.get("regions", []):
            if r["regionId"] == region and doc["fileName"].endswith("xs2031.pdf"):
                return BBox.load(r["bbox"])
    raise KeyError(region)


def _inside(inner: BBox, outer: BBox) -> bool:
    return (outer.page == inner.page and outer.x0 - 0.5 <= inner.x0 and inner.x1 <= outer.x1 + 0.5
            and outer.y0 - 0.5 <= inner.y0 and inner.y1 <= outer.y1 + 0.5)


# --- glyph geometry ---------------------------------------------------------------------------------------

def test_raised_markers_are_found_where_the_generator_printed_them() -> None:
    glyphs = GlyphLocator(TC.read_bytes())
    p1 = raised_glyphs(glyphs, 1)
    coupon_marker = _gold("tradeconf", "tc-coupon-marker")
    assert [ch for ch, box in p1 if _inside(box, coupon_marker)] == ["3"]
    assert sorted(ch for ch, _ in p1) == ["1", "1", "2", "2", "3"]  # two cell markers + two footnote leads + coupon
    (lead,) = raised_glyphs(glyphs, 2)
    assert lead[0] == "3" and _inside(lead[1], _gold("tradeconf", "tc-footnote3"))


def test_no_raised_glyphs_on_the_w2_and_two_on_quarterly_table_pages() -> None:
    assert raised_glyphs(GlyphLocator(W2.read_bytes()), 1) == []
    q = GlyphLocator(QR.read_bytes())
    assert [ch for ch, _ in raised_glyphs(q, 14)] == ["1", "1"]  # Q4 cell marker, footnote lead
    assert [ch for ch, _ in raised_glyphs(q, 13)] == []


def test_within_filters_to_a_box() -> None:
    glyphs = GlyphLocator(TC.read_bytes())
    assert [ch for ch, _ in raised_glyphs(glyphs, 1, _gold("tradeconf", "tc-coupon"))] == ["3"]
    assert raised_glyphs(glyphs, 1, _gold("tradeconf", "tc-security")) == []


# --- fields ---------------------------------------------------------------------------------------------------

def _cell(row: int, col: int, text: str, bbox: BBox | None = None, header: bool = False) -> TableCell:
    return TableCell(row=row, col=col, text=text, bbox=bbox, pointer=f"#/tables/0/cells/{row}-{col}", header=header)


def _table(rows: list[list[TableCell]]) -> LayoutBlock:
    cells = [c for r in rows for c in r]
    return LayoutBlock(block_id="b-0001", page=1, role=CanonicalRole.TABLE, text="", bbox=None, order=1,
                       engine=Engine.DOCLING, engine_label="table", artifact_key="silver/x/docling.json",
                       pointer="#/tables/0", table=TableGrid(num_rows=len(rows), num_cols=2, cells=cells))


def test_term_value_table_pairs_rows_and_keeps_header_amount_tables_unchanged() -> None:
    terms = _table([[_cell(0, 0, "Term", header=True), _cell(0, 1, "Value", header=True)],
                    [_cell(1, 0, "Security"), _cell(1, 1, "XS-2031")],
                    [_cell(2, 0, "Coupon"), _cell(2, 1, "4.25% 3")],
                    [_cell(3, 0, "Maturity"), _cell(3, 1, "15 Sep 2033")]])
    assert [(f.label, f.value) for f in table_fields(terms)] == [
        ("Security", "XS-2031"), ("Coupon", "4.25% 3"), ("Maturity", "15 Sep 2033")]
    amounts = _table([[_cell(0, 0, "Quarter", header=True), _cell(0, 1, "Total revenue", header=True)],
                      [_cell(1, 0, "Q3 FY2025"), _cell(1, 1, "4,890")],
                      [_cell(2, 0, "Q4 FY2025"), _cell(2, 1, "5,010 1")]])
    got = table_fields(amounts)
    assert [(f.label, f.value, f.needs_marker) for f in got] == [
        ("Q3 FY2025 · Total revenue", "4,890", False), ("Q4 FY2025 · Total revenue", "5,010 1", True)]


def _builder(pdf: Path) -> FieldBuilder:
    from lambdas.ingest_lib.vocab import load_vocab

    vocab = load_vocab("1.2.0", path=Path(__file__).resolve().parents[4] / "ontology" / "dist" / "1.2.0" / "vocab.json")
    return FieldBuilder(vocab=vocab, doc_type="doc:TradeConfirmation", use_concepts=True, period=None,
                        glyphs=GlyphLocator(pdf.read_bytes()))


def test_a_raised_marker_is_stripped_from_the_value_and_remembered() -> None:
    fb = _builder(TC)
    coupon = _gold("tradeconf", "tc-coupon")
    value_box = BBox(page=1, x0=250.0, y0=coupon.y0, x1=coupon.x1, y1=coupon.y1)
    (f,) = fb.build([RawField(label="Coupon", value="4.25% 3", page=1, label_bbox=None, value_bbox=value_box,
                              source="test")])
    assert (f.value, f.value_number, f.concept, f.currency) == ("4.25%", "4.25", "fin:CouponRate", None)
    assert fb.markers == {f.field_id: "3"}


def test_a_plain_trailing_digit_is_kept_and_a_marker_candidate_without_glyph_is_dropped() -> None:
    fb = _builder(TC)
    box = _gold("tradeconf", "tc-security")  # no raised glyph in this row
    got = fb.build([RawField(label="Security", value="XS 2031 7", page=1, label_bbox=None, value_bbox=box,
                             source="test"),
                    RawField(label="Nominal", value="5,000 7", page=1, label_bbox=None, value_bbox=box,
                             source="test", needs_marker=True)])
    assert [(f.label, f.value) for f in got] == [("Security", "XS 2031 7")] and fb.markers == {}


# --- D2 step event (rdf) -------------------------------------------------------------------------------------

def _terms_unit(footnote: str = "3 6.00% from year 3 if rating < BBB-", link_field: str = "f-coupon") -> Unit:
    box = BBox(page=1, x0=54, y0=340, x1=552, y1=500)
    fn_box = BBox(page=2, x0=60, y0=109.77, x1=218.01, y1=120.87)
    return Unit(
        unit_id="trade-confirmation-xs2031-9af5c5:2.3.1:eu-04", doc_id="trade-confirmation-xs2031-9af5c5",
        package_id="pkg-tradeconf-01", pipeline_version="2.3.1", unit_type=UnitType.TABLE_PANEL,
        label="Trade confirmation · Security terms", file_name="trade_confirmation_xs2031.pdf",
        doc_type="doc:TradeConfirmation", text="…", pages=[1, 2], page_width=612, page_height=792, bboxes=[box],
        members=[Member(member_id="m-01", role=CanonicalRole.TABLE, bbox=box, phase="A"),
                 Member(member_id="m-02", role=CanonicalRole.FOOTNOTE, text=footnote, bbox=fn_box, phase="marker")],
        fields=[FieldValue(field_id="f-security", label="Security", value="XS-2031", concept="fin:SecurityIdentifier"),
                FieldValue(field_id="f-coupon", label="Coupon", value="4.25%", value_number="4.25",
                           concept="fin:CouponRate", bbox=BBox(page=1, x0=60, y0=440, x1=290, y1=460))],
        allowed_principals=["group:lending-analyst"], chars=1, tokens=1,
        provenance=UnitProvenance(engine=Engine.DOCLING, source_key="bronze/x", source_sha="ab", config_sha="cd",
                                  build={"markerLinks": [{"marker": "3", "footnoteMember": "m-02",
                                                          "fieldId": link_field, "crossPage": True,
                                                          "evidence": "raised glyph"}]}),
    )


def _abox(unit: Unit):  # noqa: ANN202
    return build_abox([unit], package_id=unit.package_id, pipeline_version="2.3.1", doc_id=unit.doc_id,
                      file_name=unit.file_name, source_sha="ab", doc_type="doc:TradeConfirmation",
                      stage_activity="urn:rp:activity:x:2.3.1:units")


def test_parse_step_event_is_strict() -> None:
    assert parse_step_event("3 6.00% from year 3 if rating < BBB-", "3") == {"rate": "6.00", "year": "3",
                                                                              "rating": "BBB-"}
    assert parse_step_event("5.50% from year 2 if rating < BB+", None)["rating"] == "BB+"
    assert parse_step_event("3 6.00% from year 3 if the rating falls below BBB-", "3") is None


def test_a_linked_footnote_becomes_a_step_event_with_its_region() -> None:
    w, _ = _abox(_terms_unit())
    text = w.nquads()
    assert w.step_events == 1 and w.step_event_failures == 0
    assert '<urn:rp:ont:fin:newRate> "6.00"^^<http://www.w3.org/2001/XMLSchema#decimal>' in text
    assert '<urn:rp:ont:fin:effectiveFromYear> "3"^^<http://www.w3.org/2001/XMLSchema#integer>' in text
    assert '<urn:rp:ont:fin:triggerRating> "BBB-"' in text
    assert "<https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/StepUpBond>" in text
    assert "<urn:rp:region:trade-confirmation-xs2031-9af5c5:2.3.1:eu-04/m-02/p2>" in text  # the footnote's region


def test_an_unparsable_or_unlinked_footnote_writes_nothing() -> None:
    w, _ = _abox(_terms_unit(footnote="3 The coupon may change."))
    assert w.step_events == 0 and w.step_event_failures == 1 and "RatingTriggeredStepEvent" not in w.nquads()
    w, _ = _abox(_terms_unit(link_field="f-security"))  # a marker on a field that is not the coupon
    assert w.step_events == 0 and w.step_event_failures == 0

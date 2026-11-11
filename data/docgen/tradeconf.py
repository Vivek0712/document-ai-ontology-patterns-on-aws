"""D2: two-page trade confirmation with a coupon step-up footnote on page 2.

Page 1 terms table carries ``Coupon 4.25%`` with a raised marker 3; the footnote that changes its meaning
("6.00% from year 3 if rating < BBB-") is on page 2. Ontology reading: the security is a
``fibo-sec-dbt-bnd:StepUpBond`` with ``SteppedCouponTerms``; the trigger is a ``fin:RatingTriggeredStepEvent``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evidence_core.bbox import union

from . import layout as L
from .common import GoldDoc, GoldRegion, PdfDoc, rng


@dataclass(frozen=True)
class TradeConfSpec:
    file_name: str
    security: str  # "XS-2031"
    reference: str
    trade_date: str
    settlement_date: str
    nominal: str  # "5,000,000.00"
    coupon: str  # "4.25"
    step_rate: str  # "6.00"
    step_year: int  # 3
    trigger_rating: str  # "BBB-"
    issuer_rating: str  # "BBB (watch)"
    maturity: str
    price: str


def xs2031() -> TradeConfSpec:
    return TradeConfSpec(file_name="trade_confirmation_xs2031.pdf", security="XS-2031", reference="TC-2026-10-0031",
                         trade_date="12 Oct 2026", settlement_date="14 Oct 2026", nominal="5,000,000.00",
                         coupon="4.25", step_rate="6.00", step_year=3, trigger_rating="BBB-",
                         issuer_rating="BBB (watch)", maturity="15 Sep 2033", price="99.75")


def seeded_variant(n: int, seed: int) -> TradeConfSpec:
    r = rng(seed, f"tradeconf-{n}")
    coupon = r.choice(["3.50", "3.75", "4.00", "4.50", "4.75"])
    step = f"{float(coupon) + r.choice([1.25, 1.50, 1.75, 2.00]):.2f}"
    trig = r.choice(["BBB-", "BB+"])
    nominal = f"{r.randrange(2, 9) * 1_000_000:,}.00"
    day = r.randrange(3, 26)
    return TradeConfSpec(file_name=f"trade_confirmation_xs20{30 + n}.pdf", security=f"XS-20{30 + n}",
                         reference=f"TC-2026-10-00{30 + n}", trade_date=f"{day:02d} Oct 2026",
                         settlement_date=f"{day + 2:02d} Oct 2026", nominal=nominal, coupon=coupon, step_rate=step,
                         step_year=r.choice([2, 3, 4]), trigger_rating=trig,
                         issuer_rating="BBB (watch)" if trig == "BBB-" else "BBB- (watch)",
                         maturity=f"15 Sep 20{r.randrange(31, 36)}", price=f"{r.uniform(98.5, 100.5):.2f}")


def generate(spec: TradeConfSpec, out_dir: Path) -> GoldDoc:
    title = f"Trade Confirmation {spec.security}"
    gold = GoldDoc(file_name=spec.file_name, doc_type="doc:TradeConfirmation", route="digital", title=title)
    pdf = PdfDoc(out_dir / spec.file_name, gold, title=title)
    fb = "Helvetica-Bold"

    h1 = pdf.text(L.MARGIN_L, 60, "NewAge Global Fintech · Markets", font=fb, size=14)
    h2 = pdf.text(L.MARGIN_R, 60, "Trade Confirmation", font=fb, size=14, align="right")
    h3 = pdf.text(L.MARGIN_L, 78, f"Reference {spec.reference}", size=9)
    gold.add(GoldRegion(region_id="tc-title", kind="header", page=1, bbox=union([h1, h2, h3]), role="title",
                        label="Trade Confirmation", value=spec.reference))
    intro = ("We confirm the following transaction between the parties named below. All parties, securities and "
             "ratings in this document are fictitious.")
    ib, y = L.prose(pdf, 104, [intro])
    gold.add(GoldRegion(region_id="tc-intro", kind="text", page=1, bbox=union(ib), role="text",
                        label="confirmation statement", value=intro))

    _, y = L.heading(pdf, y + 6, "Trade details", level=2)
    details = [["Trade date", spec.trade_date], ["Settlement date", spec.settlement_date],
               ["Buyer", "Fictus Capital Partners"], ["Seller", "NewAge Global Fintech Markets desk"],
               ["Price (clean)", spec.price]]
    td = L.table(pdf, y, ["Item", "Value"], details, [L.MARGIN_L, 250.0], ["left", "left"], markers={(1, 1): "2",
                                                                                                    (4, 1): "1"})
    gold.add(GoldRegion(region_id="tc-details", kind="unit", page=1, bbox=td.box, role="table",
                        label="Trade confirmation · Trade details", extra={"unitType": "table_panel"}))
    gold.add(GoldRegion(region_id="tc-settlement", kind="field", page=1, bbox=td.row_boxes[1], role="table_cell",
                        label="Settlement date", value=spec.settlement_date, concept="fin:SettlementDate",
                        field_id="f-settlement-date", unit_ref="tc-details"))

    _, y = L.heading(pdf, td.box.y1 + 30, "Security terms", level=2)
    terms = [["Security", spec.security], ["Issuer", "NewAge Global Fintech"], ["Issuer rating", spec.issuer_rating],
             ["Nominal", f"USD {spec.nominal}"], ["Coupon", f"{spec.coupon}%"], ["Coupon frequency", "Annual"],
             ["Maturity", spec.maturity]]
    tt = L.table(pdf, y, ["Term", "Value"], terms, [L.MARGIN_L, 250.0], ["left", "left"], markers={(4, 1): "3"})
    gold.add(GoldRegion(region_id="tc-terms", kind="unit", page=1, bbox=tt.box, role="table",
                        label=f"Trade confirmation · {spec.security} · Security terms",
                        extra={"unitType": "table_panel", "ontologyClass": "fibo-sec-dbt-bnd:StepUpBond"}))
    concepts = {0: ("fin:SecurityIdentifier", "f-security"), 2: ("fin:IssuerRating", "f-issuer-rating"),
                3: ("fin:NominalAmount", "f-nominal"), 4: ("fin:CouponRate", "f-coupon"),
                6: ("fin:MaturityDate", "f-maturity")}
    for i, (concept, fid) in concepts.items():
        lab, val = tt.cells[i]
        value = terms[i][1]
        number = spec.coupon if i == 4 else (spec.nominal.replace(",", "") if i == 3 else None)
        gold.add(GoldRegion(region_id=f"tc-{fid.removeprefix('f-')}", kind="field", page=1, bbox=tt.row_boxes[i],
                            role="table_cell", label=terms[i][0], value=value, value_number=number, concept=concept,
                            field_id=fid, unit_ref="tc-terms", label_bbox=lab, value_bbox=val,
                            extra={"marker": "3"} if i == 4 else {}))
    gold.add(GoldRegion(region_id="tc-coupon-marker", kind="marker", page=1, bbox=tt.cells[4][1], role="marker_link",
                        label="footnote marker 3 on coupon", value="3", unit_ref="tc-terms",
                        extra={"links": "tc-footnote3", "footnotePage": 2}))

    fy = tt.box.y1 + 40
    f1 = union([pdf.superscript(L.MARGIN_L, fy, "1"), pdf.text(L.MARGIN_L + 5, fy, "Clean price, percent of nominal.",
                                                               size=8)])
    f2 = union([pdf.superscript(L.MARGIN_L, fy + 14, "2"),
                pdf.text(L.MARGIN_L + 5, fy + 14, "Settlement through the fictitious clearing system.", size=8)])
    gold.add(GoldRegion(region_id="tc-footnote1", kind="footnote", page=1, bbox=f1, role="footnote",
                        label="footnote 1", value="Clean price, percent of nominal."))
    gold.add(GoldRegion(region_id="tc-footnote2", kind="footnote", page=1, bbox=f2, role="footnote",
                        label="footnote 2", value="Settlement through the fictitious clearing system."))
    pdf.finish_page("Page 1 of 2")

    # page 2: the step-up footnote
    ph = pdf.text(L.MARGIN_L, 60, f"Trade Confirmation {spec.reference} (continued)", font=fb, size=11)
    gold.add(GoldRegion(region_id="tc-p2-header", kind="header", page=2, bbox=ph, role="page_header",
                        label="continued header"))
    _, y = L.heading(pdf, 96, "Notes to the security terms", level=2)
    note_text = f"{spec.step_rate}% from year {spec.step_year} if rating < {spec.trigger_rating}"
    m3 = pdf.superscript(L.MARGIN_L, y, "3", size=7)
    n3 = pdf.text(L.MARGIN_L + 6, y, note_text, size=10)
    fn3 = union([m3, n3])
    gold.add(GoldRegion(
        region_id="tc-footnote3", kind="footnote", page=2, bbox=fn3, role="footnote", label="footnote 3",
        value=note_text, value_number=spec.step_rate, concept="fin:StepUpCouponRate", field_id="f-step-up",
        unit_ref="tc-terms",
        extra={"newRate": spec.step_rate, "effectiveFromYear": spec.step_year, "triggerRating": spec.trigger_rating,
               "marker": "3", "markerPage": 1, "ontologyClass": "fin:RatingTriggeredStepEvent"},
    ))
    expl = ("The coupon steps up from the start of the coupon year stated above when the issuer's rating from any "
            "agency named in the offering terms falls below the trigger rating. The step-up applies once and does "
            "not reverse if the rating later recovers.")
    eb, y = L.prose(pdf, y + 22, [expl])
    gold.add(GoldRegion(region_id="tc-stepup-expl", kind="text", page=2, bbox=union(eb), role="text",
                        label="step-up explanation", value=expl))
    _, y = L.heading(pdf, y + 10, "Other terms", level=2)
    others = ("This confirmation supersedes any prior oral communication about the transaction. Please notify the "
              "markets desk within two business days if any detail above is incorrect. This document is generated "
              "for demonstration only and creates no obligation.")
    L.prose(pdf, y, [others])
    pdf.finish_page("Page 2 of 2")
    pdf.save()
    gold.attributes = {"security": spec.security, "coupon": spec.coupon, "stepRate": spec.step_rate,
                       "stepYear": spec.step_year, "triggerRating": spec.trigger_rating,
                       "issuerRating": spec.issuer_rating, "settlementDate": spec.settlement_date}
    return gold

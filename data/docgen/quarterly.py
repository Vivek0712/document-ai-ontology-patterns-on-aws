"""D1: 16-page quarterly report.

Table 3 (total revenue by quarter) sits on page 14 in section "3. Results" with the Q4 value carrying a
raised footnote marker and the footnote "Q4 figures are preliminary." under the table. Interpreting
prose ("modest growth in H2") sits on page 13, so caption and prose chunks can outrank the bare table
body in a naive lane. Figure 2 (revenue by segment, printed data labels) sits on page 12. Table 4
(operating expenses by region) starts on page 15 and continues on page 16, where its footnote sits:
the cross-page table case for the walkthrough.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evidence_core.bbox import union

from . import layout as L
from .common import GoldDoc, GoldRegion, PdfDoc, rng

PAGES = 16

FILLER = (
    "The quarter continued the programme of consolidating payment rails onto a single settlement platform.",
    "Operating teams completed the migration of merchant onboarding to the shared identity service.",
    "Risk management reviewed concentration limits across the lending book and found no breaches.",
    "The group kept its funding mix stable, with customer deposits remaining the primary source of liquidity.",
    "Investment in fraud analytics focused on earlier detection of account takeover patterns.",
    "Regional teams harmonised product terms so that customers see one fee schedule in every market.",
    "Technology spending shifted from new builds towards reliability work on existing services.",
    "The board approved the updated capital plan and the dividend policy remained unchanged.",
    "Customer complaints fell compared with the prior quarter after changes to dispute handling.",
    "The markets desk continued to serve corporate clients with hedging and short-term funding products.",
    "Treasury extended the maturity profile of wholesale funding during the period.",
    "The compliance function completed its annual review of sanctions screening controls.",
    "Partnership agreements with two payroll providers moved from pilot into general availability.",
    "Data platform work concentrated on lineage, so that every reported figure can be traced to its source.",
    "Staff training covered the revised conduct framework and the new incident reporting procedure.",
    "Cost discipline remained a priority, and discretionary spending was held flat.",
    "The lending business tightened underwriting criteria for unsecured products in selected segments.",
    "Card issuing volumes followed the usual seasonal pattern for the period.",
    "The group continued to simplify its legal entity structure where this reduces operating cost.",
    "Customer research informed changes to the mobile application's statement and document views.",
)


@dataclass(frozen=True)
class QuarterlySpec:
    file_name: str
    fiscal_year: int
    revenue: tuple[int, int, int, int]  # USD millions, Q1..Q4
    segments: tuple[tuple[str, int], ...]  # Q3 revenue by segment, sums to revenue[2]
    customer_accounts_m: str  # "2.4"
    opex: tuple[tuple[str, int], ...]  # Table 4 rows (region, USD millions)
    seed: int

    @property
    def label(self) -> str:
        return f"Q3 FY{self.fiscal_year}"


def fy2025_deck() -> QuarterlySpec:
    """The reference variant: Q1 4,120 · Q2 4,455 · Q3 4,890 · Q4 5,010 (preliminary)."""
    return QuarterlySpec(
        file_name="quarterly_report_q3_fy2025.pdf", fiscal_year=2025, revenue=(4120, 4455, 4890, 5010),
        segments=(("Payments", 2140), ("Lending", 1610), ("Markets", 1140)), customer_accounts_m="2.4",
        opex=(("Americas", 1180), ("EMEA", 960), ("APAC", 610), ("Corporate", 340)), seed=2025,
    )


def seeded_variant(fiscal_year: int, seed: int) -> QuarterlySpec:
    r = rng(seed, f"quarterly-{fiscal_year}")
    q1 = r.randrange(2600, 3900, 5)
    growth = [r.randrange(3, 9) for _ in range(3)]
    rev = [q1]
    for g in growth:
        rev.append(int(round(rev[-1] * (1 + g / 100) / 5.0) * 5))
    q3 = rev[2]
    pay = int(round(q3 * r.uniform(0.40, 0.46)))
    lend = int(round(q3 * r.uniform(0.30, 0.34)))
    segments = (("Payments", pay), ("Lending", lend), ("Markets", q3 - pay - lend))
    accounts = f"{r.randrange(14, 23) / 10:.1f}"
    opex = (("Americas", r.randrange(700, 1000)), ("EMEA", r.randrange(600, 900)), ("APAC", r.randrange(350, 600)),
            ("Corporate", r.randrange(200, 330)))
    return QuarterlySpec(file_name=f"quarterly_report_q3_fy{fiscal_year}.pdf", fiscal_year=fiscal_year,
                         revenue=(rev[0], rev[1], rev[2], rev[3]), segments=segments, customer_accounts_m=accounts,
                         opex=opex, seed=seed)


def _filler(spec: QuarterlySpec, page: int, n: int) -> list[str]:
    r = rng(spec.seed, f"filler-{page}")
    picks = r.sample(FILLER, k=min(n * 2, len(FILLER)))
    return [" ".join(picks[i:i + 2]) for i in range(0, len(picks), 2)][:n]


def generate(spec: QuarterlySpec, out_dir: Path) -> GoldDoc:
    title = f"NewAge Global Fintech Quarterly Report {spec.label}"
    gold = GoldDoc(file_name=spec.file_name, doc_type="doc:QuarterlyReport", route="digital", title=title)
    pdf = PdfDoc(out_dir / spec.file_name, gold, title=title)
    header_text = f"NewAge Global Fintech · Quarterly Report {spec.label}"
    fy = spec.fiscal_year

    def page_label() -> str:
        return f"Page {pdf.page} of {PAGES}"

    # p1 title + contents
    t1 = pdf.text(L.MARGIN_L, 200, "NewAge Global Fintech", font="Helvetica-Bold", size=26)
    t2 = pdf.text(L.MARGIN_L, 236, f"Quarterly Report · {spec.label}", font="Helvetica-Bold", size=18)
    t3 = pdf.text(L.MARGIN_L, 262, f"For the third quarter of fiscal year {fy}", size=11)
    gold.add(GoldRegion(region_id="qr-title", kind="header", page=1, bbox=union([t1, t2, t3]), role="title",
                        label=f"Quarterly Report · {spec.label}"))
    y = 330.0
    _, y = L.heading(pdf, y, "Contents", level=2)
    for line in ("1. Overview", "2. Strategy and operations", "3. Results", "4. Outlook"):
        pdf.text(L.MARGIN_L + 10, y, line, size=10)
        y += 16
    pdf.finish_page(page_label())

    sections = {2: "1. Overview", 5: "2. Strategy and operations", 10: "3. Results"}
    for p in range(2, 12):
        L.running_header(pdf, header_text)
        y = 84.0
        if p in sections:
            hb, y = L.heading(pdf, y, sections[p])
            gold.add(GoldRegion(region_id=f"qr-sec-p{p}", kind="header", page=p, bbox=hb, role="section_header",
                                label=sections[p]))
        if p == 3:
            sub, y = L.heading(pdf, y, "1.2 Customers", level=2)
            fact = (f"Customer accounts reached {spec.customer_accounts_m} million at the end of {spec.label}, "
                    "with most new accounts opened through the mobile application.")
            boxes, y = L.prose(pdf, y, [fact])
            gold.add(GoldRegion(region_id="qr-accounts", kind="text", page=p, bbox=union([sub, *boxes]), role="text",
                                label="1.2 Customers", value=f"{spec.customer_accounts_m} million",
                                concept="fin:CustomerAccountsCount", period=spec.label,
                                extra={"sentence": fact}))
        if p == 6:
            cats = [f"Q{i} FY{fy}" for i in (1, 2, 3)]
            vals = [float(spec.revenue[i]) * 0.5 for i in range(3)]
            cap = pdf.text(L.MARGIN_L, y, "Figure 1 · Digital transactions processed · millions",
                           font="Helvetica-Bold", size=10)
            ch = L.bar_chart(pdf, y + 10, cats, vals, [f"{v:,.0f}" for v in vals], height=120)
            gold.add(GoldRegion(region_id="qr-figure1", kind="figure", page=p, bbox=union([cap, ch.box]),
                                role="picture", label="Figure 1 · Digital transactions processed · millions",
                                extra={"unitType": "figure_panel"}))
            y = ch.box.y1 + 24
        _, y = L.prose(pdf, y, _filler(spec, p, 4 if p not in (3, 6) else 2))
        pdf.finish_page(page_label())

    # p12: Figure 2 revenue by segment
    L.running_header(pdf, header_text)
    sub, y = L.heading(pdf, 84, "3.2 Revenue by segment", level=2)
    cap = pdf.text(L.MARGIN_L, y, f"Figure 2 · Revenue by segment · {spec.label} · USD millions",
                   font="Helvetica-Bold", size=10)
    cats = [s for s, _ in spec.segments]
    vals = [float(v) for _, v in spec.segments]
    ch = L.bar_chart(pdf, y + 12, cats, vals, [f"{v:,}" for _, v in spec.segments])
    fig_box = union([cap, ch.box])
    gold.add(GoldRegion(region_id="qr-figure2", kind="figure", page=12, bbox=fig_box, role="picture",
                        label=f"Figure 2 · Revenue by segment · {spec.label} · USD millions", period=spec.label,
                        extra={"unitType": "figure_panel"}))
    gold.add(GoldRegion(region_id="qr-figure2-caption", kind="caption", page=12, bbox=cap, role="caption",
                        label="Figure 2 caption", unit_ref="qr-figure2"))
    for (seg, v), vl, cl, bar in zip(spec.segments, ch.value_labels, ch.category_labels, ch.bars, strict=True):
        gold.add(GoldRegion(region_id=f"qr-fig2-{seg.lower()}", kind="field", page=12, bbox=union([vl, bar, cl]),
                            role="text", label=f"{seg} revenue", value=f"{v:,}", value_number=f"{v}.00",
                            concept="fin:SegmentRevenue", field_id=f"f-{seg.lower()}", unit_ref="qr-figure2",
                            period=spec.label, currency="USD", value_bbox=vl, label_bbox=cl,
                            extra={"scale": "millions"}))
    _, y = L.prose(pdf, ch.box.y1 + 30, _filler(spec, 12, 2))
    pdf.finish_page(page_label())

    # p13: interpreting prose
    L.running_header(pdf, header_text)
    sub, y = L.heading(pdf, 84, "3.3 Revenue commentary", level=2)
    commentary = (f"Revenue rose in each quarter of FY{fy}. Management expects modest growth in H2, as pricing "
                  "changes in the payments business annualise and lending volumes stay selective.")
    boxes, y = L.prose(pdf, y, [commentary])
    gold.add(GoldRegion(region_id="qr-commentary", kind="text", page=13, bbox=union([sub, *boxes]), role="text",
                        label="3.3 Revenue commentary", value="modest growth in H2", period=spec.label,
                        extra={"sentence": commentary}))
    _, y = L.prose(pdf, y, _filler(spec, 13, 3))
    pdf.finish_page(page_label())

    # p14: Table 3
    L.running_header(pdf, header_text)
    sub, y = L.heading(pdf, 84, "3.4 Revenue by quarter", level=2)
    _, y = L.prose(pdf, y, ["The table below sets out total revenue for each quarter of the fiscal year."])
    cap = pdf.text(L.MARGIN_L, y + 4, "Table 3 · Total revenue · USD millions", font="Helvetica-Bold", size=10)
    rows = [[f"Q{i + 1} FY{fy}", f"{v:,}"] for i, v in enumerate(spec.revenue)]
    tb = L.table(pdf, y + 14, ["Quarter", "Total revenue"], rows, [L.MARGIN_L, 300.0], ["left", "right"],
                 markers={(3, 1): "1"})
    fn_y = tb.box.y1 + 18
    fm = pdf.superscript(L.MARGIN_L, fn_y, "1")
    ft = pdf.text(L.MARGIN_L + 5, fn_y, "Q4 figures are preliminary.", size=8)
    footnote = union([fm, ft])
    gold.add(GoldRegion(region_id="qr-table3-caption", kind="caption", page=14, bbox=cap, role="caption",
                        label="Table 3 · Total revenue · USD millions", unit_ref="qr-table3"))
    gold.add(GoldRegion(region_id="qr-table3", kind="unit", page=14, bbox=union([cap, tb.box, footnote]),
                        role="table", label="Table 3 · Total revenue · USD millions", period=f"FY{fy}",
                        extra={"unitType": "table_panel", "breadcrumb": ["3. Results", "3.4 Revenue by quarter"]}))
    for i, v in enumerate(spec.revenue):
        lab, val = tb.cells[i]
        gold.add(GoldRegion(region_id=f"qr-table3-q{i + 1}", kind="field", page=14, bbox=tb.row_boxes[i],
                            role="table_cell", label=f"Q{i + 1} FY{fy} total revenue", value=f"{v:,}",
                            value_number=f"{v}.00", concept="fin:TotalRevenue", field_id=f"f-q{i + 1}",
                            unit_ref="qr-table3", period=f"Q{i + 1} FY{fy}", currency="USD", label_bbox=lab,
                            value_bbox=val, extra={"scale": "millions", "preliminary": i == 3}))
    gold.add(GoldRegion(region_id="qr-table3-marker", kind="marker", page=14,
                        bbox=union([tb.cells[3][1]]), role="marker_link", label="footnote marker 1 on Q4",
                        value="1", unit_ref="qr-table3", extra={"links": "qr-table3-footnote"}))
    gold.add(GoldRegion(region_id="qr-table3-footnote", kind="footnote", page=14, bbox=footnote, role="footnote",
                        label="Table 3 footnote 1", value="Q4 figures are preliminary.", unit_ref="qr-table3"))
    _, y = L.prose(pdf, fn_y + 26, _filler(spec, 14, 2))
    pdf.finish_page(page_label())

    # p15-16: Table 4 split across pages
    L.running_header(pdf, header_text)
    sub, y = L.heading(pdf, 84, "3.5 Operating expenses", level=2)
    _, y = L.prose(pdf, y, _filler(spec, 15, 3))
    cap4 = pdf.text(L.MARGIN_L, y + 4, "Table 4 · Operating expenses by region · USD millions",
                    font="Helvetica-Bold", size=10)
    first = [[r, f"{v:,}"] for r, v in spec.opex[:2]]
    t4a = L.table(pdf, y + 14, ["Region", f"Q3 FY{fy}"], first, [L.MARGIN_L, 300.0], ["left", "right"])
    cont = pdf.text(L.MARGIN_L, t4a.box.y1 + 14, "Table 4 continues on the next page.", font="Helvetica-Oblique",
                    size=8)
    gold.add(GoldRegion(region_id="qr-table4-part1", kind="table", page=15, bbox=union([cap4, t4a.box, cont]),
                        role="table", label="Table 4 · Operating expenses by region (part 1)",
                        extra={"continuesOn": 16}))
    pdf.finish_page(page_label())

    L.running_header(pdf, header_text)
    cap4b = pdf.text(L.MARGIN_L, 84, "Table 4 (continued) · Operating expenses by region · USD millions",
                     font="Helvetica-Bold", size=10)
    rest = [[r, f"{v:,}"] for r, v in spec.opex[2:]]
    total = sum(v for _, v in spec.opex)
    rest.append(["Total", f"{total:,}"])
    t4b = L.table(pdf, 94, ["Region", f"Q3 FY{fy}"], rest, [L.MARGIN_L, 300.0], ["left", "right"],
                  markers={(1, 0): "2"})
    fn2y = t4b.box.y1 + 18
    fm2 = pdf.superscript(L.MARGIN_L, fn2y, "2")
    ft2 = pdf.text(L.MARGIN_L + 5, fn2y, "Excludes discontinued operations.", size=8)
    fn2 = union([fm2, ft2])
    gold.add(GoldRegion(region_id="qr-table4-part2", kind="table", page=16, bbox=union([cap4b, t4b.box, fn2]),
                        role="table", label="Table 4 · Operating expenses by region (part 2)",
                        extra={"continuesFrom": 15}))
    gold.add(GoldRegion(region_id="qr-table4-footnote", kind="footnote", page=16, bbox=fn2, role="footnote",
                        label="Table 4 footnote 2", value="Excludes discontinued operations.",
                        extra={"marker": "2", "markerRow": "Corporate", "markerPage": 16}))
    for i, (region, v) in enumerate(spec.opex):
        page = 15 if i < 2 else 16
        cells = t4a.cells[i] if i < 2 else t4b.cells[i - 2]
        gold.add(GoldRegion(region_id=f"qr-table4-{region.lower()}", kind="field", page=page,
                            bbox=union(cells), role="table_cell", label=f"{region} operating expenses",
                            value=f"{v:,}", value_number=f"{v}.00", concept="fin:OperatingExpense",
                            field_id=f"f-{region.lower()}", period=spec.label, currency="USD",
                            extra={"scale": "millions"}))
    oh, y = L.heading(pdf, fn2y + 34, "4. Outlook")
    gold.add(GoldRegion(region_id="qr-sec-p16", kind="header", page=16, bbox=oh, role="section_header",
                        label="4. Outlook"))
    L.prose(pdf, y, _filler(spec, 16, 2))
    pdf.finish_page(page_label())
    pdf.save()
    if gold.page_count != PAGES:
        raise RuntimeError(f"quarterly report has {gold.page_count} pages, expected {PAGES}")
    gold.attributes = {"fiscalYear": fy, "revenue": list(spec.revenue), "segments": dict(spec.segments),
                       "customerAccountsMillions": spec.customer_accounts_m, "opex": dict(spec.opex),
                       "opexTotal": total}
    return gold

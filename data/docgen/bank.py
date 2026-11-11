"""Two-page bank statement: account header (full account number, held by the PII gate),
a balance chart (untitled sparkline by default: the I1 example; titled with printed data labels in the
figure-question variant), a deposits table on page 1 and other transactions on page 2."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from evidence_core.bbox import BBox, union

from .common import GoldDoc, GoldRegion, PdfDoc, d, money, plain


@dataclass(frozen=True)
class Txn:
    day: int
    description: str
    amount: Decimal  # signed


@dataclass(frozen=True)
class BankSpec:
    file_name: str
    holders: tuple[str, ...]
    account_number: str  # 14 digits, random, no routing number printed
    year: int
    month: int
    month_label: str
    last_day: int
    opening: Decimal
    deposits: tuple[Txn, ...]
    others: tuple[Txn, ...]
    titled_chart: bool = False

    @property
    def period_text(self) -> str:
        return f"01–{self.last_day:02d} {self.month_label} {self.year}"

    @property
    def period_code(self) -> str:
        return f"{self.year}-{self.month:02d}"

    def daily_balances(self) -> list[Decimal]:
        bal = d(self.opening)
        out = []
        for day in range(1, self.last_day + 1):
            for t in (*self.deposits, *self.others):
                if t.day == day:
                    bal = d(bal + t.amount)
            out.append(bal)
        return out


def _txn_table(pdf: PdfDoc, gold: GoldDoc, spec: BankSpec, *, top: float, title: str, rows: tuple[Txn, ...],
               region_prefix: str, concept: str | None, unit_label: str) -> float:
    font_b = "Helvetica-Bold"
    sec = pdf.text(48, top, title, font=font_b, size=12)
    y = top + 12
    pdf.rect(48, y, 564, y + 20, fill=0.9)
    heads = [pdf.text(60, y + 14, "Date", font=font_b, size=9),
             pdf.text(150, y + 14, "Description", font=font_b, size=9),
             pdf.text(552, y + 14, "Amount (USD)", font=font_b, size=9, align="right")]
    table_top = y
    y += 20
    for i, t in enumerate(rows, start=1):
        date = f"{spec.year}-{spec.month:02d}-{t.day:02d}"
        db = pdf.text(60, y + 14, date, size=9.5)
        sb = pdf.text(150, y + 14, t.description, size=9.5)
        ab = pdf.text(552, y + 14, money(t.amount), size=9.5, align="right")
        gold.add(GoldRegion(region_id=f"{region_prefix}-row{i:02d}", kind="field", page=pdf.page,
                            bbox=union([db, sb, ab]), role="table_cell", label=t.description, value=money(t.amount),
                            value_number=plain(t.amount), concept=concept, field_id=f"f-row{i:02d}",
                            unit_ref=region_prefix, period=spec.period_code, currency="USD",
                            period_start=date, period_end=date, value_bbox=ab, label_bbox=sb))
        y += 20
    tbl = pdf.rect(48, table_top, 564, y)
    gold.add(GoldRegion(region_id=region_prefix, kind="unit", page=pdf.page, bbox=union([sec, tbl, *heads]),
                        role="table", label=unit_label, period=spec.period_code, extra={"unitType": "table_panel"}))
    return y


def generate(spec: BankSpec, out_dir: Path) -> GoldDoc:
    title = f"Account Statement {spec.period_text}"
    gold = GoldDoc(file_name=spec.file_name, doc_type="doc:BankStatement", route="digital", title=title)
    pdf = PdfDoc(out_dir / spec.file_name, gold, title=title)
    font_b = "Helvetica-Bold"
    balances = spec.daily_balances()
    closing = balances[-1]
    period_start = f"{spec.year}-{spec.month:02d}-01"
    period_end = f"{spec.year}-{spec.month:02d}-{spec.last_day:02d}"

    # ---- page 1: header
    t1 = pdf.text(48, 60, "NewAge Global Fintech · Deposit Services", font=font_b, size=14)
    t2 = pdf.text(564, 60, "Account Statement", font=font_b, size=14, align="right")
    gold.add(GoldRegion(region_id="bs-title", kind="header", page=1, bbox=union([t1, t2]), role="title",
                        label="Account Statement", value="NewAge Global Fintech · Deposit Services"))
    pdf.rect(48, 76, 564, 168)
    kv = [
        ("Account holders", " & ".join(spec.holders), "f-holders", None, None),
        ("Account number", spec.account_number, "f-account-number", "fin:BankAccountNumber", "held"),
        ("Statement period", spec.period_text, "f-statement-period", "fin:StatementPeriod", None),
        ("Opening balance", money(spec.opening), "f-opening-balance", "fin:OpeningBalance", None),
        ("Closing balance", money(closing), "f-closing-balance", "fin:ClosingBalance", None),
    ]
    kv_boxes: list[BBox] = []
    for i, (label, value, fid, concept, pii) in enumerate(kv):
        col_x = 60 if i < 3 else 340
        row_y = 96 + (i if i < 3 else i - 3) * 26
        lb = pdf.text(col_x, row_y, label, size=8)
        vb = pdf.text(col_x, row_y + 13, value, font=font_b, size=10.5)
        kv_boxes.extend([lb, vb])
        number = plain(value.replace(",", "")) if fid.endswith("balance") else None
        gold.add(GoldRegion(region_id=f"bs-{fid.removeprefix('f-')}", kind="field", page=1, bbox=union([lb, vb]),
                            role="key_value", label=label, value=value, value_number=number, concept=concept,
                            field_id=fid, unit_ref="bs-account-header", period=spec.period_code,
                            period_start=period_start, period_end=period_end, pii=pii, label_bbox=lb, value_bbox=vb,
                            currency="USD" if number else None))
    gold.add(GoldRegion(region_id="bs-account-header", kind="unit", page=1,
                        bbox=BBox(page=1, x0=48, y0=76, x1=564, y1=168), role="key_value",
                        label="Bank statement · Account header", period=spec.period_code, pii="held",
                        period_start=period_start, period_end=period_end,
                        extra={"unitType": "field_group", "heldBecause": "unmasked 14-digit account number"}))

    # ---- page 1: balance chart
    cx0, cy0, cx1, cy1 = 60.0, 196.0, 552.0, 286.0
    lo, hi = min(balances), max(balances)
    span = (hi - lo) or Decimal(1)
    pts = []
    for i, b in enumerate(balances):
        x = cx0 + (cx1 - cx0) * i / (len(balances) - 1)
        y = cy1 - float((b - lo) / span) * (cy1 - cy0)
        pts.append((x, y))
    chart_parts = [pdf.polyline(pts, width=1.2)]
    if spec.titled_chart:
        cap = pdf.text(48, 190, "Figure 1 · Daily balance · USD", font=font_b, size=10)
        labels = []
        for idx, name in ((0, "start"), (len(balances) - 1, "end")):
            x, y = pts[idx]
            align = "left" if idx == 0 else "right"
            labels.append(pdf.text(x + (4 if idx == 0 else -4), y - 6, money(balances[idx]), size=8, align=align))
            gold.add(GoldRegion(region_id=f"bs-chart-{name}", kind="field", page=1, bbox=labels[-1],
                                role="text", label=f"Daily balance · {name}", value=money(balances[idx]),
                                value_number=plain(balances[idx]), concept="fin:DailyBalance",
                                field_id=f"f-balance-{name}", unit_ref="bs-chart", currency="USD",
                                period=spec.period_code))
        gold.add(GoldRegion(region_id="bs-chart-caption", kind="caption", page=1, bbox=cap, role="caption",
                            label="Figure 1 · Daily balance · USD", unit_ref="bs-chart"))
        chart_box = union([cap, *chart_parts, *labels]).clamp(612, 792)
        gold.add(GoldRegion(region_id="bs-chart", kind="figure", page=1, bbox=chart_box, role="picture",
                            label="Figure 1 · Daily balance · USD", period=spec.period_code,
                            extra={"titled": True, "unitType": "figure_panel"}))
    else:
        gold.add(GoldRegion(region_id="bs-chart", kind="chart", page=1, bbox=union(chart_parts), role="picture",
                            label="untitled balance sparkline", period=spec.period_code,
                            extra={"titled": False, "i1": "no textual anchor; demoted to text when I1 enforces"}))

    # ---- page 1: deposits table
    _txn_table(pdf, gold, spec, top=318, title="Deposits", rows=spec.deposits, region_prefix="bs-deposits",
               concept="fin:BankDepositAmount", unit_label="Bank statement · Deposits")
    pdf.finish_page("Page 1 of 2")

    # ---- page 2: other transactions
    h = pdf.text(48, 60, f"Account Statement {spec.period_text} (continued)", font=font_b, size=11)
    gold.add(GoldRegion(region_id="bs-p2-header", kind="header", page=2, bbox=h, role="page_header",
                        label="continued header"))
    _txn_table(pdf, gold, spec, top=96, title="Other transactions", rows=spec.others, region_prefix="bs-others",
               concept="fin:BankWithdrawalAmount", unit_label="Bank statement · Other transactions")
    pdf.finish_page("Page 2 of 2")
    pdf.save()

    gold.attributes = {"holders": list(spec.holders), "period": spec.period_code, "opening": plain(spec.opening),
                       "closing": plain(closing), "titledChart": spec.titled_chart,
                       "accountNumberDigits": len(spec.account_number)}
    gold.notes.append("Account header holds the only full identifier in the package: held by the PII gate.")
    return gold

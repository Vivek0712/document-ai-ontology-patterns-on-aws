"""Monthly payslip (earnings statement). One layout, two renderers: digital PDF or simulated scan."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Protocol

from evidence_core.bbox import BBox, union

from .common import GoldDoc, GoldRegion, PdfDoc, d, money, plain
from .raster import RasterPage, finish_scan


class Surface(Protocol):
    page: int

    def text(self, x: float, y: float, text: str, *, font: str = ..., size: float = ...,
             align: str = ...) -> BBox: ...

    def rect(self, x0: float, y0: float, x1: float, y1: float, *, width: float = ..., fill: float | None = ...,
             stroke: bool = ...) -> BBox: ...

    def line(self, x0: float, y0: float, x1: float, y1: float, *, width: float = ...) -> None: ...


@dataclass(frozen=True)
class PayslipSpec:
    file_name: str
    employee_name: str
    employee_id: str
    employer_name: str
    employer_address: str
    year: int
    month: int  # 9 = September
    month_label: str  # "Sep"
    last_day: int
    gross: Decimal  # current period gross
    deferral: Decimal  # 401(k) employee deferral per period
    federal: Decimal  # federal income tax withheld per period
    scanned: bool = False
    scan_seed: int = 305
    skew_degrees: float = 0.8

    @property
    def months_ytd(self) -> int:
        return self.month

    def lines(self) -> dict[str, tuple[Decimal, Decimal]]:
        """name -> (current, year to date); equal pay every month of the year."""
        g = d(self.gross)
        k = d(self.deferral)
        fed = d(self.federal)
        ss = d(g * Decimal("0.062"))
        med = d(g * Decimal("0.0145"))
        net = d(g - k - fed - ss - med)
        n = self.months_ytd
        return {
            "gross": (g, d(g * n)),
            "401k": (k, d(k * n)),
            "federal": (fed, d(fed * n)),
            "ss": (ss, d(ss * n)),
            "medicare": (med, d(med * n)),
            "net": (net, d(net * n)),
        }

    @property
    def period_text(self) -> str:
        return f"01–{self.last_day:02d} {self.month_label} {self.year}"

    @property
    def pay_date_text(self) -> str:
        return f"{self.last_day:02d} {self.month_label} {self.year}"

    @property
    def period_code(self) -> str:
        return f"{self.year}-{self.month:02d}"


ROWS: tuple[tuple[str, str, str | None, str | None], ...] = (
    # key, printed description, concept(current), concept(ytd)
    ("gross", "Gross pay", "fin:PayslipGrossPay", "fin:PayslipGrossPayYtd"),
    ("401k", "401(k) employee deferral", "fin:Payslip401kDeferral", None),
    ("federal", "Federal income tax", None, None),
    ("ss", "Social Security", None, None),
    ("medicare", "Medicare", None, None),
    ("net", "Net pay", "fin:PayslipNetPay", None),
)
FIELD_IDS = {"gross": "f-gross-pay", "401k": "f-401k", "federal": "f-federal-tax", "ss": "f-social-security",
             "medicare": "f-medicare", "net": "f-net-pay"}


def _layout(s: Surface, spec: PayslipSpec, gold: GoldDoc) -> None:
    font_b = "Helvetica-Bold"
    h1 = s.text(48, 64, spec.employer_name, font=font_b, size=14)
    h2 = s.text(48, 80, spec.employer_address, size=9)
    h3 = s.text(564, 64, "Earnings Statement", font=font_b, size=14, align="right")
    gold.add(GoldRegion(region_id="ps-title", kind="header", page=1, bbox=union([h1, h2, h3]), role="title",
                        label="Earnings Statement", value=spec.employer_name, employer=spec.employer_name))

    # key/value header block
    kv = [("Employee", spec.employee_name, "f-employee", "fin:PayslipEmployeeName"),
          ("Employee ID", spec.employee_id, "f-employee-id", None),
          ("Pay period", spec.period_text, "f-pay-period", "fin:PayslipPayPeriod"),
          ("Pay date", spec.pay_date_text, "f-pay-date", "fin:PayslipPayDate")]
    s.rect(48, 100, 564, 176)
    kv_boxes = []
    y = 120.0
    period_start = f"{spec.year}-{spec.month:02d}-01"
    period_end = f"{spec.year}-{spec.month:02d}-{spec.last_day:02d}"
    for i, (label, value, fid, concept) in enumerate(kv):
        col_x = 60 if i % 2 == 0 else 312
        row_y = y + (i // 2) * 30
        lb = s.text(col_x, row_y, label, size=8)
        vb = s.text(col_x, row_y + 14, value, font=font_b, size=11)
        kv_boxes.extend([lb, vb])
        gold.add(GoldRegion(region_id=f"ps-{fid.removeprefix('f-')}", kind="field", page=1, bbox=union([lb, vb]),
                            role="key_value", label=label, value=value, field_id=fid, concept=concept,
                            unit_ref="ps-header", period=spec.period_code, period_start=period_start,
                            period_end=period_end, borrower=spec.employee_name, label_bbox=lb, value_bbox=vb))
    gold.add(GoldRegion(region_id="ps-header", kind="unit", page=1, bbox=BBox(page=1, x0=48, y0=100, x1=564, y1=176),
                        role="key_value", label="Payslip · Employee and period", period=spec.period_code,
                        period_start=period_start, period_end=period_end, borrower=spec.employee_name,
                        extra={"unitType": "field_group"}))

    # earnings and deductions table
    top = 204.0
    cols = (60.0, 400.0, 552.0)  # description left, current right edge, ytd right edge
    s.rect(48, top, 564, top + 22, fill=0.9)
    hd = [s.text(cols[0], top + 15, "Description", font=font_b, size=9),
          s.text(cols[1], top + 15, "Current", font=font_b, size=9, align="right"),
          s.text(cols[2], top + 15, "Year to date", font=font_b, size=9, align="right")]
    lines = spec.lines()
    row_h = 22.0
    y = top + 22
    for key, desc, concept_cur, concept_ytd in ROWS:
        cur, ytd = lines[key]
        bold = key in ("gross", "net")
        f = font_b if bold else "Helvetica"
        if key == "net":
            s.line(48, y, 564, y, width=1.0)
        db = s.text(cols[0], y + 15, desc, font=f, size=10)
        cb = s.text(cols[1], y + 15, money(cur), font=f, size=10, align="right")
        yb = s.text(cols[2], y + 15, money(ytd), font=f, size=10, align="right")
        fid = FIELD_IDS[key]
        common = {"borrower": spec.employee_name, "employer": spec.employer_name, "currency": "USD",
                  "unit_ref": "ps-earnings"}
        gold.add(GoldRegion(region_id=f"ps-{key}", kind="field", page=1, bbox=union([db, cb]), role="table_cell",
                            label=f"{desc} · Current", value=money(cur), value_number=plain(cur), concept=concept_cur,
                            field_id=fid, period=spec.period_code, period_start=period_start, period_end=period_end,
                            label_bbox=db, value_bbox=cb, **common))
        gold.add(GoldRegion(region_id=f"ps-{key}-ytd", kind="field", page=1, bbox=union([db, yb]), role="table_cell",
                            label=f"{desc} · Year to date", value=money(ytd), value_number=plain(ytd),
                            concept=concept_ytd, field_id=f"{fid}-ytd", period=f"YTD{spec.period_code}",
                            period_start=f"{spec.year}-01-01", period_end=period_end, label_bbox=db, value_bbox=yb,
                            **common))
        y += row_h
    table = s.rect(48, top, 564, y)
    gold.add(GoldRegion(region_id="ps-earnings", kind="unit", page=1, bbox=union([table, *hd]), role="table",
                        label="Payslip · Earnings and deductions", period=spec.period_code,
                        period_start=period_start, period_end=period_end, borrower=spec.employee_name,
                        extra={"unitType": "table_panel", "rows": [r[1] for r in ROWS]}))
    note = s.text(48, y + 24, f"Year-to-date figures cover 01 Jan {spec.year} to {spec.pay_date_text}.", size=8)
    gold.add(GoldRegion(region_id="ps-note", kind="text", page=1, bbox=note, role="text",
                        label="year-to-date note", value=f"YTD to {spec.pay_date_text}"))


def generate(spec: PayslipSpec, out_dir: Path) -> GoldDoc:
    title = f"Earnings Statement {spec.period_text} {spec.employee_name}"
    gold = GoldDoc(file_name=spec.file_name, doc_type="doc:Payslip", route="scan" if spec.scanned else "digital",
                   title=title)
    lines = spec.lines()
    gold.attributes = {"employee": spec.employee_name, "employer": spec.employer_name, "period": spec.period_code,
                       "grossCurrent": plain(lines["gross"][0]), "grossYtd": plain(lines["gross"][1]),
                       "netCurrent": plain(lines["net"][0])}
    if spec.scanned:
        page = RasterPage(gold, dpi=150)
        _layout(page, spec, gold)
        finish_scan(page, out_dir / spec.file_name, skew_degrees=spec.skew_degrees, seed=spec.scan_seed)
        gold.notes.append(f"Simulated scan: 150 dpi raster, {spec.skew_degrees}° skew, light noise (seed "
                          f"{spec.scan_seed}); no text layer.")
    else:
        pdf = PdfDoc(out_dir / spec.file_name, gold, title=title)
        _layout(pdf, spec, gold)
        pdf.finish_page()
        pdf.save()
    return gold

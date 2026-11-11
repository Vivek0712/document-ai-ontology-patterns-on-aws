"""Form W-2 (Copy C, employee's records), printed the way payroll software often prints it.

The form template (grid and box labels) is drawn first; the payroll values are drawn afterwards
as an overlay layer in the overlay field order (default 12a, 3, 5, 1, 2, 4, 6). Stream-order text
extraction (pypdf default mode) therefore delivers the label block followed by a run of values,
so values arrive detached from their box labels. Layout-aware extraction uses geometry and keeps
each value in its box. This is a data lever, disclosed in the ledger; nothing in any pipeline
is tuned for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from evidence_core.bbox import BBox, union

from .common import PAGE_W, GoldDoc, GoldRegion, PdfDoc, d, money, plain

OVERLAY_ORDER_DEFAULT: tuple[str, ...] = ("12a", "3", "5", "1", "2", "4", "6")

# Box key -> (printed label, fin: concept, fieldId)
BOX_LABELS: dict[str, tuple[str, str | None, str]] = {
    "1": ("1 Wages, tips, other compensation", "fin:W2WagesBox1", "f-box1"),
    "2": ("2 Federal income tax withheld", "fin:W2FederalIncomeTaxWithheldBox2", "f-box2"),
    "3": ("3 Social security wages", "fin:W2SocialSecurityWagesBox3", "f-box3"),
    "4": ("4 Social security tax withheld", "fin:W2SocialSecurityTaxWithheldBox4", "f-box4"),
    "5": ("5 Medicare wages and tips", "fin:W2MedicareWagesBox5", "f-box5"),
    "6": ("6 Medicare tax withheld", "fin:W2MedicareTaxWithheldBox6", "f-box6"),
    "7": ("7 Social security tips", None, "f-box7"),
    "8": ("8 Allocated tips", None, "f-box8"),
    "9": ("9", None, "f-box9"),
    "10": ("10 Dependent care benefits", None, "f-box10"),
    "11": ("11 Nonqualified plans", None, "f-box11"),
    "12a": ("12a See instructions for box 12", "fin:W2Box12Amount", "f-box12a"),
    "12b": ("12b", None, "f-box12b"),
    "12c": ("12c", None, "f-box12c"),
    "12d": ("12d", None, "f-box12d"),
    "13": ("13 Retirement plan", None, "f-box13"),
    "14": ("14 Other", None, "f-box14"),
}

# Right-hand grid: row -> (left box, right box)
RIGHT_ROWS: tuple[tuple[str, str], ...] = (
    ("1", "2"), ("3", "4"), ("5", "6"), ("7", "8"), ("9", "10"), ("11", "12a"), ("13", "12b"), ("14", "12c"),
    ("", "12d"),
)
WAGE_BOXES = ("1", "2", "3", "4", "5", "6")

X_LEFT, X_MID, X_RCOL, X_RIGHT = 36.0, 306.0, 441.0, 576.0
GRID_TOP = 96.0
ROW_H = 36.0
LABEL_FONT, LABEL_SIZE = "Helvetica", 6.5
VALUE_FONT, VALUE_SIZE = "Courier", 10.0


@dataclass(frozen=True)
class W2Spec:
    file_name: str
    tax_year: int
    employee_name: str
    employee_address: tuple[str, str]
    ssn_last4: str
    employer_name: str
    employer_address: tuple[str, str]
    ein: str  # prefix not issued by the IRS ("00-")
    control_number: str
    gross: Decimal  # social security / medicare wages (boxes 3 and 5)
    deferral_401k: Decimal  # box 12a code D
    federal_tax: Decimal  # box 2
    state: str = "WA"  # no state income tax: boxes 16/17 stay blank
    overlay_order: tuple[str, ...] = OVERLAY_ORDER_DEFAULT

    @property
    def boxes(self) -> dict[str, Decimal]:
        box1 = d(self.gross - self.deferral_401k)
        return {
            "1": box1,
            "2": d(self.federal_tax),
            "3": d(self.gross),
            "4": d(self.gross * Decimal("0.062")),
            "5": d(self.gross),
            "6": d(self.gross * Decimal("0.0145")),
            "12a": d(self.deferral_401k),
        }


def _cells() -> dict[str, BBox]:
    """Canonical boxes of every right-hand grid cell, page 1."""
    cells: dict[str, BBox] = {}
    for i, (left, right) in enumerate(RIGHT_ROWS):
        y0 = GRID_TOP + i * ROW_H
        if left:
            cells[left] = BBox(page=1, x0=X_MID, y0=y0, x1=X_RCOL, y1=y0 + ROW_H)
        cells[right] = BBox(page=1, x0=X_RCOL, y0=y0, x1=X_RIGHT, y1=y0 + ROW_H)
    return cells


def generate(spec: W2Spec, out_dir: Path) -> GoldDoc:
    gold = GoldDoc(file_name=spec.file_name, doc_type="doc:W2", route="digital",
                   title=f"Form W-2 Wage and Tax Statement {spec.tax_year}")
    pdf = PdfDoc(out_dir / spec.file_name, gold, title=gold.title)
    boxes = spec.boxes
    cells = _cells()
    period = f"TY{spec.tax_year}"
    common_fact = {
        "period": period,
        "tax_year": str(spec.tax_year),
        "period_start": f"{spec.tax_year}-01-01",
        "period_end": f"{spec.tax_year}-12-31",
        "borrower": spec.employee_name,
        "employer": spec.employer_name,
    }

    # ---- 1. template layer: title, grid, every label (in template order) -------------------
    t1 = pdf.text(X_LEFT, 52, "Form W-2", font="Helvetica-Bold", size=16)
    t2 = pdf.text(X_LEFT + 84, 52, "Wage and Tax Statement", font="Helvetica-Bold", size=12)
    t3 = pdf.text(X_RIGHT, 52, str(spec.tax_year), font="Helvetica-Bold", size=16, align="right")
    t4 = pdf.text(X_LEFT, 70, "Copy C—For EMPLOYEE'S RECORDS (See Notice to Employee on the back of Copy B.)",
                  font="Helvetica", size=7.5)
    t5 = pdf.text(X_RIGHT, 70, "Department of the Treasury—Internal Revenue Service (form layout, fictitious data)",
                  font="Helvetica", size=6.5, align="right")
    gold.add(GoldRegion(region_id="w2-title", kind="header", page=1, bbox=union([t1, t2, t3, t4, t5]), role="title",
                        label="Form W-2 Wage and Tax Statement", value=str(spec.tax_year)))

    grid_bottom = GRID_TOP + len(RIGHT_ROWS) * ROW_H
    left_rows = [("a", 1), ("b", 1), ("c", 3), ("d", 1), ("ef", 3)]
    left_cells: dict[str, BBox] = {}
    y = GRID_TOP
    for key, span in left_rows:
        left_cells[key] = pdf.rect(X_LEFT, y, X_MID, y + span * ROW_H)
        y += span * ROW_H
    for cell in cells.values():
        pdf.rect(cell.x0, cell.y0, cell.x1, cell.y1)
    pdf.rect(X_MID, GRID_TOP + 8 * ROW_H, X_RCOL, grid_bottom)  # empty cell under box 14
    state_top = grid_bottom
    state_cols = [(X_LEFT, 120.0), (120.0, 306.0), (306.0, 441.0), (441.0, X_RIGHT)]
    for x0, x1 in state_cols:
        pdf.rect(x0, state_top, x1, state_top + ROW_H)

    left_labels = {
        "a": "a Employee's social security number",
        "b": "b Employer identification number (EIN)",
        "c": "c Employer's name, address, and ZIP code",
        "d": "d Control number",
        "ef": "e Employee's first name and initial   Last name      f Employee's address and ZIP code",
    }
    label_boxes: dict[str, BBox] = {}
    for key, cell in left_cells.items():
        label_boxes[key] = pdf.text(cell.x0 + 3, cell.y0 + 9, left_labels[key], font=LABEL_FONT, size=LABEL_SIZE)
    for left, right in RIGHT_ROWS:
        for key in (left, right):
            if key:
                cell = cells[key]
                label_boxes[key] = pdf.text(cell.x0 + 3, cell.y0 + 9, BOX_LABELS[key][0], font=LABEL_FONT,
                                            size=LABEL_SIZE)
    state_labels = ["15 State", "Employer's state ID number", "16 State wages, tips, etc.", "17 State income tax"]
    for (x0, _x1), lab in zip(state_cols, state_labels, strict=True):
        pdf.text(x0 + 3, state_top + 9, lab, font=LABEL_FONT, size=LABEL_SIZE)
    code_label = pdf.text(cells["12a"].x0 + 3, cells["12a"].y0 + 18, "Code", font=LABEL_FONT, size=LABEL_SIZE)

    # ---- 2. overlay layer: payroll values in overlay order, then the rest ---------------------
    value_boxes: dict[str, BBox] = {}
    for key in spec.overlay_order:
        cell = cells[key]
        value_boxes[key] = pdf.text(cell.x1 - 8, cell.y1 - 8, money(boxes[key]), font=VALUE_FONT, size=VALUE_SIZE,
                                    align="right")
        if key == "12a":
            value_boxes["12a-code"] = pdf.text(cell.x0 + 8, cell.y1 - 8, "D", font=VALUE_FONT, size=VALUE_SIZE)
    x13 = pdf.text(cells["13"].x0 + 12, cells["13"].y1 - 8, "X", font=VALUE_FONT, size=VALUE_SIZE)
    ssn_text = f"XXX-XX-{spec.ssn_last4}"
    ssn_box = pdf.text(left_cells["a"].x0 + 8, left_cells["a"].y1 - 8, ssn_text, font=VALUE_FONT, size=VALUE_SIZE)
    ein_box = pdf.text(left_cells["b"].x0 + 8, left_cells["b"].y1 - 8, spec.ein, font=VALUE_FONT, size=VALUE_SIZE)
    emp_lines = [spec.employer_name, *spec.employer_address]
    emp_boxes = [pdf.text(left_cells["c"].x0 + 8, left_cells["c"].y0 + 26 + 13 * i, line, font=VALUE_FONT,
                          size=VALUE_SIZE) for i, line in enumerate(emp_lines)]
    ctl_box = pdf.text(left_cells["d"].x0 + 8, left_cells["d"].y1 - 8, spec.control_number, font=VALUE_FONT,
                       size=VALUE_SIZE)
    ee_lines = [spec.employee_name, *spec.employee_address]
    ee_boxes = [pdf.text(left_cells["ef"].x0 + 8, left_cells["ef"].y0 + 26 + 13 * i, line, font=VALUE_FONT,
                         size=VALUE_SIZE) for i, line in enumerate(ee_lines)]
    state_box = pdf.text(state_cols[0][0] + 8, state_top + ROW_H - 8, spec.state, font=VALUE_FONT, size=VALUE_SIZE)

    # ---- gold regions ------------------------------------------------------------------------
    wage_panel = union([cells[k] for k in WAGE_BOXES])
    gold.add(GoldRegion(region_id="w2-wages-panel", kind="unit", page=1, bbox=wage_panel, role="table",
                        label="W-2 · Boxes 1–6", period=period, tax_year=str(spec.tax_year),
                        borrower=spec.employee_name, employer=spec.employer_name,
                        extra={"unitType": "table_panel", "boxes": list(WAGE_BOXES)}))
    other_keys = [k for row in RIGHT_ROWS[3:] for k in row if k]
    gold.add(GoldRegion(region_id="w2-other-boxes", kind="unit", page=1,
                        bbox=union([cells[k] for k in other_keys]), role="table", label="W-2 · Boxes 7–14",
                        period=period, extra={"unitType": "table_panel", "boxes": other_keys}))
    for key in (*WAGE_BOXES, "12a"):
        label, concept, fid = BOX_LABELS[key]
        parts = [label_boxes[key], value_boxes[key]] + ([value_boxes["12a-code"], code_label] if key == "12a" else [])
        gold.add(GoldRegion(
            region_id=f"w2-box{key}", kind="field", page=1, bbox=union(parts), role="table_cell",
            label=label, value=money(boxes[key]), value_number=plain(boxes[key]), concept=concept, field_id=fid,
            unit_ref="w2-wages-panel" if key in WAGE_BOXES else "w2-other-boxes", currency="USD",
            label_bbox=label_boxes[key], value_bbox=value_boxes[key], cell_bbox=cells[key],
            extra={"code": "D"} if key == "12a" else {}, **common_fact,
        ))
    gold.add(GoldRegion(region_id="w2-box13", kind="field", page=1, bbox=union([label_boxes["13"], x13]),
                        role="table_cell", label=BOX_LABELS["13"][0], value="X (Retirement plan)",
                        field_id="f-box13", unit_ref="w2-other-boxes", cell_bbox=cells["13"]))
    identity = union(list(left_cells.values()))
    gold.add(GoldRegion(region_id="w2-identity", kind="unit", page=1, bbox=identity, role="key_value",
                        label="W-2 · Employee and employer (boxes a–f)", borrower=spec.employee_name,
                        employer=spec.employer_name, pii="masked", extra={"unitType": "field_group"}))
    gold.add(GoldRegion(region_id="w2-ssn", kind="field", page=1, bbox=union([label_boxes["a"], ssn_box]),
                        role="key_value", label=left_labels["a"], value=ssn_text, field_id="f-ssn",
                        unit_ref="w2-identity", pii="masked", value_bbox=ssn_box, cell_bbox=left_cells["a"]))
    gold.add(GoldRegion(region_id="w2-ein", kind="field", page=1, bbox=union([label_boxes["b"], ein_box]),
                        role="key_value", label=left_labels["b"], value=spec.ein, field_id="f-ein",
                        unit_ref="w2-identity", value_bbox=ein_box, cell_bbox=left_cells["b"]))
    gold.add(GoldRegion(region_id="w2-employer", kind="field", page=1, bbox=union([label_boxes["c"], *emp_boxes]),
                        role="key_value", label=left_labels["c"], value=spec.employer_name, field_id="f-employer",
                        concept="fin:W2EmployerName", unit_ref="w2-identity", employer=spec.employer_name,
                        value_bbox=emp_boxes[0], cell_bbox=left_cells["c"]))
    gold.add(GoldRegion(region_id="w2-control", kind="field", page=1, bbox=union([label_boxes["d"], ctl_box]),
                        role="key_value", label=left_labels["d"], value=spec.control_number, field_id="f-control",
                        unit_ref="w2-identity", cell_bbox=left_cells["d"]))
    gold.add(GoldRegion(region_id="w2-employee", kind="field", page=1, bbox=union([label_boxes["ef"], *ee_boxes]),
                        role="key_value", label="e Employee's first name and initial   Last name",
                        value=spec.employee_name, field_id="f-employee", concept="fin:W2EmployeeName",
                        unit_ref="w2-identity", borrower=spec.employee_name, value_bbox=ee_boxes[0],
                        cell_bbox=left_cells["ef"]))
    gold.add(GoldRegion(region_id="w2-state", kind="unit", page=1,
                        bbox=BBox(page=1, x0=X_LEFT, y0=state_top, x1=X_RIGHT, y1=state_top + ROW_H),
                        role="table", label="W-2 · State (boxes 15–17)", value=spec.state,
                        extra={"unitType": "table_panel", "stateBox": state_box.dump()}))
    gold.attributes = {
        "taxYear": spec.tax_year,
        "employee": spec.employee_name,
        "employer": spec.employer_name,
        "overlayOrder": list(spec.overlay_order),
        "boxes": {k: plain(v) for k, v in boxes.items()},
        "box12aCode": "D",
        "pageWidth": PAGE_W,
    }
    gold.notes.append(
        "Constructed: template labels drawn first, values drawn afterwards as an overlay in order "
        + ", ".join(spec.overlay_order) + "."
    )
    pdf.finish_page()
    pdf.save()
    return gold

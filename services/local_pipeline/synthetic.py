"""Small synthetic lending PDFs for tests and ``make units --synthetic`` (dev only).

These are NOT the generator's corpus (``data/``). They reproduce the shapes the pipeline
must handle: a W-2 whose values are drawn in overlay order (12a, 3, 5, 1, 2, 4, 6) so the text layer
detaches values from labels, a digital payslip, an image-only scanned payslip (no text layer), and a
bank statement whose header carries a full 14-digit account number. All data is fictitious; SSNs are
truncated.
"""

from __future__ import annotations

import io
from pathlib import Path

FOOTER = "Fictitious document · NewAge Global Fintech · for demonstration only"
W2_BOXES = [("1 Wages, tips, other compensation", "98,400.00"), ("2 Federal income tax withheld", "13,776.00"),
            ("3 Social security wages", "105,000.00"), ("4 Social security tax withheld", "6,510.00"),
            ("5 Medicare wages and tips", "105,000.00"), ("6 Medicare tax withheld", "1,522.50")]
OVERLAY_ORDER = ["12a", "3", "5", "1", "2", "4", "6"]
ACCOUNT_NUMBER = "40917305583216"  # random, fictitious, no routing number printed


def _canvas():  # noqa: ANN202
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    return buf, canvas.Canvas(buf, pagesize=letter), letter


def _footer(c, w: float) -> None:  # noqa: ANN001
    c.setFont("Helvetica", 7)
    c.drawString(50, 30, FOOTER)


def w2_pdf(name: str = "Maya Fictus", employer: str = "NewAge Global Fintech Services", ssn_last4: str = "0417",
           boxes: list[tuple[str, str]] | None = None, box12a: str = "6,600.00") -> bytes:
    buf, c, (w, h) = _canvas()
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, h - 60, "Form W-2 Wage and Tax Statement 2025")
    c.setFont("Helvetica", 9)
    c.drawString(50, h - 80, f"Employee: {name}   SSN XXX-XX-{ssn_last4}   Employer: {employer}")
    boxes = boxes or W2_BOXES
    x0, y0, cw, ch = 50, h - 216, 256, 34
    cells = {}
    for i, (label, value) in enumerate(boxes):
        col, row = i % 2, i // 2
        x, y = x0 + col * cw, y0 - row * ch
        c.rect(x, y - ch, cw, ch)
        cells[label.split(" ", 1)[0]] = (x, y, label, value)
    for _, (x, y, label, _) in cells.items():  # labels first
        c.drawString(x + 4, y - 12, label)
    y12 = y0 - 3 * ch
    c.rect(x0, y12 - ch, cw, ch)
    c.drawString(x0 + 4, y12 - 12, "12a See instructions for box 12")
    c.setFont("Helvetica", 10)
    for key in OVERLAY_ORDER:  # values in overlay order: the content stream detaches them from labels
        if key == "12a":
            c.drawRightString(x0 + cw - 6, y12 - 28, f"D {box12a}")
            continue
        x, y, _, value = cells[key]
        c.drawRightString(x + cw - 6, y - 28, value)
    _footer(c, w)
    c.save()
    return buf.getvalue()


def payslip_pdf(name: str = "Maya Fictus", gross: str = "9,000.00", k401: str = "600.00",
                ytd: str = "81,000.00") -> bytes:
    buf, c, (w, h) = _canvas()
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, h - 60, "Payslip · NewAge Global Fintech Payments")
    c.setFont("Helvetica", 10)
    c.drawString(50, h - 82, f"Employee: {name}")
    c.drawString(50, h - 96, "Pay period 01–30 Sep 2026   Pay date 30 Sep 2026")
    rows = [("Gross pay", gross), ("401(k) deferral", k401), ("YTD gross", ytd)]
    y = h - 140
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Earnings")
    c.setFont("Helvetica", 10)
    for label, value in rows:
        y -= 18
        c.drawString(60, y, label)
        c.drawRightString(400, y, value)
    _footer(c, w)
    c.save()
    return buf.getvalue()


def scanned_pdf(source_pdf: bytes, dpi: int = 150) -> bytes:
    """Rasterise page 1 of ``source_pdf`` and wrap it as an image-only PDF (no text layer)."""
    import pypdfium2 as pdfium
    from reportlab.lib.utils import ImageReader

    doc = pdfium.PdfDocument(source_pdf)
    try:
        img = doc[0].render(scale=dpi / 72).to_pil().convert("L").rotate(0.8, fillcolor=255, expand=False)
    finally:
        doc.close()
    buf, c, (w, h) = _canvas()
    png = io.BytesIO()
    img.save(png, format="PNG")
    png.seek(0)
    c.drawImage(ImageReader(png), 0, 0, width=w, height=h)
    c.save()
    return buf.getvalue()


def bank_statement_pdf() -> bytes:
    buf, c, (w, h) = _canvas()
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, h - 60, "Bank statement")
    c.setFont("Helvetica", 10)
    c.drawString(50, h - 84, "Account holders: Maya Fictus and Arjun Fictus")
    c.drawString(50, h - 98, f"Account number {ACCOUNT_NUMBER}")
    c.drawString(50, h - 112, "Statement period 01–30 Sep 2026")
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, h - 160, "Deposits")
    c.setFont("Helvetica", 10)
    rows = [("30 Sep 2026", "Payroll NewAge Global Fintech Services", "6,612.40"),
            ("30 Sep 2026", "Payroll NewAge Global Fintech Payments", "4,873.15")]
    y = h - 180
    for d, desc, amt in rows:
        c.drawString(50, y, d)
        c.drawString(140, y, desc)
        c.drawRightString(520, y, amt)
        y -= 16
    _footer(c, w)
    c.showPage()
    c.setFont("Helvetica-Bold", 11)
    c.drawString(50, h - 60, "Other transactions")
    c.setFont("Helvetica", 10)
    c.drawString(50, h - 80, "02 Sep 2026   Grocery market   -182.40")
    _footer(c, w)
    c.save()
    return buf.getvalue()


def write_package(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    arjun_payslip = payslip_pdf(name="Arjun Fictus", gross="6,500.00", k401="400.00", ytd="58,500.00")
    files = {
        "w2_2025_maya_fictus.pdf": w2_pdf(),
        "w2_2025_arjun_fictus.pdf": w2_pdf(
            name="Arjun Fictus", employer="NewAge Global Fintech Payments", ssn_last4="2290", box12a="3,750.00",
            boxes=[("1 Wages, tips, other compensation", "71,250.00"), ("2 Federal income tax withheld", "8,550.00"),
                   ("3 Social security wages", "75,000.00"), ("4 Social security tax withheld", "4,650.00"),
                   ("5 Medicare wages and tips", "75,000.00"), ("6 Medicare tax withheld", "1,087.50")]),
        "payslip_2026-09_maya_fictus.pdf": payslip_pdf(),
        "payslip_2026-09_arjun_fictus_scan.pdf": scanned_pdf(arjun_payslip),
        "bank_statement_2026-09.pdf": bank_statement_pdf(),
    }
    paths = []
    for name, data in files.items():
        p = out_dir / name
        p.write_bytes(data)
        paths.append(p)
    return paths

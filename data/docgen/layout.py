"""Reusable page furniture for the multi-page reports: running header, prose, tables, bar charts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from evidence_core.bbox import BBox, union

from .common import PdfDoc

MARGIN_L, MARGIN_R = 60.0, 552.0
TEXT_W = MARGIN_R - MARGIN_L


@dataclass
class TableResult:
    box: BBox
    header: list[BBox]
    cells: list[list[BBox]]  # rows x cols, glyph boxes of each cell's text
    row_boxes: list[BBox]


def running_header(pdf: PdfDoc, text: str) -> BBox:
    box = pdf.text(MARGIN_L, 40, text, font="Helvetica", size=8)
    pdf.line(MARGIN_L, 46, MARGIN_R, 46, width=0.4)
    return box


def heading(pdf: PdfDoc, y: float, text: str, *, level: int = 1) -> tuple[BBox, float]:
    size = {1: 15.0, 2: 12.0}[level]
    box = pdf.text(MARGIN_L, y, text, font="Helvetica-Bold", size=size)
    return box, y + size * 1.9


def prose(pdf: PdfDoc, y: float, paragraphs: Sequence[str], *, size: float = 10.0) -> tuple[list[BBox], float]:
    boxes = []
    for p in paragraphs:
        box, y = pdf.paragraph(MARGIN_L, y, p, width=TEXT_W, size=size)
        boxes.append(box)
        y += size * 0.8
    return boxes, y


def table(pdf: PdfDoc, y: float, header: Sequence[str], rows: Sequence[Sequence[str]], col_x: Sequence[float],
          aligns: Sequence[str], *, row_h: float = 20.0, size: float = 9.5,
          markers: dict[tuple[int, int], str] | None = None) -> TableResult:
    """Ruled table. ``col_x`` is the anchor per column (left x for 'left', right edge for 'right').

    ``markers`` maps (row, col) to a superscript marker drawn after that cell's text.
    """
    markers = markers or {}
    top = y
    pdf.rect(MARGIN_L - 6, y, MARGIN_R, y + row_h, fill=0.9)
    head = [pdf.text(x, y + row_h - 6, h, font="Helvetica-Bold", size=size, align=a)
            for h, x, a in zip(header, col_x, aligns, strict=True)]
    y += row_h
    cells: list[list[BBox]] = []
    row_boxes: list[BBox] = []
    for r, row in enumerate(rows):
        boxes = []
        for c, (val, x, a) in enumerate(zip(row, col_x, aligns, strict=True)):
            b = pdf.text(x, y + row_h - 6, val, size=size, align=a)
            if (r, c) in markers:
                m = pdf.superscript(b.x1 + 0.8, y + row_h - 6, markers[(r, c)])
                b = union([b, m])
            boxes.append(b)
        cells.append(boxes)
        pdf.line(MARGIN_L - 6, y + row_h, MARGIN_R, y + row_h, width=0.3)
        row_boxes.append(BBox(page=pdf.page, x0=MARGIN_L - 6, y0=y, x1=MARGIN_R, y1=y + row_h))
        y += row_h
    box = pdf.rect(MARGIN_L - 6, top, MARGIN_R, y)
    return TableResult(box=box, header=head, cells=cells, row_boxes=row_boxes)


@dataclass
class BarChartResult:
    box: BBox
    bars: list[BBox]
    value_labels: list[BBox]
    category_labels: list[BBox]


def bar_chart(pdf: PdfDoc, y: float, categories: Sequence[str], values: Sequence[float], labels: Sequence[str], *,
              height: float = 150.0, x0: float = 110.0, x1: float = 500.0) -> BarChartResult:
    vmax = max(values)
    n = len(categories)
    slot = (x1 - x0) / n
    base = y + height
    pdf.line(x0 - 10, base, x1 + 10, base, width=0.8)
    bars, vls, cls = [], [], []
    for i, (cat, v, lab) in enumerate(zip(categories, values, labels, strict=True)):
        bw = slot * 0.5
        bx0 = x0 + i * slot + (slot - bw) / 2
        bh = (height - 24) * v / vmax
        bars.append(pdf.rect(bx0, base - bh, bx0 + bw, base, fill=0.55, width=0.4))
        vls.append(pdf.text(bx0 + bw / 2, base - bh - 5, lab, font="Helvetica-Bold", size=9, align="center"))
        cls.append(pdf.text(bx0 + bw / 2, base + 13, cat, size=9, align="center"))
    box = union([*bars, *vls, *cls])
    return BarChartResult(box=box, bars=bars, value_labels=vls, category_labels=cls)

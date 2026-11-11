"""Shared drawing helpers and gold-region records for the fictitious document generator.

Every coordinate this module records is in the canonical frame: PDF points,
top-left origin, 1-based pages. reportlab draws in bottom-left points, so every helper
converts at the moment it records a region; generator code never handles a flipped y.
"""

from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

from evidence_core.bbox import BBox, union
from reportlab import rl_config
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas

rl_config.invariant = 1  # no timestamps or random document IDs: byte-identical output per seed

PAGE_W, PAGE_H = letter  # 612 x 792 points
FOOTER_TEXT = "Fictitious document · NewAge Global Fintech · for demonstration only"
GENERATOR_VERSION = "1.0.0"
ORANGE = (1.0, 0.384, 0.0)  # #FF6200, unused in documents; kept for debug overlays


# --------------------------------------------------------------------------- numbers


def d(value: str | int | float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money(value: Decimal | str | int | float) -> str:
    """``98400`` -> ``98,400.00`` (as printed on every document)."""
    return f"{d(value):,.2f}"


def plain(value: Decimal | str | int | float) -> str:
    """Normalised decimal string with no separators: ``98400.00``."""
    return f"{d(value):.2f}"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rng(seed: int, salt: str) -> random.Random:
    """Independent deterministic stream per (seed, purpose)."""
    digest = hashlib.sha256(f"{seed}:{salt}".encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


# --------------------------------------------------------------------------- gold records


@dataclass
class GoldRegion:
    """A region the generator drew, with what it means. Independent of any parser."""

    region_id: str
    kind: str  # field | unit | table | caption | footnote | marker | figure | chart | header | text | footer
    page: int
    bbox: BBox
    role: str  # canonical role (evidence_core.enums.CanonicalRole value)
    label: str | None = None
    value: str | None = None  # as printed
    value_number: str | None = None
    concept: str | None = None  # fin:... field concept
    field_id: str | None = None  # suggested fieldId inside the unit that holds it
    unit_ref: str | None = None  # region_id of the enclosing unit-level region
    period: str | None = None  # "TY2025", "2026-09", "Q3 FY2025"
    tax_year: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    borrower: str | None = None
    employer: str | None = None
    currency: str | None = None
    pii: str | None = None  # "held" (unmasked identifier) | "masked" (truncated)
    label_bbox: BBox | None = None
    value_bbox: BBox | None = None
    cell_bbox: BBox | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "regionId": self.region_id,
            "kind": self.kind,
            "page": self.page,
            "bbox": self.bbox.rounded().dump(),
            "role": self.role,
        }
        optional = {
            "label": self.label,
            "value": self.value,
            "valueNumber": self.value_number,
            "concept": self.concept,
            "fieldId": self.field_id,
            "unitRef": self.unit_ref,
            "period": self.period,
            "taxYear": self.tax_year,
            "periodStart": self.period_start,
            "periodEnd": self.period_end,
            "borrower": self.borrower,
            "employer": self.employer,
            "currency": self.currency,
            "pii": self.pii,
        }
        out.update({k: v for k, v in optional.items() if v is not None})
        for key, box in (("labelBbox", self.label_bbox), ("valueBbox", self.value_bbox), ("cellBbox", self.cell_bbox)):
            if box is not None:
                out[key] = box.rounded().dump()
        if self.extra:
            out["extra"] = self.extra
        return out


@dataclass
class GoldDoc:
    file_name: str
    doc_type: str  # doc:W2 ...
    route: str  # digital | scan
    title: str
    page_count: int = 0
    page_width: float = PAGE_W
    page_height: float = PAGE_H
    regions: list[GoldRegion] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    sha256: str | None = None
    doc_id: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    def add(self, region: GoldRegion) -> GoldRegion:
        if any(r.region_id == region.region_id for r in self.regions):
            raise ValueError(f"duplicate region id {region.region_id} in {self.file_name}")
        self.regions.append(region)
        return region

    def region(self, region_id: str) -> GoldRegion:
        for r in self.regions:
            if r.region_id == region_id:
                return r
        raise KeyError(f"{self.file_name}: no region {region_id}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "docId": self.doc_id,
            "docType": self.doc_type,
            "route": self.route,
            "title": self.title,
            "pageCount": self.page_count,
            "pageWidth": self.page_width,
            "pageHeight": self.page_height,
            "sha256": self.sha256,
            "bboxFrame": "pdf-points-top-left",
            "attributes": self.attributes,
            "notes": self.notes,
            "regions": [r.to_dict() for r in self.regions],
        }


# --------------------------------------------------------------------------- drawing


class PdfDoc:
    """reportlab canvas wrapper that draws in top-left coordinates and returns canonical boxes."""

    def __init__(self, path: Path, gold: GoldDoc, *, title: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.gold = gold
        self.c = Canvas(str(path), pagesize=letter, invariant=1, pageCompression=1)
        self.c.setTitle(title)
        self.c.setAuthor("Regionproof generator (fictitious)")
        self.c.setCreator(f"regionproof data/generate.py {GENERATOR_VERSION}")
        self.c.setSubject(FOOTER_TEXT)
        self.page = 1

    # -- coordinates

    @staticmethod
    def text_box(x: float, baseline_top: float, text: str, font: str, size: float, page: int) -> BBox:
        """Canonical box of a string drawn with its baseline at ``baseline_top`` (top-left y)."""
        width = pdfmetrics.stringWidth(text, font, size)
        ascent, descent = pdfmetrics.getAscentDescent(font, size)
        return BBox(page=page, x0=x, y0=baseline_top - ascent, x1=x + width, y1=baseline_top - descent)

    def box(self, x0: float, y0: float, x1: float, y1: float) -> BBox:
        return BBox(page=self.page, x0=x0, y0=y0, x1=x1, y1=y1)

    # -- primitives (all y values are top-left)

    def text(self, x: float, y: float, text: str, *, font: str = "Helvetica", size: float = 10.0,
             align: str = "left") -> BBox:
        """Draw ``text`` with its baseline at top-left ``y``. Returns the glyph box."""
        width = pdfmetrics.stringWidth(text, font, size)
        if align == "right":
            x -= width
        elif align == "center":
            x -= width / 2
        self.c.setFont(font, size)
        self.c.drawString(x, PAGE_H - y, text)
        return self.text_box(x, y, text, font, size, self.page)

    def superscript(self, x: float, y: float, marker: str, *, font: str = "Helvetica", size: float = 6.0,
                    rise: float = 4.0) -> BBox:
        """A raised small glyph (footnote marker), detected downstream by size and baseline."""
        return self.text(x, y - rise, marker, font=font, size=size)

    def rect(self, x0: float, y0: float, x1: float, y1: float, *, width: float = 0.6, fill: float | None = None,
             stroke: bool = True) -> BBox:
        self.c.setLineWidth(width)
        if fill is not None:
            self.c.setFillGray(fill)
        self.c.rect(x0, PAGE_H - y1, x1 - x0, y1 - y0, stroke=1 if stroke else 0, fill=1 if fill is not None else 0)
        self.c.setFillGray(0)
        return self.box(x0, y0, x1, y1)

    def line(self, x0: float, y0: float, x1: float, y1: float, *, width: float = 0.6) -> None:
        self.c.setLineWidth(width)
        self.c.line(x0, PAGE_H - y0, x1, PAGE_H - y1)

    def polyline(self, points: list[tuple[float, float]], *, width: float = 1.0) -> BBox:
        self.c.setLineWidth(width)
        path = self.c.beginPath()
        path.moveTo(points[0][0], PAGE_H - points[0][1])
        for px, py in points[1:]:
            path.lineTo(px, PAGE_H - py)
        self.c.drawPath(path, stroke=1, fill=0)
        return BBox(page=self.page, x0=min(p[0] for p in points), y0=min(p[1] for p in points),
                    x1=max(p[0] for p in points), y1=max(p[1] for p in points))

    def paragraph(self, x: float, y: float, text: str, *, width: float, font: str = "Helvetica",
                  size: float = 10.0, leading: float | None = None) -> tuple[BBox, float]:
        """Greedy word-wrap. Returns (box of all lines, baseline y of the next line)."""
        leading = leading or size * 1.35
        words = text.split()
        lines: list[str] = []
        current = ""
        for w in words:
            trial = f"{current} {w}".strip()
            if pdfmetrics.stringWidth(trial, font, size) <= width or not current:
                current = trial
            else:
                lines.append(current)
                current = w
        if current:
            lines.append(current)
        boxes = []
        for line in lines:
            boxes.append(self.text(x, y, line, font=font, size=size))
            y += leading
        return union(boxes), y

    def footer(self, page_label: str | None = None) -> BBox:
        box = self.text(PAGE_W / 2, PAGE_H - 24, FOOTER_TEXT, font="Helvetica-Oblique", size=7.5, align="center")
        if page_label:
            self.text(PAGE_W - 36, PAGE_H - 24, page_label, font="Helvetica", size=7.5, align="right")
        return box

    def finish_page(self, page_label: str | None = None) -> None:
        fbox = self.footer(page_label)
        self.gold.add(GoldRegion(region_id=f"footer-p{self.page}", kind="footer", page=self.page, bbox=fbox,
                                 role="page_footer", label="footer", value=FOOTER_TEXT))
        self.c.showPage()
        self.page += 1

    def save(self) -> None:
        self.gold.page_count = self.page - 1
        self.c.save()
        self.gold.sha256 = sha256_file(self.path)

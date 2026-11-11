"""Canonical bounding boxes.

One frame everywhere: ``{page, x0, y0, x1, y1}`` in PDF points, top-left origin,
1-based page numbers, ``x0 < x1`` and ``y0 < y1``. Units carry ``pageWidth`` and
``pageHeight`` so the UI scales by ``imageWidthPx / pageWidth`` with no axis flip.

Engine conversions:
- Textract: ``Geometry.BoundingBox`` ratios (Left, Top, Width, Height in 0..1, top-left)
  multiplied by page size.
- Docling: ``prov[].bbox`` ``{l, t, r, b, coord_origin}``; ``BOTTOMLEFT`` boxes become
  ``y' = pageHeight - y`` (and t/b swap roles).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from pydantic import Field, model_validator

from .base import RpModel


class BBox(RpModel):
    page: int = Field(ge=1)
    x0: float
    y0: float
    x1: float
    y1: float

    @model_validator(mode="after")
    def _ordered(self) -> BBox:
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValueError(f"bbox corners out of order: {self!r}")
        return self

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    def rounded(self, ndigits: int = 2) -> BBox:
        return BBox(
            page=self.page,
            x0=round(self.x0, ndigits),
            y0=round(self.y0, ndigits),
            x1=round(self.x1, ndigits),
            y1=round(self.y1, ndigits),
        )

    def clamp(self, page_width: float, page_height: float) -> BBox:
        return BBox(
            page=self.page,
            x0=min(max(self.x0, 0.0), page_width),
            y0=min(max(self.y0, 0.0), page_height),
            x1=min(max(self.x1, 0.0), page_width),
            y1=min(max(self.y1, 0.0), page_height),
        )

    def expand(self, margin: float) -> BBox:
        return BBox(
            page=self.page,
            x0=self.x0 - margin,
            y0=self.y0 - margin,
            x1=self.x1 + margin,
            y1=self.y1 + margin,
        )

    def to_pixels(self, page_width: float, image_width_px: int) -> tuple[int, int, int, int]:
        """Pixel rectangle for a page image rendered at ``image_width_px`` wide."""
        scale = image_width_px / page_width
        return (
            round(self.x0 * scale),
            round(self.y0 * scale),
            round(self.x1 * scale),
            round(self.y1 * scale),
        )


def from_textract(
    geometry_bbox: Mapping[str, float], *, page: int, page_width: float, page_height: float
) -> BBox:
    """Textract ``Geometry.BoundingBox`` (ratios, top-left) to canonical points."""
    left = float(geometry_bbox["Left"])
    top = float(geometry_bbox["Top"])
    width = float(geometry_bbox["Width"])
    height = float(geometry_bbox["Height"])
    return BBox(
        page=page,
        x0=left * page_width,
        y0=top * page_height,
        x1=(left + width) * page_width,
        y1=(top + height) * page_height,
    ).clamp(page_width, page_height)


def from_docling(
    bbox: Mapping[str, Any], *, page: int, page_height: float, page_width: float | None = None
) -> BBox:
    """Docling ``{l, t, r, b, coord_origin}`` to canonical points.

    For ``BOTTOMLEFT`` origin, ``t`` is the larger y (top edge measured from the bottom).
    """
    left = float(bbox["l"])
    right = float(bbox["r"])
    t = float(bbox["t"])
    b = float(bbox["b"])
    origin = str(bbox.get("coord_origin", "TOPLEFT")).upper()
    if origin.endswith("BOTTOMLEFT"):
        y0 = page_height - t
        y1 = page_height - b
    else:
        y0, y1 = t, b
    if y1 < y0:
        y0, y1 = y1, y0
    if right < left:
        left, right = right, left
    box = BBox(page=page, x0=left, y0=y0, x1=right, y1=y1)
    return box.clamp(page_width, page_height) if page_width is not None else box


def union(boxes: Iterable[BBox]) -> BBox:
    """Smallest box containing all boxes; all must be on the same page."""
    items = list(boxes)
    if not items:
        raise ValueError("union of no boxes")
    pages = {b.page for b in items}
    if len(pages) != 1:
        raise ValueError(f"union across pages {sorted(pages)}; group by page first")
    return BBox(
        page=items[0].page,
        x0=min(b.x0 for b in items),
        y0=min(b.y0 for b in items),
        x1=max(b.x1 for b in items),
        y1=max(b.y1 for b in items),
    )


def union_by_page(boxes: Iterable[BBox]) -> list[BBox]:
    """One union box per page, sorted by page."""
    by_page: dict[int, list[BBox]] = {}
    for b in boxes:
        by_page.setdefault(b.page, []).append(b)
    return [union(v) for _, v in sorted(by_page.items())]


def intersection(a: BBox, b: BBox) -> BBox | None:
    if a.page != b.page:
        return None
    x0, y0 = max(a.x0, b.x0), max(a.y0, b.y0)
    x1, y1 = min(a.x1, b.x1), min(a.y1, b.y1)
    if x1 <= x0 or y1 <= y0:
        return None
    return BBox(page=a.page, x0=x0, y0=y0, x1=x1, y1=y1)


def iou(a: BBox, b: BBox) -> float:
    inter = intersection(a, b)
    if inter is None:
        return 0.0
    denom = a.area + b.area - inter.area
    return inter.area / denom if denom > 0 else 0.0


def coverage(of: BBox, by: BBox) -> float:
    """Fraction of ``of``'s area covered by ``by`` (RegionHit@1 uses coverage(gold, item) >= 0.5)."""
    inter = intersection(of, by)
    if inter is None or of.area <= 0:
        return 0.0
    return inter.area / of.area


def contains(outer: BBox, inner: BBox, tolerance: float = 0.5) -> bool:
    return (
        outer.page == inner.page
        and inner.x0 >= outer.x0 - tolerance
        and inner.y0 >= outer.y0 - tolerance
        and inner.x1 <= outer.x1 + tolerance
        and inner.y1 <= outer.y1 + tolerance
    )

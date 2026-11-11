"""Glyph-level boxes from the PDF text layer (pypdfium2), used to tighten field bboxes on digital
pages: a Docling table cell can hold a value *and* the next box's label, so the cell bbox is too
coarse for a citation. Searching the value string inside the cell gives the printed glyphs' box.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evidence_core.bbox import BBox, contains, union


def pdfium_text(raw: str) -> str:
    """pdfium reports a soft hyphen at a line break as U+0002; restore it as "-" (ontology/data README)."""
    return raw.replace("\x02", "-").strip()


@dataclass
class GlyphLocator:
    pdf: bytes
    _doc: Any = field(default=None, init=False)
    _pages: dict[int, tuple[Any, Any, float]] = field(default_factory=dict, init=False)
    _raised: dict[int, list[tuple[str, BBox]]] = field(default_factory=dict, init=False)

    def _page(self, page: int) -> tuple[Any, Any, float] | None:
        import pypdfium2 as pdfium

        if self._doc is None:
            self._doc = pdfium.PdfDocument(self.pdf)
        if page not in self._pages:
            if page < 1 or page > len(self._doc):
                return None
            p = self._doc[page - 1]
            _, h = p.get_size()
            self._pages[page] = (p, p.get_textpage(), h)
        return self._pages[page]

    def find(self, page: int, text: str, within: BBox | None = None, *, margin: float = 3.0) -> BBox | None:
        """Box of the first occurrence of ``text`` on ``page`` (inside ``within`` when given)."""
        text = text.strip()
        if not text:
            return None
        entry = self._page(page)
        if entry is None:
            return None
        _, tp, h = entry
        searcher = tp.search(text, match_case=True, match_whole_word=False)
        try:
            while True:
                hit = searcher.get_next()
                if not hit:
                    return None
                start, count = hit
                boxes = []
                for i in range(start, start + count):
                    left, bottom, right, top = tp.get_charbox(i, loose=False)
                    if right <= left or top <= bottom:
                        continue
                    boxes.append(BBox(page=page, x0=left, y0=h - top, x1=right, y1=h - bottom))
                if not boxes:
                    continue
                box = union(boxes)
                if within is None or contains(within.expand(margin), box, tolerance=margin):
                    return box.rounded()
        finally:
            searcher.close()

    def close(self) -> None:
        for p, tp, _ in self._pages.values():
            tp.close()
            p.close()
        self._pages.clear()
        if self._doc is not None:
            self._doc.close()
            self._doc = None


def _runs(locator: GlyphLocator, page: int) -> list[tuple[str, BBox]]:
    entry = locator._page(page)  # noqa: SLF001 - same module family
    if entry is None:
        return []
    _, tp, h = entry
    out = []
    for i in range(tp.count_rects()):
        left, bottom, right, top = tp.get_rect(i)
        text = pdfium_text(tp.get_text_bounded(left, bottom, right, top))
        if text and right > left and top > bottom:
            out.append((text, BBox(page=page, x0=left, y0=h - top, x1=right, y1=h - bottom).rounded()))
    return out


def text_runs(locator: GlyphLocator, page: int, within: BBox | None = None, margin: float = 2.0
              ) -> list[tuple[str, BBox]]:
    """Text-layer runs (pdfium text rectangles) on a page, optionally inside a block bbox."""
    runs = _runs(locator, page)
    if within is None:
        return runs
    area = within.expand(margin)
    return [(t, b) for t, b in runs if contains(area, b, tolerance=margin)]



MARKER_GLYPHS = frozenset("123456789*†‡")
_REF_MAX_GAP = 40.0  # points: a neighbour further away on the line is not a reference
_REF_CHARS = 6  # neighbours per side that set the line's baseline and cap height


def raised_glyphs(locator: GlyphLocator, page: int, within: BBox | None = None, margin: float = 2.0
                  ) -> list[tuple[str, BBox]]:
    """Footnote-marker glyphs printed raised and smaller than their neighbour on the same line: a plain
    ``3`` set as a superscript after ``4.25%``, or at the start of the footnote it refers to.

    The text layer carries such a marker as an ordinary digit (``Coupon 4.25%3``), so the text alone
    cannot tell it from a number; the glyph geometry can. A glyph counts as raised when its baseline sits
    at least 20 % of the line's cap height above the line's baseline and it is at most 80 % as tall.
    The line's baseline (median) and cap height come from up to six letters, digits or ``%`` on each side.
    """
    if page not in locator._raised:  # noqa: SLF001 - same module family
        locator._raised[page] = _raised_on_page(locator, page)  # noqa: SLF001
    found = locator._raised[page]  # noqa: SLF001
    if within is None:
        return list(found)
    area = within.expand(margin)
    return [(ch, bb) for ch, bb in found if contains(area, bb, tolerance=margin)]


def _raised_on_page(locator: GlyphLocator, page: int) -> list[tuple[str, BBox]]:
    entry = locator._page(page)  # noqa: SLF001 - same module family
    if entry is None:
        return []
    _, tp, h = entry
    n = tp.count_chars()
    chars = tp.get_text_range(0, n) if n else ""
    n = min(n, len(chars))
    boxes: list[tuple[float, float, float, float] | None] = []
    for i in range(n):
        left, bottom, right, top = tp.get_charbox(i, loose=False)
        boxes.append((left, bottom, right, top) if right > left and top > bottom else None)

    def neighbours(i: int, step: int) -> list[tuple[float, float, float, float]]:
        """Up to ``_REF_CHARS`` letters, digits or ``%`` next to glyph ``i`` on its text line, one direction."""
        out: list[tuple[float, float, float, float]] = []
        j = i + step
        while 0 <= j < n and len(out) < _REF_CHARS:
            ch, box = chars[j], boxes[j]
            if ch in "\r\n":
                break
            if box is not None and (ch.isalnum() or ch == "%"):
                if abs(box[0] - boxes[i][0]) > _REF_MAX_GAP:  # type: ignore[index]
                    break
                out.append(box)
            j += step
        return out

    out = []
    for i in range(n):
        ch, box = chars[i], boxes[i]
        if ch not in MARKER_GLYPHS or box is None:
            continue
        refs = neighbours(i, -1) + neighbours(i, 1)
        if not refs:
            continue
        left, bottom, right, top = box
        baseline = sorted(b[1] for b in refs)[len(refs) // 2]  # median: descenders do not move it
        ref_h = max(b[3] for b in refs) - baseline
        if ref_h <= 0 or bottom - baseline < 0.2 * ref_h or top - bottom > 0.8 * ref_h:
            continue
        out.append((ch, BBox(page=page, x0=left, y0=h - top, x1=right, y1=h - bottom).rounded()))
    return out

"""PDF helpers on pypdf (pinned in v1.0.yaml) for validation, routing, text layer and page subsets."""

from __future__ import annotations

import io
from dataclasses import dataclass, field

import pypdf
from evidence_core.models.stage_outputs import PageInfo
from pypdf import PdfReader, PdfWriter

PDF_MAGIC = b"%PDF-"


def pypdf_version() -> str:
    return f"pypdf {pypdf.__version__}"


@dataclass
class PdfInspection:
    ok: bool
    reason: str | None = None
    pages: list[PageInfo] = field(default_factory=list)

    @property
    def page_count(self) -> int:
        return len(self.pages)


def _reader(data: bytes) -> PdfReader:
    return PdfReader(io.BytesIO(data), strict=False)


def page_size(page: pypdf.PageObject) -> tuple[float, float]:
    """(width, height) in PDF points as displayed (mediabox, rotation applied)."""
    box = page.cropbox or page.mediabox
    w, h = float(box.width), float(box.height)
    rotate = int(page.get("/Rotate", 0) or 0) % 360
    return (h, w) if rotate in (90, 270) else (w, h)


def extract_page_text(page: pypdf.PageObject) -> str:
    """pypdf ``extract_text()`` in its default (content-stream order) mode: the naive lane.

    See docs/decisions/0002-naive-lane-frozen.md."""
    try:
        return page.extract_text() or ""
    except Exception:  # noqa: BLE001 - a broken content stream yields no text, like a scan
        return ""


def inspect_pdf(data: bytes, *, max_bytes: int) -> PdfInspection:
    """Validate an upload and collect per-page size + text-layer character counts."""
    if len(data) > max_bytes:
        return PdfInspection(False, f"file is larger than {max_bytes // (1024 * 1024)} MB")
    if not data.startswith(PDF_MAGIC) and PDF_MAGIC not in data[:1024]:
        return PdfInspection(False, "not a PDF (missing %PDF- header)")
    try:
        reader = _reader(data)
        if reader.is_encrypted:
            return PdfInspection(False, "encrypted PDF")
        pages: list[PageInfo] = []
        for i, page in enumerate(reader.pages, start=1):
            w, h = page_size(page)
            text = extract_page_text(page)
            pages.append(PageInfo(page=i, width=round(w, 2), height=round(h, 2), text_chars=len(text.strip())))
    except Exception as exc:  # noqa: BLE001 - any parse failure quarantines the object
        return PdfInspection(False, f"unreadable PDF: {type(exc).__name__}")
    if not pages:
        return PdfInspection(False, "PDF has 0 pages")
    return PdfInspection(True, None, pages)


def page_texts(data: bytes) -> list[tuple[int, str, float, float]]:
    """[(page, text, width, height)] using the default extraction mode."""
    reader = _reader(data)
    out = []
    for i, page in enumerate(reader.pages, start=1):
        w, h = page_size(page)
        out.append((i, extract_page_text(page), w, h))
    return out


def subset_pdf(data: bytes, pages: list[int]) -> bytes:
    """A new PDF holding only ``pages`` (1-based), in the given order."""
    reader = _reader(data)
    writer = PdfWriter()
    for p in pages:
        writer.add_page(reader.pages[p - 1])
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def page_count(data: bytes) -> int:
    return len(_reader(data).pages)

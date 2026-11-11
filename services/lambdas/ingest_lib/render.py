"""Gold page images (stage ``promote``): pypdfium2 render at ``render.dpi`` with held regions filled
black before the PNG is written, so a held value can never be shown from Gold."""

from __future__ import annotations

import io
from dataclasses import dataclass

from evidence_core.bbox import BBox


def pdfium_version() -> str:
    import pypdfium2

    return f"pdfium via pypdfium2 {pypdfium2.version.PYPDFIUM_INFO}"


@dataclass
class RenderedPage:
    page: int
    png: bytes
    width_px: int
    height_px: int
    held_regions: int


def render_pages(pdf: bytes, *, dpi: int, held: dict[int, list[BBox]], pages: list[int] | None = None,
                 margin_pt: float = 2.0) -> list[RenderedPage]:
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    scale = dpi / 72.0
    doc = pdfium.PdfDocument(pdf)
    out: list[RenderedPage] = []
    try:
        wanted = pages or list(range(1, len(doc) + 1))
        for p in wanted:
            page = doc[p - 1]
            try:
                image = page.render(scale=scale).to_pil().convert("RGB")
            finally:
                page.close()
            draw = ImageDraw.Draw(image)
            boxes = held.get(p, [])
            for box in boxes:
                b = box.expand(margin_pt)
                draw.rectangle([b.x0 * scale, b.y0 * scale, b.x1 * scale, b.y1 * scale], fill=(0, 0, 0))
            buf = io.BytesIO()
            image.save(buf, format="PNG", optimize=True)
            out.append(RenderedPage(page=p, png=buf.getvalue(), width_px=image.width, height_px=image.height,
                                    held_regions=len(boxes)))
    finally:
        doc.close()
    return out

"""Scanned-page simulation: draw a page as a raster, skew it, add noise, wrap it in an image-only PDF.

The page is drawn with the same top-left point coordinates the PDF renderer uses, at ``dpi``. Gold
boxes are measured on the unrotated drawing, then rotated with the same transform that is applied
to the image, so they stay true to the pixels Textract will see. The PDF has no text layer, so the
v1.0 text-layer lane gets no text from it.
"""

from __future__ import annotations

import io
import math
from pathlib import Path

import reportlab
from evidence_core.bbox import BBox
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

from .common import FOOTER_TEXT, GENERATOR_VERSION, PAGE_H, PAGE_W, GoldDoc, GoldRegion, rng, sha256_file

_FONT_DIR = Path(reportlab.__file__).parent / "fonts"
_FONT_FILES = {
    "Helvetica": "Vera.ttf",
    "Helvetica-Bold": "VeraBd.ttf",
    "Helvetica-Oblique": "VeraIt.ttf",
    "Courier": "Vera.ttf",
}


class RasterPage:
    """Same drawing surface as ``PdfDoc`` (top-left points), backed by a grayscale image."""

    def __init__(self, gold: GoldDoc, *, dpi: int = 150) -> None:
        self.gold = gold
        self.dpi = dpi
        self.scale = dpi / 72.0
        self.page = 1
        self.image = Image.new("L", (round(PAGE_W * self.scale), round(PAGE_H * self.scale)), 255)
        self.draw = ImageDraw.Draw(self.image)
        self._fonts: dict[tuple[str, float], ImageFont.FreeTypeFont] = {}

    def _font(self, name: str, size: float) -> ImageFont.FreeTypeFont:
        key = (name, size)
        if key not in self._fonts:
            self._fonts[key] = ImageFont.truetype(str(_FONT_DIR / _FONT_FILES.get(name, "Vera.ttf")),
                                                  round(size * self.scale))
        return self._fonts[key]

    def text(self, x: float, y: float, text: str, *, font: str = "Helvetica", size: float = 10.0,
             align: str = "left") -> BBox:
        f = self._font(font, size)
        anchor = {"left": "ls", "right": "rs", "center": "ms"}[align]
        px, py = x * self.scale, y * self.scale
        self.draw.text((px, py), text, font=f, fill=20, anchor=anchor)
        left, top, right, bottom = self.draw.textbbox((px, py), text, font=f, anchor=anchor)
        return BBox(page=self.page, x0=left / self.scale, y0=top / self.scale, x1=right / self.scale,
                    y1=bottom / self.scale)

    def rect(self, x0: float, y0: float, x1: float, y1: float, *, width: float = 0.6, fill: float | None = None,
             stroke: bool = True) -> BBox:
        s = self.scale
        self.draw.rectangle((x0 * s, y0 * s, x1 * s, y1 * s), outline=40 if stroke else None,
                            fill=None if fill is None else round(fill * 255), width=max(1, round(width * s)))
        return BBox(page=self.page, x0=x0, y0=y0, x1=x1, y1=y1)

    def line(self, x0: float, y0: float, x1: float, y1: float, *, width: float = 0.6) -> None:
        s = self.scale
        self.draw.line((x0 * s, y0 * s, x1 * s, y1 * s), fill=40, width=max(1, round(width * s)))

    def footer(self) -> BBox:
        return self.text(PAGE_W / 2, PAGE_H - 24, FOOTER_TEXT, font="Helvetica-Oblique", size=7.5, align="center")


def rotate_point(x: float, y: float, degrees: float) -> tuple[float, float]:
    """Where PIL's ``Image.rotate(degrees)`` (counter-clockwise, about the centre) moves a point.

    Coordinates are top-left points; the rotation is the same in points and pixels.
    """
    cx, cy = PAGE_W / 2, PAGE_H / 2
    th = math.radians(degrees)
    dx, dy = x - cx, y - cy
    return cx + dx * math.cos(th) + dy * math.sin(th), cy - dx * math.sin(th) + dy * math.cos(th)


def rotate_bbox(box: BBox, degrees: float) -> BBox:
    corners = [rotate_point(x, y, degrees) for x in (box.x0, box.x1) for y in (box.y0, box.y1)]
    out = BBox(page=box.page, x0=min(c[0] for c in corners), y0=min(c[1] for c in corners),
               x1=max(c[0] for c in corners), y1=max(c[1] for c in corners))
    return out.clamp(PAGE_W, PAGE_H)


def _rotate_region(r: GoldRegion, degrees: float) -> None:
    r.bbox = rotate_bbox(r.bbox, degrees)
    for attr in ("label_bbox", "value_bbox", "cell_bbox"):
        box = getattr(r, attr)
        if box is not None:
            setattr(r, attr, rotate_bbox(box, degrees))


def finish_scan(page: RasterPage, path: Path, *, skew_degrees: float, seed: int, noise_level: int = 18) -> None:
    """Footer, skew, noise, slight blur; writes an image-only one-page PDF and rotates the gold boxes."""
    fbox = page.footer()
    page.gold.add(GoldRegion(region_id="footer-p1", kind="footer", page=1, bbox=fbox, role="page_footer",
                             label="footer", value=FOOTER_TEXT))
    img = page.image.rotate(skew_degrees, resample=Image.Resampling.BICUBIC, fillcolor=255)
    r = rng(seed, "scan-noise")
    noise = Image.frombytes("L", img.size, r.randbytes(img.size[0] * img.size[1]))
    # Map noise to a narrow band around 0 and add it: speckle without destroying glyphs.
    noise = noise.point(lambda v: 128 + (v * noise_level) // 255 - noise_level // 2)
    img = ImageChops.add(img, noise, scale=1.0, offset=-128)  # clip(img + noise - 128)
    img = img.filter(ImageFilter.GaussianBlur(radius=0.5))
    for region in page.gold.regions:
        _rotate_region(region, skew_degrees)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82, optimize=False, progressive=False)
    buf.seek(0)
    path.parent.mkdir(parents=True, exist_ok=True)
    c = Canvas(str(path), pagesize=(PAGE_W, PAGE_H), invariant=1, pageCompression=1)
    c.setTitle(page.gold.title + " (scanned)")
    c.setAuthor("Regionproof generator (fictitious)")
    c.setCreator(f"regionproof data/generate.py {GENERATOR_VERSION}")
    c.drawImage(ImageReader(buf), 0, 0, width=PAGE_W, height=PAGE_H)
    c.showPage()
    c.save()
    page.gold.page_count = 1
    page.gold.sha256 = sha256_file(path)
    page.gold.attributes.update({"scanDpi": page.dpi, "skewDegrees": skew_degrees, "noiseSeed": seed})

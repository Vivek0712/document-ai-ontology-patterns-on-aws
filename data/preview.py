#!/usr/bin/env python3
"""Render sample pages with their gold regions drawn on top (orange = field, thin grey = unit/other).

    python data/preview.py data/samples/lending --out build/preview [--dpi 100]

A review aid for the generator: it shows that the gold boxes sit on the drawn content in the
canonical frame (PDF points, top-left origin), including the rotated boxes of the scanned payslip.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pypdfium2 as pdfium
from PIL import ImageDraw

FIELD = (255, 98, 0)
OTHER = (90, 90, 90)
HELD = (200, 0, 120)


def render(package_dir: Path, out_dir: Path, dpi: int = 100) -> list[Path]:
    gold = json.loads((package_dir / "gold.json").read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    scale = dpi / 72.0
    for doc in gold["documents"]:
        pdf = pdfium.PdfDocument(str(package_dir / doc["fileName"]))
        for page_no in range(1, doc["pageCount"] + 1):
            img = pdf[page_no - 1].render(scale=scale).to_pil().convert("RGB")
            draw = ImageDraw.Draw(img)
            for r in doc["regions"]:
                if r["page"] != page_no:
                    continue
                b = r["bbox"]
                color = HELD if r.get("pii") == "held" else FIELD if r["kind"] == "field" else OTHER
                width = 2 if r["kind"] == "field" else 1
                draw.rectangle([b["x0"] * scale, b["y0"] * scale, b["x1"] * scale, b["y1"] * scale], outline=color,
                               width=width)
            target = out_dir / f"{Path(doc['fileName']).stem}_p{page_no:02d}.png"
            img.save(target)
            written.append(target)
        pdf.close()
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package_dir", type=Path)
    ap.add_argument("--out", type=Path, default=Path("build/preview"))
    ap.add_argument("--dpi", type=int, default=100)
    args = ap.parse_args()
    for path in render(args.package_dir, args.out, args.dpi):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

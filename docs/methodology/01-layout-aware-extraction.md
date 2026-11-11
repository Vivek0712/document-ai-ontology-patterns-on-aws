# Pattern 1 · Layout-aware extraction

**Rule:** extract regions with roles and bounding boxes, never a flat text stream.

Plane: ingestion. Next: [pattern 2, evidence units](02-evidence-units.md).

---

## The problem

A PDF stores drawing instructions, not reading order. Its text layer returns characters in the order they were
painted. On a form, that is often every label first and every value afterwards.

The fictitious W-2s in [`data/samples/lending/`](../../data/samples/lending/) are built to show this. Their
labels are drawn first, then the values in the order 12a, 3, 5, 1, 2, 4, 6. pypdf's default `extract_text()`
returns the label block followed by `6,600.00 105,000.00 105,000.00 98,400.00 …`. The value 98,400.00 no longer
sits next to "Wages, tips, other compensation". A fixed-size chunker then packs that stream into chunks, and a
model has to guess which number is Box 1.

In the logged local runs, that guess went wrong on the `lending-02` package. The `1.0` lane reported Box 3
(Social Security wages) as wages, so it answered 199,000.00 instead of 188,090.00. The other failure modes come
from the same root cause:

- A scanned page has no text layer at all, so a text-layer pipeline silently drops it.
- Page headers and footers repeat on every page and pollute every chunk.
- A footnote marker such as the raised `³` in `Coupon 4.25%³` becomes the digit `3`, which reads as part of
  the number.

## The principle

Run a layout engine, and normalize every engine's output into **one canonical layout model** before anything
else reads it:

- **One block per region**, with a canonical **role** (`title`, `section_header`, `text`, `table`, `key_value`,
  `caption`, `footnote`, `picture`, `page_header`, `page_footer`, …).
- **One coordinate frame**: PDF points, top-left origin, 1-based pages. Engines disagree on origin and units, so
  convert once, at the edge.
- **Reading order** as an explicit index, not as an accident of string concatenation.
- **A pointer back** into the engine's own output (a Docling JSON pointer, an Amazon Textract block ID). Every
  later unit and field can then be traced to the exact engine item.
- **Furniture excluded**: page headers and footers are kept, but marked so they never enter a unit.

Route each page to the engine that suits it. A page with a usable text layer goes to a layout engine for digital
documents. A page without one goes to OCR with layout analysis.

## How it is implemented here

| Piece | File |
|---|---|
| Canonical layout model (`LayoutBlock`, `TableGrid`, `KeyValue`, `DocLayout`) | [`services/lambdas/ingest_lib/layout.py`](../../services/lambdas/ingest_lib/layout.py) |
| Canonical roles | `CanonicalRole` in [`packages/evidence-core/evidence_core/enums.py`](../../packages/evidence-core/evidence_core/enums.py) |
| Bounding-box frame and conversions (`from_docling`, `from_textract`) | [`packages/evidence-core/evidence_core/bbox.py`](../../packages/evidence-core/evidence_core/bbox.py) |
| Page router: more than 50 text-layer characters means digital, otherwise scan | [`services/lambdas/route/handler.py`](../../services/lambdas/route/handler.py), `preprocessing.extraction.router` in [`config/pipelines/v2.3.1.yaml`](../../config/pipelines/v2.3.1.yaml) |
| Digital pages: docling-serve on AWS Fargate, reached through Amazon SQS and a task token | [`services/lambdas/docling_call/handler.py`](../../services/lambdas/docling_call/handler.py), adapter [`ingest_lib/docling_layout.py`](../../services/lambdas/ingest_lib/docling_layout.py) |
| Scanned pages: Amazon Textract `AnalyzeDocument` with `LAYOUT`, `TABLES` and `FORMS`, one page per Map iteration | [`services/lambdas/textract_page/handler.py`](../../services/lambdas/textract_page/handler.py), adapter [`ingest_lib/textract_layout.py`](../../services/lambdas/ingest_lib/textract_layout.py) (tables and forms parsed with `amazon-textract-textractor`) |
| Engine label → canonical role mapping, as data | [`ontology/src/rp/roles.ttl`](../../ontology/src/rp/roles.ttl) (`skos:exactMatch` from Docling labels and Textract block types; aligned to DoCO) |
| Glyph-level boxes from the text layer, used to tighten field boxes and to find raised footnote markers | [`services/lambdas/ingest_lib/glyphs.py`](../../services/lambdas/ingest_lib/glyphs.py) |
| The control lane: pypdf text layer and fixed chunks | [`services/lambdas/text_layer/handler.py`](../../services/lambdas/text_layer/handler.py), [`ingest_lib/chunker.py`](../../services/lambdas/ingest_lib/chunker.py) |
| X-ray: shows on the page what the control lane read, and in what order. It is an inspection, never an extraction | [`services/lambdas/xray/core.py`](../../services/lambdas/xray/core.py) |

Two details are worth copying:

- **Cell boxes are too coarse for citations.** A Docling table cell on a form can hold a value and the next
  box's label. The builder searches the value string inside the cell's glyphs, and the field gets the box of the
  printed digits.
- **Mapping engine labels is a vocabulary change, not a code change.** A new engine label means a new
  `skos:exactMatch` and a new ontology version.

## Adopt it in your own pipeline

You need three things: a canonical block type, one adapter per engine, and a single frame. The sketch below is
standalone Python. The repository's version is `ingest_lib/layout.py`.

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class BBox:                       # PDF points, top-left origin, 1-based page
    page: int
    x0: float
    y0: float
    x1: float
    y1: float

@dataclass
class Block:
    block_id: str
    page: int
    order: int                    # reading order across the document
    role: str                     # canonical role: "table", "key_value", "footnote", ...
    text: str
    bbox: BBox | None
    engine: str                   # "docling" | "textract" | ...
    pointer: str                  # where this came from in the engine output
    excluded: bool = False        # page header or footer: never in a unit

ROLE_MAP = {                      # keep this in data (SKOS, YAML), not in code
    ("textract", "LAYOUT_TABLE"): "table",
    ("textract", "LAYOUT_FOOTER"): "page_footer",
    ("docling", "footnote"): "footnote",
    ("docling", "page_header"): "page_header",
}

def from_textract_geometry(box: dict, page: int, width_pt: float, height_pt: float) -> BBox:
    """Textract gives ratios of the page size with a top-left origin."""
    return BBox(page, box["Left"] * width_pt, box["Top"] * height_pt,
                (box["Left"] + box["Width"]) * width_pt, (box["Top"] + box["Height"]) * height_pt)

def from_bottom_left(x0, y0, x1, y1, page: int, height_pt: float) -> BBox:
    """Engines with a bottom-left origin (such as many Docling items): flip y once, here."""
    return BBox(page, x0, height_pt - y1, x1, height_pt - y0)

def route_page(text_layer_chars: int, threshold: int = 50) -> str:
    return "digital" if text_layer_chars > threshold else "scan"
```

On AWS, call Amazon Textract per page for scanned pages:

```python
import boto3

textract = boto3.client("textract")
resp = textract.analyze_document(
    Document={"Bytes": page_png_bytes},
    FeatureTypes=["LAYOUT", "TABLES", "FORMS"],
)
blocks = [b for b in resp["Blocks"] if b["BlockType"].startswith("LAYOUT_")]
```

Then map `BlockType` to your canonical role, convert `Geometry.BoundingBox` with `from_textract_geometry`, and
keep `Id` as the pointer.

## Pitfalls

- **Mixing coordinate frames.** Bottom-left and top-left origins, points and pixels, ratios and absolute values.
  Pick one frame, convert at the adapter, and test it with a known box.
- **Throwing away the engine output.** Keep the raw engine JSON (here it goes to Silver) and a pointer from each
  block back into it. Lineage depends on it.
- **Trusting cell boxes for citations.** Tighten to the printed glyphs when a text layer exists.
- **Dropping scans silently.** If a page has no text layer, route it to OCR or mark it as unavailable. Never let
  it vanish. The control lane in this repository shows what happens otherwise: the scanned payslip produces no
  chunks at all.
- **Letting furniture into units.** A repeated footer such as "Fictitious document · …" adds noise to every
  chunk's embedding.

## How to evaluate it

- **Label-value adjacency.** For each gold field, check whether its value appears beside its printed label in
  the extracted text. `make units` writes `build/units/diff.md`, which lists every field "detached" in the
  control lane. In the local lending build, every field on both W-2s was detached in `1.0`.
- **Missing values.** Count gold values that appear in no unit at all, for example everything on a scanned page.
- **Region recall.** Check whether the extracted block for a field covers its gold bounding box. The generator
  writes gold regions for every field, table, caption, footnote and marker (`gold.json` in each sample folder).
- **Visual check.** The X-ray view and `python data/preview.py data/samples/lending --out build/preview` draw
  boxes on page images. Look at them.

## Try it

```bash
make units PACKAGE=data/samples/lending      # control-lane chunks vs evidence units, with a diff
.venv/bin/python -m pytest services/lambdas/ingest_lib/tests/test_docling.py \
                           services/lambdas/ingest_lib/tests/test_ingest_lib.py \
                           services/lambdas/xray/tests/test_xray.py
```

For a guided, hands-on walk through this pattern, see
[Quickstart 3: layout-aware extraction](../quickstarts/03-layout-aware-extraction.md).

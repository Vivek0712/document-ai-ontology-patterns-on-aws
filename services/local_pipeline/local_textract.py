"""Dev-only stand-in for Amazon Textract in ``make units``: local Docling with OCR on the single page,
re-shaped as an ``AnalyzeDocument`` response (LAYOUT_* + LINE blocks, ratio geometry) so the
``textract_page`` handler and the Textract layout parser run unchanged.

The response carries ``AnalyzeDocumentModelVersion = "LOCAL-DOCLING-OCR (not Textract)"`` and no
KEY_VALUE_SET / TABLE blocks: confidences and FORMS pairs only come from the real service.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any

from lambdas.ingest_lib.docling_layout import iter_items

MODEL_VERSION = "LOCAL-DOCLING-OCR (not Textract)"
_LABEL_TO_LAYOUT = {
    "title": "LAYOUT_TITLE", "section_header": "LAYOUT_SECTION_HEADER", "text": "LAYOUT_TEXT",
    "paragraph": "LAYOUT_TEXT", "list_item": "LAYOUT_LIST", "table": "LAYOUT_TABLE", "picture": "LAYOUT_FIGURE",
    "key_value_region": "LAYOUT_KEY_VALUE", "page_header": "LAYOUT_HEADER", "page_footer": "LAYOUT_FOOTER",
    "caption": "LAYOUT_TEXT", "footnote": "LAYOUT_TEXT",
}


@dataclass
class LocalOcrTextract:
    _converter: Any = None
    calls: list[str] = field(default_factory=list)

    def _convert(self, pdf: bytes) -> dict[str, Any]:
        from docling.datamodel.base_models import DocumentStream, InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        if self._converter is None:
            opts = PdfPipelineOptions()
            opts.do_ocr = True
            opts.ocr_options.force_full_page_ocr = True
            opts.do_table_structure = True
            self._converter = DocumentConverter(
                format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})
        res = self._converter.convert(DocumentStream(name="page.pdf", stream=io.BytesIO(pdf)))
        return res.document.export_to_dict()

    def analyze_document(self, Document: dict[str, bytes], FeatureTypes: list[str]) -> dict[str, Any]:  # noqa: N803
        self.calls.append(",".join(FeatureTypes))
        doc = self._convert(Document["Bytes"])
        size = (doc.get("pages") or {}).get("1", {}).get("size", {"width": 612.0, "height": 792.0})
        w, h = float(size["width"]), float(size["height"])
        blocks: list[dict[str, Any]] = []
        children: list[str] = []
        for i, item in enumerate(iter_items(doc)):
            prov = (item.get("prov") or [None])[0]
            if not prov:
                continue
            bb = prov["bbox"]
            top, bottom = (h - bb["t"], h - bb["b"]) if str(bb.get("coord_origin", "")).endswith("BOTTOMLEFT") \
                else (bb["t"], bb["b"])
            top, bottom = min(top, bottom), max(top, bottom)
            geo = {"BoundingBox": {"Left": bb["l"] / w, "Top": top / h, "Width": (bb["r"] - bb["l"]) / w,
                                   "Height": (bottom - top) / h}}
            text = str(item.get("text") or "")
            if item.get("label") == "table":
                rows: dict[int, list[str]] = {}
                for c in (item.get("data") or {}).get("table_cells") or []:
                    rows.setdefault(int(c["start_row_offset_idx"]), []).append(str(c.get("text", "")))
                text = "\n".join(" ".join(v) for _, v in sorted(rows.items()))
            line_id = f"line-{i}"
            blocks.append({"BlockType": "LINE", "Id": line_id, "Text": text, "Confidence": 0.0, "Geometry": geo,
                           "Page": 1})
            layout = _LABEL_TO_LAYOUT.get(str(item.get("label")), "LAYOUT_TEXT")
            blocks.append({"BlockType": layout, "Id": f"layout-{i}", "Confidence": 0.0, "Geometry": geo, "Page": 1,
                           "Relationships": [{"Type": "CHILD", "Ids": [line_id]}]})
            children += [line_id, f"layout-{i}"]
        blocks.insert(0, {"BlockType": "PAGE", "Id": "page-1", "Page": 1, "Relationships": [
            {"Type": "CHILD", "Ids": children}],
            "Geometry": {"BoundingBox": {"Left": 0, "Top": 0, "Width": 1, "Height": 1}}})
        return {"DocumentMetadata": {"Pages": 1}, "Blocks": blocks, "AnalyzeDocumentModelVersion": MODEL_VERSION}


@dataclass
class TextractUnavailable:
    """Used when Docling is not installed: scanned pages fail with a clear message."""

    def analyze_document(self, **kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Amazon Textract is not called in local mode and Docling (OCR stand-in) is not "
                           "installed: pip install -r services/local_pipeline/requirements-docling.txt")

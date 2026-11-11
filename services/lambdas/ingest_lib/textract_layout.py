"""Textract ``AnalyzeDocument`` (LAYOUT + TABLES + FORMS) response for one page -> canonical layout.

Layout blocks come from the raw ``LAYOUT_*`` blocks in response order (Textract's implied
reading order); tables and key-value pairs are parsed with ``amazon-textract-textractor``
(``textractor.parsers.response_parser``) and attached to the layout block they sit in.
Geometry is converted with ``bbox.from_textract`` (ratios x page size, top-left origin).
"""

from __future__ import annotations

from typing import Any

from evidence_core.bbox import BBox, coverage, from_textract
from evidence_core.enums import CanonicalRole, Engine

from .layout import DocLayout, KeyValue, LayoutBlock, LayoutPage, TableCell, TableGrid, table_text
from .vocab import VocabIndex

_EXCLUDED = (CanonicalRole.PAGE_HEADER, CanonicalRole.PAGE_FOOTER)


def textractor_version() -> str:
    try:
        from importlib.metadata import version

        return f"textract AnalyzeDocument + textractor {version('amazon-textract-textractor')}"
    except Exception:  # noqa: BLE001
        return "textract AnalyzeDocument"


def _bbox_from_geometry(block: dict[str, Any], page: int, w: float, h: float) -> BBox | None:
    geo = (block.get("Geometry") or {}).get("BoundingBox")
    return from_textract(geo, page=page, page_width=w, page_height=h) if geo else None


def _bbox_from_entity(entity: Any, page: int, w: float, h: float) -> BBox | None:
    bb = getattr(entity, "bbox", None)
    if bb is None:
        return None
    return from_textract({"Left": bb.x, "Top": bb.y, "Width": bb.width, "Height": bb.height}, page=page,
                         page_width=w, page_height=h)


def _child_ids(block: dict[str, Any]) -> list[str]:
    return [i for r in block.get("Relationships") or [] if r.get("Type") == "CHILD" for i in r.get("Ids", [])]


def _text_of(block: dict[str, Any], by_id: dict[str, dict[str, Any]], depth: int = 0) -> str:
    if block.get("BlockType") in ("LINE", "WORD"):
        return str(block.get("Text") or "")
    if depth > 4:
        return ""
    parts = []
    for cid in _child_ids(block):
        child = by_id.get(cid)
        if child is None:
            continue
        if child.get("BlockType") == "LINE":
            parts.append(str(child.get("Text") or ""))
        elif str(child.get("BlockType", "")).startswith("LAYOUT_"):
            parts.append(_text_of(child, by_id, depth + 1))
    return "\n".join(p for p in parts if p)


def _parse_entities(response: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    from textractor.parsers import response_parser

    doc = response_parser.parse(response)
    return list(doc.tables), list(doc.key_values)


def textract_to_layout(response: dict[str, Any], *, page: int, page_width: float, page_height: float,
                       artifact_key: str, vocab: VocabIndex) -> DocLayout:
    w, h = page_width, page_height
    raw = response.get("Blocks") or []
    by_id = {b["Id"]: b for b in raw if "Id" in b}
    blocks: list[LayoutBlock] = []
    unknown: dict[str, int] = {}
    nested_layout_ids = {cid for b in raw if b.get("BlockType") == "LAYOUT_LIST" for cid in _child_ids(b)}

    for order, b in enumerate(raw):
        bt = str(b.get("BlockType", ""))
        if not (bt.startswith("LAYOUT_") or bt in ("TABLE_TITLE", "TABLE_FOOTER")) or b["Id"] in nested_layout_ids:
            continue
        role = vocab.role_for("textract", bt)
        if role is None:
            unknown[bt] = unknown.get(bt, 0) + 1
            role = CanonicalRole.TEXT
        conf = b.get("Confidence")
        blocks.append(
            LayoutBlock(
                block_id="", page=page, order=order, role=role, engine=Engine.TEXTRACT, engine_label=bt,
                text=_text_of(b, by_id).strip(), bbox=_bbox_from_geometry(b, page, w, h),
                artifact_key=artifact_key, pointer=b["Id"],
                confidence=round(float(conf) / 100, 4) if conf is not None else None,
                excluded=role in _EXCLUDED,
            )
        )

    if not blocks:  # LAYOUT not returned: fall back to lines so the page is not lost
        for order, b in enumerate(raw):
            if b.get("BlockType") != "LINE":
                continue
            conf = b.get("Confidence")
            blocks.append(LayoutBlock(
                block_id="", page=page, order=order, role=CanonicalRole.TEXT, engine=Engine.TEXTRACT,
                engine_label="LINE", text=str(b.get("Text") or ""), bbox=_bbox_from_geometry(b, page, w, h),
                artifact_key=artifact_key, pointer=b["Id"],
                confidence=round(float(conf) / 100, 4) if conf is not None else None))

    tables, kvs = _parse_entities(response) if raw else ([], [])
    end_order = len(raw) + 1
    for t in tables:
        grid = TableGrid(num_rows=0, num_cols=0, cells=[])
        for c in t.table_cells:
            grid.cells.append(TableCell(
                row=int(c.row_index) - 1, col=int(c.col_index) - 1, row_span=int(c.row_span or 1),
                col_span=int(c.col_span or 1), text=str(c.text or "").strip(),
                bbox=_bbox_from_entity(c, page, w, h), pointer=str(c.id), header=bool(c.is_column_header),
                confidence=round(float(c.confidence), 4) if c.confidence is not None else None))
        grid.num_rows = max((c.row + c.row_span for c in grid.cells), default=0)
        grid.num_cols = max((c.col + c.col_span for c in grid.cells), default=0)
        tb = _bbox_from_entity(t, page, w, h)
        host = _best_host(blocks, tb, CanonicalRole.TABLE)
        if host is None:
            host = LayoutBlock(block_id="", page=page, order=end_order, role=CanonicalRole.TABLE,
                               engine=Engine.TEXTRACT, engine_label="TABLE", text="", bbox=tb,
                               artifact_key=artifact_key, pointer=str(t.id))
            end_order += 1
            blocks.append(host)
        host.role = CanonicalRole.TABLE
        host.table = grid
        host.pointer = host.pointer if host.engine_label.startswith("LAYOUT_") else str(t.id)
        host.text = table_text(grid)

    for kv in kvs:
        key_text = " ".join(str(getattr(wd, "text", "")) for wd in (kv.key or [])).strip()
        value_text = (kv.value.get_text() if kv.value is not None else "").strip()
        if not key_text and not value_text:
            continue
        key_raw = by_id.get(str(kv.id), {})
        key_bbox = _bbox_from_geometry(key_raw, page, w, h) if key_raw else _bbox_from_entity(kv, page, w, h)
        value_bbox = _bbox_from_entity(kv.value, page, w, h) if kv.value is not None else None
        item = KeyValue(key=key_text, value=value_text, key_bbox=key_bbox, value_bbox=value_bbox,
                        pointer=str(kv.id), value_pointer=str(kv.value.id) if kv.value is not None else None,
                        confidence=round(float(kv.confidence), 4) if kv.confidence is not None else None)
        host = _best_host(blocks, key_bbox, CanonicalRole.KEY_VALUE)
        if host is None:
            host = LayoutBlock(block_id="", page=page, order=end_order, role=CanonicalRole.KEY_VALUE,
                               engine=Engine.TEXTRACT, engine_label="KEY_VALUE_SET", text="", bbox=key_bbox,
                               artifact_key=artifact_key, pointer=str(kv.id))
            end_order += 1
            blocks.append(host)
        host.role = CanonicalRole.KEY_VALUE
        host.kvs.append(item)
    for b in blocks:
        if b.kvs:
            b.text = "\n".join(f"{kv.key} {kv.value}".strip() for kv in b.kvs)
            b.confidence = min((kv.confidence for kv in b.kvs if kv.confidence is not None), default=b.confidence)

    layout = DocLayout(pages={page: LayoutPage(page=page, width=w, height=h, engine=Engine.TEXTRACT)},
                       blocks=blocks, unknown_labels=unknown)
    layout.renumber()
    return layout


def _best_host(blocks: list[LayoutBlock], box: BBox | None, role: CanonicalRole) -> LayoutBlock | None:
    """Layout block of ``role`` (or plain text) that covers most of ``box``."""
    if box is None:
        return None
    best, best_cov = None, 0.5
    for b in blocks:
        if b.bbox is None or b.role not in (role, CanonicalRole.TEXT) or (b.table is not None):
            continue
        cov = coverage(box, b.bbox)
        if cov > best_cov or (cov == best_cov and best is not None and b.role is role):
            best, best_cov = b, cov
    return best if best is not None and (best.role is role or best_cov >= 0.8) else None


def textract_block_count(response: dict[str, Any]) -> int:
    return sum(1 for b in response.get("Blocks") or [] if str(b.get("BlockType", "")).startswith("LAYOUT_"))

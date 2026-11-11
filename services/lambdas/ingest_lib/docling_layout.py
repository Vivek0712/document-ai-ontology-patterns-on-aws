"""DoclingDocument JSON (as returned by docling-serve, ``json_content``) -> canonical layout.

Reading order follows ``body.children`` (and ``furniture.children`` for page headers/footers),
descending into groups. Docling item bboxes are usually ``BOTTOMLEFT``; table cell bboxes are
usually ``TOPLEFT``; ``bbox.from_docling`` converts both.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from evidence_core.bbox import BBox, from_docling
from evidence_core.enums import CanonicalRole, Engine

from .layout import DocLayout, KeyValue, LayoutBlock, LayoutPage, TableCell, TableGrid, table_text
from .vocab import VocabIndex

_EXCLUDED = (CanonicalRole.PAGE_HEADER, CanonicalRole.PAGE_FOOTER)


def _resolve(doc: dict[str, Any], ref: str) -> dict[str, Any] | None:
    # "#/texts/12" -> doc["texts"][12]
    parts = ref.lstrip("#/").split("/")
    node: Any = doc
    try:
        for p in parts:
            node = node[int(p)] if isinstance(node, list) else node[p]
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    return node if isinstance(node, dict) else None


def _walk(doc: dict[str, Any], node: dict[str, Any], seen: set[str]) -> Iterator[dict[str, Any]]:
    for child in node.get("children", []) or []:
        ref = child.get("$ref") if isinstance(child, dict) else None
        if not ref or ref in seen:
            continue
        seen.add(ref)
        item = _resolve(doc, ref)
        if item is None:
            continue
        if ref.startswith("#/groups/"):
            yield from _walk(doc, item, seen)
            continue
        yield item
        if item.get("children"):
            yield from _walk(doc, item, seen)


def iter_items(doc: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Items in reading order: body first, then furniture (headers/footers) last."""
    seen: set[str] = set()
    yield from _walk(doc, doc.get("body", {}), seen)
    yield from _walk(doc, doc.get("furniture", {}), seen)


def _page_sizes(doc: dict[str, Any]) -> dict[int, tuple[float, float]]:
    out: dict[int, tuple[float, float]] = {}
    for key, p in (doc.get("pages") or {}).items():
        size = p.get("size") or {}
        out[int(p.get("page_no", key))] = (float(size.get("width", 612.0)), float(size.get("height", 792.0)))
    return out


def _table_grid(item: dict[str, Any], page: int, w: float, h: float) -> TableGrid:
    data = item.get("data") or {}
    cells = []
    for i, c in enumerate(data.get("table_cells") or []):
        bb = c.get("bbox")
        cells.append(
            TableCell(
                row=int(c.get("start_row_offset_idx", 0)),
                col=int(c.get("start_col_offset_idx", 0)),
                row_span=int(c.get("row_span", 1) or 1),
                col_span=int(c.get("col_span", 1) or 1),
                text=str(c.get("text") or "").strip(),
                bbox=from_docling(bb, page=page, page_height=h, page_width=w) if bb else None,
                pointer=f"{item.get('self_ref', '')}/data/table_cells/{i}",
                header=bool(c.get("column_header")),
            )
        )
    return TableGrid(num_rows=int(data.get("num_rows", 0) or 0), num_cols=int(data.get("num_cols", 0) or 0),
                     cells=cells)


def _key_values(item: dict[str, Any], page: int, w: float, h: float) -> list[KeyValue]:
    graph = item.get("graph") or {}
    cells = {c.get("cell_id"): c for c in graph.get("cells") or []}

    def bbox_of(cell: dict[str, Any]) -> BBox | None:
        prov = cell.get("prov") or {}
        bb = prov.get("bbox") if isinstance(prov, dict) else None
        pg = int(prov.get("page_no", page)) if isinstance(prov, dict) else page
        return from_docling(bb, page=pg, page_height=h, page_width=w) if bb else None

    out = []
    for link in graph.get("links") or []:
        if link.get("label") not in ("to_value", "to_key"):
            continue
        src, dst = cells.get(link.get("source_cell_id")), cells.get(link.get("target_cell_id"))
        if not src or not dst:
            continue
        key, val = (src, dst) if link.get("label") == "to_value" else (dst, src)
        out.append(KeyValue(key=str(key.get("text", "")).strip(), value=str(val.get("text", "")).strip(),
                            key_bbox=bbox_of(key), value_bbox=bbox_of(val),
                            pointer=f"{item.get('self_ref', '')}/graph/cells/{key.get('cell_id')}",
                            value_pointer=f"{item.get('self_ref', '')}/graph/cells/{val.get('cell_id')}"))
    return out


def docling_to_layout(doc: dict[str, Any], *, artifact_key: str, vocab: VocabIndex,
                      page_map: dict[int, int] | None = None) -> DocLayout:
    """``page_map`` maps the page numbers inside ``doc`` (a page subset sent to Docling) to the
    source PDF's page numbers. Identity when the whole document was converted."""
    sizes = _page_sizes(doc)
    pmap = page_map or {p: p for p in sizes}
    pages = {pmap.get(p, p): LayoutPage(page=pmap.get(p, p), width=w, height=h, engine=Engine.DOCLING)
             for p, (w, h) in sizes.items()}
    blocks: list[LayoutBlock] = []
    unknown: dict[str, int] = {}
    for order, item in enumerate(iter_items(doc)):
        label = str(item.get("label") or "text")
        prov = (item.get("prov") or [None])[0]
        if not prov:
            continue
        src_page = int(prov.get("page_no", 1))
        page = pmap.get(src_page, src_page)
        w, h = sizes.get(src_page, (612.0, 792.0))
        bb = prov.get("bbox")
        bbox = from_docling(bb, page=page, page_height=h, page_width=w) if bb else None
        role = vocab.role_for("docling", label)
        if role is None:
            unknown[label] = unknown.get(label, 0) + 1
            role = CanonicalRole.TEXT
        grid = None
        kvs: list[KeyValue] = []
        text = str(item.get("text") or "").strip()
        if role is CanonicalRole.TABLE:
            grid = _table_grid(item, page, w, h)
            text = table_text(grid)
        elif role is CanonicalRole.KEY_VALUE:
            kvs = _key_values(item, page, w, h)
            text = "\n".join(f"{kv.key} {kv.value}".strip() for kv in kvs) or text
        captions = [c.get("$ref") for c in item.get("captions") or [] if isinstance(c, dict) and c.get("$ref")]
        level = item.get("level")
        blocks.append(
            LayoutBlock(
                block_id="", page=page, order=order, role=role, engine=Engine.DOCLING, engine_label=label,
                text=text, bbox=bbox, artifact_key=artifact_key, pointer=str(item.get("self_ref", "")),
                level=int(level) if isinstance(level, int) else None, table=grid, kvs=kvs,
                caption_refs=[str(c) for c in captions], excluded=role in _EXCLUDED,
            )
        )
    layout = DocLayout(pages=pages, blocks=blocks, unknown_labels=unknown)
    layout.renumber()
    return layout


def docling_block_count(doc: dict[str, Any]) -> int:
    return sum(len(doc.get(k) or []) for k in ("texts", "tables", "pictures", "key_value_items"))

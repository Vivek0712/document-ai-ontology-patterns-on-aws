"""Field extraction: labelled values inside a unit, each with page + bbox (citations point here).

Sources, in order of trust:
1. key-value pairs from the engine (Textract FORMS ``KEY_VALUE_SET``, Docling key-value items);
2. table cells: the row-major cell sequence is split into label / amount segments and each
   amount pairs with the label before it (a Docling cell can hold ``98,400.00 2 Federal income
   tax withheld``: the value of box 1 followed by the label of box 2); a header-row table pairs
   amounts with ``row label / column header``; a two-column header-row table with no amount cells
   (``Term | Value``: ``Coupon | 4.25%3``) pairs each row's label cell with its value cell;
3. text lines ending in an amount (``Gross pay 9,000.00``).
Labels map to ``fin:`` concepts through the vocab snapshot (SKOS altLabel matching).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from evidence_core.bbox import BBox, contains, union
from evidence_core.enums import CanonicalRole
from evidence_core.ids import field_id as make_field_id
from evidence_core.ids import slugify
from evidence_core.models.units import BlockRef, FieldValue

from lambdas.ingest_lib.glyphs import GlyphLocator, raised_glyphs
from lambdas.ingest_lib.layout import LayoutBlock, TableCell
from lambdas.ingest_lib.vocab import VocabIndex, split_notation

AMOUNT = r"\(?-?\$?\s?(?:\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?|\d+\.\d{2})\)?%?|\d+(?:\.\d+)?%"
_AMOUNT_RE = re.compile(rf"(?<![\w.,])(?:{AMOUNT})(?![\w,])")
_LETTERS = re.compile(r"[A-Za-z]")
_TRAILING_MARKER = re.compile(r"^(?P<value>.*?\S)\s*(?P<m>[1-9])$")
_MARKED_AMOUNT = re.compile(rf"(?:{AMOUNT})\s*[1-9]")


def normalise_amount(value: str) -> str | None:
    """``"98,400.00"`` -> ``"98400.00"``; ``"(1,200.00)"`` -> ``"-1200.00"``; ``"4.25%"`` -> ``"4.25"``."""
    v = value.strip()
    neg = v.startswith("(") and v.endswith(")") or v.startswith("-")
    digits = re.sub(r"[^\d.]", "", v)
    if not digits or digits.count(".") > 1 or not re.search(r"\d", digits):
        return None
    if "." not in digits and "%" not in v:
        digits += ".00"
    return f"-{digits}" if neg else digits


@dataclass
class RawField:
    label: str
    value: str
    page: int
    label_bbox: BBox | None
    value_bbox: BBox | None
    source: str
    block_refs: list[BlockRef] = field(default_factory=list)
    confidence: float | None = None
    hint: BBox | None = None  # block bbox used when the engine gives no label/value boxes
    needs_marker: bool = False  # kept only if its trailing digit is a raised footnote marker (``5,010 1``)


def _segments(text: str) -> list[tuple[str, str]]:
    """Split a cell's text into ('label' | 'value', text) parts, in order."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _AMOUNT_RE.finditer(text):
        before = text[pos:m.start()].strip(" :;|")
        if before and _LETTERS.search(before):
            out.append(("label", before))
        out.append(("value", m.group(0).strip()))
        pos = m.end()
    rest = text[pos:].strip(" :;|")
    if rest and _LETTERS.search(rest):
        out.append(("label", rest))
    return out


def _cell_ref(block: LayoutBlock, cell: TableCell) -> BlockRef:
    return BlockRef(engine=block.engine, artifact_key=block.artifact_key, pointer=cell.pointer,
                    engine_label=f"{block.engine_label} cell")


def _has_header_row(rows: list[list[TableCell]]) -> bool:
    if not rows or len(rows) < 2:
        return False
    first = rows[0]
    # an engine's column_header flag is not trusted on its own: a header row holds no amounts
    no_amounts = all(not _AMOUNT_RE.search(c.text) for c in first if c.text)
    later_amounts = any(_AMOUNT_RE.search(c.text) for row in rows[1:] for c in row)
    if any(c.header for c in first):
        return no_amounts
    return no_amounts and later_amounts and len(first) >= 2


def table_fields(block: LayoutBlock) -> list[RawField]:
    grid = block.table
    if grid is None:
        return []
    rows = grid.rows()
    out: list[RawField] = []
    if _is_term_value_table(rows):
        return _two_column_fields(block, rows)
    if _has_header_row(rows):
        headers = {c.col: c for c in rows[0]}
        for row in rows[1:]:
            row_label_cells = [c for c in row if c.text and not _AMOUNT_RE.fullmatch(c.text.strip())
                               and not _MARKED_AMOUNT.fullmatch(c.text.strip())]
            row_label = " ".join(c.text for c in row_label_cells[:2]).strip()
            for c in row:
                m = _AMOUNT_RE.fullmatch(c.text.strip()) if c.text else None
                marked = _MARKED_AMOUNT.fullmatch(c.text.strip()) if c.text and not m else None
                if not m and not marked:
                    continue
                head = headers.get(c.col)
                label = " · ".join(x for x in (row_label, head.text if head else "") if x) or "value"
                lbox = row_label_cells[0].bbox if row_label_cells else None
                out.append(RawField(label=label, value=c.text.strip(), page=block.page, label_bbox=lbox,
                                    value_bbox=c.bbox,
                                    source=f"{block.engine.value} table cell (row {c.row}, col {c.col})",
                                    block_refs=[_cell_ref(block, c)] + ([_cell_ref(block, row_label_cells[0])]
                                                                        if row_label_cells else []),
                                    confidence=c.confidence, needs_marker=marked is not None))
        return out
    pending: tuple[str, TableCell] | None = None
    for row in rows:
        for c in row:
            for kind, text in _segments(c.text):
                if kind == "label":
                    if pending is not None and len(text) <= 2:  # a code such as "D" in "12a | D 6,600.00"
                        pending = (f"{pending[0]} {text}", pending[1])
                    else:
                        pending = (text, c)
                    continue
                if pending is None:
                    continue
                label, lcell = pending
                conf = min((x for x in (c.confidence, lcell.confidence) if x is not None), default=None)
                refs = [_cell_ref(block, lcell)] + ([_cell_ref(block, c)] if c is not lcell else [])
                out.append(RawField(label=label, value=text, page=block.page, label_bbox=lcell.bbox,
                                    value_bbox=c.bbox,
                                    source=f"{block.engine.value} table cell (row {c.row}, col {c.col})",
                                    block_refs=refs, confidence=conf))
                pending = None
    return out


def _is_term_value_table(rows: list[list[TableCell]]) -> bool:
    """``Term | Value`` / ``Item | Value``: two columns, a text header row, and mostly text values (dates, names,
    identifiers), so pairing amounts with a column header would drop most rows."""
    if len(rows) < 3 or any(len(r) != 2 for r in rows):
        return False
    head = sorted(rows[0], key=lambda c: c.col)
    if not all(c.text and _LETTERS.search(c.text) and not _AMOUNT_RE.search(c.text) for c in head):
        return False
    values = [sorted(r, key=lambda c: c.col)[1].text.strip() for r in rows[1:]]
    amounts = sum(1 for v in values if _AMOUNT_RE.fullmatch(v) or _MARKED_AMOUNT.fullmatch(v))
    return amounts * 2 < len(values)


def _two_column_fields(block: LayoutBlock, rows: list[list[TableCell]]) -> list[RawField]:
    """``Term | Value`` tables: each body row's first cell labels its second (header row skipped)."""
    if any(len(row) != 2 for row in rows):
        return []
    out = []
    for row in rows[1:]:
        lcell, vcell = sorted(row, key=lambda c: c.col)
        if not lcell.text or not vcell.text or not _LETTERS.search(lcell.text):
            continue
        out.append(RawField(label=lcell.text.strip(), value=vcell.text.strip(), page=block.page,
                            label_bbox=lcell.bbox, value_bbox=vcell.bbox,
                            source=f"{block.engine.value} table cell (row {vcell.row}, col {vcell.col})",
                            block_refs=[_cell_ref(block, lcell), _cell_ref(block, vcell)],
                            confidence=min((x for x in (lcell.confidence, vcell.confidence) if x is not None),
                                           default=None)))
    return out


def kv_fields(block: LayoutBlock) -> list[RawField]:
    out = []
    for kv in block.kvs:
        if not kv.value:
            continue
        refs = [BlockRef(engine=block.engine, artifact_key=block.artifact_key, pointer=kv.pointer,
                         engine_label="KEY_VALUE_SET" if block.engine.value == "textract" else "key_value")]
        if kv.value_pointer:
            refs.append(BlockRef(engine=block.engine, artifact_key=block.artifact_key, pointer=kv.value_pointer,
                                 engine_label="KEY_VALUE_SET VALUE" if block.engine.value == "textract" else "value"))
        out.append(RawField(label=kv.key, value=kv.value, page=block.page, label_bbox=kv.key_bbox,
                            value_bbox=kv.value_bbox, source=f"{block.engine.value} key-value",
                            block_refs=refs, confidence=kv.confidence))
    return out


def line_fields(block: LayoutBlock) -> list[RawField]:
    """Label/amount pairs inside text: ``Gross pay 9,000.00 401(k) deferral 600.00`` -> two fields."""
    roles = (CanonicalRole.TEXT, CanonicalRole.LIST_ITEM, CanonicalRole.KEY_VALUE, CanonicalRole.TABLE)
    if block.role not in roles or block.kvs or block.table is not None:
        return []
    out = []
    for line in block.text.splitlines():
        pending: str | None = None
        for kind, text in _segments(line):
            if kind == "label":
                pending = text.strip(" :.-–")
                continue
            if pending is None or len(pending) > 80:
                pending = None
                continue
            out.append(RawField(label=pending, value=text, page=block.page, label_bbox=None, value_bbox=None,
                                source=f"{block.engine.value} text line", block_refs=[block.block_ref()],
                                confidence=block.confidence, hint=block.bbox))
            pending = None
    return out


_CODE_MAX = 2  # one- or two-letter runs ("D", "X") are box codes, not labels


@dataclass
class GeoField:
    field: RawField
    label_block: LayoutBlock
    value_block: LayoutBlock
    concept: str | None


def geometric_fields(blocks: list[LayoutBlock], glyphs: GlyphLocator, vocab: VocabIndex, doc_type: str | None
                     ) -> list[GeoField]:
    """Pair values with labels by position on one digital page, across engine blocks (a form whose
    boxes the engine emitted as separate text items).

    Runs come from the PDF text layer and are owned by the block whose bbox contains them. For each
    amount run, candidate labels are runs on the same line to its left (gap <= 320 pt) or above-left
    within the same box (<= 45 pt above, starting <= 300 pt to the left). Ranking: labels that map
    to a vocab concept, then labels not used yet, then same-line, then the nearest. String concepts
    (names) take the run directly below their label.
    """
    from lambdas.ingest_lib.glyphs import text_runs

    eligible = [b for b in blocks if b.bbox is not None and b.table is None and not b.kvs and not b.excluded]
    if not eligible:
        return []
    page = eligible[0].page
    runs: list[tuple[str, BBox, LayoutBlock]] = []
    for text, box in text_runs(glyphs, page):
        owner = next((b for b in eligible if b.bbox is not None and contains(b.bbox.expand(2), box, 2)), None)
        if owner is not None:
            runs.append((text, box, owner))
    amounts = [r for r in runs if _AMOUNT_RE.fullmatch(r[0])]
    labels = [r for r in runs if _LETTERS.search(r[0]) and len(r[0]) > _CODE_MAX and not _AMOUNT_RE.fullmatch(r[0])]
    concept_of = {r[0]: vocab.concept_for_label(r[0], doc_type) for r in labels}
    out: list[GeoField] = []
    used: set[int] = set()

    def make(label: str, lb: BBox, lblock: LayoutBlock, value: str, vb: BBox, vblock: LayoutBlock) -> GeoField:
        refs = [lblock.block_ref()] + ([vblock.block_ref()] if vblock is not lblock else [])
        fc = concept_of.get(label)
        return GeoField(RawField(label=label, value=value, page=page, label_bbox=lb, value_bbox=vb,
                                 source=f"{lblock.engine.value} blocks + text-layer geometry", block_refs=refs),
                        lblock, vblock, fc.concept if fc else None)

    for value, vb, vblock in amounts:
        best: tuple[tuple[int, int, int, float, float], tuple[str, BBox, LayoutBlock]] | None = None
        for cand in labels:
            label, lb, _ = cand
            same_line = min(lb.y1, vb.y1) - max(lb.y0, vb.y0) > 0.5 * min(lb.height, vb.height)
            if same_line:
                gap = vb.x0 - lb.x1
                if not 0 <= gap <= 320:
                    continue
                dist = gap / 10
            else:
                dy = vb.y0 - lb.y1
                if not (0 <= dy <= 45 and lb.x0 <= vb.x0 + 2 and vb.x0 - lb.x0 <= 300):
                    continue
                dist = dy
            key = (0 if concept_of.get(label) else 1, 1 if id(lb) in used else 0, 0 if same_line else 1, dist, -lb.x0)
            if best is None or key < best[0]:
                best = (key, cand)
        if best is None:
            continue
        label, lb, lblock = best[1]
        used.add(id(lb))
        out.append(make(label, lb, lblock, value, vb, vblock))
    for label, lb, lblock in labels:
        fc = concept_of.get(label)
        if not fc or fc.datatype != "string":
            continue
        below = [r for r in runs if r[1] is not lb and 0 <= r[1].y0 - lb.y1 <= 30 and abs(r[1].x0 - lb.x0) <= 15
                 and not concept_of.get(r[0]) and not _AMOUNT_RE.fullmatch(r[0])]
        if below:
            t, b, blk = min(below, key=lambda r: r[1].y0)
            out.append(make(label, lb, lblock, t, b, blk))
    return out


def raw_fields_for(block: LayoutBlock) -> list[RawField]:
    if block.table is not None:
        return table_fields(block)
    if block.kvs:
        return kv_fields(block)
    return line_fields(block)


@dataclass
class FieldBuilder:
    vocab: VocabIndex
    doc_type: str | None
    use_concepts: bool
    period: str | None
    glyphs: GlyphLocator | None = None
    markers: dict[str, str] = field(default_factory=dict)  # fieldId -> raised marker stripped from its value

    def _refine(self, page: int, text: str, hint: BBox | None) -> BBox | None:
        if self.glyphs is None:
            return hint
        found = self.glyphs.find(page, text, hint)
        return found or hint

    def _split_marker(self, text: str, page: int, box: BBox | None) -> tuple[str, str | None]:
        """``4.25%3`` -> (``4.25%``, ``3``) when the trailing digit is printed raised inside ``box``."""
        m = _TRAILING_MARKER.match(text)
        if not m or self.glyphs is None or box is None:
            return text, None
        if any(ch == m["m"] for ch, _ in raised_glyphs(self.glyphs, page, box)):
            return m["value"], m["m"]
        return text, None

    def build(self, raw: list[RawField]) -> list[FieldValue]:
        self.markers = {}
        out: list[FieldValue] = []
        used_ids: set[str] = set()
        seen_concepts: set[str] = set()
        for rf in raw:
            # a raised footnote marker printed after a value ("4.25%3") or a row label ("Corporate2") is not part
            # of either: strip it, remember it for the unit's marker link
            value, marker = self._split_marker(rf.value, rf.page, rf.value_bbox or rf.hint)
            if rf.needs_marker and marker is None:
                continue  # "5,010 1" with a plain 1: not a printed value we can name
            label, label_marker = self._split_marker(rf.label, rf.page, rf.label_bbox)
            marker = marker or label_marker
            fc = self.vocab.concept_for_label(label, self.doc_type) if self.use_concepts else None
            if fc and fc.concept in seen_concepts:
                continue
            hint = rf.hint
            label_box = self._refine(rf.page, label, rf.label_bbox or hint)
            value_box = self._refine(rf.page, value, rf.value_bbox or hint)
            boxes = [b for b in (label_box, value_box) if b is not None and b.page == rf.page]
            bbox = union(boxes).rounded() if boxes else None
            if fc:
                seen_concepts.add(fc.concept)
                fid = make_field_id(f"box{fc.notation}") if fc.notation else make_field_id(fc.pref_label)
            else:
                notation, tail = split_notation(label)
                fid = make_field_id(f"box{notation}" if notation and not tail else label)
            base, n = fid, 2
            while fid in used_ids:
                fid = f"{base[:37]}-{n}"
                n += 1
            used_ids.add(fid)
            if marker:
                self.markers[fid] = marker
            numeric = normalise_amount(value) if _AMOUNT_RE.fullmatch(value.strip()) else None
            out.append(FieldValue(
                field_id=fid, label=label, value=value, value_number=numeric,
                currency="USD" if numeric is not None and "%" not in value else None,
                concept=fc.concept if fc else None,
                period=self.period if (fc is None or fc.doc_type == self.doc_type) else None,
                bbox=bbox, source=rf.source, confidence=rf.confidence, block_refs=rf.block_refs,
            ))
        return out


def field_slug(label: str) -> str:
    return slugify(label, 40)

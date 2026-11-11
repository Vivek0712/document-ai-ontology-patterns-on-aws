"""X-ray core: what the v1.0 naive lane "saw" on a page, mapped back onto the page.

This is an **inspection**, never an extraction. v1.0 chunks carry no bbox and must stay
unresolvable for citations, so nothing computed here is written into units, the index or claims.

Inputs (all real artifacts, no model calls):

- ``pdf``: the Bronze PDF bytes (the same object both lanes read);
- ``chunks``: the v1.0 chunk texts in order (from ``silver/.../1.0/units/units.jsonl``), or a what-if
  re-split of the same text layer with the real v1.0 splitter (``ingest_lib.chunker.split_pages``);
- ``evidence``: the v2.3.1 evidence units on the page (Gold, so held units are already absent);
- ``held``: regions the PII gate held on the page (tokens inside them are dropped, never shipped). On a page
  with held regions the drop fails closed: a token the aligner could not place is dropped too.

Method:

1. The page text layer is re-read with the same pypdf call the 1.0 ``extract`` stage uses, and split
   into whitespace tokens. This token stream is the order the naive lane reads the page in.
2. Its non-whitespace characters are aligned (``difflib.SequenceMatcher``, no autojunk) to the
   pypdfium2 character stream of the same bytes; each aligned character takes its pdfium char box.
   A token's box is the union of its characters' boxes. Two guards keep a spurious match from stretching a
   box across the page: a one-character matching block only counts when it is a whole token, and a token
   whose characters do not sit on one row, close together, gets no box. Coverage = aligned / total
   characters; a page with no text layer has no coverage (``None``), not a perfect one.
3. Each chunk is located in the token stream (chunks are contiguous token runs; overlap repeats a
   tail), and its token boxes are merged into line rectangles: the chunk "slice" on the page.
4. Text-layer lines (pypdf line breaks) become the reading path, in stream order, each classified as
   ``label`` (a v2.3.1 field label or a numbered box label), ``value`` (an amount) or ``other``.
5. Pairs: for every v2.3.1 field with an amount, the **positional** value (the i-th numbered label in
   stream order read with the i-th amount in stream order) and the **geometric** value (the field value
   whose glyphs lie inside the field's box). The positional value is a *derived hypothesis* about a
   position-only reading, never a statement about what a model did. ``positionalSameLine``
   says whether both readings land on the same printed line; ``positionalSameValue`` whether they give the
   same figure (Box 3 and Box 5 of a W-2 can print the same amount).
6. Answer numbers (optional): the money figures of a logged naive answer, matched to the value lines on the
   page. A match with a pair's positional value is what turns the hypothesis into an observation on that run.

Everything is deterministic for given bytes. Coordinates are PDF points, top-left origin. The output
follows the `XrayPage` contract in contracts/api.ts (plus the documented `units`,
`heldRegions` and per-chunk `coverage` extensions).
"""

from __future__ import annotations

import difflib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

SCHEMA = "rp.xray/1"

# An amount as printed on the lending forms: 98,400.00 · 1,522.50 · -12.00 · $6,541.50
_AMOUNT = re.compile(r"^[-−]?\$?\d{1,3}(?:,\d{3})*\.\d{2}$")
# A numbered form box label: "1 Wages, tips…", "12a See instructions…", a bare "9"
_BOX_LABEL = re.compile(r"^(\d{1,2}[a-d]?)(?:\s+\S.*)?$")
_WS = re.compile(r"\s+")

Box = tuple[float, float, float, float]  # x0, y0, x1, y1


# --------------------------------------------------------------------------------------------- geometry


def _union(boxes: Iterable[Box]) -> Box | None:
    bs = list(boxes)
    if not bs:
        return None
    return (min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs))


def _intersects(a: Box, b: Box) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _inside(inner: Box, outer: Box, tol: float = 2.0) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2, (inner[1] + inner[3]) / 2
    return outer[0] - tol <= cx <= outer[2] + tol and outer[1] - tol <= cy <= outer[3] + tol


def _rect(b: Box, nd: int = 2) -> dict[str, float]:
    """XrayRect: ``{x0, y0, x1, y1}`` in PDF points, top-left origin."""
    return {"x0": round(b[0], nd), "y0": round(b[1], nd), "x1": round(b[2], nd), "y1": round(b[3], nd)}


def merge_rows(boxes: Sequence[Box], *, gap: float = 18.0, pad: float = 1.0) -> list[Box]:
    """Merge boxes, in reading order, into row rectangles: consecutive boxes on the same text row
    (vertical centres within half a line height) and less than ``gap`` points apart join one rect."""
    rows: list[list[float]] = []
    for b in boxes:
        h = b[3] - b[1]
        cy = (b[1] + b[3]) / 2
        if rows:
            r = rows[-1]
            rh = r[3] - r[1]
            rcy = (r[1] + r[3]) / 2
            same_row = abs(cy - rcy) <= max(h, rh) * 0.5
            near = b[0] - r[2] <= gap and r[0] - b[2] <= gap
            if same_row and near:
                r[0], r[1], r[2], r[3] = min(r[0], b[0]), min(r[1], b[1]), max(r[2], b[2]), max(r[3], b[3])
                continue
        rows.append(list(b))
    return [(r[0] - pad, r[1] - pad, r[2] + pad, r[3] + pad) for r in rows]


# --------------------------------------------------------------------------------------------- inputs


@dataclass(frozen=True)
class PageChar:
    char: str
    box: Box


@dataclass
class PdfPage:
    page: int
    width: float
    height: float
    text: str  # pypdf text layer, default mode (the 1.0 extract stage)
    chars: list[PageChar]  # pypdfium2 characters, whitespace removed, in pdfium order


def read_pdf(pdf: bytes) -> list[PdfPage]:
    """Text layer (pypdf, as the 1.0 lane reads it) and character boxes (pypdfium2) for every page."""
    import pypdfium2 as pdfium

    from lambdas.ingest_lib.pdf import page_texts

    texts = {p: (t, w, h) for p, t, w, h in page_texts(pdf)}
    out: list[PdfPage] = []
    doc = pdfium.PdfDocument(pdf)
    try:
        for i in range(len(doc)):
            page = doc[i]
            tp = page.get_textpage()
            try:
                _, ph = page.get_size()
                chars: list[PageChar] = []
                for k in range(tp.count_chars()):
                    c = tp.get_text_range(k, 1).replace("\x02", "-")
                    if not c or c.isspace():
                        continue
                    left, bottom, right, top = tp.get_charbox(k, loose=False)
                    if right <= left or top <= bottom:
                        continue
                    chars.append(PageChar(c, (left, ph - top, right, ph - bottom)))
            finally:
                tp.close()
                page.close()
            text, w, h = texts.get(i + 1, ("", 0.0, 0.0))
            out.append(PdfPage(page=i + 1, width=round(w, 2), height=round(h, 2), text=text, chars=chars))
    finally:
        doc.close()
    return out


# --------------------------------------------------------------------------------------------- tokens


@dataclass
class Token:
    index: int  # ordinal in the document token stream (0-based)
    page: int
    line: int  # 1-based line ordinal on its page
    text: str
    box: Box | None = None
    aligned: int = 0  # aligned characters
    held: bool = False


@dataclass
class Line:
    page: int
    ordinal: int
    text: str
    tokens: list[int] = field(default_factory=list)  # global token indices
    kind: str = "other"  # label | value | other
    field_id: str | None = None


def tokenize(pages: Sequence[PdfPage]) -> tuple[list[Token], list[Line]]:
    """The document token stream, page by page and line by line, as the 1.0 splitter sees it."""
    tokens: list[Token] = []
    lines: list[Line] = []
    for p in pages:
        ordinal = 0
        for raw in p.text.strip().split("\n"):
            words = raw.split()
            if not words:
                continue
            ordinal += 1
            line = Line(page=p.page, ordinal=ordinal, text=" ".join(words))
            for w in words:
                line.tokens.append(len(tokens))
                tokens.append(Token(index=len(tokens), page=p.page, line=ordinal, text=w))
            lines.append(line)
    return tokens, lines


def _one_row_together(boxes: Sequence[Box]) -> bool:
    """True when character boxes sit on one text row and close together (a plausible single token)."""
    if len(boxes) < 2:
        return True
    h = max(b[3] - b[1] for b in boxes)
    cys = [(b[1] + b[3]) / 2 for b in boxes]
    if max(cys) - min(cys) > 0.6 * h:
        return False
    xs = sorted(boxes)
    return all(nxt[0] - cur[2] <= 3 * h for cur, nxt in zip(xs, xs[1:], strict=False))


def align_page(tokens: Sequence[Token], page: PdfPage) -> float | None:
    """Give each token of ``page`` the union box of its aligned pdfium characters. Returns coverage, or None
    when the page has no text layer (nothing to align is not the same as everything aligned)."""
    mine = [t for t in tokens if t.page == page.page]
    owner: list[int] = []  # non-whitespace char -> position in ``mine``
    for i, t in enumerate(mine):
        owner.extend([i] * len(t.text))
    target = "".join(t.text for t in mine)
    source = "".join(c.char for c in page.chars)
    if not target:
        return None
    sm = difflib.SequenceMatcher(None, target, source, autojunk=False)
    per_token: dict[int, list[Box]] = {}
    for a, b, n in sm.get_matching_blocks():
        # a lone matched character is only trusted when it is a whole token (a bare "9", a box code "D");
        # otherwise it is as likely to be a digit picked up from an unrelated glyph further down the page
        if n == 1 and len(mine[owner[a]].text) != 1:
            continue
        for k in range(n):
            per_token.setdefault(owner[a + k], []).append(page.chars[b + k].box)
    for i, t in enumerate(mine):
        boxes = per_token.get(i, [])
        if not _one_row_together(boxes):
            boxes = []  # a union across rows or across the page would draw a box around unrelated glyphs
        t.aligned = len(boxes)
        t.box = _union(boxes)
    return sum(t.aligned for t in mine) / len(target)


# --------------------------------------------------------------------------------------------- chunks


@dataclass
class ChunkSpan:
    n: int  # 1-based chunk ordinal
    start: int  # first token index (inclusive)
    end: int  # last token index (exclusive)
    tokens: int
    pages: list[int]


def locate_chunks(tokens: Sequence[Token], chunk_texts: Sequence[str]) -> tuple[list[ChunkSpan], list[int]]:
    """Find each chunk as a contiguous run of the token stream (chunks are emitted in order; an overlap
    repeats the previous chunk's tail, so each search starts after the previous chunk's start).
    Returns the spans found and the ordinals of chunks that are not a contiguous run (reported, never raised)."""
    words = [t.text for t in tokens]
    spans: list[ChunkSpan] = []
    missing: list[int] = []
    cursor = 0
    for n, text in enumerate(chunk_texts, start=1):
        cw = text.split()
        if not cw:
            continue
        start = _find_run(words, cw, cursor)
        if start is None:
            missing.append(n)
            continue
        end = start + len(cw)
        spans.append(ChunkSpan(n=n, start=start, end=end, tokens=len(cw),
                               pages=sorted({tokens[i].page for i in range(start, end)})))
        cursor = start + 1
    return spans, missing


def _find_run(words: Sequence[str], run: Sequence[str], frm: int) -> int | None:
    first, k = run[0], len(run)
    for i in range(frm, len(words) - k + 1):
        if words[i] == first and list(words[i:i + k]) == list(run):
            return i
    return None


def resplit(pages: Sequence[PdfPage], size: int, overlap_pct: int) -> list[str]:
    """A what-if re-split of the same text layer with the real v1.0 splitter."""
    from lambdas.ingest_lib.chunker import split_pages

    overlap = size * overlap_pct // 100
    return [c.text for c in split_pages([(p.page, p.text) for p in pages], size=size, overlap=overlap)]


# --------------------------------------------------------------------------------------------- lines


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip().lower()


def classify_lines(lines: Sequence[Line], field_labels: Iterable[str]) -> None:
    """``value`` = an amount; ``label`` = a v2.3.1 field label, or a numbered box label printed before
    the first amount on its page (the form's label block). Everything else is ``other``."""
    known = {_norm(x) for x in field_labels}
    first_value: dict[int, int] = {}
    for ln in lines:
        if _AMOUNT.match(ln.text):
            ln.kind = "value"
            first_value.setdefault(ln.page, ln.ordinal)
    for ln in lines:
        if ln.kind == "value":
            continue
        before_values = ln.ordinal < first_value.get(ln.page, 10**9)
        if _norm(ln.text) in known or (before_values and _BOX_LABEL.match(ln.text)):
            ln.kind = "label"


# --------------------------------------------------------------------------------------------- pairs


@dataclass
class EvidenceField:
    field_id: str
    label: str
    value: str
    concept: str | None
    box: Box | None
    page: int | None
    numeric: bool


@dataclass
class EvidenceUnit:
    unit_id: str
    label: str
    unit_type: str
    boxes: list[tuple[int, Box]]
    members: list[dict[str, Any]]
    fields: list[EvidenceField]


def evidence_from_units(units: Iterable[dict[str, Any]]) -> list[EvidenceUnit]:
    """v2.3.1 Unit JSON (camelCase, as in units.jsonl) -> the parts the X-ray draws. Held units are skipped."""

    def bx(b: dict[str, Any] | None) -> tuple[int, Box] | None:
        if not b:
            return None
        return int(b["page"]), (float(b["x0"]), float(b["y0"]), float(b["x1"]), float(b["y1"]))

    out: list[EvidenceUnit] = []
    for u in units:
        if u.get("piiFlag") == "held":
            continue
        fields = []
        for f in u.get("fields", []):
            fb = bx(f.get("bbox"))
            fields.append(EvidenceField(
                field_id=f["fieldId"], label=f.get("label", ""), value=str(f.get("value", "")),
                concept=f.get("concept"), box=fb[1] if fb else None, page=fb[0] if fb else None,
                numeric=f.get("valueNumber") is not None))
        members = []
        for m in u.get("members", []):
            mb = bx(m.get("bbox"))
            if mb:
                members.append({"memberId": m["memberId"], "role": m.get("role"), "page": mb[0],
                                "bbox": _rect(mb[1]), "text": m.get("text"), "phase": m.get("phase")})
        out.append(EvidenceUnit(
            unit_id=u["unitId"], label=u.get("label", ""), unit_type=u.get("unitType", ""),
            boxes=[b for b in (bx(x) for x in u.get("bboxes", [])) if b], members=members, fields=fields))
    return out


@dataclass
class Pair:
    field: EvidenceField
    unit_id: str
    label: Line
    label_index: int  # 1-based position in the label sequence used for the positional reading
    positional: Line | None
    geometric: Line | None


def pair_fields(lines: Sequence[Line], tokens: Sequence[Token], evidence: Sequence[EvidenceUnit]) -> list[Pair]:
    """Positional vs geometric value for every v2.3.1 field that holds an amount."""
    by_page: dict[int, list[Line]] = {}
    for ln in lines:
        by_page.setdefault(ln.page, []).append(ln)
    pairs: list[Pair] = []
    for eu in evidence:
        for f in eu.fields:
            if not f.numeric or f.page is None:
                continue
            page_lines = by_page.get(f.page, [])
            labels = [ln for ln in page_lines if ln.kind == "label"]
            numbered = [ln for ln in labels if _BOX_LABEL.match(ln.text)]
            values = [ln for ln in page_lines if ln.kind == "value"]
            label_line = next((ln for ln in labels if _norm(ln.text) == _norm(f.label)), None)
            if label_line is None:
                continue
            label_line.field_id = label_line.field_id or f.field_id
            # positional (derived hypothesis): i-th label of the label block with the i-th amount in stream order
            seq = numbered if label_line in numbered else labels
            i = seq.index(label_line)
            positional = values[i] if i < len(values) else None
            # geometric: the field's value printed inside the field's box
            geometric = None
            for ln in values:
                box = _line_box(ln, tokens)
                if ln.text == f.value and f.box is not None and box is not None and _inside(box, f.box):
                    geometric = ln
                    break
            if geometric is not None:
                geometric.field_id = geometric.field_id or f.field_id
            pairs.append(Pair(field=f, unit_id=eu.unit_id, label=label_line, label_index=i + 1,
                              positional=positional, geometric=geometric))
    return pairs


def _line_box(ln: Line, tokens: Sequence[Token]) -> Box | None:
    return _union(tokens[i].box for i in ln.tokens if tokens[i].box is not None and not tokens[i].held)


# --------------------------------------------------------------------------------------------- answer numbers

_MONEY_IN_TEXT = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}(?![\d])")


def canonical_money(s: str) -> str | None:
    """'98400.00' / '98,400.00' / '$98,400.00' -> '98,400.00' (two decimals, thousands separators)."""
    digits = s.replace("$", "").replace(",", "").strip()
    try:
        return f"{float(digits):,.2f}"
    except ValueError:
        return None


def answer_numbers(run_id: str, texts: Iterable[str], lines: Sequence[Line], pairs: Sequence[Pair], page: int
                   ) -> list[dict[str, Any]]:
    """Each money figure of a logged answer (in order, unique), matched to value lines on ``page`` by exact
    canonical string. ``isPositionalValueOf`` names the field whose positional value the figure equals there."""
    seen: list[str] = []
    for text in texts:
        for m in _MONEY_IN_TEXT.findall(text or ""):
            c = canonical_money(m)
            if c and c not in seen:
                seen.append(c)
    on_page = [ln for ln in lines if ln.page == page and ln.kind == "value"]
    out = []
    for value in seen:
        matched = [ln.ordinal for ln in on_page if canonical_money(ln.text) == value]
        owner = next((p.field.field_id for p in pairs if p.positional is not None and p.label.page == page
                      and p.positional.ordinal in matched), None)
        entry: dict[str, Any] = {"runId": run_id, "value": value, "matchedLines": matched}
        if owner:
            entry["isPositionalValueOf"] = owner
        out.append(entry)
    return out


# --------------------------------------------------------------------------------------------- assemble

COVERAGE_FLOOR = 0.98  # below this a page's X-ray layer is disabled


@dataclass
class XrayInput:
    doc_id: str
    page: int
    pdf_pages: list[PdfPage]
    chunk_texts: list[str]  # control chunks (stored v1.0 units), in order
    size_tokens: int  # requested size; == control size means "control"
    control_size: int
    overlap_pct: int
    evidence: list[EvidenceUnit] = field(default_factory=list)
    held: list[Box] = field(default_factory=list)  # union of both lanes' held regions on ``page``
    what_if_sizes: tuple[int, ...] = ()  # sizes the API serves (control first), echoed for the UI
    chunk_ids: list[str] = field(default_factory=list)  # unitIds of the control chunks
    held_chunk_ids: list[str] = field(default_factory=list)  # control chunks the PII gate held (dropped whole)
    answer: tuple[str, list[str]] | None = None  # (runId, answer text + claim texts) of a logged naive run
    sources: dict[str, Any] = field(default_factory=dict)
    image: dict[str, Any] = field(default_factory=dict)
    computed_at: str = ""


def _short_doc(doc_id: str) -> str:
    return doc_id.rsplit("-", 1)[-1]


def build_xray(inp: XrayInput) -> dict[str, Any]:
    """The X-ray payload for one page (``XrayPage``, schema ``rp.xray/1``)."""
    page = next((p for p in inp.pdf_pages if p.page == inp.page), None)
    if page is None:
        raise ValueError(f"page {inp.page} is not in the document")
    tokens, lines = tokenize(inp.pdf_pages)
    coverage = {p.page: align_page(tokens, p) for p in inp.pdf_pages}
    warnings: list[str] = []

    # held PII: tokens inside either lane's held regions, plus every token of a held 1.0 chunk. On a page with
    # held regions this fails closed: a token without a (complete) box cannot be shown to lie outside them.
    control_spans, control_missing = locate_chunks(tokens, inp.chunk_texts)
    held_ordinals = {n for n, uid in enumerate(inp.chunk_ids, start=1) if uid in inp.held_chunk_ids}
    for s in control_spans:
        if s.n in held_ordinals:
            for i in range(s.start, s.end):
                tokens[i].held = True
    for t in tokens:
        if t.page != inp.page or not inp.held:
            continue
        if t.box is None or t.aligned < len(t.text) or any(_intersects(t.box, h) for h in inp.held):
            t.held = True

    what_if = inp.size_tokens != inp.control_size
    if what_if:
        spans, missing = locate_chunks(tokens, resplit(inp.pdf_pages, inp.size_tokens, inp.overlap_pct))
    else:
        spans, missing = control_spans, control_missing
    if missing:
        warnings.append(f"chunks not found as contiguous runs of the text layer: {missing}")
    page_coverage = coverage.get(inp.page)
    if page_coverage is not None and page_coverage < COVERAGE_FLOOR:
        warnings.append(f"character alignment {page_coverage:.3f} is below {COVERAGE_FLOOR}")
    splitter_check = resplit(inp.pdf_pages, inp.control_size, inp.overlap_pct) == list(inp.chunk_texts)

    classify_lines(lines, [f.label for eu in inp.evidence for f in eu.fields])
    pairs = [p for p in pair_fields(lines, tokens, inp.evidence) if p.label.page == inp.page]

    chunk_of: dict[int, list[int]] = {}
    for s in spans:
        for i in range(s.start, s.end):
            chunk_of.setdefault(i, []).append(s.n)

    def line_out(ln: Line) -> dict[str, Any]:
        toks = [tokens[i] for i in ln.tokens]
        visible = [t.box for t in toks if not t.held and t.box is not None]
        box = _union(visible)
        out = {
            "ordinal": ln.ordinal, "kind": ln.kind,
            "text": " ".join("[held]" if t.held else t.text for t in toks),
            "rects": [_rect(b) for b in merge_rows(visible)],
            "tokenStart": ln.tokens[0], "tokenEnd": ln.tokens[-1] + 1,
            "chunkOrdinals": sorted({c for i in ln.tokens for c in chunk_of.get(i, [])}),
            "bbox": _rect(box) if box else None,
        }
        if ln.field_id:
            out["fieldId"] = ln.field_id
        return out

    page_lines = [ln for ln in lines if ln.page == inp.page and any(not tokens[i].held for i in ln.tokens)]
    lines_out = {ln.ordinal: line_out(ln) for ln in page_lines}

    chunks_out = []
    for s in spans:
        on_page = [tokens[i] for i in range(s.start, s.end) if tokens[i].page == inp.page]
        if not on_page:
            continue
        chars = sum(len(t.text) for t in on_page)
        # a what-if chunk is not a stored unit: it never gets a stored-looking id or label
        cid, label = (f"w-{s.n:02d}", f"what-if #{s.n}") if what_if else (f"c-{s.n:02d}", None)
        chunks_out.append({
            "chunkId": cid, "label": label or f"chunk {_short_doc(inp.doc_id)}:{cid}", "ordinal": s.n,
            "tokenStart": s.start, "tokenEnd": s.end,
            "rects": [_rect(b) for b in merge_rows([t.box for t in on_page if t.box is not None and not t.held])],
            "unitId": inp.chunk_ids[s.n - 1] if not what_if and s.n - 1 < len(inp.chunk_ids) else None,
            "tokens": s.tokens, "pages": s.pages,
            "coverage": round(sum(t.aligned for t in on_page) / chars, 4) if chars else None,
        })

    pairs_out = []
    for p in pairs:
        if p.label.ordinal not in lines_out:
            continue
        geo = lines_out.get(p.geometric.ordinal) if p.geometric else None
        pos = lines_out.get(p.positional.ordinal) if p.positional else None
        same = bool(set(lines_out[p.label.ordinal]["chunkOrdinals"]) & set((geo or {}).get("chunkOrdinals", [])))
        pairs_out.append({
            "fieldId": p.field.field_id, "concept": p.field.concept, "label": lines_out[p.label.ordinal],
            "geometricValue": geo, "positionalValue": pos, "positionalProvenance": "derived",
            "tokenDistance": (p.geometric.tokens[0] - p.label.tokens[0]) if p.geometric else None,
            "sameChunk": same,
            # extensions
            "unitId": p.unit_id, "labelIndex": p.label_index,
            "fieldBbox": _rect(p.field.box) if p.field.box else None,
            "positionalSameLine": bool(pos and geo and pos["ordinal"] == geo["ordinal"]),
            "positionalSameValue": bool(pos and geo and canonical_money(p.positional.text)  # type: ignore[union-attr]
                                        == canonical_money(p.geometric.text)),  # type: ignore[union-attr]
        })

    units_out = []
    for eu in inp.evidence:
        boxes = [b for pg, b in eu.boxes if pg == inp.page]
        if not boxes:
            continue
        units_out.append({
            "unitId": eu.unit_id, "label": eu.label, "unitType": eu.unit_type,
            "bboxes": [_rect(b) for b in boxes],
            "members": [m for m in eu.members if m["page"] == inp.page],
            "fields": [{"fieldId": f.field_id, "label": f.label, "value": f.value, "concept": f.concept,
                        "bbox": _rect(f.box)} for f in eu.fields if f.page == inp.page and f.box is not None],
        })

    page_tokens = [t for t in tokens if t.page == inp.page]
    payload: dict[str, Any] = {
        "schema": SCHEMA,
        "docId": inp.doc_id,
        "page": inp.page,
        "pageCount": len(inp.pdf_pages),
        "pageWidth": page.width,
        "pageHeight": page.height,
        "frame": "pdf-points-top-left",
        "lane": "1.0",
        "sizeTokens": inp.size_tokens,
        "overlapPct": inp.overlap_pct,
        "control": {"sizeTokens": inp.control_size, "overlapPct": inp.overlap_pct,
                    "library": inp.sources.get("textLayer", "pypdf")},
        "whatIf": what_if,
        "splitterMatchesControl": splitter_check,
        "textLayer": page_coverage is not None,
        "coverage": round(page_coverage, 4) if page_coverage is not None else None,
        "whatIfSizes": list(inp.what_if_sizes) or [inp.control_size],
        "chunkCount": len(spans),
        "chunks": chunks_out,
        "tokens": [{"i": t.index, "text": "[held]" if t.held else t.text,
                    "rect": _rect(t.box) if t.box is not None and not t.held else None,
                    "chunkOrdinals": chunk_of.get(t.index, []), "line": t.line} for t in page_tokens],
        "lines": list(lines_out.values()),
        "pairs": pairs_out,
        "heldTokensDropped": sum(1 for t in page_tokens if t.held),
        "heldRegions": [_rect(b) for b in dict.fromkeys(inp.held)],
        "units": units_out,
        "image": inp.image,
        "sources": inp.sources,
        "computedAt": inp.computed_at,
    }
    if inp.answer is not None:
        run_id, texts = inp.answer
        payload["answerNumbers"] = answer_numbers(run_id, texts, lines, pairs, inp.page)
    if warnings:
        payload["warning"] = "; ".join(warnings)
    return payload

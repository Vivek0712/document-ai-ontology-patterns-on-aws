"""Evidence-unit builder (pipeline 2.3.1): canonical layout -> evidence units with fields.

Phases (paper: Han, arXiv 2604.00500; adaptations for this repository noted):

0. Roles. Engine labels were mapped to canonical roles via the vocab snapshot. Derived roles:
   ``unit_label`` ("USD millions" pattern) and ``footnote`` (bottom band + marker, or a block that
   opens with a raised marker glyph, read from the text layer's glyph geometry). Page headers and
   footers are excluded.
A. Seeds. Every table, picture and key-value group is a seed. Cross-page table merge (part n ends
   in the bottom 8 %, part n+1 starts in the top 12 %, same column count, x-centroids within 2 %).
   Captions within 1.5 line heights (or linked by the engine) and unit labels within 3 line
   heights attach to their seed.
   Marker link (hard rule, before B): a footnote whose marker glyph appears in a seed's text
   attaches to that seed, also across pages (our builder's extension; the paper's units are
   single-page). A plain-digit marker (``Coupon 4.25%3`` in the text layer) links only when the
   seed prints that digit as a raised glyph (``ingest_lib.glyphs.raised_glyphs``); the seed on the
   footnote's page wins, else the nearest earlier page. Each link is recorded on the unit
   (``provenance.build.markerLinks``) with the marker's glyph box and the field it follows.
B. Allocation. Each remaining body block joins the same-page seed with the highest cosine
   similarity of Titan V2 embeddings when cosine >= tau (0.40, the paper's default). Layout
   proximity breaks exact ties and is recorded with a reference-match flag in the build report;
   neither changes the decision (config ``phaseB: cosine``).
C. Residual. Unallocated blocks are grouped by section into ``section_text`` units of at most
   ``maxResidualTokens`` whitespace tokens, split on block (paragraph) boundaries.

I1/I2 invariants are deferred to pipelineVersion 2.4.0 (docs/decisions/0004-evidence-unit-invariants.md); the
report's wording below is recorded in existing builds, so it is kept.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from evidence_core.bbox import union_by_page
from evidence_core.config import EnrichmentConfig, UnitsConfig
from evidence_core.enums import CanonicalRole, Engine, UnitType
from evidence_core.ids import eu_local, unit_id
from evidence_core.models.status import iso
from evidence_core.models.units import FieldValue, Member, ReviewItem, Unit, UnitProvenance

from lambdas.ingest_lib.embedder import Embedder, cosine
from lambdas.ingest_lib.glyphs import GlyphLocator, raised_glyphs
from lambdas.ingest_lib.layout import DocLayout, LayoutBlock, table_text
from lambdas.ingest_lib.periods import detect_party, detect_period, period_display
from lambdas.ingest_lib.vocab import VocabIndex

from .fields import FieldBuilder, RawField, geometric_fields, raw_fields_for

UNIT_LABEL_RE = re.compile(
    r"(?:\b(?:USD|EUR|GBP|INR|JPY)\b|\$)\s*(?:in\s+)?(?:thousands|millions|billions|bn|m)\b"
    r"|\bin\s+(?:USD\s+)?(?:thousands|millions|billions)\b|\bamounts?\s+in\s+[A-Z]{3}\b",
    re.IGNORECASE,
)
SUPERSCRIPTS = {"¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
                "*": "*", "†": "†", "‡": "‡"}
_FOOTNOTE_START = re.compile(r"^\s*(?P<m>[¹²³⁴⁵⁶⁷⁸⁹*†‡]|\(?\d\)?)\s+\S")
_PLAIN_MARKER_START = re.compile(r"^\s*(?P<m>[1-9])\s+\S")
_BODY_ROLES = (CanonicalRole.TEXT, CanonicalRole.LIST_ITEM, CanonicalRole.CAPTION, CanonicalRole.FOOTNOTE,
               CanonicalRole.UNIT_LABEL, CanonicalRole.FORMULA)
_HEADING_ROLES = (CanonicalRole.TITLE, CanonicalRole.SECTION_HEADER)
DEFAULT_LINE_HEIGHT = 12.0
PARTY_CONCEPT = re.compile(r"(Employee|Borrower|AccountHolder)Name$")


def _tokens(text: str) -> int:
    return len(text.split())


@dataclass
class BuildInput:
    doc_id: str
    package_id: str
    pipeline_version: str
    file_name: str
    allowed_principals: list[str]
    source_key: str
    source_version_id: str | None
    source_sha: str
    config_sha: str
    ontology_version: str | None
    layout: DocLayout
    vocab: VocabIndex
    units_cfg: UnitsConfig
    enrichment: EnrichmentConfig
    embedder: Embedder
    doc_type: str | None = None
    engine_versions: dict[str, str] = field(default_factory=dict)  # engine value -> version string
    extract_artifacts: list[str] = field(default_factory=list)
    glyphs: GlyphLocator | None = None


@dataclass
class Draft:
    """A unit under construction."""

    unit_type: UnitType
    anchor: LayoutBlock | None
    members: list[tuple[LayoutBlock, str, float | None]] = field(default_factory=list)  # block, phase, score
    section: str | None = None
    merged_tables: int = 0
    raw_fields: list[RawField] | None = None  # fields already paired (derived form groups)
    marker_links: list[dict[str, Any]] = field(default_factory=list)  # {block, marker, markerBox?, evidence}

    def blocks(self) -> list[LayoutBlock]:
        return sorted((m[0] for m in self.members), key=lambda b: b.order)

    def add(self, block: LayoutBlock, phase: str, score: float | None = None) -> None:
        self.members.append((block, phase, score))

    @property
    def first_page(self) -> int:
        return min(b.page for b in self.blocks())

    @property
    def sort_key(self) -> tuple[int, float, int]:
        first = self.blocks()[0]
        return (first.page, first.bbox.y0 if first.bbox else 0.0, first.order)


@dataclass
class BuildOutput:
    units: list[Unit]
    review_items: list[ReviewItem]
    report: dict[str, Any]


class EvidenceUnitBuilder:
    def __init__(self, inp: BuildInput) -> None:
        self.inp = inp
        self.cfg = inp.units_cfg
        self.blocks = [b for b in inp.layout.blocks if b.text or b.table is not None or b.role is CanonicalRole.PICTURE]
        self.assigned: dict[str, Draft] = {}
        self.allocations: list[dict[str, Any]] = []
        self.derived: Counter[str] = Counter()
        self.phase_counts: Counter[str] = Counter()
        self.form_groups: list[tuple[list[LayoutBlock], list[RawField]]] = []

    # ------------------------------------------------------------------ phase 0
    def derive_roles(self) -> None:
        self._derive_forms()
        for b in self.blocks:
            if b.excluded or b.role not in (CanonicalRole.TEXT, CanonicalRole.CAPTION):
                continue
            if len(b.text) <= 80 and UNIT_LABEL_RE.search(b.text):
                b.role, b.derived_role = CanonicalRole.UNIT_LABEL, True
                self.derived["unit_label"] += 1
                continue
            if b.role is CanonicalRole.TEXT and b.bbox is not None and len(b.text) <= 400:
                _, h = self.inp.layout.page_size(b.page)
                if b.bbox.y0 >= 0.85 * h and _FOOTNOTE_START.match(b.text):
                    b.role, b.derived_role = CanonicalRole.FOOTNOTE, True
                    self.derived["footnote"] += 1
                elif self._opens_with_raised_marker(b):
                    b.role, b.derived_role = CanonicalRole.FOOTNOTE, True
                    self.derived["footnote_raised_marker"] += 1

    def _raised(self, page: int) -> list[tuple[str, Any]]:
        """Raised marker glyphs on a digital page (cached by the locator); none without a text layer."""
        return raised_glyphs(self.inp.glyphs, page) if self.inp.glyphs is not None else []

    def _opens_with_raised_marker(self, b: LayoutBlock) -> bool:
        """``3 6.00% from year 3 …``: the block's first glyph is the raised digit it starts with."""
        m = _PLAIN_MARKER_START.match(b.text)
        if not m or b.bbox is None or b.engine is not Engine.DOCLING:
            return False
        first_line = b.bbox.y0 + 12.0
        return any(ch == m["m"] and box.x0 <= b.bbox.x0 + 6.0 and box.y0 <= first_line
                   and b.bbox.expand(2.0).x0 <= box.x0 for ch, box in self._raised(b.page))

    def _derive_forms(self) -> None:
        """Form regions the engine emitted as loose text items: on each digital page, pair values with
        labels by text-layer geometry; when >= 3 distinct vocab concepts pair up, the blocks holding
        those labels and values become one derived ``field_group`` seed (a builder extension in this repository)."""
        if self.inp.glyphs is None:
            return
        pages = sorted({b.page for b in self.blocks if b.engine is Engine.DOCLING})
        for page in pages:
            blocks = [b for b in self.blocks if b.page == page and b.engine is Engine.DOCLING
                      and b.role in (CanonicalRole.TEXT, CanonicalRole.LIST_ITEM, CanonicalRole.KEY_VALUE)]
            geo = geometric_fields(blocks, self.inp.glyphs, self.inp.vocab, self.inp.doc_type)
            concept_pairs = [g for g in geo if g.concept]
            if len({g.concept for g in concept_pairs}) < 3:
                continue
            members: list[LayoutBlock] = []
            for g in concept_pairs:
                for blk in (g.label_block, g.value_block):
                    if blk not in members:
                        members.append(blk)
            self.form_groups.append((members, [g.field for g in concept_pairs]))
            self.derived["form_field_group"] += 1

    def sections(self) -> dict[str, str | None]:
        """block_id -> nearest preceding heading text (title or section header)."""
        current: str | None = None
        out: dict[str, str | None] = {}
        for b in sorted(self.blocks, key=lambda x: x.order):
            if b.excluded:
                continue
            if b.role in _HEADING_ROLES and b.text:
                current = b.text.strip()[:80]
            out[b.block_id] = current
        return out

    # ------------------------------------------------------------------ phase A
    def seeds(self) -> list[Draft]:
        drafts: list[Draft] = []
        for members, fields in self.form_groups:
            anchor = min(members, key=lambda x: x.order)
            d = Draft(UnitType.FIELD_GROUP, anchor=anchor, raw_fields=fields)
            for blk in members:
                d.add(blk, "A")
                self.assigned[blk.block_id] = d
            drafts.append(d)
        prev_kv: Draft | None = None
        prev_order = -10
        for b in sorted(self.blocks, key=lambda x: x.order):
            if b.block_id in self.assigned:
                continue
            if b.excluded or not b.is_visual_seed:
                if b.role in _HEADING_ROLES:
                    prev_kv = None
                continue
            if b.role is CanonicalRole.KEY_VALUE:
                # consecutive key-value regions on one page form one field group
                same_page = prev_kv is not None and prev_kv.anchor is not None and prev_kv.anchor.page == b.page
                if prev_kv is not None and same_page and b.order - prev_order <= 2:
                    prev_kv.add(b, "A")
                    self.assigned[b.block_id] = prev_kv
                    prev_order = b.order
                    continue
                d = Draft(UnitType.FIELD_GROUP, anchor=b)
                prev_kv = d
                prev_order = b.order
            elif b.role is CanonicalRole.TABLE:
                d = Draft(UnitType.TABLE_PANEL, anchor=b)
                prev_kv = None
            else:
                d = Draft(UnitType.FIGURE_PANEL, anchor=b)
                prev_kv = None
            d.add(b, "A")
            self.assigned[b.block_id] = d
            drafts.append(d)
        self.phase_counts["A"] += len(drafts)
        return self._merge_cross_page_tables(drafts)

    def _merge_cross_page_tables(self, drafts: list[Draft]) -> list[Draft]:
        out: list[Draft] = []
        for d in drafts:
            prev = out[-1] if out else None
            if prev and self._continues(prev, d):
                for blk, _, _ in d.members:
                    prev.add(blk, "A")
                    self.assigned[blk.block_id] = prev
                prev.merged_tables += 1
                continue
            out.append(d)
        return out

    def _continues(self, a: Draft, b: Draft) -> bool:
        ta, tb = a.blocks()[-1], b.anchor
        if not (ta.table and tb and tb.table and ta.bbox and tb.bbox) or tb.page != ta.page + 1:
            return False
        wa, ha = self.inp.layout.page_size(ta.page)
        wb, hb = self.inp.layout.page_size(tb.page)
        ends_low = ta.bbox.y1 >= 0.92 * ha
        starts_high = tb.bbox.y0 <= 0.12 * hb
        same_cols = ta.table.num_cols == tb.table.num_cols and ta.table.num_cols > 0
        cx_a = (ta.bbox.x0 + ta.bbox.x1) / 2 / wa
        cx_b = (tb.bbox.x0 + tb.bbox.x1) / 2 / wb
        return ends_low and starts_high and same_cols and abs(cx_a - cx_b) <= 0.02

    def attach_captions(self, drafts: list[Draft]) -> None:
        by_pointer = {b.pointer: b for b in self.blocks}
        for d in drafts:
            anchor = d.anchor
            if anchor is None:
                continue
            for ref in anchor.caption_refs:  # engine-linked captions first
                cap = by_pointer.get(ref)
                if cap is not None and cap.block_id not in self.assigned:
                    d.add(cap, "A")
                    self.assigned[cap.block_id] = d
        for b in self.blocks:
            if b.block_id in self.assigned or b.bbox is None:
                continue
            if b.role is CanonicalRole.CAPTION:
                limit = 1.5
            elif b.role is CanonicalRole.UNIT_LABEL:
                limit = 3.0
            else:
                continue
            best, best_gap = None, None
            for d in drafts:
                a = d.anchor
                if a is None or a.bbox is None or a.page != b.page:
                    continue
                line_h = max(b.bbox.height, 1.0) if b.bbox.height < 40 else DEFAULT_LINE_HEIGHT
                gap = max(a.bbox.y0 - b.bbox.y1, b.bbox.y0 - a.bbox.y1, 0.0)
                overlaps_x = min(a.bbox.x1, b.bbox.x1) > max(a.bbox.x0, b.bbox.x0) - 20.0
                if overlaps_x and gap <= limit * line_h and (best_gap is None or gap < best_gap):
                    best, best_gap = d, gap
            if best is not None:
                best.add(b, "A", None)
                self.assigned[b.block_id] = best

    def marker_links(self, drafts: list[Draft]) -> int:
        if self.cfg.marker_link == "off":
            return 0
        n = 0
        for b in self.blocks:
            if b.block_id in self.assigned or b.role is not CanonicalRole.FOOTNOTE:
                continue
            m = _FOOTNOTE_START.match(b.text)
            if not m:
                continue
            marker = m.group("m").strip("()")
            glyphs = [g for g, v in SUPERSCRIPTS.items() if v == SUPERSCRIPTS.get(marker, marker)]
            if marker in SUPERSCRIPTS:
                glyphs.append(marker)
            link: tuple[Draft, dict[str, Any]] | None = None
            for d in drafts:
                text = " ".join(x.text for x in d.blocks() if x.block_id != b.block_id)
                if any(g in text for g in glyphs):
                    link = d, {"evidence": "marker glyph in text"}
                    break
            if link is None and marker.isdigit():
                # a plain digit in the text layer is a marker only where it is printed raised
                link = self._raised_marker_seed(b, marker, drafts)
            if link is None:
                continue
            d, detail = link
            d.add(b, "marker", 1.0)
            self.assigned[b.block_id] = d
            entry = {"block": b.block_id, "marker": marker, "crossPage": d.first_page != b.page} | detail
            d.marker_links.append(entry)
            self.allocations.append({"block": b.block_id, "pointer": b.pointer, "role": b.role.value,
                                     "phase": "marker", "unit": id(d)} | {k: v for k, v in entry.items()
                                                                           if k != "block"})
            n += 1
        self.phase_counts["marker"] += n
        return n

    def _raised_marker_seed(self, footnote: LayoutBlock, marker: str,
                            drafts: list[Draft]) -> tuple[Draft, dict[str, Any]] | None:
        """The seed that prints ``marker`` as a raised glyph: same page first, else the nearest earlier page."""
        found: list[tuple[int, Draft, Any]] = []
        for d in drafts:
            for blk in d.blocks():
                if blk.block_id == footnote.block_id or blk.bbox is None or blk.page > footnote.page:
                    continue
                area = blk.bbox.expand(2.0)
                hit = next((box for ch, box in self._raised(blk.page)
                            if ch == marker and area.x0 <= box.x0 and box.x1 <= area.x1
                            and area.y0 <= box.y0 and box.y1 <= area.y1), None)
                if hit is not None:
                    found.append((footnote.page - blk.page, d, hit))
                    break
        if not found:
            return None
        _, d, box = min(found, key=lambda f: f[0])
        return d, {"evidence": "raised glyph", "markerBox": box.dump()}

    # ------------------------------------------------------------------ phase B
    def allocate(self, drafts: list[Draft]) -> None:
        candidates = [b for b in self.blocks if b.block_id not in self.assigned and not b.excluded
                      and b.role in _BODY_ROLES and b.text.strip()]
        if not drafts or not candidates:
            return
        seed_texts = [self._draft_text(d) or "[figure]" for d in drafts]
        vectors = self.inp.embedder.embed(seed_texts + [b.text for b in candidates])
        seed_vecs, block_vecs = vectors[: len(drafts)], vectors[len(drafts):]
        for b, bv in zip(candidates, block_vecs, strict=True):
            scored = []
            for d, sv in zip(drafts, seed_vecs, strict=True):
                if d.anchor is None or d.anchor.page != b.page:
                    continue
                cos = cosine(bv, sv)
                prox = self._proximity(b, d)
                ref = self._reference_match(b, d)
                scored.append((cos, prox, ref, d))
            if not scored:
                continue
            scored.sort(key=lambda s: (-round(s[0], 6), -s[1]))
            cos, prox, ref, best = scored[0]
            decision = cos >= self.cfg.tau
            self.allocations.append({
                "block": b.block_id, "pointer": b.pointer, "role": b.role.value, "phase": "B",
                "cosine": round(cos, 4), "proximity": round(prox, 4), "referenceMatch": ref,
                "tau": self.cfg.tau, "allocated": decision, "unit": id(best),
            })
            if decision:
                best.add(b, "B", round(cos, 4))
                self.assigned[b.block_id] = best
                self.phase_counts["B"] += 1

    def _proximity(self, b: LayoutBlock, d: Draft) -> float:
        a = d.anchor
        if a is None or a.bbox is None or b.bbox is None:
            return 0.0
        _, h = self.inp.layout.page_size(b.page)
        gap = max(a.bbox.y0 - b.bbox.y1, b.bbox.y0 - a.bbox.y1, 0.0)
        return max(0.0, 1.0 - gap / h)

    def _reference_match(self, b: LayoutBlock, d: Draft) -> bool:
        caps = [x.text for x in d.blocks() if x.role is CanonicalRole.CAPTION]
        for cap in caps:
            m = re.match(r"\s*((?:table|figure|chart|exhibit)\s+[\w.]+)", cap, re.IGNORECASE)
            if m and m.group(1).lower() in b.text.lower():
                return True
        return False

    def _draft_text(self, d: Draft) -> str:
        parts = []
        for b in d.blocks():
            if b.table is not None:
                parts.append(table_text(b.table))
            elif b.text:
                parts.append(b.text)
        return "\n".join(p for p in parts if p)

    # ------------------------------------------------------------------ phase C
    def residual(self, sections: dict[str, str | None]) -> list[Draft]:
        """Phase C: leftover body text grouped per section under a token budget. A run of consecutive
        headings stays in one group, and a heading-only group belongs to the text that FOLLOWS it (it
        is that text's heading); only a heading-only group at the very end joins the previous group."""
        out: list[Draft] = []
        current: Draft | None = None
        tokens = 0
        heads_only = False
        for b in sorted(self.blocks, key=lambda x: x.order):
            if b.block_id in self.assigned or b.excluded or not b.text.strip():
                continue
            if b.role not in _BODY_ROLES and b.role not in _HEADING_ROLES and b.role is not CanonicalRole.KEY_VALUE:
                continue
            sec = sections.get(b.block_id)
            n = _tokens(b.text)
            is_head = b.role in _HEADING_ROLES
            new_section = current is None or current.section != sec or is_head
            too_big = current is not None and tokens > 0 and tokens + n > self.cfg.max_residual_tokens
            if is_head and current is not None and heads_only and not too_big:
                new_section = False  # consecutive headings stay together
            if new_section or too_big:
                current = Draft(UnitType.SECTION_TEXT, anchor=None, section=sec)
                out.append(current)
                tokens, heads_only = 0, True
            assert current is not None
            current.add(b, "C")
            self.assigned[b.block_id] = current
            tokens += n
            heads_only = heads_only and is_head
        merged: list[Draft] = []
        pending: list[Draft] = []  # heading-only groups waiting for the text they introduce
        for d in out:
            if all(b.role in _HEADING_ROLES for b in d.blocks()):
                pending.append(d)
                continue
            for p in pending:
                for blk, ph, sc in p.members:
                    d.add(blk, ph, sc)
                    self.assigned[blk.block_id] = d
            pending = []
            merged.append(d)
        if pending:
            target = merged[-1] if merged else pending.pop(0)
            if not merged:
                merged.append(target)
            for p in pending:
                for blk, ph, sc in p.members:
                    target.add(blk, ph, sc)
                    self.assigned[blk.block_id] = target
        self.phase_counts["C"] += len(merged)
        return merged

    # ------------------------------------------------------------------ assemble
    def build(self) -> BuildOutput:
        inp = self.inp
        self.derive_roles()
        sections = self.sections()
        drafts = self.seeds()
        self.attach_captions(drafts)
        markers = self.marker_links(drafts)
        self.allocate(drafts)
        residual = self.residual(sections)
        all_drafts = sorted(drafts + residual, key=lambda d: d.sort_key)

        doc_type = inp.doc_type if inp.enrichment.doc_type == "ontology" else None
        dt = inp.vocab.doc_type(inp.doc_type)
        title_text = " ".join(b.text for b in self.blocks if b.role in _HEADING_ROLES)[:500]
        full_text = inp.layout.full_text()
        period = detect_period(dt.period_kind, title_text, full_text) if (dt and inp.enrichment.period) else None
        for d in all_drafts:
            if d.raw_fields is None:
                d.raw_fields = [rf for b in d.blocks() for rf in raw_fields_for(b)]
        party = self._party_from_fields(all_drafts) or detect_party(full_text)

        field_builder = FieldBuilder(vocab=inp.vocab, doc_type=inp.doc_type,
                                     use_concepts=inp.enrichment.field_concept == "ontology", period=period,
                                     glyphs=inp.glyphs if inp.units_cfg.field_bbox else None)
        units: list[Unit] = []
        reviews: list[ReviewItem] = []
        draft_ids: dict[int, str] = {}
        for n, d in enumerate(all_drafts, start=1):
            uid = unit_id(inp.doc_id, inp.pipeline_version, eu_local(n))
            draft_ids[id(d)] = uid
            unit, unit_reviews = self._assemble(d, uid, n, field_builder, doc_type, dt, period, party, sections)
            units.append(unit)
            reviews.extend(unit_reviews)

        for a in self.allocations:
            a["unit"] = draft_ids.get(a["unit"])
        report = {
            "docId": inp.doc_id,
            "pipelineVersion": inp.pipeline_version,
            "builtAt": iso(),
            "embedModel": inp.embedder.model_id,
            "tau": self.cfg.tau,
            "phaseB": self.cfg.phase_b,
            "markerLink": self.cfg.marker_link,
            "phaseCounts": {"A": self.phase_counts["A"], "B": self.phase_counts["B"],
                            "C": self.phase_counts["C"], "marker": markers},
            "derivedRoles": dict(self.derived),
            "unknownEngineLabels": inp.layout.unknown_labels,
            "blocks": len(self.blocks),
            "excludedBlocks": sum(1 for b in self.blocks if b.excluded),
            "allocations": self.allocations,
            "units": [{"unitId": u.unit_id, "unitType": u.unit_type.value, "members": len(u.members),
                       "fields": len(u.fields), "pages": u.pages, "chars": u.chars} for u in units],
            "docType": inp.doc_type,
            "period": period,
            "party": party,
            "reviewItems": [r.dump() for r in reviews],
            "invariants": {"I1": "deferred (pipelineVersion 2.4.0, post-lock)",
                           "I2": "deferred (pipelineVersion 2.4.0, post-lock)"},
        }
        return BuildOutput(units=units, review_items=reviews, report=report)

    def _assemble(self, d: Draft, uid: str, n: int, fb: FieldBuilder, doc_type: str | None, dt: Any,
                  period: str | None, party: str | None,
                  sections: dict[str, str | None]) -> tuple[Unit, list[ReviewItem]]:
        inp = self.inp
        blocks = d.blocks()
        phase_of = {m[0].block_id: (m[1], m[2]) for m in d.members}
        members = []
        for i, b in enumerate(blocks, start=1):
            phase, score = phase_of[b.block_id]
            members.append(Member(member_id=f"m-{i:02d}", role=b.role, text=b.text or None,
                                  bbox=b.bbox.rounded() if b.bbox else None, block_ref=b.block_ref(),
                                  phase=phase, alloc_score=score))
        raw: list[RawField] = list(d.raw_fields or [])
        fields: list[FieldValue] = fb.build(raw)
        concepts = sorted({f.concept for f in fields if f.concept})
        text = "\n".join(table_text(b.table) if b.table is not None else b.text for b in blocks if b.text or b.table)
        if not text.strip():
            text = "[figure without caption]" if d.unit_type is UnitType.FIGURE_PANEL else ""
        boxes = [b.bbox for b in blocks if b.bbox is not None]
        bboxes = [bb.rounded() for bb in union_by_page(boxes)] if boxes else []
        pages = sorted({b.page for b in blocks})
        w, h = inp.layout.page_size(pages[0])
        confs = [c for c in [b.confidence for b in blocks] + [f.confidence for f in fields] if c is not None]
        group_tail = self._unit_group(concepts) if concepts else None
        section = d.section or (sections.get(blocks[0].block_id) if blocks else None)
        breadcrumb = self._breadcrumb(dt, period, party, section, group_tail) if inp.enrichment.breadcrumb else []
        label = self._label(d, dt, party, group_tail, section, blocks)
        engines = Counter(b.engine for b in blocks)
        engine = engines.most_common(1)[0][0] if engines else Engine.EU_BUILDER
        unit = Unit(
            unit_id=uid, doc_id=inp.doc_id, package_id=inp.package_id, pipeline_version=inp.pipeline_version,
            unit_type=d.unit_type, label=label, file_name=inp.file_name, doc_type=doc_type,
            breadcrumb=breadcrumb, text=text, pages=pages, page_width=w, page_height=h,
            bboxes=bboxes if inp.enrichment.bbox else [], members=members, fields=fields,
            field_concepts=concepts, period=period if inp.enrichment.period else None,
            allowed_principals=inp.allowed_principals,
            confidence=round(min(confs), 4) if confs else None, chars=len(text), tokens=_tokens(text),
            provenance=UnitProvenance(
                engine=engine, engine_version=inp.engine_versions.get(engine.value),
                ontology_version=inp.ontology_version, source_key=inp.source_key,
                source_version_id=inp.source_version_id, source_sha=inp.source_sha,
                extract_artifacts=sorted({b.artifact_key for b in blocks}), config_sha=inp.config_sha,
                build={"phases": dict(Counter(m.phase for m in members)),
                       "anchor": d.anchor.pointer if d.anchor else None,
                       "mergedTables": d.merged_tables, "tau": self.cfg.tau}
                | ({"markerLinks": self._marker_links_of(d, members, fields, fb)} if d.marker_links else {}),
            ),
        )
        reviews = []
        for f in fields:
            if f.confidence is not None and f.confidence < self.cfg.review_min_confidence:
                reviews.append(ReviewItem(
                    package_id=inp.package_id, review_key=f"{inp.doc_id}#{inp.pipeline_version}#{uid}#{f.field_id}",
                    doc_id=inp.doc_id, pipeline_version=inp.pipeline_version, unit_id=uid, field_id=f.field_id,
                    label=f.label, value=f.value, confidence=f.confidence, threshold=self.cfg.review_min_confidence,
                    engine=engine, bbox=f.bbox, reason="confidence below threshold", created_at=iso()))
        return unit, reviews

    def _marker_links_of(self, d: Draft, members: list[Member], fields: list[FieldValue],
                         fb: FieldBuilder) -> list[dict[str, Any]]:
        """Each marker link of the unit: the footnote member, the marker, its glyph box and the field the
        marker follows (a field whose printed value carried that raised marker), when there is one."""
        member_of = {m.block_ref.pointer: m.member_id for m in members if m.block_ref is not None}
        block_of = {b.block_id: b for b in d.blocks()}
        out = []
        for link in d.marker_links:
            blk = block_of.get(link["block"])
            fid = next((f.field_id for f in fields if fb.markers.get(f.field_id) == link["marker"]), None)
            out.append({"marker": link["marker"], "footnoteMember": member_of.get(blk.pointer) if blk else None,
                        "fieldId": fid, "crossPage": link["crossPage"], "evidence": link["evidence"]}
                       | ({"markerBox": link["markerBox"]} if "markerBox" in link else {}))
        return out

    def _party_from_fields(self, drafts: list[Draft]) -> str | None:
        """Borrower name from a string field bound to an *EmployeeName / *BorrowerName /
        *AccountHolderName concept (vocab), first line of the printed value."""
        for d in drafts:
            for rf in d.raw_fields or []:
                fc = self.inp.vocab.concept_for_label(rf.label, self.inp.doc_type)
                if fc and fc.datatype == "string" and PARTY_CONCEPT.search(fc.concept):
                    name = rf.value.splitlines()[0].strip()
                    if 2 <= len(name.split()) <= 4:
                        return name
        return None

    def _unit_group(self, concepts: list[str]) -> str | None:
        groups = Counter(fc.unit_group for c in concepts if (fc := self.inp.vocab.concept(c)) and fc.unit_group)
        return groups.most_common(1)[0][0] if groups else None

    def _doc_label(self, dt: Any, period: str | None) -> str:
        if dt is not None:
            disp = period_display(period)
            return f"{dt.short_label} {disp}" if disp else dt.short_label
        stem = self.inp.file_name.rsplit(".", 1)[0]
        return stem.replace("_", " ")

    def _breadcrumb(self, dt: Any, period: str | None, party: str | None, section: str | None,
                    group_tail: str | None) -> list[str]:
        crumb = [self._doc_label(dt, period)]
        if party:
            crumb.append(party)
        tail = group_tail or section
        # a section that is just the document title adds nothing to "W-2 2025"
        is_title = group_tail is None and dt is not None and dt.short_label.lower() in (tail or "").lower()
        if tail and tail not in crumb and not is_title:
            crumb.append(tail)
        return crumb

    def _label(self, d: Draft, dt: Any, party: str | None, group_tail: str | None, section: str | None,
               blocks: list[LayoutBlock]) -> str:
        head = dt.short_label if dt is not None else self.inp.file_name.rsplit(".", 1)[0]
        parts = [head]
        if party:
            parts.append(party)
        if group_tail:
            parts.append(group_tail)
        else:
            caption = next((b.text for b in blocks if b.role is CanonicalRole.CAPTION), None)
            kind = {UnitType.TABLE_PANEL: "table", UnitType.FIGURE_PANEL: "figure",
                    UnitType.FIELD_GROUP: "fields", UnitType.SECTION_TEXT: "text"}[d.unit_type]
            parts.append((caption or section or kind)[:60])
        return " · ".join(parts)


def build_units(inp: BuildInput) -> BuildOutput:
    return EvidenceUnitBuilder(inp).build()


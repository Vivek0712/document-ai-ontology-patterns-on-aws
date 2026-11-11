"""Canonical layout model: what every extraction engine is normalised into before
the evidence-unit builder runs. One frame: PDF points, top-left origin, 1-based pages.

A ``LayoutBlock`` keeps the pointer back into the engine artifact (Docling JSON pointer or
Textract Block Id), so every unit member and field can be traced to the exact engine item.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from evidence_core.bbox import BBox
from evidence_core.enums import CanonicalRole, Engine
from evidence_core.models.units import BlockRef


@dataclass
class TableCell:
    row: int  # 0-based
    col: int
    text: str
    bbox: BBox | None
    pointer: str
    row_span: int = 1
    col_span: int = 1
    header: bool = False
    confidence: float | None = None  # 0..1 (Textract)


@dataclass
class TableGrid:
    num_rows: int
    num_cols: int
    cells: list[TableCell]

    def rows(self) -> list[list[TableCell]]:
        out: list[list[TableCell]] = [[] for _ in range(self.num_rows)]
        for c in sorted(self.cells, key=lambda c: (c.row, c.col)):
            if 0 <= c.row < self.num_rows:
                out[c.row].append(c)
        return out


def table_text(grid: TableGrid) -> str:
    """Row-major text with the cell separator the units use (``│``)."""
    return "\n".join(" │ ".join(c.text for c in row if c.text) for row in grid.rows() if any(c.text for c in row))


@dataclass
class KeyValue:
    key: str
    value: str
    key_bbox: BBox | None
    value_bbox: BBox | None
    pointer: str  # engine id of the key item
    value_pointer: str | None = None
    confidence: float | None = None  # 0..1


@dataclass
class LayoutBlock:
    block_id: str  # stable within the document lane: "b-0007"
    page: int
    order: int  # reading order across the document
    role: CanonicalRole
    engine: Engine
    engine_label: str
    text: str
    bbox: BBox | None
    artifact_key: str
    pointer: str
    confidence: float | None = None
    level: int | None = None  # heading level when the engine gives one
    table: TableGrid | None = None
    kvs: list[KeyValue] = field(default_factory=list)
    caption_refs: list[str] = field(default_factory=list)  # engine pointers of captions linked by the engine
    excluded: bool = False  # page_header / page_footer furniture: never in a unit
    derived_role: bool = False  # role assigned by our rules (unit_label, footnote band), not the engine

    def block_ref(self) -> BlockRef:
        return BlockRef(engine=self.engine, artifact_key=self.artifact_key, pointer=self.pointer,
                        engine_label=self.engine_label)

    @property
    def is_visual_seed(self) -> bool:
        return self.role in (CanonicalRole.TABLE, CanonicalRole.PICTURE) or (
            self.role is CanonicalRole.KEY_VALUE and bool(self.kvs)
        )


@dataclass
class LayoutPage:
    page: int
    width: float
    height: float
    engine: Engine


@dataclass
class DocLayout:
    pages: dict[int, LayoutPage]
    blocks: list[LayoutBlock]
    unknown_labels: dict[str, int] = field(default_factory=dict)  # engine label -> count (mapped to text)

    def page_size(self, page: int) -> tuple[float, float]:
        p = self.pages[page]
        return p.width, p.height

    def renumber(self) -> None:
        """Sort by (page, engine reading order) and assign stable block ids."""
        self.blocks.sort(key=lambda b: (b.page, b.order))
        for i, b in enumerate(self.blocks, start=1):
            b.order = i
            b.block_id = f"b-{i:04d}"

    @classmethod
    def merge(cls, parts: list[DocLayout]) -> DocLayout:
        pages: dict[int, LayoutPage] = {}
        blocks: list[LayoutBlock] = []
        unknown: dict[str, int] = {}
        for part in parts:
            pages.update(part.pages)
            blocks.extend(part.blocks)
            for k, v in part.unknown_labels.items():
                unknown[k] = unknown.get(k, 0) + v
        merged = cls(pages=pages, blocks=blocks, unknown_labels=unknown)
        merged.renumber()
        return merged

    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks if b.text and not b.excluded)

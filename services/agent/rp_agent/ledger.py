"""What the tools returned in this session: the only source of citation detail.

Every unit and field a tool result mentioned is recorded with the page geometry the tool
returned. The citation validator reads page/bbox from here, never from model text.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from evidence_core.bbox import BBox
from evidence_core.enums import UnitType
from evidence_core.models.tools import (
    GraphHopResult,
    RegionResult,
    RetrievedUnit,
    SearchUnitsResult,
)


@dataclass
class FieldInfo:
    field_id: str
    label: str | None = None
    value: str | None = None
    value_number: str | None = None
    concept: str | None = None
    bbox: BBox | None = None
    sources: set[str] = field(default_factory=set)


@dataclass
class UnitInfo:
    unit_id: str
    doc_id: str | None = None
    file_name: str | None = None
    label: str | None = None
    chunk_label: str | None = None
    unit_type: UnitType | None = None
    text: str = ""
    bboxes: list[BBox] = field(default_factory=list)
    fields: dict[str, FieldInfo] = field(default_factory=dict)
    sources: set[str] = field(default_factory=set)

    @property
    def is_chunk(self) -> bool:
        return self.unit_type is UnitType.CHUNK

    def field_info(self, field_id: str) -> FieldInfo:
        return self.fields.setdefault(field_id, FieldInfo(field_id=field_id))


@dataclass
class GraphTotal:
    total: str
    operands: list[tuple[str, str]]  # (unitId, fieldId)


@dataclass
class ToolLedger:
    units: dict[str, UnitInfo] = field(default_factory=dict)
    retrieved: list[RetrievedUnit] = field(default_factory=list)  # first-appearance order
    graph_totals: list[GraphTotal] = field(default_factory=list)

    def unit(self, unit_id: str) -> UnitInfo:
        return self.units.setdefault(unit_id, UnitInfo(unit_id=unit_id))

    def record(self, tool: str, payload: dict[str, Any]) -> None:
        """Add a successful tool result (camelCase JSON as returned by the tool Lambda)."""
        if tool == "search_units":
            self._search(SearchUnitsResult.load(payload))
        elif tool == "get_region":
            self._region(RegionResult.load(payload))
        elif tool == "graph_hop":
            self._graph(GraphHopResult.load(payload))

    def _search(self, res: SearchUnitsResult) -> None:
        seen = {r.unit_id for r in self.retrieved}
        for r in res.units:
            if r.unit_id not in seen:
                self.retrieved.append(r)
                seen.add(r.unit_id)
            u = self.unit(r.unit_id)
            u.sources.add("search_units")
            u.doc_id, u.file_name, u.label = r.doc_id, r.file_name, r.label
            u.chunk_label, u.unit_type, u.text = r.chunk_label, r.unit_type, r.text
            if r.bboxes:
                u.bboxes = list(r.bboxes)
            for fb in r.fields:
                f = u.field_info(fb.field_id)
                f.sources.add("search_units")
                f.label, f.value, f.value_number, f.concept = fb.label, fb.value, fb.value_number, fb.concept
                f.bbox = fb.bbox or f.bbox

    def _region(self, res: RegionResult) -> None:
        u = self.unit(res.unit_id)
        u.sources.add("get_region")
        u.doc_id, u.file_name = res.doc_id, res.file_name
        u.label = u.label or res.label
        u.unit_type = u.unit_type or res.unit_type
        if res.unit_bboxes:
            u.bboxes = list(res.unit_bboxes)
        if res.field_id:
            f = u.field_info(res.field_id)
            f.sources.add("get_region")
            f.bbox = res.field_bbox or f.bbox
            f.value = f.value or res.value
            f.label = f.label or res.field_label
            f.concept = f.concept or res.concept

    def _graph(self, res: GraphHopResult) -> None:
        for op in res.operands:
            u = self.unit(op.unit_id)
            u.sources.add("graph_hop")
            f = u.field_info(op.field_id)
            f.sources.add("graph_hop")
            f.value_number = f.value_number or op.value
            f.concept = f.concept or op.concept
        if res.total is not None and res.operands:
            self.graph_totals.append(GraphTotal(res.total, [(o.unit_id, o.field_id) for o in res.operands]))

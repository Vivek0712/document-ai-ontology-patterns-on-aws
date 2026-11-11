"""Unit records (Silver/Gold JSONL lines), fields, members, PII decisions, index documents."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..base import RpModel
from ..bbox import BBox
from ..enums import CanonicalRole, Engine, PiiFlag, UnitType
from .governance import ReviewDecision


class BlockRef(RpModel):
    """Pointer back into the engine output a member or field came from (lineage)."""

    engine: Engine
    artifact_key: str  # silver/.../extract/docling.json or .../textract/p001.json or text_layer.json
    pointer: str  # docling JSON pointer ("#/texts/12", "#/tables/0/data/table_cells/3") or Textract Block Id
    engine_label: str | None = None  # "section_header", "LAYOUT_TABLE", "KEY_VALUE_SET", ...


class Member(RpModel):
    """A layout block allocated to a unit (paper: unit members)."""

    member_id: str  # "m-01"
    role: CanonicalRole
    text: str | None = None
    bbox: BBox | None = None
    block_ref: BlockRef | None = None
    phase: Literal["A", "B", "C", "marker"] | None = None  # how it joined the unit
    alloc_score: float | None = None  # Phase B cosine


class FieldValue(RpModel):
    """A labelled value inside a unit (a W-2 box, a payslip YTD line). Citations point here."""

    field_id: str  # "f-box1"
    label: str  # "1 Wages, tips, other compensation"
    value: str  # as printed: "98,400.00"
    value_number: str | None = None  # normalised decimal string "98400.00" (no thousands separators)
    currency: str | None = None
    concept: str | None = None  # "fin:W2WagesBox1" (set when enrichment.fieldConcept = ontology)
    period: str | None = None  # "TY2025", "2026-09"
    bbox: BBox | None = None  # field-level bbox (Box 1 cell); None in v1.0
    source: str | None = None  # "docling table cell (row 0, col 0)", "textract KEY_VALUE_SET"
    confidence: float | None = None  # Textract confidence 0..1; None for Docling
    block_refs: list[BlockRef] = Field(default_factory=list)


class UnitProvenance(RpModel):
    engine: Engine
    engine_version: str | None = None
    ontology_version: str | None = None
    bbox_frame: Literal["pdf-points-top-left"] = "pdf-points-top-left"
    source_key: str  # bronze key
    source_version_id: str | None = None
    source_sha: str
    extract_artifacts: list[str] = Field(default_factory=list)  # silver keys used
    config_sha: str
    build: dict[str, Any] = Field(default_factory=dict)  # phase stats for this unit


class Unit(RpModel):
    """One retrievable item. v1.0 = fixed chunk (unitType chunk), v2.3.1 = evidence unit.

    Written to silver/.../units/units.jsonl by `units`; passed units copied to Gold by `promote`.
    """

    unit_id: str
    doc_id: str
    package_id: str
    pipeline_version: str
    unit_type: UnitType
    label: str  # human label for chips/cards: "W-2 · Maya Fictus · Boxes 1–6" or "chunk c-03"
    chunk_label: str | None = None  # v1.0 only: "chunk c-03"
    file_name: str
    doc_type: str | None = None  # "doc:W2" when enrichment.docType = ontology
    breadcrumb: list[str] = Field(default_factory=list)
    text: str
    pages: list[int]
    page_width: float | None = None
    page_height: float | None = None
    bboxes: list[BBox] = Field(default_factory=list)  # empty in v1.0 (no region)
    members: list[Member] = Field(default_factory=list)
    fields: list[FieldValue] = Field(default_factory=list)
    field_concepts: list[str] = Field(default_factory=list)
    period: str | None = None
    pii_flag: PiiFlag = PiiFlag.NONE
    allowed_principals: list[str]
    confidence: float | None = None
    chars: int = 0
    tokens: int | None = None
    provenance: UnitProvenance

    @property
    def resolvable(self) -> bool:
        """True when the unit can open a page region (has a bbox)."""
        return bool(self.bboxes)

    def field(self, field_id: str) -> FieldValue | None:
        return next((f for f in self.fields if f.field_id == field_id), None)

    def breadcrumb_text(self) -> str:
        return " › ".join(self.breadcrumb)

    def embedding_text(self) -> str:
        """Text sent to Titan V2: breadcrumb prepended when present."""
        crumb = self.breadcrumb_text()
        return f"{crumb}\n{self.text}" if crumb else self.text


class PiiEntityHit(RpModel):
    type: str
    score: float
    begin: int
    end: int
    digits: int  # count of digits in the matched span
    masked: bool  # True when the span is masked/truncated per rule (passes)


class PiiDecision(RpModel):
    unit_id: str
    decision: PiiFlag  # none | masked | held
    rule: str  # "rp-pii-v1"
    entities: list[PiiEntityHit] = Field(default_factory=list)
    held_bboxes: list[BBox] = Field(default_factory=list)  # filled black on Gold page images


class PiiDecisions(RpModel):
    """silver/.../pii_gate/decisions.json"""

    doc_id: str
    pipeline_version: str
    rule: str
    decisions: list[PiiDecision]

    @property
    def held(self) -> list[str]:
        return [d.unit_id for d in self.decisions if d.decision is PiiFlag.HELD]


class UnitStatusItem(RpModel):
    """Table rp-unit-status (pk unitId). docLane = ``{docId}#{pv}``, packageLane = ``{packageId}#{pv}``."""

    unit_id: str
    doc_id: str
    package_id: str
    pipeline_version: str
    doc_lane: str
    package_lane: str
    unit_type: UnitType
    label: str
    pii_flag: PiiFlag = PiiFlag.NONE
    held: bool = False
    silver_key: str
    gold_key: str | None = None
    aoss_id: str | None = None  # auto _id returned by OpenSearch Serverless
    indexed_at: str | None = None
    graph_triples: int | None = None
    review_items: int = 0
    updated_at: str | None = None


class IndexDocument(RpModel):
    """Document body in units-v1-0 / units-v2-3-1. ``_id`` is auto-assigned."""

    unit_id: str
    doc_id: str
    package_id: str
    pipeline_version: str
    unit_type: UnitType
    label: str
    chunk_label: str | None = None
    file_name: str
    doc_type: str | None = None
    field_concepts: list[str] = Field(default_factory=list)
    period: str | None = None
    pii_flag: PiiFlag
    breadcrumb: str = ""
    text: str
    pages: list[int]
    page_width: float | None = None
    page_height: float | None = None
    bboxes: list[BBox] = Field(default_factory=list)
    field_ids: list[str] = Field(default_factory=list)
    fields: list[dict[str, Any]] = Field(default_factory=list)  # {fieldId,label,value,valueNumber,concept,period,bbox}
    confidence: float | None = None
    allowed_principals: list[str]
    config_sha: str
    indexed_at: str
    embedding: list[float]

    @classmethod
    def from_unit(cls, unit: Unit, *, embedding: list[float], config_sha: str, indexed_at: str) -> IndexDocument:
        return cls(
            unit_id=unit.unit_id,
            doc_id=unit.doc_id,
            package_id=unit.package_id,
            pipeline_version=unit.pipeline_version,
            unit_type=unit.unit_type,
            label=unit.label,
            chunk_label=unit.chunk_label,
            file_name=unit.file_name,
            doc_type=unit.doc_type,
            field_concepts=unit.field_concepts,
            period=unit.period,
            pii_flag=unit.pii_flag,
            breadcrumb=unit.breadcrumb_text(),
            text=unit.text,
            pages=unit.pages,
            page_width=unit.page_width,
            page_height=unit.page_height,
            bboxes=unit.bboxes,
            field_ids=[f.field_id for f in unit.fields],
            fields=[
                f.model_dump(by_alias=True, exclude_none=True, mode="json", exclude={"block_refs", "source"})
                for f in unit.fields
            ],
            confidence=unit.confidence,
            allowed_principals=unit.allowed_principals,
            config_sha=config_sha,
            indexed_at=indexed_at,
            embedding=embedding,
        )


class ReviewItem(RpModel):
    """Table rp-review-queue: pk packageId, sk reviewKey = ``{docId}#{pv}#{unitId}#{fieldId}``."""

    package_id: str
    review_key: str
    doc_id: str
    pipeline_version: str
    unit_id: str
    field_id: str
    label: str
    value: str
    confidence: float
    threshold: float
    engine: Engine
    bbox: BBox | None = None
    reason: str  # "confidence below threshold"
    # GET /review: the extract artifact's model version (Textract ``AnalyzeDocumentModelVersion``). A version that
    # starts with ``LOCAL-`` is the dev stand-in (``local_pipeline/local_textract.py``), which has no confidences:
    # its 0.0 is a placeholder, not a measurement, and the UI says so.
    engine_model_version: str | None = None
    state: Literal["OPEN", "ACCEPTED", "CORRECTED", "REJECTED"] = "OPEN"
    created_at: str
    # W-14: the latest decision and every earlier one. A CORRECT keeps ``value`` (the extracted
    # value) and records the reviewer's value beside it as ``decision.value``.
    decision: ReviewDecision | None = None
    history: list[ReviewDecision] = Field(default_factory=list)


class VocabRole(RpModel):
    role: CanonicalRole
    concept: str  # "doc:Table"
    pref_label: str
    doco: str | None = None  # "doco:Table"
    engine_labels: dict[str, list[str]]  # {"docling": ["table"], "textract": ["LAYOUT_TABLE", "TABLE"]}
    derived: bool = False


class VocabDocType(RpModel):
    concept: str  # "doc:W2"
    pref_label: str  # "W-2 Wage and Tax Statement"
    short_label: str  # "W-2"
    alt_labels: list[str] = Field(default_factory=list)
    indicators: list[str]  # phrases scored by classify
    period_kind: Literal["tax_year", "pay_period", "statement_period", "quarter", "none"] = "none"


class VocabFieldConcept(RpModel):
    concept: str  # "fin:W2WagesBox1"
    pref_label: str  # "Wages, tips, other compensation"
    alt_labels: list[str]  # label variants as printed: "1 Wages, tips, other compensation", "Box 1"
    notation: str | None = None  # "1"
    doc_type: str  # "doc:W2"
    datatype: Literal["decimal", "string", "date"] = "decimal"
    unit_group: str | None = None  # "Wages (Boxes 1–6)" -> breadcrumb tail for the unit that holds it


class VocabSnapshot(RpModel):
    """config/ontology/{ontologyVersion}/vocab.json. Built by ontology/build.py (A) and,
    in AWS, re-exported from Neptune by rp-vocab-export with the same shape."""

    ontology_version: str
    generated_at: str
    source: Literal["build", "neptune"]
    roles: list[VocabRole]
    doc_types: list[VocabDocType]
    field_concepts: list[VocabFieldConcept]

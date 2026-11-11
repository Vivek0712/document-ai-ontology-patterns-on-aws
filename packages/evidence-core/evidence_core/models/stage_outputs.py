"""Typed ``StageResult.output`` per stage, plus the SQS / Map item payloads of rp-ingest.

The ASL reads a few of these fields with JSONPath, so the names are part of the contract:
  $.stages.route.output.digitalPages   (Choice HasDigital, SQS message body)
  $.stages.route.output.scanPages      (Choice HasScan, Map ItemsPath)
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..base import RpModel
from .status import DocCtx, StageResult


class PageInfo(RpModel):
    page: int
    width: float  # PDF points
    height: float
    text_chars: int  # pypdf text-layer characters on this page
    route: Literal["digital", "scan"] | None = None


class RegisterOutput(RpModel):
    """Also written as silver/.../register/manifest.json."""

    page_count: int
    pages: list[PageInfo]
    sha256: str
    size_bytes: int
    content_type: str
    source_version_id: str | None = None
    validation: Literal["ok", "quarantined", "skipped"] = "ok"  # ASL ModeChoice reads this
    quarantine_reason: str | None = None


class RouteOutput(RpModel):
    page_count: int
    digital_pages: list[int]
    scan_pages: list[int]
    threshold_chars: int
    pages: list[PageInfo]


class ExtractOutput(RpModel):
    engines: list[Literal["text-layer", "docling", "textract"]]
    pages_by_engine: dict[str, list[int]]
    blocks: int  # layout items (docling texts+tables+pictures, textract LAYOUT_* blocks) or text pages
    empty_pages: list[int] = Field(default_factory=list)  # v1.0: scanned pages with no text layer


class ClassifyOutput(RpModel):
    doc_type: str  # "doc:W2"
    label: str  # "W-2"
    scores: dict[str, float]  # indicator score per candidate concept
    margin: float
    tie_break_used: bool
    tie_break_model: str | None = None


class UnitsOutput(RpModel):
    units: int
    unit_types: dict[str, int]  # {"table_panel": 2, "section_text": 2}
    fields: int
    review_items: int = 0
    phase_counts: dict[str, int] = Field(default_factory=dict)  # {"A": 3, "B": 5, "C": 1, "marker": 0}
    mean_chars: float = 0


class PiiGateOutput(RpModel):
    units: int
    passed: int
    masked: int
    held: int
    held_unit_ids: list[str] = Field(default_factory=list)


class PromoteOutput(RpModel):
    gold_units: int
    pages_rendered: int
    held_regions: int


class IndexOutput(RpModel):
    index: str
    indexed: int
    deleted_previous: int
    embed_model: str
    dimensions: int


class GraphOutput(RpModel):
    graph: str
    triples: int
    facts: int
    deleted_previous: int


class ReadyOutput(RpModel):
    index: str
    expected: int
    indexed: int
    waited_ms: int


STAGE_OUTPUT_MODELS: dict[str, type[RpModel]] = {
    "register": RegisterOutput,
    "route": RouteOutput,
    "extract": ExtractOutput,
    "classify": ClassifyOutput,
    "units": UnitsOutput,
    "pii_gate": PiiGateOutput,
    "promote": PromoteOutput,
    "index": IndexOutput,
    "graph": GraphOutput,
    "ready": ReadyOutput,
}


class RegisterResult(RpModel):
    """What rp-register returns: the whole new execution state (ASL ResultPath ``$``)."""

    ctx: DocCtx
    stages: dict[str, StageResult]


class DoclingMessage(RpModel):
    """SQS body sent by state DoclingExtract (sqs:sendMessage.waitForTaskToken)."""

    task_token: str
    ctx: DocCtx
    pages: list[int]  # digital pages to convert (1-based)
    execution_arn: str


class TextractPageInput(RpModel):
    """Map ItemSelector for state TextractPage."""

    ctx: DocCtx
    page: int
    execution_arn: str

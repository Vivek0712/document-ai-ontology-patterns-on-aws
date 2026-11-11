"""String enums used on the wire, in DynamoDB and in the UI."""

from __future__ import annotations

from enum import StrEnum


class StageId(StrEnum):
    """Ordered ingestion stages. Order and metadata live in stages.REGISTRY."""

    REGISTER = "register"
    ROUTE = "route"
    EXTRACT = "extract"
    CLASSIFY = "classify"
    UNITS = "units"
    PII_GATE = "pii_gate"
    PROMOTE = "promote"
    INDEX = "index"
    GRAPH = "graph"
    READY = "ready"


class StageGroup(StrEnum):
    """Display columns on the stage board."""

    UPLOAD = "upload"
    EXTRACT = "extract"
    UNITS = "units"
    GATE = "gate"
    INDEX = "index"


class StageState(StrEnum):
    """Current state of one stage record."""

    PENDING = "PENDING"
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"

    @property
    def terminal(self) -> bool:
        return self in (StageState.SUCCEEDED, StageState.FAILED, StageState.SKIPPED)


class StageEventType(StrEnum):
    """Append-only events written next to stage records."""

    QUEUED = "QUEUED"
    STARTED = "STARTED"
    HEARTBEAT = "HEARTBEAT"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class LaneState(StrEnum):
    """State of one (document, pipelineVersion) lane = one Step Functions execution."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class Engine(StrEnum):
    """Engines that can produce a stage's output. Shown in the stage inspector."""

    TEXT_LAYER = "text-layer"  # pypdf, v1.0 naive lane
    DOCLING = "docling"  # docling-serve on Fargate
    TEXTRACT = "textract"  # Amazon Textract sync AnalyzeDocument
    PYPDF = "pypdf"  # routing (text-layer char counts)
    SKOS = "skos-indicators"  # classify by SKOS DocumentType indicators
    NOVA = "nova"  # compact model tie-break
    EU_BUILDER = "eu-builder"
    FIXED_CHUNKER = "fixed-chunker"
    COMPREHEND = "comprehend"
    PDFIUM = "pdfium"  # page rendering in promote
    TITAN_AOSS = "titan-v2+aoss"
    NEPTUNE = "neptune"
    LAMBDA = "lambda"


class UnitType(StrEnum):
    """Unit types. v1.0 produces only CHUNK; v2.3.1 produces the evidence-unit types."""

    CHUNK = "chunk"
    TABLE_PANEL = "table_panel"
    SECTION_TEXT = "section_text"
    FIGURE_PANEL = "figure_panel"
    FIELD_GROUP = "field_group"
    LIST_BLOCK = "list_block"


class PiiFlag(StrEnum):
    """Result of the PII gate for one unit."""

    NONE = "none"
    MASKED = "masked"  # PII-shaped span present but masked/truncated: passes
    HELD = "held"  # unmasked PII: unit stays in Silver, never indexed


class CanonicalRole(StrEnum):
    """Canonical layout roles (aligned to DoCO). Engine labels map here via the vocab snapshot."""

    TITLE = "title"
    SECTION_HEADER = "section_header"
    TEXT = "text"
    LIST_ITEM = "list_item"
    TABLE = "table"
    TABLE_CELL = "table_cell"
    KEY_VALUE = "key_value"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    PICTURE = "picture"
    FORMULA = "formula"
    PAGE_HEADER = "page_header"
    PAGE_FOOTER = "page_footer"
    UNIT_LABEL = "unit_label"  # derived
    MARKER_LINK = "marker_link"  # derived


class ArtifactKind(StrEnum):
    """What an artifact is; the UI picks a viewer by kind."""

    SOURCE_PDF = "source_pdf"
    MANIFEST = "manifest"
    ROUTE = "route"
    TEXT_LAYER = "text_layer"
    DOCLING_JSON = "docling_json"
    TEXTRACT_JSON = "textract_json"
    CLASSIFICATION = "classification"
    UNITS_JSONL = "units_jsonl"
    BUILD_REPORT = "build_report"
    PII_DECISIONS = "pii_decisions"
    GOLD_UNIT = "gold_unit"
    GOLD_UNITS_JSONL = "gold_units_jsonl"
    PAGE_IMAGE = "page_image"
    INDEX_RECEIPT = "index_receipt"
    ABOX_NQUADS = "abox_nquads"
    GRAPH_RECEIPT = "graph_receipt"
    READY_RECEIPT = "ready_receipt"
    CONFIG = "config"
    RUN = "run"


class ToolName(StrEnum):
    """MCP tools exposed through AgentCore Gateway (version 1 of each)."""

    SEARCH_UNITS = "search_units"
    GRAPH_HOP = "graph_hop"
    GET_REGION = "get_region"
    DOC_STATUS = "doc_status"


class AskEventType(StrEnum):
    """Events streamed by the agent for one ask (SSE `data:` payloads)."""

    RUN_START = "run_start"
    TOOL_START = "tool_start"
    TOOL_END = "tool_end"
    TEXT = "text"
    ANSWER = "answer"
    CHECK = "check"
    RUN = "run"
    ERROR = "error"

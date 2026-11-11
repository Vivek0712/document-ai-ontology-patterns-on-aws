"""MCP tool contracts (AgentCore Gateway Lambda targets), version 1.

Model-visible arguments are the ``*Args`` models. The REQUEST interceptor deletes any
model-supplied value for the injected keys and writes the caller's real values
. The Lambda receives ``Args + InjectedContext`` merged in one dict.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..base import RpModel
from ..bbox import BBox
from ..enums import LaneState, PiiFlag, StageState, UnitType

INJECTED_FIELDS: tuple[str, ...] = ("callerSub", "groups", "pipelineVersion", "packageId", "runId")
"""Keys the interceptor owns. Headers it reads (set by agent code on the MCP client):
Authorization: Bearer <user access token> -> callerSub (sub), groups (cognito:groups)
X-Rp-Pipeline-Version -> pipelineVersion;  X-Rp-Package -> packageId;  X-Rp-Run -> runId"""

HEADER_PIPELINE_VERSION = "X-Rp-Pipeline-Version"
HEADER_PACKAGE = "X-Rp-Package"
HEADER_RUN = "X-Rp-Run"

GATEWAY_TARGETS: dict[str, str] = {
    "search_units": "search-units",
    "graph_hop": "graph-hop",
    "get_region": "get-region",
    "doc_status": "doc-status",
}
"""Gateway target name per tool. Gateway exposes tools to MCP clients as
``{target}___{tool}`` (e.g. ``search-units___search_units``); agent code strips the prefix."""


class InjectedContext(RpModel):
    caller_sub: str
    groups: list[str] = Field(default_factory=list)
    pipeline_version: str
    package_id: str
    run_id: str | None = None

    @classmethod
    def from_arguments(cls, args: dict[str, Any]) -> InjectedContext:
        missing = [k for k in ("callerSub", "pipelineVersion", "packageId") if not args.get(k)]
        if missing:
            raise PermissionError(f"interceptor did not inject {missing}")
        return cls.load({k: args.get(k) for k in INJECTED_FIELDS})


def split_arguments(args: dict[str, Any]) -> tuple[dict[str, Any], InjectedContext]:
    """Separate model arguments from injected context inside a tool Lambda."""
    ctx = InjectedContext.from_arguments(args)
    model_args = {k: v for k, v in args.items() if k not in INJECTED_FIELDS}
    return model_args, ctx


# --- search_units@1 ---------------------------------------------------------------------

class SearchUnitsArgs(RpModel):
    query: str = Field(min_length=1, max_length=1000, description="Natural-language search query.")
    k: int | None = Field(default=None, ge=1, le=10, description="Results to return; default from config (5).")
    doc_type: str | None = Field(default=None, description="Optional docType filter, e.g. doc:W2.")
    unit_types: list[UnitType] | None = Field(default=None, description="Optional unit type filter.")


class UnitScores(RpModel):
    dense_raw: float | None = None  # logged, never displayed
    bm25_raw: float | None = None
    dense_norm: float
    bm25_norm: float
    fused: float


class FieldBrief(RpModel):
    field_id: str
    label: str
    value: str
    value_number: str | None = None
    concept: str | None = None
    period: str | None = None
    page: int | None = None
    bbox: BBox | None = None


class RetrievedUnit(RpModel):
    rank: int
    unit_id: str
    doc_id: str
    file_name: str
    unit_type: UnitType
    label: str
    chunk_label: str | None = None
    doc_type: str | None = None
    breadcrumb: str = ""
    text: str
    pages: list[int]
    page_width: float | None = None
    page_height: float | None = None
    bboxes: list[BBox] = Field(default_factory=list)
    fields: list[FieldBrief] = Field(default_factory=list)
    period: str | None = None
    pii_flag: PiiFlag = PiiFlag.NONE
    resolvable: bool
    scores: UnitScores


class SearchQueryEcho(RpModel):
    """What the retriever actually ran (shown in the query panel, F11)."""

    index: str
    k: int
    sub_query_k: int
    weights: dict[str, float]  # {"dense": 0.7, "bm25": 0.3}
    normalization: Literal["min_max"] = "min_max"
    filter: dict[str, Any]  # the bool filter placed INSIDE both sub-queries
    embed_model: str


class SearchUnitsResult(RpModel):
    units: list[RetrievedUnit]
    query: SearchQueryEcho
    latency_ms: int


# --- graph_hop@1 ---------------------------------------------------------------------------

GraphTemplateId = Literal[
    "qi_sum_w2_box1_v1",  # fin:QualifyingIncome: sum W-2 Box 1 per borrower, latest tax year
    "field_lookup_v1",  # facts for a concept (optional borrower), with lineage
    "concept_by_label_v1",  # label text -> fin: concept via skos:prefLabel/altLabel
    "unit_lineage_v1",  # unit -> regions -> document -> package (PROV-O)
    "coupon_terms_v1",  # a bond's coupon and its rating-triggered step event, with the footnote region (D2)
]
GRAPH_TEMPLATES: tuple[str, ...] = (
    "qi_sum_w2_box1_v1",
    "field_lookup_v1",
    "concept_by_label_v1",
    "unit_lineage_v1",
    "coupon_terms_v1",
)


class GraphHopArgs(RpModel):
    template: GraphTemplateId
    params: dict[str, str] = Field(
        default_factory=dict,
        description="Template parameters: field_lookup_v1 {concept, borrower?}; concept_by_label_v1 {label}; "
        "unit_lineage_v1 {unitId}; qi_sum_w2_box1_v1 {} (taxYear optional); "
        "coupon_terms_v1 {security} (a security identifier such as XS-2031).",
    )


# Templates only rp-api may run ("rp-api → rp-graph-hop"): accepted from an invocation whose Lambda
# client context says ``rpSource: rp-api``, never from the Gateway. They are not in ``GraphTemplateId``, so the
# agent's graph_hop tool schema (``mcp_tools.json``, and every run's toolSpecSha) does not change.
ApiGraphTemplateId = Literal[
    "neighbourhood_v1",  # an IRI's depth-1/2 neighbourhood in the TBox and the package ABox (W-11, W-09)
]
API_GRAPH_TEMPLATES: tuple[str, ...] = ("neighbourhood_v1",)


class GraphHopApiArgs(GraphHopArgs):
    """``GraphHopArgs`` as rp-api may send them: the agent's templates plus the API-only ones."""

    template: GraphTemplateId | ApiGraphTemplateId  # type: ignore[assignment]


class GraphOperand(RpModel):
    unit_id: str
    field_id: str
    value: str
    concept: str | None = None
    label: str | None = None  # e.g. borrower name


class GraphHopResult(RpModel):
    template: str
    sparql: str  # rendered query as sent (prefixes included)
    graphs: list[str]  # FROM graphs used (ACL-selected)
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    total: str | None = None  # decimal string when the template computes one
    operands: list[GraphOperand] = Field(default_factory=list)
    note: str | None = None  # "no ABox for pipelineVersion 1.0 (ontology: none)"
    latency_ms: int


# --- get_region@1 ----------------------------------------------------------------------------

class GetRegionArgs(RpModel):
    unit_id: str
    field_id: str | None = None


class RegionResult(RpModel):
    """Shared shape of the get_region tool result and GET /region/{unitId} (HTTP adds url)."""

    unit_id: str
    field_id: str | None = None
    doc_id: str
    file_name: str
    label: str
    page: int
    bbox: BBox  # field bbox when fieldId given and present, else first unit bbox
    field_bbox: BBox | None = None
    unit_bboxes: list[BBox]
    page_width: float
    page_height: float
    page_image_key: str
    held_regions: list[BBox] = Field(default_factory=list)
    value: str | None = None
    field_label: str | None = None
    concept: str | None = None
    unit_type: UnitType
    breadcrumb: str = ""
    engine: str | None = None
    pii_flag: PiiFlag = PiiFlag.NONE
    pipeline_version: str
    ontology_version: str | None = None
    url: str | None = None  # presigned GET (HTTP route only), 300 s
    expires_in: int | None = None


# --- doc_status@1 ------------------------------------------------------------------------------

class DocStatusArgs(RpModel):
    doc_id: str | None = None


class DocStatusDoc(RpModel):
    doc_id: str
    file_name: str
    doc_type: str | None = None
    lane_state: LaneState
    current_stage: str | None = None
    stages: dict[str, StageState]
    units: int | None = None
    held: int | None = None
    indexed: int | None = None


class DocStatusResult(RpModel):
    package_id: str
    pipeline_version: str
    ready: bool
    docs: list[DocStatusDoc]


TOOL_ARG_MODELS: dict[str, type[RpModel]] = {
    "search_units": SearchUnitsArgs,
    "graph_hop": GraphHopArgs,
    "get_region": GetRegionArgs,
    "doc_status": DocStatusArgs,
}
# rp-api's argument models (invocation source ``rp-api``); every other caller gets ``TOOL_ARG_MODELS``
API_TOOL_ARG_MODELS: dict[str, type[RpModel]] = {"graph_hop": GraphHopApiArgs}
TOOL_RESULT_MODELS: dict[str, type[RpModel]] = {
    "search_units": SearchUnitsResult,
    "graph_hop": GraphHopResult,
    "get_region": RegionResult,
    "doc_status": DocStatusResult,
}
TOOL_DESCRIPTIONS: dict[str, str] = {
    "search_units": "Hybrid (dense + BM25) search over the package's units for the active pipeline version. "
    "Returns the top K units with normalised scores, fields and page regions when available.",
    "graph_hop": "Run an approved SPARQL template over the package's knowledge graph (FIBO subset, DoCO, "
    "SKOS, PROV-O, fin:). Returns rows, a computed total when the template has one, and operand lineage.",
    "get_region": "Resolve the page region for a unit or one of its fields: page, bbox in PDF points "
    "(top-left origin), page size.",
    "doc_status": "Ingestion status of the package's documents for the active pipeline version.",
}


def _simplify(node: Any, defs: dict[str, Any]) -> Any:
    if isinstance(node, list):
        return [_simplify(n, defs) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        target = defs[node["$ref"].rsplit("/", 1)[-1]]
        merged = {**target, **{k: v for k, v in node.items() if k != "$ref"}}
        return _simplify(merged, defs)
    if "anyOf" in node:
        options = [o for o in node["anyOf"] if o.get("type") != "null"]
        if len(options) == 1:
            rest = {k: v for k, v in node.items() if k != "anyOf"}
            return _simplify({**options[0], **rest}, defs)
    out: dict[str, Any] = {}
    for k, v in node.items():
        if k in ("title", "default", "$defs"):
            continue
        out[k] = _simplify(v, defs) if k != "properties" else {pk: _simplify(pv, defs) for pk, pv in v.items()}
    return out


def tool_input_schema(tool: str) -> dict[str, Any]:
    """Gateway target inputSchema: model-visible args only, camelCase, no $ref/anyOf/title/default
    (AgentCore Gateway accepts a plain JSON-Schema subset)."""
    schema = TOOL_ARG_MODELS[tool].model_json_schema(by_alias=True)
    return _simplify(schema, schema.get("$defs", {}))


def tool_specs() -> list[dict[str, Any]]:
    """Tool definitions for the Gateway Lambda targets (scripts/deploy_agentcore.sh reads the
    exported contracts/generated/mcp_tools.json)."""
    return [
        {
            "name": name,
            "target": GATEWAY_TARGETS[name],
            "description": TOOL_DESCRIPTIONS[name],
            "inputSchema": tool_input_schema(name),
        }
        for name in TOOL_ARG_MODELS
    ]

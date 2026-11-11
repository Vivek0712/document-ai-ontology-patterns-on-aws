"""v2 inspection routes: the evidence-unit build replay, the retrieval filter funnel, graph
paths and showcase payloads. Every value is read from a stored artifact or counted by a query; nothing is
inferred. Mirrored in contracts/api.ts.

Routes (rp-api): ``GET /documents/{docId}/lanes/{pv}/build`` (``BuildReplay``), ``GET /inspect/filters``
(``FilterInspection``), ``GET /runs/{runId}/calls/{callId}/path`` (``GraphNeighbourhood``), ``GET /showcases``
(``ShowcaseCatalog``) and ``GET /showcases/{id}/payload`` (``ShowcasePayload``).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..base import RpModel
from ..bbox import BBox

Provenance = Literal["measured", "local-run", "generator", "derived", "design-target", "estimate", "what-if",
                     "counterfactual", "snapshot", "replay"]


class SourceRef(RpModel):
    """One stored object or query a payload was read from (the receipts drawer lists these)."""

    kind: Literal["s3", "ddb", "aoss", "neptune", "route", "coa", "local"]  # local: a dev-machine stand-in
    key: str
    version_id: str | None = None
    sha256: str | None = None
    at: str | None = None


# --- GET /documents/{docId}/lanes/{pv}/build ---------------------------------------------------------------

class BuildAllocation(RpModel):
    """How one layout block joined its unit. No block text: positions, roles and scores only."""

    block_pointer: str  # engine pointer, e.g. "#/texts/12"
    member_id: str | None = None  # the member it became, in ``unit_id``
    role: str  # canonical role after phase 0 (derived roles included)
    page: int | None = None
    bbox: BBox | None = None
    phase: Literal["A", "marker", "B", "C"]  # how it joined ``unit_id``
    score: float | None = None  # phase B cosine against ``candidate_unit_id`` (also when below tau)
    tau: float | None = None
    allocated: bool  # joined a seeded unit (pass A, marker link, pass B); False = residual section text (pass C)
    unit_id: str  # the unit it ended up in
    candidate_unit_id: str | None = None  # phase B: the seed with the highest score
    cross_page: bool | None = None  # marker links: the footnote is on another page than its seed


class MarkerLink(RpModel):
    """A footnote attached to the seed that prints its marker (eu_builder hard rule)."""

    unit_id: str
    marker: str
    footnote_member: str | None = None
    footnote_page: int | None = None
    footnote_bbox: BBox | None = None
    marker_box: BBox | None = None  # the raised glyph in the seed, when that was the evidence
    field_id: str | None = None  # the field whose printed value carries the marker
    cross_page: bool
    evidence: str  # "raised glyph" | "marker glyph in text"

    @classmethod
    def from_build(cls, unit_id: str, raw: dict[str, Any], members: dict[str, Any]) -> MarkerLink:
        """One eu_builder ``markerLinks`` entry of a unit. ``members`` maps member id -> the unit's member (for the
        footnote's page and box). The one place SC-2's build replay and SC-3's composer read a link."""
        member = members.get(raw.get("footnoteMember") or "")
        bbox = member.bbox if member is not None else None
        return cls(unit_id=unit_id, marker=str(raw.get("marker")), footnote_member=raw.get("footnoteMember"),
                   footnote_page=bbox.page if bbox else None, footnote_bbox=bbox,
                   marker_box=BBox.load(raw["markerBox"]) if raw.get("markerBox") else None,
                   field_id=raw.get("fieldId"), cross_page=bool(raw.get("crossPage")),
                   evidence=str(raw.get("evidence") or "marker glyph in text"))


class BuildUnitSummary(RpModel):
    unit_id: str
    unit_type: str
    members: int
    fields: int
    pages: list[int]
    merged_tables: int = 0


class BuildInvariants(RpModel):
    mode: Literal["off", "log", "enforce"]
    violations: list[dict[str, Any]] = Field(default_factory=list)
    note: str | None = None


class BuildReplay(RpModel):
    doc_id: str
    package_id: str
    pipeline_version: str
    built_at: str | None = None
    embed_model: str  # Phase B embeddings ("local-hash-embedder" marks a run without Titan)
    tau: float
    phase_b: str | None = None
    phase_counts: dict[str, int]
    merged_tables: int
    derived_roles: dict[str, int]
    allocations: list[BuildAllocation]
    marker_links: list[MarkerLink]
    units: list[BuildUnitSummary]
    invariants: BuildInvariants | None = None
    sources: list[SourceRef]


# --- GET /inspect/filters?runId=&callId= -------------------------------------------------------------------

class FilterCounts(RpModel):
    """``_count`` queries with the logged filter, and the sizes the tool's sub-queries could return."""

    index_total: int  # documents of the run's package and lane that the caller may read (``scope``)
    after_filter: int  # documents the logged filter admits
    bm25_matches: int  # of those, documents the BM25 sub-query matches at all
    dense_top_n: int  # min(subQueryK, afterFilter): what the filtered k-NN sub-query can return (derived)
    bm25_top_n: int  # min(subQueryK, bm25Matches) (derived)
    fused_k: int  # units the tool returned after fusion (from the logged tool output)
    derived: list[str] = Field(default_factory=lambda: ["denseTopN", "bm25TopN"])  # computed, not counted


class FilterCounterfactual(RpModel):
    label: Literal["counterfactual · counts only"] = "counterfactual · counts only"
    top_k_unfiltered: int
    surviving_after_acl: int
    other_packages_in_top_k: int


class FilterInspection(RpModel):
    run_id: str
    call_id: str
    package_id: str
    pipeline_version: str
    index: str
    query_text: str
    filter: dict[str, Any]
    k: int
    sub_query_k: int
    weights: dict[str, float]
    normalization: str
    counts: FilterCounts
    counterfactual: FilterCounterfactual | None = None
    counted_by: str  # "OpenSearch Serverless _count" or the local stand-in's label
    counted_at: str | None = None  # when the counts were taken: now, at request time, not at the run's time
    scope: dict[str, Any] | None = None  # the first bar's query: the run's package and lane, the caller's principals
    sources: list[SourceRef]


# --- GET /runs/{runId}/calls/{callId}/path · GET /graph/neighbourhood --------------------------------

class GraphNode(RpModel):
    iri: str
    kind: Literal["class", "individual", "literal", "unit", "field", "region"]
    label: str
    types: list[str] = Field(default_factory=list)
    graph: str


class GraphEdge(RpModel):
    s: str
    p: str
    o: str
    label: str
    graph: str


class GraphNeighbourhood(RpModel):
    focus: str
    depth: int
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    truncated: bool
    template: str
    sparql: str | None = None
    latency_ms: int | None = None
    source: Literal["logged-rows", "neptune"]  # logged-rows: derived from a run's graph_hop rows, no graph call
    run_id: str | None = None
    call_id: str | None = None


# --- GET /showcases · GET /showcases/{id}/payload -----------------------------------------------------------

class ShowcaseComposer(RpModel):
    showcase_id: str  # "sc-3"
    title: str
    params: list[str]  # query parameters the composer reads
    reads: list[str]  # what it reads, for the receipts drawer


class ShowcaseCatalog(RpModel):
    items: list[ShowcaseComposer]


class ShowcasePayload(RpModel):
    showcase_id: str
    version: int
    mode: Literal["live", "replay", "local-run"]  # local-run: composed over the local pipeline snapshot
    package_id: str
    run_ids: list[str] = Field(default_factory=list)
    snapshot_id: str | None = None
    recorded_at: str | None = None
    payload_sha: str
    data: dict[str, Any]
    sources: list[SourceRef]
    provenance: dict[str, Provenance]  # JSONPath -> provenance of the values under it


class CouponTermsData(RpModel):
    """SC-3: the coupon, its raised marker, the footnote it links and the step event the graph holds."""

    security: str
    doc_id: str | None = None
    file_name: str | None = None
    unit_id: str | None = None
    coupon_field: dict[str, Any] | None = None  # FieldValue of the coupon (value as printed, bbox)
    marker_link: MarkerLink | None = None
    classify: dict[str, Any] | None = None  # the classify StageRecord's output (docType, scores, tie-break)
    graph: dict[str, Any] | None = None  # graph_hop coupon_terms_v1 result as rp-graph-hop returned it
    step_event: dict[str, Any] | None = None  # the template's row: newRate, effectiveFromYear, triggerRating, region
    step_event_parse: Literal["ok", "failed", "no-link"]
    # the template's bound triple patterns as a graph (api/graph_paths.py, source "logged-rows"); no extra query
    path: GraphNeighbourhood | None = None

"""Governance contracts (SC-7, SC-8, W-13, W-14). Mirrored in contracts/api.ts.

Routes (rp-api):
- ``GET /access/decisions?runId=|since=`` -> ``AccessDecisionPage``: what the gateway injected into each tool call
  and whether it let the call through (SC-7, W-13). Rows come from ``rp-access-log`` (the REQUEST interceptor writes
  one per ``tools/call``); a run logged before the table existed is answered from its RunRecord's tool calls, which
  carry the same injected values, and every such row says ``source: run-record``.
- ``GET /governance/definitions/{name}`` -> ``GovernedDefinitionView``: the Git-governed definition of a metric
  (SC-8 fallback card): the built TBox's labels and comment, the template that computes it, the commit and review
  record of its Turtle source, and the ``make ontology-check`` result recorded at build time. With ``coaMode``
  ``snapshot`` the COA slot carries a recorded COA session (metric card, cross-check, hashes, latency); with ``off``
  it stays ``not-captured``.
- ``POST /review/{packageId}/items/{reviewKey}/decision`` -> ``ReviewItem`` (W-14): ACCEPT, CORRECT or REJECT a
  low-confidence field. A CORRECT records an override beside the extracted value; it never overwrites it.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..base import RpModel

InjectionPoint = Literal["interceptor", "agent_hook", "local-gateway", "coa-control"]


class AccessInjected(RpModel):
    pipeline_version: str
    package_id: str
    run_id: str | None = None


Decision = Literal["allow", "deny", "unknown"]


class AccessDecision(RpModel):
    """Table ``rp-access-log``: pk ``runId``, sk ``{at}#{callId}``; GSI ``bySince`` (``day``, ``at``); TTL 30 days."""

    at: str
    run_id: str
    call_id: str
    tool: str
    target: str  # Gateway target name ("search-units"), or the tool name when the gateway is local
    caller_sub6: str  # first 6 characters of the caller's sub (never the full sub)
    groups: list[str]
    injected: AccessInjected
    injection_point: InjectionPoint
    # "unknown": a run-record row whose call errored before the tool decided (``routes/access.record_decision``)
    decision: Decision
    reason: str | None = None
    stripped_model_keys: list[str] = Field(default_factory=list)  # identity keys the model supplied and lost
    model_args: list[str] = Field(default_factory=list)  # argument names the model supplied (no values)
    source: Literal["access-log", "run-record"] = "access-log"
    day: str | None = None  # GSI bySince partition (YYYY-MM-DD)
    ttl: int | None = None


class AccessDecisionPage(RpModel):
    items: list[AccessDecision]
    run_id: str | None = None
    since: str | None = None
    next_cursor: str | None = None
    note: str | None = None


# --- governed definition (SC-8 fallback card) --------------------------------------------------------------

class OntologyCheckResult(RpModel):
    name: str
    ok: bool
    # what the check found ("urn:rp:ont:fin:QualifyingIncome" for a label check), so a screen can show the result
    detail: str | None = None


class OntologyCheckRecord(RpModel):
    command: str  # "make ontology-check"
    passed: int
    total: int
    checks: list[OntologyCheckResult]
    inputs_sha256: str  # sha256 of the built rp.nq the checks ran on
    ran_at: str | None = None


class GitRecord(RpModel):
    commit: str
    author: str
    committed_at: str


class ReviewRecord(RpModel):
    """Who reviewed the Turtle change (a reviewed, merged pull request). None until one exists."""

    reviewer: str
    url: str | None = None
    approved_at: str | None = None


class DefinitionTerm(RpModel):
    iri: str
    name: str  # metric name, e.g. "qualifying_income"
    label: str | None = None
    description: str | None = None  # rdfs:comment, verbatim
    sub_class_of: list[str] = Field(default_factory=list)
    alt_labels: list[str] = Field(default_factory=list)
    operand_concepts: list[str] = Field(default_factory=list)
    computed_by: str | None = None  # rp:computedBy template id
    graph: str | None = None


class TemplateRef(RpModel):
    name: str
    path: str
    sha256: str


class OntologySourceRef(RpModel):
    ontology_id: str  # "urn:rp:ont:fin"
    version: str
    nquads: str
    source: str  # "ontology/src/rp/fin.ttl"
    source_sha256: str


class CoaTraceStep(RpModel):
    """One step of COA's Tier-1 resolver trace, as COA returned it (``t1.metric_match`` → ``metric_match``)."""

    step: str
    status: str
    tool: str | None = None  # cedar | sql-firewall | sql-engine
    engine: str | None = None  # athena (the execute step)
    duration_ms: int | None = None


class CoaMetricCard(RpModel):
    """COA's metric definition as ``GET /namespaces/{ns}/metrics/{name}`` returned it. ``defined_by`` and
    ``effective_from`` are set by COA's server (the API takes neither); the snapshot redacts ``defined_by``."""

    name: str
    label: str | None = None
    description: str | None = None
    dialect: str
    sql: str
    unit: str | None = None
    return_type: str | None = None
    default_time_grain: str | None = None
    synonyms: list[str] = Field(default_factory=list)
    ontology_concepts: list[str] = Field(default_factory=list)
    source_table: str | None = None
    defined_by: str
    defined_by_redacted: bool = False
    effective_from: str | None = None


class CoaOntologyAnchor(RpModel):
    """Where the metric's concept sits in COA's copy of our ontology. Uploaded Turtle has no steward review in COA."""

    concept: str  # urn:rp:ont:fin:QualifyingIncome
    ontology_id: str
    ontology_type: str  # user_uploaded
    steward_reviewed: bool = False
    label: str  # "uploaded, not steward-reviewed"
    sub_class_of: list[str] = Field(default_factory=list)
    operand_concepts: list[str] = Field(default_factory=list)
    class_count: int | None = None
    axiom_count: int | None = None
    matches_git_source: bool | None = None  # the uploaded fin.ttl's sha256 equals the Git source's


class CoaCrossCheck(RpModel):
    """COA's independent recomputation of the governed metric (REST ``POST /namespaces/{ns}/query``, Tier 1)."""

    metric: str
    tier: int
    confidence: float
    rationale: str | None = None
    value: str  # resultRows[0][metric], verbatim
    dimensions: dict[str, str] = Field(default_factory=dict)
    trace: list[CoaTraceStep] = Field(default_factory=list)
    runs: int = 1
    identical_runs: int = 1
    mcp_value: str | None = None  # the same query through COA's MCP server
    mcp_runs: int = 0


class CoaHash(RpModel):
    name: str
    sha256: str
    detail: str | None = None


class CoaLatency(RpModel):
    call: str
    n: int
    median_ms: float
    min_ms: float
    max_ms: float


class CoaLatencyRecord(RpModel):
    measured: bool = True
    basis: str  # where the clock ran
    calls: list[CoaLatency] = Field(default_factory=list)


class CoaRefusal(RpModel):
    token: str
    status: int
    message: str | None = None


class CoaAccessRule(RpModel):
    rule: str
    refused: list[CoaRefusal] = Field(default_factory=list)


class CoaSnapshot(RpModel):
    """What one recorded session against a COA deployment returned, read from ``runs/local/coa/<date>/``."""

    captured_at: str  # the day the calls ran (YYYY-MM-DD)
    coa_version: str | None = None
    namespace: str | None = None
    region: str | None = None
    metric: CoaMetricCard
    ontology: CoaOntologyAnchor
    cross_check: CoaCrossCheck
    hashes: list[CoaHash] = Field(default_factory=list)
    latency: CoaLatencyRecord
    access: CoaAccessRule | None = None
    files: dict[str, str] = Field(default_factory=dict)  # snapshot file -> sha256


class CoaSlot(RpModel):
    """The COA variant's place on the card. ``off``: the Git record only (``not-captured``). ``snapshot``: the
    recorded COA calls in ``snapshot``, labelled as a snapshot with its capture date; nothing is called live."""

    mode: Literal["off", "live", "snapshot"] = "off"
    state: Literal["not-captured", "captured"] = "not-captured"
    snapshot_id: str | None = None
    snapshot_at: str | None = None
    note: str = "COA is off in this build: the Git record governs the definition."
    snapshot: CoaSnapshot | None = None


class GovernedDefinitionView(RpModel):
    source: Literal["git", "coa-live", "coa-snapshot"]
    definition: DefinitionTerm
    ontology: OntologySourceRef
    template: TemplateRef | None = None
    git: GitRecord | None = None  # None when the source file has no commit yet
    review: ReviewRecord | None = None
    check: OntologyCheckRecord
    coa: CoaSlot = Field(default_factory=CoaSlot)
    recorded_at: str  # when the build wrote this record (ontology/governance.py)
    recorded_by: str = "ontology/governance.py"


# --- review queue decisions (W-14) --------------------------------------------------------------------------

class ReviewDecisionRequest(RpModel):
    action: Literal["ACCEPT", "CORRECT", "REJECT"]
    value: str | None = Field(default=None, max_length=200)  # CORRECT only: the reviewer's value
    note: str | None = Field(default=None, max_length=500)
    # the ``decision.at`` of the item as the reviewer saw it; None (or absent) = it had no decision. A decision
    # made since then answers 409, so a stale page never overwrites another reviewer silently.
    expected_decision_at: str | None = None


class ReviewDecision(RpModel):
    action: Literal["ACCEPT", "CORRECT", "REJECT"]
    by: str  # reviewer sub (6 characters)
    at: str
    value: str | None = None  # the override, beside the extracted value
    note: str | None = None

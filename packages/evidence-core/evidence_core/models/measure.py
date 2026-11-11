"""Evaluate and Cost contracts (SC-12, SC-13, W-16, W-17). Mirrored in contracts/api.ts.

Both screens sit behind the publication flag ``RP_PUBLIC_METRICS``. With the flag ``off`` (the
default) the API itself withholds the numbers the board ruled out, so they are never in a payload:
- ``GET /eval/runs/{id}`` returns the per-question outcomes only (pass/fail dots), never a rate or a Recall@1;
- ``GET /cost`` returns usage (tokens, pages, seconds, calls) and no dollar amount.
``published`` says which view a response is, and ``withheld`` names what was left out.

Every amount is ``estimate · measured usage × list price`` (``config/prices.yaml``, with its source and date).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ..base import RpModel
from ..bbox import BBox

COST_LABEL = "estimate · measured usage × list price"
EVAL_CAPTION = "Measured on our constructed corpus. Your documents will differ."


# --- Evaluate -----------------------------------------------------------------------------------------------

class EvalGold(RpModel):
    """Where the golden answer is printed (``GoldenEntry`` doc, page and gold box)."""

    doc_id: str
    page: int | None = None
    bbox: BBox | None = None


class EvalOutcome(RpModel):
    """One golden question asked once on one lane, scored by eval/run.py (``outcome_from_events``)."""

    qid: str
    type: str  # table | prose | figure | footnote
    pipeline_version: str
    repeat: int = 0
    run_id: str | None = None
    recall_at1: bool
    region_hit_at1: bool | None = None
    answer_correct: bool | None = None
    citation_valid: bool | None = None
    top1_unit_id: str | None = None
    top1_chars: int | None = None
    error: str | None = None
    # The golden answer (display form) and where it is printed. From the outcome when the result recorded it
    # (eval/run.py since it writes ``expected``), else joined by qid from the deployed golden set
    # (``config/golden.jsonl``); ``EvalRunView.expected_source`` says which.
    expected: str | None = None
    gold: EvalGold | None = None


class EvalRate(RpModel):
    hits: int
    n: int
    rate: float | None = None


class EvalTypeMetrics(RpModel):
    n: int
    recall_at1: EvalRate
    region_hit_at1: EvalRate
    answer_correct: EvalRate
    citation_valid: EvalRate
    errors: int = 0


class EvalLane(RpModel):
    pipeline_version: str
    config_sha: str | None = None
    prompt_sha: str | None = None
    model_id: str | None = None
    mean_unit_chars: float | None = None
    by_type: dict[str, EvalTypeMetrics]


class EvalRunSummary(RpModel):
    eval_run_id: str
    created_at: str
    mode: str  # ask | search | local | logged
    mode_note: str
    package_ids: list[str]
    pipeline_versions: list[str]
    questions: int  # distinct golden questions with an outcome
    outcomes: int
    golden_sha: str
    corpus_hash: str | None = None


class EvalRunList(RpModel):
    items: list[EvalRunSummary]
    published: bool


class EvalRunView(RpModel):
    eval_run_id: str
    created_at: str
    mode: str
    mode_note: str
    caption: str = EVAL_CAPTION
    golden_path: str
    golden_sha: str
    package_ids: list[str]
    pipeline_versions: list[str]
    corpus_hash: str | None = None
    repeats: int = 1
    golden_types: dict[str, int]  # questions per type in the golden set (generator)
    outcomes: list[EvalOutcome]
    lanes: dict[str, EvalLane] | None = None  # only when published
    published: bool
    withheld: list[str] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)
    # where the outcomes' ``expected`` came from: "eval result" (recorded by the harness), "config/golden.jsonl"
    # (joined by qid: a result written before the harness recorded it), or None when neither had it
    expected_source: str | None = None


# --- Cost ---------------------------------------------------------------------------------------------------

UsageUnit = Literal["page", "unit_100_chars", "token_in", "token_out", "vcpu_s", "gb_s", "request", "lambda_gb_s",
                    "ms"]


class CostUsage(RpModel):
    unit: UsageUnit
    quantity: float
    measured: bool  # True: logged by the stage or the model call; False: derived from a configured size (labelled)
    basis: str | None = None  # how a derived quantity was computed


class CostLine(RpModel):
    package_id: str
    doc_id: str | None = None
    doc_type: str | None = None
    pipeline_version: str
    stage_id: str  # a stage id, or "ask"
    service: str  # prices.yaml service key or model id
    usage: CostUsage
    unit_price_usd: float | None = None  # None when unpublished
    price_ref: str | None = None
    estimate_usd: float | None = None  # None when unpublished
    run_id: str | None = None
    at: str | None = None


class StageDuration(RpModel):
    """A stage's measured wall time from its StageRecord (``durationMs``): measured, never an estimate."""

    doc_id: str
    pipeline_version: str
    stage_id: str
    engine: str | None = None
    duration_ms: int | None = None
    started_at: str | None = None


class CostGroup(RpModel):
    key: str
    estimate_usd: float | None = None  # the priced lines only; ``unpriced_lines`` says how many were left out
    by_service: dict[str, float] = Field(default_factory=dict)
    lines: int
    unpriced_lines: int = 0  # lines with no list price (a model or service missing from prices.yaml)


class CostPerDocument(RpModel):
    doc_id: str
    doc_type: str | None = None
    lane: str
    pages: int | None = None
    estimate_usd: float | None = None


class TieringRow(RpModel):
    call_type: str  # "answer", "answer (frontier)", "classify tie-break", "embedding"
    model_key: str
    model_id: str
    calls: int
    tokens_in: int
    tokens_out: int
    estimate_usd: float | None = None
    source: str  # where the calls were counted


class CostPrices(RpModel):
    source: str  # config/prices.yaml
    as_of: str
    refs: dict[str, str] = Field(default_factory=dict)  # price key -> source URL


class CostView(RpModel):
    label: str = COST_LABEL
    package_id: str
    lane: str | None = None
    group_by: str
    published: bool
    withheld: list[str] = Field(default_factory=list)
    prices: CostPrices
    groups: list[CostGroup]
    per_document: list[CostPerDocument]
    tiering: list[TieringRow]
    stages: list[StageDuration]
    lines: list[CostLine]
    notes: list[str] = Field(default_factory=list)
    truncated: bool = False  # the package has more runs than the route reads (the newest ``MAX_RUNS``)

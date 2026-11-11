"""Golden-set metrics. Pure functions, no AWS.

Rules, applied identically to both pipeline versions:

- **Recall@1** (per question type): the top-1 retrieved item's page set contains the gold page
  **and** its text matches the entry's ``answerPattern`` (after text normalisation).
- **RegionHit@1**: the gold bbox is covered >= 50% by one of the top-1 item's bboxes on the gold
  page (``bbox.coverage(gold, item)``). Only items that carry a bbox are counted, so the metric
  is effectively v2.3.1-only; v1.0 chunks have no region and report ``n = 0``.
- **IoU@0.5**: the design doc's original region rule (max IoU >= 0.5), kept as a secondary column
  because it under-counts units larger than the gold field. Same "items with a bbox" basis.
- **Answer correctness** (ask mode): numeric answers compare ``normalise_amount`` of the answer's
  ``total`` (falling back to the first amount in the answer text); non-numeric answers match
  ``answerPattern`` against the answer text.
- **Citation validity** (ask mode): at least one claim, every claim region-resolvable, no uncited
  numbers left after the retry. ``coversGold`` additionally records whether a cited bbox covers the
  gold region.

Guardrail: no single aggregate accuracy figure is computed. Everything is reported per
question type and per pipeline version, with ``n``.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from statistics import fmean
from typing import Any

from evidence_core.base import RpModel
from evidence_core.bbox import BBox, coverage, iou
from evidence_core.models.answer import Claim
from evidence_core.models.golden import GoldenEntry, QuestionType, normalise_amount
from evidence_core.models.tools import RetrievedUnit
from evidence_core.models.units import Unit
from pydantic import Field

QUESTION_TYPES: tuple[QuestionType, ...] = ("table", "prose", "figure", "footnote")
REGION_COVERAGE_MIN = 0.5
IOU_MIN = 0.5
CAPTION = "These numbers describe our synthetic documents, not your corpus."

_WS = re.compile(r"\s+")
_DASHES = str.maketrans({"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"})
_AMOUNT = re.compile(r"-?\$?\d[\d,]*(?:\.\d+)?")


# --- normalisation ------------------------------------------------------------------------------

def normalise_text(text: str) -> str:
    """NFKC, unified dashes, single spaces. Patterns are matched against this form."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text).translate(_DASHES)).strip()


def pattern_matches(pattern: str, text: str) -> bool:
    try:
        return re.search(pattern, normalise_text(text), flags=re.IGNORECASE) is not None
    except re.error as exc:  # a broken golden line must fail loudly, not score 0
        raise ValueError(f"invalid answerPattern {pattern!r}: {exc}") from exc


def first_amount(text: str) -> str | None:
    for m in _AMOUNT.finditer(text):
        token = m.group(0)
        if re.search(r"\d", token):
            return normalise_amount(token)
    return None


# --- retrieved items ----------------------------------------------------------------------------

@dataclass(frozen=True)
class RetrievedItem:
    """The fields of a top-1 retrieval result the metrics need (source-agnostic)."""

    unit_id: str
    pages: tuple[int, ...]
    text: str
    bboxes: tuple[BBox, ...] = ()
    field_bboxes: tuple[BBox, ...] = ()
    chars: int | None = None

    @classmethod
    def from_retrieved(cls, ru: RetrievedUnit) -> RetrievedItem:
        field_text = " ".join(f"{f.label} {f.value}" for f in ru.fields)
        return cls(
            unit_id=ru.unit_id,
            pages=tuple(ru.pages),
            text=f"{ru.breadcrumb} {ru.text} {field_text}".strip(),
            bboxes=tuple(ru.bboxes),
            field_bboxes=tuple(f.bbox for f in ru.fields if f.bbox is not None),
            chars=len(ru.text),
        )

    @classmethod
    def from_unit(cls, unit: Unit) -> RetrievedItem:
        field_text = " ".join(f"{f.label} {f.value}" for f in unit.fields)
        return cls(
            unit_id=unit.unit_id,
            pages=tuple(unit.pages),
            text=f"{unit.breadcrumb_text()} {unit.text} {field_text}".strip(),
            bboxes=tuple(unit.bboxes),
            field_bboxes=tuple(f.bbox for f in unit.fields if f.bbox is not None),
            chars=unit.chars or len(unit.text),
        )

    @property
    def has_region(self) -> bool:
        return bool(self.bboxes or self.field_bboxes)

    def regions(self) -> tuple[BBox, ...]:
        return self.bboxes + self.field_bboxes


# --- per-question checks --------------------------------------------------------------------------

def recall_hit(item: RetrievedItem | None, gold: GoldenEntry) -> bool:
    if item is None:
        return False
    if gold.page is not None and gold.page not in item.pages:
        return False
    return pattern_matches(gold.answer_pattern, item.text)


def region_hit(item: RetrievedItem | None, gold: GoldenEntry) -> bool | None:
    """None when not measurable (no gold bbox, no item, or the item carries no region)."""
    if item is None or gold.gold_bbox is None or not item.has_region:
        return None
    return any(coverage(gold.gold_bbox, b) >= REGION_COVERAGE_MIN for b in item.regions())


def iou_hit(item: RetrievedItem | None, gold: GoldenEntry, threshold: float = IOU_MIN) -> bool | None:
    if item is None or gold.gold_bbox is None or not item.has_region:
        return None
    return max(iou(gold.gold_bbox, b) for b in item.regions()) >= threshold


def answer_correct(gold: GoldenEntry, *, answer: str | None, total: str | None) -> bool:
    expected = normalise_amount(gold.answer)
    if expected is not None and re.fullmatch(r"[\s$\d,.\-]+", gold.answer.strip()):
        actual = normalise_amount(total) if total else None
        if actual is None and answer:
            actual = first_amount(answer)
        return actual == expected
    return bool(answer) and pattern_matches(gold.answer_pattern, answer or "")


class CitationCheck(RpModel):
    claims: int
    resolvable: int
    uncited_numbers: int
    valid: bool
    covers_gold: bool | None = None  # None when the entry has no gold bbox


def citation_check(claims: Sequence[Claim], uncited_numbers: Sequence[str], gold: GoldenEntry) -> CitationCheck:
    resolvable = [c for c in claims if c.resolvable and c.bbox is not None]
    covers: bool | None = None
    if gold.gold_bbox is not None:
        covers = any(coverage(gold.gold_bbox, c.bbox) >= REGION_COVERAGE_MIN for c in resolvable if c.bbox)
    return CitationCheck(
        claims=len(claims),
        resolvable=len(resolvable),
        uncited_numbers=len(uncited_numbers),
        valid=bool(claims) and len(resolvable) == len(claims) and not uncited_numbers,
        covers_gold=covers,
    )


# --- outcomes and aggregation ---------------------------------------------------------------------

class QuestionOutcome(RpModel):
    """One golden question asked once on one pipeline version."""

    qid: str
    type: QuestionType
    pipeline_version: str
    repeat: int = 0
    expected: str | None = None  # the golden answer (GoldenEntry.answer, display form), so a score can show it
    top1_unit_id: str | None = None
    top1_pages: list[int] = Field(default_factory=list)
    top1_chars: int | None = None
    recall_at1: bool
    region_hit_at1: bool | None = None
    iou_at05: bool | None = None
    answer: str | None = None
    total: str | None = None
    answer_correct: bool | None = None  # None when the answer path was not exercised
    citation: CitationCheck | None = None
    check_match: bool | None = None  # agent's own golden check event, when emitted
    run_id: str | None = None
    latency_ms: int | None = None
    error: str | None = None


def score_retrieval(gold: GoldenEntry, pipeline_version: str, top1: RetrievedItem | None, **extra: Any
                    ) -> QuestionOutcome:
    return QuestionOutcome(
        qid=gold.qid,
        type=gold.type,
        pipeline_version=pipeline_version,
        expected=gold.answer,
        top1_unit_id=top1.unit_id if top1 else None,
        top1_pages=list(top1.pages) if top1 else [],
        top1_chars=top1.chars if top1 else None,
        recall_at1=recall_hit(top1, gold),
        region_hit_at1=region_hit(top1, gold),
        iou_at05=iou_hit(top1, gold),
        **extra,
    )


class Rate(RpModel):
    hits: int = 0
    n: int = 0
    rate: float | None = None  # None when n == 0 (not measurable), never 0 by default

    @classmethod
    def of(cls, values: Iterable[bool | None]) -> Rate:
        measured = [v for v in values if v is not None]
        hits = sum(1 for v in measured if v)
        return cls(hits=hits, n=len(measured), rate=round(hits / len(measured), 4) if measured else None)


class TypeMetrics(RpModel):
    n: int
    recall_at1: Rate
    region_hit_at1: Rate
    iou_at05: Rate
    answer_correct: Rate
    citation_valid: Rate
    errors: int = 0


class LaneMetrics(RpModel):
    pipeline_version: str
    config_sha: str | None = None
    prompt_sha: str | None = None
    model_id: str | None = None
    mean_unit_chars: float | None = None
    by_type: dict[str, TypeMetrics]


def type_metrics(outcomes: Sequence[QuestionOutcome]) -> TypeMetrics:
    return TypeMetrics(
        n=len(outcomes),
        recall_at1=Rate.of(o.recall_at1 if o.error is None else False for o in outcomes),
        region_hit_at1=Rate.of(o.region_hit_at1 for o in outcomes),
        iou_at05=Rate.of(o.iou_at05 for o in outcomes),
        answer_correct=Rate.of(o.answer_correct for o in outcomes),
        citation_valid=Rate.of(o.citation.valid if o.citation else None for o in outcomes),
        errors=sum(1 for o in outcomes if o.error),
    )


def lane_metrics(pipeline_version: str, outcomes: Sequence[QuestionOutcome], *, unit_chars: Sequence[int] = (),
                 config_sha: str | None = None, prompt_sha: str | None = None, model_id: str | None = None
                 ) -> LaneMetrics:
    mine = [o for o in outcomes if o.pipeline_version == pipeline_version]
    by_type = {t: type_metrics([o for o in mine if o.type == t]) for t in QUESTION_TYPES}
    chars = list(unit_chars) or [o.top1_chars for o in mine if o.top1_chars]
    return LaneMetrics(
        pipeline_version=pipeline_version,
        config_sha=config_sha,
        prompt_sha=prompt_sha,
        model_id=model_id,
        mean_unit_chars=round(fmean(chars), 1) if chars else None,
        by_type=by_type,
    )


class HarnessRow(RpModel):
    """One row of the harness table."""

    type: str
    n: int
    recall_at1: dict[str, float | None]
    region_hit_at1: dict[str, float | None]
    mean_unit_chars: dict[str, float | None]


def harness_table(lanes: dict[str, LaneMetrics]) -> list[HarnessRow]:
    rows: list[HarnessRow] = []
    for t in QUESTION_TYPES:
        ns = {lm.by_type[t].n for lm in lanes.values()}
        rows.append(
            HarnessRow(
                type=t,
                n=max(ns) if ns else 0,
                recall_at1={pv: lm.by_type[t].recall_at1.rate for pv, lm in lanes.items()},
                region_hit_at1={pv: lm.by_type[t].region_hit_at1.rate for pv, lm in lanes.items()},
                mean_unit_chars={pv: lm.mean_unit_chars for pv, lm in lanes.items()},
            )
        )
    return rows


class EvalResult(RpModel):
    """eval/results/{evalRunId}.json (and s3 eval/results/{evalRunId}.json)."""

    eval_run_id: str
    created_at: str
    mode: str  # ask | search | local
    mode_note: str
    caption: str = CAPTION
    golden_path: str
    golden_sha: str
    package_ids: list[str]
    pipeline_versions: list[str]
    corpus_hash: str | None = None
    repeats: int = 1
    lanes: dict[str, LaneMetrics]
    table: list[HarnessRow]
    outcomes: list[QuestionOutcome]
    meta: dict[str, Any] = Field(default_factory=dict)


# --- compare ----------------------------------------------------------------------------------------

COMPARED_METRICS: tuple[str, ...] = ("recall_at1", "region_hit_at1", "answer_correct", "citation_valid")


class MetricDelta(RpModel):
    pipeline_version: str
    type: str
    metric: str
    baseline: float | None
    candidate: float | None
    baseline_n: int
    candidate_n: int
    delta: float | None
    regression: bool


class Comparison(RpModel):
    baseline_run_id: str
    candidate_run_id: str
    same_golden: bool
    same_corpus: bool | None
    deltas: list[MetricDelta]
    regressions: list[MetricDelta] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def blocks_promotion(self) -> bool:
        return bool(self.regressions)


@dataclass
class _Pair:
    base: Rate
    cand: Rate
    notes: list[str] = field(default_factory=list)


def compare(baseline: EvalResult, candidate: EvalResult, *, tolerance: float = 0.0) -> Comparison:
    """Per version x type x metric deltas. A regression is a measured rate that drops by more than
    ``tolerance``; any regression blocks promotion of the candidate."""
    warnings: list[str] = []
    same_golden = baseline.golden_sha == candidate.golden_sha
    if not same_golden:
        warnings.append("golden set differs between runs; deltas are not like-for-like")
    same_corpus = (
        None if baseline.corpus_hash is None or candidate.corpus_hash is None
        else baseline.corpus_hash == candidate.corpus_hash
    )
    if same_corpus is False:
        warnings.append("corpus hash differs between runs")
    if baseline.mode != candidate.mode:
        warnings.append(f"modes differ: {baseline.mode} vs {candidate.mode}")
    deltas: list[MetricDelta] = []
    for pv in sorted(set(baseline.lanes) | set(candidate.lanes)):
        b_lane, c_lane = baseline.lanes.get(pv), candidate.lanes.get(pv)
        if b_lane is None or c_lane is None:
            warnings.append(f"pipeline version {pv} present in only one run")
            continue
        for t in QUESTION_TYPES:
            for metric in COMPARED_METRICS:
                b: Rate = getattr(b_lane.by_type[t], metric)
                c: Rate = getattr(c_lane.by_type[t], metric)
                delta = None if b.rate is None or c.rate is None else round(c.rate - b.rate, 4)
                deltas.append(
                    MetricDelta(
                        pipeline_version=pv, type=t, metric=metric, baseline=b.rate, candidate=c.rate,
                        baseline_n=b.n, candidate_n=c.n, delta=delta,
                        regression=delta is not None and delta < -tolerance,
                    )
                )
    return Comparison(
        baseline_run_id=baseline.eval_run_id,
        candidate_run_id=candidate.eval_run_id,
        same_golden=same_golden,
        same_corpus=same_corpus,
        deltas=deltas,
        regressions=[d for d in deltas if d.regression],
        warnings=warnings,
    )


# --- local BM25 (dev proxy for --mode local) -------------------------------------------------------

_TOKEN = re.compile(r"[a-z0-9]+(?:[.,][0-9]+)*")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(normalise_text(text).lower())


def bm25_rank(query: str, docs: Sequence[tuple[str, str]], *, k1: float = 1.2, b: float = 0.75
              ) -> list[tuple[str, float]]:
    """Okapi BM25 over ``(id, text)`` pairs; ties broken by id (same rule as retrieval.fuse)."""
    import math

    toks = [(doc_id, tokenize(text)) for doc_id, text in docs]
    if not toks:
        return []
    avgdl = fmean(len(t) for _, t in toks) or 1.0
    df: dict[str, int] = {}
    for _, t in toks:
        for term in set(t):
            df[term] = df.get(term, 0) + 1
    n = len(toks)
    q_terms = tokenize(query)
    scored: list[tuple[str, float]] = []
    for doc_id, t in toks:
        tf: dict[str, int] = {}
        for term in t:
            tf[term] = tf.get(term, 0) + 1
        score = 0.0
        for term in q_terms:
            if term not in tf:
                continue
            idf = math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5))
            f = tf[term]
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(t) / avgdl))
        scored.append((doc_id, round(score, 6)))
    scored.sort(key=lambda s: (-s[1], s[0]))
    return scored

from __future__ import annotations

import pytest
from evidence_core.bbox import BBox
from evidence_core.models.answer import Claim
from evidence_core.models.golden import GoldenEntry
from evidence_core.testing import make_ctx, make_w2_unit

from eval.metrics import (
    QUESTION_TYPES,
    EvalResult,
    QuestionOutcome,
    Rate,
    RetrievedItem,
    answer_correct,
    bm25_rank,
    citation_check,
    compare,
    harness_table,
    iou_hit,
    lane_metrics,
    normalise_text,
    pattern_matches,
    recall_hit,
    region_hit,
    score_retrieval,
)

BOX1 = BBox(page=1, x0=50, y0=216, x1=306, y1=284)


def gold(**kw: object) -> GoldenEntry:
    base: dict[str, object] = {
        "qid": "q-lend-02", "type": "table", "question": "What are Maya Fictus's 2025 W-2 Box 1 wages?",
        "answer": "98,400.00", "answer_pattern": r"98,?400\.00", "package_id": "pkg-lending-01",
        "page": 1, "gold_bbox": BOX1,
    }
    base.update(kw)
    return GoldenEntry(**base)  # type: ignore[arg-type]


def chunk_item(text: str = "Wages 6,600.00 105,000.00 105,000.00 98,400.00", pages: tuple[int, ...] = (1,)
               ) -> RetrievedItem:
    return RetrievedItem(unit_id="w2-2025-maya-fictus-abc123:1.0:c-01", pages=pages, text=text, chars=len(text))


# --- text -------------------------------------------------------------------------------------------

def test_normalise_text_unifies_dashes_and_space() -> None:
    assert normalise_text("Boxes 1–6 \n  total") == "Boxes 1-6 total"


def test_pattern_matches_is_case_insensitive_and_rejects_bad_regex() -> None:
    assert pattern_matches(r"box 1", "BOX 1 Wages")
    with pytest.raises(ValueError, match="invalid answerPattern"):
        pattern_matches("(", "x")


# --- recall / region --------------------------------------------------------------------------------

def test_recall_hit_requires_gold_page_and_pattern() -> None:
    g = gold()
    assert recall_hit(chunk_item(), g)
    assert not recall_hit(chunk_item(pages=(2,)), g)
    assert not recall_hit(chunk_item(text="Box 3 105,000.00"), g)
    assert not recall_hit(None, g)


def test_recall_hit_without_gold_page_uses_pattern_only() -> None:
    assert recall_hit(chunk_item(pages=(3,)), gold(page=None))


def test_region_metrics_are_not_measurable_for_chunks() -> None:
    g = gold()
    assert region_hit(chunk_item(), g) is None
    assert iou_hit(chunk_item(), g) is None


def test_unit_covering_gold_hits_region_but_not_iou() -> None:
    """Why the metric is coverage and not IoU >= 0.5: a whole-panel unit covers the Box 1 cell fully but its
    IoU with the cell is small."""
    item = RetrievedItem.from_unit(make_w2_unit())
    g = gold()
    assert recall_hit(item, g)
    assert region_hit(item, g) is True
    unit_only = RetrievedItem(unit_id=item.unit_id, pages=item.pages, text=item.text, bboxes=item.bboxes)
    assert iou_hit(unit_only, g) is False
    assert iou_hit(item, g) is True  # the field bbox is the gold cell


def test_region_hit_false_on_other_page_region() -> None:
    item = RetrievedItem(unit_id="u", pages=(1,), text="98,400.00",
                         bboxes=(BBox(page=1, x0=300, y0=500, x1=400, y1=600),))
    assert region_hit(item, gold()) is False


# --- answers and citations --------------------------------------------------------------------------

def test_answer_correct_numeric_uses_total_then_text() -> None:
    g = gold(answer="169,650.00", answer_pattern=r"169,?650\.00", type="table")
    assert answer_correct(g, answer="The total is 169,650.00", total="$169,650.00")
    assert not answer_correct(g, answer="The total is 180,000.00", total="180,000.00")
    assert answer_correct(g, answer="Total qualifying income: 169,650.00 USD", total=None)
    assert not answer_correct(g, answer=None, total=None)


def test_answer_correct_text_answer_uses_pattern() -> None:
    g = gold(answer="preliminary", answer_pattern=r"\bpreliminary\b", type="footnote")
    assert answer_correct(g, answer="Q4 figures are preliminary.", total=None)
    assert not answer_correct(g, answer="Q4 figures are final.", total=None)


def _claim(resolvable: bool, bbox: BBox | None = None) -> Claim:
    return Claim(text="98,400.00", value="98400.00", unit_id="u", field_id="f-box1", resolvable=resolvable,
                 bbox=bbox, page=bbox.page if bbox else None)


def test_citation_check_valid_only_when_every_claim_resolves() -> None:
    g = gold()
    ok = citation_check([_claim(True, BOX1)], [], g)
    assert ok.valid and ok.covers_gold
    chunk = citation_check([_claim(False)], [], g)
    assert not chunk.valid and chunk.covers_gold is False
    assert not citation_check([], [], g).valid
    assert not citation_check([_claim(True, BOX1)], ["71,250.00"], g).valid


# --- aggregation ---------------------------------------------------------------------------------------

def _outcome(pv: str, t: str, recall: bool, region: bool | None = None, chars: int = 100,
             error: str | None = None) -> QuestionOutcome:
    return QuestionOutcome(qid=f"q-{t}", type=t, pipeline_version=pv, recall_at1=recall,  # type: ignore[arg-type]
                           region_hit_at1=region, top1_chars=chars, error=error)


def test_rate_is_none_when_nothing_measured() -> None:
    assert Rate.of([None, None]).rate is None
    r = Rate.of([True, False, None, True])
    assert (r.hits, r.n, r.rate) == (2, 3, 0.6667)


def test_lane_metrics_per_type_and_errors_count_as_misses() -> None:
    outs = [
        _outcome("2.3.1", "table", True, True),
        _outcome("2.3.1", "table", True, False),
        _outcome("2.3.1", "prose", True, None, error="timeout"),
        _outcome("1.0", "table", False, None, chars=1200),
    ]
    lane = lane_metrics("2.3.1", outs)
    assert lane.by_type["table"].recall_at1.rate == 1.0
    assert lane.by_type["table"].region_hit_at1.rate == 0.5
    assert lane.by_type["prose"].recall_at1.rate == 0.0 and lane.by_type["prose"].errors == 1
    assert lane.by_type["figure"].n == 0 and lane.by_type["figure"].recall_at1.rate is None
    assert lane.mean_unit_chars == 100.0
    assert set(lane.by_type) == set(QUESTION_TYPES)


def test_lane_metrics_prefers_supplied_unit_chars() -> None:
    assert lane_metrics("1.0", [_outcome("1.0", "table", False)], unit_chars=[1000, 1200]).mean_unit_chars == 1100.0


def test_harness_table_has_one_row_per_type_and_no_aggregate() -> None:
    outs = [_outcome("1.0", "table", False), _outcome("2.3.1", "table", True, True)]
    lanes = {pv: lane_metrics(pv, outs) for pv in ("1.0", "2.3.1")}
    rows = harness_table(lanes)
    assert [r.type for r in rows] == list(QUESTION_TYPES)
    assert rows[0].recall_at1 == {"1.0": 0.0, "2.3.1": 1.0}
    assert rows[0].region_hit_at1 == {"1.0": None, "2.3.1": 1.0}


def _result(run_id: str, outs: list[QuestionOutcome], golden_sha: str = "g", corpus: str | None = "c") -> EvalResult:
    lanes = {pv: lane_metrics(pv, outs) for pv in ("1.0", "2.3.1")}
    return EvalResult(eval_run_id=run_id, created_at="2026-11-03T00:00:00.000Z", mode="search", mode_note="",
                      golden_path="eval/golden.jsonl", golden_sha=golden_sha, package_ids=["pkg-lending-01"],
                      pipeline_versions=["1.0", "2.3.1"], corpus_hash=corpus, lanes=lanes,
                      table=harness_table(lanes), outcomes=outs)


def test_compare_flags_regressions_and_ignores_unmeasured() -> None:
    base = _result("e-base", [_outcome("2.3.1", "table", True, True), _outcome("2.3.1", "prose", False)])
    cand = _result("e-cand", [_outcome("2.3.1", "table", False, True), _outcome("2.3.1", "prose", True)])
    cmp = compare(base, cand)
    regs = {(d.pipeline_version, d.type, d.metric) for d in cmp.regressions}
    assert regs == {("2.3.1", "table", "recall_at1")}
    assert cmp.blocks_promotion
    assert not compare(base, base).blocks_promotion
    assert not any(d.regression for d in cmp.deltas if d.baseline is None)


def test_compare_warns_on_different_golden_or_corpus() -> None:
    base = _result("a", [_outcome("1.0", "table", True)])
    cand = _result("b", [_outcome("1.0", "table", True)], golden_sha="other", corpus="other")
    cmp = compare(base, cand)
    assert not cmp.same_golden and cmp.same_corpus is False and len(cmp.warnings) == 2


def test_compare_tolerance() -> None:
    base = _result("a", [_outcome("1.0", "table", True), _outcome("1.0", "table", True)])
    cand = _result("b", [_outcome("1.0", "table", True), _outcome("1.0", "table", False)])
    assert compare(base, cand).blocks_promotion
    assert not compare(base, cand, tolerance=0.5).blocks_promotion


# --- scoring + local BM25 -------------------------------------------------------------------------------

def test_score_retrieval_records_top1() -> None:
    unit = make_w2_unit(make_ctx("2.3.1"))
    o = score_retrieval(gold(), "2.3.1", RetrievedItem.from_unit(unit))
    assert o.recall_at1 and o.region_hit_at1 and o.top1_unit_id == unit.unit_id and o.top1_pages == [1]


def test_bm25_rank_orders_by_relevance_with_id_tiebreak() -> None:
    ranked = bm25_rank("box 1 wages", [("b", "box 1 wages tips"), ("a", "bank deposits"), ("c", "box 1 wages tips")])
    assert [r[0] for r in ranked] == ["b", "c", "a"]
    assert ranked[2][1] == 0.0
    assert bm25_rank("x", []) == []

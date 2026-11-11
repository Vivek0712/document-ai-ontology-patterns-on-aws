"""GET /eval/runs, GET /eval/runs/{evalRunId} (SC-12, W-16): golden-set results by question type and lane.

Reads ``eval/results/{evalRunId}.json`` (written by ``eval/run.py``; ``--mode logged`` re-scores the logged runs with
the same ``outcome_from_events`` the harness uses). The publication flag ``RP_PUBLIC_METRICS`` (default
``off``) is applied here, so an unpublished build never carries a rate: with it off the response holds the
per-question outcomes (pass/fail dots) and the golden set's counts by type, and ``lanes`` (n, hits, Recall@1,
RegionHit@1, mean unit size) is withheld. There is no single aggregate accuracy in either view.

Results are corpus-level, not package data: a caller sees a result only when it may read every package it covers.

Each outcome carries the golden answer (``expected``) and where it is printed (``gold``): from the outcome when the
harness recorded it, else joined by qid from the deployed golden set ``config/golden.jsonl`` (the file the UI's
golden-set screens read), so results stored before the harness recorded it show it too. ``expectedSource`` says
which; an outcome whose qid is in neither keeps ``expected`` empty.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from typing import Any

from evidence_core import s3keys
from evidence_core.ids import is_allowed
from evidence_core.models.golden import GoldenEntry
from evidence_core.models.measure import (
    EVAL_CAPTION,
    EvalGold,
    EvalLane,
    EvalOutcome,
    EvalRunList,
    EvalRunSummary,
    EvalRunView,
)

from .. import store
from ..auth import Caller
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request, forbidden, not_found
from . import EVAL_RUN, EVAL_RUNS, router

EVAL_ID_RE = re.compile(r"^e-[a-z0-9-]{4,64}$")
GOLDEN_KEY = "config/golden.jsonl"  # eval/golden.jsonl as deployed (scripts/seed.sh)
SOURCE_RESULT = "eval result"


def metrics_published() -> bool:
    return os.environ.get("RP_PUBLIC_METRICS", "off").strip().lower() == "on"


def _gold(g: GoldenEntry | None) -> EvalGold | None:
    return EvalGold(doc_id=g.doc_id, page=g.page, bbox=g.gold_bbox) if g is not None and g.doc_id else None


def _outcome(o: dict[str, Any], golden: dict[str, GoldenEntry] | None = None) -> EvalOutcome:
    citation = o.get("citation") or {}
    g = (golden or {}).get(o["qid"])
    return EvalOutcome(qid=o["qid"], type=o["type"], pipeline_version=o["pipelineVersion"], repeat=o.get("repeat", 0),
                       run_id=o.get("runId"), recall_at1=bool(o.get("recallAt1")), region_hit_at1=o.get("regionHitAt1"),
                       answer_correct=o.get("answerCorrect"),
                       citation_valid=citation.get("valid") if citation else None,
                       top1_unit_id=o.get("top1UnitId"), top1_chars=o.get("top1Chars"), error=o.get("error"),
                       expected=o.get("expected") or (g.answer if g else None), gold=_gold(g))


def golden_index(deps: Deps) -> dict[str, GoldenEntry]:
    """The deployed golden set by qid (``config/golden.jsonl``); empty when it is not deployed."""
    rows = store.read_jsonl(deps, GOLDEN_KEY) or []
    return {g.qid: g for g in (GoldenEntry.load(r) for r in rows)}


def expected_source(result: dict[str, Any], golden: dict[str, GoldenEntry]) -> str | None:
    outcomes = result.get("outcomes", [])
    recorded = any(o.get("expected") for o in outcomes)
    joined = any(not o.get("expected") and o["qid"] in golden for o in outcomes)
    parts = ([SOURCE_RESULT] if recorded else []) + ([f"{GOLDEN_KEY} (joined by qid)"] if joined else [])
    return " + ".join(parts) or None


def _readable(deps: Deps, caller: Caller, package_ids: list[str]) -> bool:
    for pid in package_ids:
        pkg = store.get_package(deps, pid)
        if pkg is None or not is_allowed(caller.principals, pkg.allowed_principals):
            return False
    return True


def golden_types(result: dict[str, Any]) -> dict[str, int]:
    """Questions per type: the golden set's counts recorded with the result, else the distinct qids it scored."""
    recorded = (result.get("meta") or {}).get("goldenTypes")
    if recorded:
        return dict(recorded)
    seen = {(o["qid"], o["type"]) for o in result.get("outcomes", [])}
    return dict(Counter(t for _q, t in seen))


def view(result: dict[str, Any], published: bool, golden: dict[str, GoldenEntry] | None = None) -> EvalRunView:
    golden = golden or {}
    lanes = {pv: EvalLane.load(lm) for pv, lm in (result.get("lanes") or {}).items()} if published else None
    return EvalRunView(
        eval_run_id=result["evalRunId"], created_at=result["createdAt"], mode=result["mode"],
        mode_note=result["modeNote"], caption=EVAL_CAPTION, golden_path=result["goldenPath"],
        golden_sha=result["goldenSha"], package_ids=result["packageIds"], pipeline_versions=result["pipelineVersions"],
        corpus_hash=result.get("corpusHash"), repeats=result.get("repeats", 1), golden_types=golden_types(result),
        outcomes=[_outcome(o, golden) for o in result.get("outcomes", [])], lanes=lanes, published=published,
        withheld=[] if published else ["per-lane rates by type: RP_PUBLIC_METRICS=off"],
        meta={k: v for k, v in (result.get("meta") or {}).items() if k != "goldenTypes"},
        expected_source=expected_source(result, golden))


def _keys(deps: Deps) -> list[str]:
    keys: list[str] = []
    for page in deps.s3.get_paginator("list_objects_v2").paginate(Bucket=deps.bucket, Prefix="eval/results/"):
        keys += [o["Key"] for o in page.get("Contents", []) if o["Key"].endswith(".json")]
    return sorted(keys)


@router.route("GET", EVAL_RUNS)
def list_eval_runs(req: Request) -> Reply:
    deps = get_deps()
    items = []
    for key in _keys(deps):
        result = store.read_json(deps, key)
        if not result or not _readable(deps, req.caller, result.get("packageIds") or []):
            continue
        outcomes = result.get("outcomes", [])
        items.append(EvalRunSummary(
            eval_run_id=result["evalRunId"], created_at=result["createdAt"], mode=result["mode"],
            mode_note=result["modeNote"], package_ids=result["packageIds"],
            pipeline_versions=result["pipelineVersions"], questions=len({o["qid"] for o in outcomes}),
            outcomes=len(outcomes), golden_sha=result["goldenSha"], corpus_hash=result.get("corpusHash")))
    items.sort(key=lambda s: s.created_at, reverse=True)
    return Reply(EvalRunList(items=items, published=metrics_published()))


@router.route("GET", EVAL_RUN)
def get_eval_run(req: Request) -> Reply:
    eval_run_id = req.param("evalRunId")
    if not EVAL_ID_RE.match(eval_run_id):
        raise bad_request(f"invalid evalRunId {eval_run_id!r}")
    deps = get_deps()
    result = store.read_json(deps, s3keys.eval_result_key(eval_run_id))
    if result is None:
        raise not_found(f"Eval run {eval_run_id} not found.")
    if not _readable(deps, req.caller, result.get("packageIds") or []):
        raise forbidden("This result covers a package you don't have access to.")
    return Reply(view(result, metrics_published(), golden_index(deps)))

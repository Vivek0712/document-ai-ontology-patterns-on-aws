#!/usr/bin/env python3
"""Golden-set harness. Writes eval/results/{evalRunId}.json.

Modes (``run --mode``):
  ask     POST /ask on the deployed API for every golden question and version (default). Measures
          Recall@1 / RegionHit@1 from the first search_units result in the tool trace, answer
          correctness and citation validity from the answer event.
  search  Invoke the production rp-search-units Lambda directly with a service principal
          ("unit of retrieval"). Retrieval metrics only, no model call.
  local   Dev proxy, no AWS: BM25 over the units written by `make units` (B's local pipeline).
          Not the production retriever (no dense half); the result file says so.

  logged  No new model call: re-score the logged runs (``runs/local``, ``make local-ask``) from their event
          streams with ``outcome_from_events``. Only the gate cohort (default generation profile, User A) counts.

Compare: ``run.py compare BASELINE.json CANDIDATE.json`` prints per version x type deltas and exits 1
when any measured rate regresses (regressions block promotion of a new pipelineVersion).

Examples:
  .venv/bin/python eval/run.py run --mode ask --versions 1.0,2.3.1
  .venv/bin/python eval/run.py run --mode ask --qid q-lend-01 --repeat 10      # gate runs
  .venv/bin/python eval/run.py run --mode ask --qid q-lend-01 --versions 1.0 --repeat 10 --generation-profile frontier
  .venv/bin/python eval/run.py run --mode ask --qid q-lend-01 --repeat 2 --user user-b --via runtime     # SC-7
  .venv/bin/python eval/run.py run --mode local --units-dir build/units
  .venv/bin/python eval/run.py run --mode logged --runs runs/local                 # SC-12 / W-16 over logged runs
  .venv/bin/python eval/run.py compare eval/results/e-aaa.json eval/results/e-bbb.json
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
for p in (REPO, REPO / "packages" / "evidence-core", REPO / "scripts" / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from evidence_core.config import LocalConfigSource, load_pipeline  # noqa: E402
from evidence_core.hashing import corpus_hash, sha256_bytes  # noqa: E402
from evidence_core.models.answer import (  # noqa: E402
    AnswerEvent,
    AskRequest,
    CheckEvent,
    ErrorEvent,
    RunEvent,
    ToolEndEvent,
)
from evidence_core.models.golden import GoldenEntry, load_golden  # noqa: E402
from evidence_core.models.status import iso  # noqa: E402
from evidence_core.models.tools import SearchUnitsResult  # noqa: E402
from evidence_core.models.units import Unit  # noqa: E402

from eval.metrics import (  # noqa: E402
    EvalResult,
    QuestionOutcome,
    RetrievedItem,
    answer_correct,
    bm25_rank,
    citation_check,
    compare,
    harness_table,
    lane_metrics,
    recall_hit,
    score_retrieval,
)

DEFAULT_GOLDEN = REPO / "eval" / "golden.jsonl"
RESULTS_DIR = REPO / "eval" / "results"
DEFAULT_PACKAGE = "pkg-lending-01"
EVAL_SUB = "svc-eval"  # the harness's service principal

MODE_NOTES = {
    "ask": "Deployed answer path: API Gateway -> AgentCore Runtime -> Gateway tools. Top-1 = first search_units hit.",
    "search": "Production rp-search-units Lambda invoked directly with a service principal (same fusion, filters, K).",
    "local": "Dev proxy: BM25 only over `make units` output; not the production hybrid retriever.",
    "logged": "Logged runs re-scored: each run's event stream through outcome_from_events (top-1 = first "
              "search_units hit). Local runs: Bedrock model, local stand-ins for the AWS stores.",
}


def new_eval_run_id() -> str:
    return f"e-{secrets.token_hex(6)}"


def log(msg: str) -> None:
    print(f"  {msg}", file=sys.stderr, flush=True)


# --- golden selection ------------------------------------------------------------------------------

def select(entries: Sequence[GoldenEntry], *, qids: Sequence[str] = (), types: Sequence[str] = (),
           package_id: str | None = None, headline_only: bool = False, limit: int | None = None
           ) -> list[GoldenEntry]:
    out = [
        e for e in entries
        if (not qids or e.qid in qids)
        and (not types or e.type in types)
        and (not package_id or e.package_id in (None, package_id))
        and (not headline_only or e.headline)
    ]
    return out[:limit] if limit else out


# --- ask mode --------------------------------------------------------------------------------------

def outcome_from_events(gold: GoldenEntry, pv: str, events: Sequence[Any], repeat: int = 0) -> QuestionOutcome:
    top1: RetrievedItem | None = None
    answer: AnswerEvent | None = None
    check: CheckEvent | None = None
    run: RunEvent | None = None
    error: str | None = None
    for ev in events:
        if isinstance(ev, ToolEndEvent) and ev.tool == "search_units" and top1 is None and ev.retrieved:
            top1 = RetrievedItem.from_retrieved(ev.retrieved[0])
        elif isinstance(ev, AnswerEvent):
            answer = ev
        elif isinstance(ev, CheckEvent):
            check = ev
        elif isinstance(ev, RunEvent):
            run = ev
        elif isinstance(ev, ErrorEvent):
            error = f"{ev.code}: {ev.message}"
    extra: dict[str, Any] = {"repeat": repeat, "error": error}
    if answer is not None:
        extra |= {
            "answer": answer.answer,
            "total": answer.total,
            "answer_correct": answer_correct(gold, answer=answer.answer, total=answer.total),
            "citation": citation_check(answer.claims, answer.uncited_numbers, gold),
        }
    elif error is None:
        extra["error"] = "stream ended without an answer event"
    if check is not None:
        extra["check_match"] = check.match
    if run is not None:
        extra |= {"run_id": run.run_id, "latency_ms": run.latency_ms}
    return score_retrieval(gold, pv, top1, **extra)


def run_ask(entries: Sequence[GoldenEntry], versions: Sequence[str], *, package_id: str, repeat: int,
            user: str, generation_profile: str = "default", via: str = "api"
            ) -> tuple[list[QuestionOutcome], dict[str, dict[str, Any]], str | None]:
    from rp_ops.api import ApiClient, ApiHttpError
    from rp_ops.auth import bearer, sign_in
    from rp_ops.aws import Aws

    aws = Aws()
    api_url = aws.ssm("api_url")
    assert api_url
    runtime_url = aws.ssm("agent_runtime_url") if via == "runtime" else None
    if via == "runtime" and not runtime_url:
        raise SystemExit("--via runtime needs SSM /rp/agent/runtime-url (written by deploy_agentcore.sh)")
    api = ApiClient(api_url, bearer(sign_in(aws, user)), runtime_url=runtime_url)
    outcomes: list[QuestionOutcome] = []
    lane_meta: dict[str, dict[str, Any]] = {}
    corpus: str | None = None
    for gold in entries:
        for pv in versions:
            for r in range(repeat):
                req = AskRequest(question=gold.question, package_id=gold.package_id or package_id, pipeline_version=pv,
                                 generation_profile=generation_profile)
                t0 = time.monotonic()
                try:
                    events = list(api.ask(req))
                except ApiHttpError as exc:
                    events = [ErrorEvent(code="http", message=str(exc))]
                o = outcome_from_events(gold, pv, events, repeat=r)
                for ev in events:
                    if isinstance(ev, RunEvent):
                        lane_meta.setdefault(pv, {"config_sha": ev.config_sha, "prompt_sha": ev.prompt_sha,
                                                  "model_id": ev.model_id})
                        corpus = corpus or ev.corpus_hash
                outcomes.append(o)
                log(f"{gold.qid} {pv} #{r}: recall@1 {o.recall_at1} answer {o.answer_correct} "
                    f"cites {o.citation.valid if o.citation else None} ({int((time.monotonic() - t0) * 1000)} ms)"
                    + (f" ERROR {o.error}" if o.error else ""))
    return outcomes, lane_meta, corpus


# --- search mode ------------------------------------------------------------------------------------

def run_search(entries: Sequence[GoldenEntry], versions: Sequence[str], *, package_id: str, groups: list[str]
               ) -> list[QuestionOutcome]:
    from evidence_core.ids import new_run_id
    from rp_ops.aws import Aws, lambda_name
    from rp_ops.warm import unwrap_tool_result

    aws = Aws()
    outcomes = []
    for gold in entries:
        for pv in versions:
            payload = {"query": gold.question, "callerSub": EVAL_SUB, "groups": groups, "pipelineVersion": pv,
                       "packageId": gold.package_id or package_id, "runId": new_run_id()}
            try:
                res = SearchUnitsResult.load(unwrap_tool_result(aws.invoke(lambda_name("search_units"), payload)))
                top1 = RetrievedItem.from_retrieved(res.units[0]) if res.units else None
                o = score_retrieval(gold, pv, top1, latency_ms=res.latency_ms)
            except Exception as exc:  # noqa: BLE001 - one failing question must not stop the run
                o = score_retrieval(gold, pv, None, error=f"{type(exc).__name__}: {exc}")
            outcomes.append(o)
            log(f"{gold.qid} {pv}: recall@1 {o.recall_at1} region {o.region_hit_at1}"
                + (f" ERROR {o.error}" if o.error else ""))
    return outcomes


# --- local mode ---------------------------------------------------------------------------------------

def load_local_units(units_dir: Path) -> dict[str, list[Unit]]:
    by_pv: dict[str, list[Unit]] = {}
    files = sorted(units_dir.rglob("*.jsonl"))
    if not files:
        raise SystemExit(f"no *.jsonl under {units_dir}: run `make units` first")
    for f in files:
        for line in f.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                u = Unit.load(json.loads(line))
            except (json.JSONDecodeError, ValueError):
                continue  # diff/report lines that are not Unit records
            by_pv.setdefault(u.pipeline_version, []).append(u)
    return by_pv


def rank_local(units: Sequence[Unit], question: str, package_id: str | None) -> RetrievedItem | None:
    pool = [u for u in units if u.pii_flag.value != "held"]
    scoped = [u for u in pool if package_id and u.package_id == package_id]
    pool = scoped or pool
    ranked = bm25_rank(question, [(u.unit_id, RetrievedItem.from_unit(u).text) for u in pool])
    if not ranked or ranked[0][1] <= 0:
        return None
    by_id = {u.unit_id: u for u in pool}
    return RetrievedItem.from_unit(by_id[ranked[0][0]])


def run_local(entries: Sequence[GoldenEntry], versions: Sequence[str], units_dir: Path
              ) -> tuple[list[QuestionOutcome], dict[str, list[int]]]:
    by_pv = load_local_units(units_dir)
    outcomes = []
    for gold in entries:
        for pv in versions:
            top1 = rank_local(by_pv.get(pv, []), gold.question, gold.package_id)
            outcomes.append(score_retrieval(gold, pv, top1))
    chars = {pv: [u.chars or len(u.text) for u in by_pv.get(pv, []) if u.pii_flag.value != "held"] for pv in versions}
    return outcomes, chars


# --- assembling and writing ----------------------------------------------------------------------------

def local_lane_meta(pv: str) -> dict[str, Any]:
    try:
        lp = load_pipeline(pv, LocalConfigSource(REPO))
    except FileNotFoundError:
        return {}
    return {"config_sha": lp.config_sha, "prompt_sha": lp.prompt_sha, "model_id": lp.config.generation.model_id}


def local_corpus_hash(package_dir: Path) -> str | None:
    pdfs = sorted(package_dir.glob("*.pdf"))
    return corpus_hash(sha256_bytes(p.read_bytes()) for p in pdfs) if pdfs else None


def run_logged(runs_dir: Path, golden: Sequence[GoldenEntry], versions: Sequence[str]
               ) -> tuple[list[QuestionOutcome], list[dict[str, Any]]]:
    """Every logged run of the gate cohort (default generation profile, User A) re-scored from its event stream.
    -> (outcomes, the index rows used). A row without events (an agent error before the stream) is skipped."""
    from evidence_core.models.answer import AskEvent
    from pydantic import TypeAdapter

    adapter = TypeAdapter(AskEvent)
    by_qid = {g.qid: g for g in golden}
    index = json.loads((runs_dir / "index.json").read_text(encoding="utf-8"))
    rows = [r for r in index.get("runs", []) if r.get("runId") and r["qid"] in by_qid
            and r["pipelineVersion"] in versions and (r.get("generationProfile") or "default") == "default"
            and (r.get("caller") or "a") == "a"]
    outcomes, used = [], []
    for r in sorted(rows, key=lambda r: (r["qid"], r["pipelineVersion"], r["runId"])):
        path = runs_dir / "events" / f"{r['runId']}.jsonl"
        if not path.exists():
            continue
        events = [adapter.validate_python(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
        outcomes.append(outcome_from_events(by_qid[r["qid"]], r["pipelineVersion"], events, repeat=r.get("repeat", 0)))
        used.append(r)
    return outcomes, used


# The local builds whose units decide which answers a single unit prints (``multi_unit_qids``).
LOCAL_BUILDS = ("build/e2e", "build/e2e-lending-02", "build/e2e-quarterly", "build/e2e-tradeconf")


def multi_unit_qids(golden: Sequence[GoldenEntry], builds: Sequence[Path]) -> list[str] | None:
    """Golden questions whose answer is printed in no single unit of any lane (a total computed from several
    fields): top-1 cannot hold the answer, so Recall@1 is not applicable to them. Read from the local builds' unit
    JSONL (the answer pattern on the gold page, as ``recall_hit`` reads a top-1 unit); None when no build exists."""
    by_pv: dict[str, list[Unit]] = {}
    for b in builds:
        if b.is_dir() and any(b.rglob("*.units.jsonl")):
            for pv, units in load_local_units(b).items():
                by_pv.setdefault(pv, []).extend(units)
    if not by_pv:
        return None
    out = []
    for g in golden:
        pools = [[u for u in units if u.package_id == g.package_id] for units in by_pv.values()]
        if not any(pools):
            continue
        if not any(recall_hit(RetrievedItem.from_unit(u), g) for pool in pools for u in pool):
            out.append(g.qid)
    return sorted(out)


def logged_result(runs_dir: Path, golden_path: Path, versions: Sequence[str]) -> EvalResult:
    """Deterministic: the id hashes the run ids it scored, ``createdAt`` is the last scored run's time."""
    import hashlib
    from collections import Counter

    golden = load_golden(golden_path)
    outcomes, used = run_logged(runs_dir, golden, versions)
    if not outcomes:
        raise SystemExit(f"no logged runs to score in {runs_dir}")
    qids = {o.qid for o in outcomes}
    entries = [g for g in golden if g.qid in qids]
    digest = hashlib.sha256("\n".join(sorted(r["runId"] for r in used)).encode()).hexdigest()[:12]
    records = {r["runId"]: json.loads((runs_dir / f"{r['runId']}.json").read_text()) for r in used
               if (runs_dir / f"{r['runId']}.json").exists()}
    result = build_result(
        mode="logged", golden_path=golden_path, entries=entries, versions=versions, outcomes=outcomes,
        lane_meta={pv: local_lane_meta(pv) for pv in versions}, repeats=max(r.get("repeat", 0) for r in used) + 1,
        eval_run_id=f"e-logged-{digest}",
        meta={"runs": str(runs_dir.relative_to(REPO)) if runs_dir.is_relative_to(REPO) else str(runs_dir),
              "cohort": "generationProfile default · User A (the gate runs)", "provenance": "local run · Bedrock",
              "goldenTypes": dict(Counter(g.type for g in golden)), "questionsScored": len(qids),
              "corpusHashes": sorted({rec.get("corpusHash") for rec in records.values() if rec.get("corpusHash")}),
              "configShas": {pv: sorted({rec["configSha"] for rec in records.values()
                                         if rec["pipelineVersion"] == pv}) for pv in versions},
              **({"multiUnit": multi, "multiUnitBasis": "answer printed in no single unit of either lane (local "
                                                        "builds' units): top-1 cannot hold it"}
                 if (multi := multi_unit_qids(entries, [REPO / b for b in LOCAL_BUILDS])) is not None else {})})
    last = max((rec.get("createdAt") for rec in records.values()), default=result.created_at)
    return result.model_copy(update={"created_at": last})


def build_result(*, mode: str, golden_path: Path, entries: Sequence[GoldenEntry], versions: Sequence[str],
                 outcomes: list[QuestionOutcome], lane_meta: dict[str, dict[str, Any]],
                 unit_chars: dict[str, list[int]] | None = None, corpus: str | None = None, repeats: int = 1,
                 meta: dict[str, Any] | None = None, eval_run_id: str | None = None) -> EvalResult:
    lanes = {
        pv: lane_metrics(pv, outcomes, unit_chars=(unit_chars or {}).get(pv, ()), **lane_meta.get(pv, {}))
        for pv in versions
    }
    return EvalResult(
        eval_run_id=eval_run_id or new_eval_run_id(),
        created_at=iso(),
        mode=mode,
        mode_note=MODE_NOTES[mode],
        golden_path=str(golden_path.relative_to(REPO)) if golden_path.is_relative_to(REPO) else str(golden_path),
        golden_sha=sha256_bytes(golden_path.read_bytes()),
        package_ids=sorted({e.package_id for e in entries if e.package_id}),
        pipeline_versions=list(versions),
        corpus_hash=corpus,
        repeats=repeats,
        lanes=lanes,
        table=harness_table(lanes),
        outcomes=outcomes,
        meta=meta or {},
    )


def write_result(result: EvalResult, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{result.eval_run_id}.json"
    path.write_text(json.dumps(result.dump(exclude_none=False), indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
    return path


def render_table(result: EvalResult) -> str:
    pvs = result.pipeline_versions

    def pct(v: float | None) -> str:
        return "  —  " if v is None else f"{v * 100:5.1f}%"

    head = ["type", "n"] + [f"R@1 {pv}" for pv in pvs] + [f"Region@1 {pv}" for pv in pvs] + \
        [f"chars {pv}" for pv in pvs]
    lines = [f"Harness · {result.mode} · {result.eval_run_id}", "  ".join(f"{h:>14}" for h in head)]
    for row in result.table:
        cells = [row.type, str(row.n)] + [pct(row.recall_at1[pv]) for pv in pvs] + \
            [pct(row.region_hit_at1[pv]) for pv in pvs] + \
            [("—" if row.mean_unit_chars[pv] is None else f"{row.mean_unit_chars[pv]:.0f}") for pv in pvs]
        lines.append("  ".join(f"{c:>14}" for c in cells))
    if result.mode in ("ask", "logged"):
        for pv, lane in result.lanes.items():
            parts = [f"{t}: answer {m.answer_correct.hits}/{m.answer_correct.n}, cites {m.citation_valid.hits}/"
                     f"{m.citation_valid.n}" for t, m in lane.by_type.items() if m.n]
            lines.append(f"  {pv}  " + " · ".join(parts))
    lines.append(result.caption)
    return "\n".join(lines)


def upload_result(path: Path) -> str:
    from evidence_core import s3keys
    from rp_ops.aws import Aws

    aws = Aws()
    bucket = aws.ssm("data_bucket")
    key = s3keys.eval_result_key(path.stem)
    aws.client("s3").put_object(Bucket=bucket, Key=key, Body=path.read_bytes(), ContentType="application/json")
    return f"s3://{bucket}/{key}"


# --- CLI ----------------------------------------------------------------------------------------------

def cmd_run(a: argparse.Namespace) -> int:
    golden_path = Path(a.golden)
    if not golden_path.exists():
        raise SystemExit(f"{golden_path} not found (eval/golden.jsonl is written by `make data`)")
    versions = [v.strip() for v in a.versions.split(",") if v.strip()]
    if a.mode == "logged":
        result = logged_result(Path(a.runs), golden_path, versions)
        path = write_result(result, Path(a.out))
        print(render_table(result))
        print(f"\nwrote {path}")
        return 0
    entries = select(load_golden(golden_path), qids=a.qid or (), types=a.types.split(",") if a.types else (),
                     package_id=a.package_id, headline_only=a.headline_only, limit=a.limit)
    if not entries:
        raise SystemExit("no golden entries selected")
    log(f"{len(entries)} question(s) x {len(versions)} version(s), mode {a.mode}")
    lane_meta: dict[str, dict[str, Any]] = {pv: local_lane_meta(pv) for pv in versions}
    unit_chars: dict[str, list[int]] | None = None
    corpus = local_corpus_hash(REPO / "data" / "samples" / "lending")
    if a.mode == "ask":
        outcomes, ask_meta, ask_corpus = run_ask(entries, versions, package_id=a.package_id, repeat=a.repeat,
                                                 user=a.user, generation_profile=a.generation_profile, via=a.via)
        lane_meta |= ask_meta
        corpus = ask_corpus or corpus
    elif a.mode == "search":
        outcomes = run_search(entries, versions, package_id=a.package_id, groups=a.groups.split(","))
    else:
        outcomes, unit_chars = run_local(entries, versions, Path(a.units_dir))
    result = build_result(mode=a.mode, golden_path=golden_path, entries=entries, versions=versions,
                          outcomes=outcomes, lane_meta=lane_meta, unit_chars=unit_chars, corpus=corpus,
                          repeats=a.repeat if a.mode == "ask" else 1,
                          meta={"packageId": a.package_id, "argv": sys.argv[1:],
                                **({"generationProfile": a.generation_profile, "via": a.via}
                                   if a.mode == "ask" else {})})
    path = write_result(result, Path(a.out))
    print(render_table(result))
    print(f"\nwrote {path}")
    if a.upload:
        print(f"uploaded {upload_result(path)}")
    return 0


def cmd_compare(a: argparse.Namespace) -> int:
    base = EvalResult.load(json.loads(Path(a.baseline).read_text(encoding="utf-8")))
    cand = EvalResult.load(json.loads(Path(a.candidate).read_text(encoding="utf-8")))
    cmp = compare(base, cand, tolerance=a.tolerance)
    for w in cmp.warnings:
        print(f"WARNING: {w}")
    print(f"baseline {cmp.baseline_run_id}  ->  candidate {cmp.candidate_run_id}")
    print(f"{'version':>8} {'type':>9} {'metric':>15} {'baseline':>9} {'candidate':>9} {'delta':>8}")
    for d in cmp.deltas:
        if d.baseline is None and d.candidate is None:
            continue

        def fmt(v: float | None) -> str:
            return "—" if v is None else f"{v:.3f}"

        flag = "  REGRESSION" if d.regression else ""
        print(f"{d.pipeline_version:>8} {d.type:>9} {d.metric:>15} {fmt(d.baseline):>9} {fmt(d.candidate):>9} "
              f"{fmt(d.delta):>8}{flag}")
    if a.json:
        Path(a.json).write_text(cmp.dump_json(exclude_none=False), encoding="utf-8")
    if cmp.blocks_promotion:
        print(f"\n{len(cmp.regressions)} regression(s): candidate must not be promoted")
        return 1
    print("\nno regressions")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="eval/run.py", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("run", help="run the golden set (default)")
    r.add_argument("--mode", choices=("ask", "search", "local", "logged"), default="ask")
    r.add_argument("--runs", default=str(REPO / "runs" / "local"), help="logged mode: the logged runs")
    r.add_argument("--golden", default=str(DEFAULT_GOLDEN))
    r.add_argument("--versions", default="1.0,2.3.1")
    r.add_argument("--package-id", default=DEFAULT_PACKAGE)
    r.add_argument("--qid", action="append", help="restrict to these qids (repeatable)")
    r.add_argument("--types", help="comma list of question types: table,prose,figure,footnote")
    r.add_argument("--headline-only", action="store_true")
    r.add_argument("--limit", type=int)
    r.add_argument("--repeat", type=int, default=1, help="ask mode: runs per question and version (gate: 10)")
    r.add_argument("--user", default="user-a", help="ask mode: demo user to sign in as")
    r.add_argument("--generation-profile", choices=("default", "frontier", "compact"), default="default",
                   help="ask mode: AskRequest.generationProfile, swaps the answer model for these runs only "
                        "(frontier = MODEL_FRONTIER, SC-10); the gate and the golden set use default")
    r.add_argument("--via", choices=("api", "runtime"), default="api",
                   help="ask mode: POST /ask on the API (default), or straight to the AgentCore Runtime, so a user "
                        "outside the package ACL still runs the agent and its tools scope that identity (SC-7)")
    r.add_argument("--groups", default="lending-analyst", help="search mode: groups for the service principal")
    r.add_argument("--units-dir", default=str(REPO / "build" / "units"), help="local mode: `make units` output")
    r.add_argument("--out", default=str(RESULTS_DIR))
    r.add_argument("--upload", action="store_true", help="also put the result at s3://<data bucket>/eval/results/")
    r.set_defaults(fn=cmd_run)
    c = sub.add_parser("compare", help="baseline vs candidate; exit 1 on any regression")
    c.add_argument("baseline")
    c.add_argument("candidate")
    c.add_argument("--tolerance", type=float, default=0.0)
    c.add_argument("--json", help="also write the comparison JSON here")
    c.set_defaults(fn=cmd_compare)
    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("run", "compare", "-h", "--help"):
        argv = ["run", *argv]
    args = build_parser().parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())

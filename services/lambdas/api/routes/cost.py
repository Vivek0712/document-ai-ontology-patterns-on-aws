"""GET /cost?packageId=&groupBy=docType|stage|lane|service&lane= (SC-13, W-17).

Every amount is ``estimate · measured usage × list price`` (``config/prices.yaml``: price, source URL, date). The
usage is read, never assumed, except where a line says ``measured: false`` and gives its ``basis``:

| line | usage | measured? |
|---|---|---|
| ask | the run's Converse token usage (RunRecord ``usage``), per model | yes |
| extract · Textract | pages the extract stage sent to Textract (``pagesByEngine.textract``) | yes |
| extract · Docling | ``doclingMs`` × the Docling task's vCPU and memory (an allocation of a running task) | no |
| pii_gate · Comprehend | 100-character units from the units stage's ``units × meanChars`` | no |

Stage wall times are listed as they were logged (``StageRecord.durationMs``). Lambda compute and Titan embeddings are
not estimated in this build (named in ``notes``). The classify tie-break's Nova calls are counted from the stage
metric ``tieBreak``; their tokens are not logged, so that tiering row has no estimate.

Publication flag ``RP_PUBLIC_METRICS``: when off (default) every dollar field is null and ``withheld``
says so; usage, durations and the tiering table without dollars remain.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core.models.answer import RunRecord, RunSummary
from evidence_core.models.measure import (
    CostGroup,
    CostLine,
    CostPerDocument,
    CostPrices,
    CostUsage,
    CostView,
    StageDuration,
    TieringRow,
)
from evidence_core.models.status import StageRecord
from evidence_core.prices import PRICES_KEY, Prices

from .. import store
from ..acl import can_read_doc, require_package
from ..deps import Deps, get_deps
from ..http import Reply, Request, bad_request
from . import COST, router
from .evaluate import metrics_published

GROUP_BY = ("docType", "stage", "lane", "service")
MAX_RUNS = 500
CALL_TYPES = {"MODEL_ANSWER": "answer", "MODEL_FRONTIER": "answer · frontier profile",
              "MODEL_COMPACT": "answer · compact profile"}


def load_prices(deps: Deps) -> Prices:
    text, _ = deps.source().read_text(PRICES_KEY)
    return Prices.parse(text)


def _line(prices: Prices, *, service: str, unit: Any, quantity: float, measured: bool, basis: str | None = None,
          **kw: Any) -> CostLine:
    sp = prices.services.get(service)
    unit_price = sp.usd if sp else None
    return CostLine(service=service, usage=CostUsage(unit=unit, quantity=round(quantity, 3), measured=measured,
                                                     basis=basis),
                    unit_price_usd=unit_price, price_ref=sp.source if sp else None,
                    estimate_usd=round(quantity * unit_price, 6) if unit_price is not None else None, **kw)


def stage_lines(prices: Prices, package_id: str, doc_id: str, doc_type: str | None, pv: str,
                records: list[StageRecord]) -> list[CostLine]:
    by_stage = {r.stage_id.value: r for r in records}
    common = {"package_id": package_id, "doc_id": doc_id, "doc_type": doc_type, "pipeline_version": pv}
    out: list[CostLine] = []
    extract = by_stage.get("extract")
    if extract is not None:
        engines = (extract.output or {}).get("pagesByEngine") or {}
        textract_pages = len(engines.get("textract") or [])
        if textract_pages:
            out.append(_line(prices, service="textract.analyze_document.layout", unit="page", quantity=textract_pages,
                             measured=True, stage_id="extract", at=extract.ended_at, **common))
        docling_ms = (extract.metrics or {}).get("doclingMs")
        task = prices.sizes.get("doclingTask") or {}
        if docling_ms and task:
            seconds = float(docling_ms) / 1000
            basis = f"doclingMs {docling_ms} × the task size ({task.get('source')}); the service bills running tasks"
            out.append(_line(prices, service="fargate.vcpu", unit="vcpu_s", quantity=seconds * float(task["vcpu"]),
                             measured=False, basis=basis, stage_id="extract", at=extract.ended_at, **common))
            out.append(_line(prices, service="fargate.memory", unit="gb_s",
                             quantity=seconds * float(task["memoryGb"]), measured=False, basis=basis,
                             stage_id="extract", at=extract.ended_at, **common))
    gate, units = by_stage.get("pii_gate"), by_stage.get("units")
    if gate is not None and gate.engine is not None and gate.engine.value == "comprehend" and units is not None:
        n = int((units.metrics or {}).get("units") or 0)
        mean = float((units.metrics or {}).get("meanChars") or 0)
        sp = prices.services.get("comprehend.detect_pii_entities")
        if n and sp:
            per_unit = max(sp.min_units_per_request, math.ceil(mean / 100))
            out.append(_line(prices, service="comprehend.detect_pii_entities", unit="unit_100_chars",
                             quantity=n * per_unit, measured=False,
                             basis=f"{n} units × ceil({mean:.0f} mean chars / 100), at least "
                                   f"{sp.min_units_per_request} per request", stage_id="pii_gate",
                             at=gate.ended_at, **common))
    return out


def ask_lines(prices: Prices, runs: Iterable[RunRecord]) -> list[CostLine]:
    """Two lines per ask (input and output tokens). A model missing from prices.yaml still gets its lines, with no
    price, so a group counts it as unpriced instead of dropping its usage."""
    out = []
    for r in runs:
        if not r.usage:
            continue
        mp = prices.models.get(r.model_id)
        common = {"package_id": r.package_id, "pipeline_version": r.pipeline_version, "stage_id": "ask",
                  "service": r.model_id, "price_ref": mp.source if mp else None, "run_id": r.run_id,
                  "at": r.created_at}
        for unit, key, per in (("token_in", "inputTokens", mp.input_per_mtok if mp else None),
                               ("token_out", "outputTokens", mp.output_per_mtok if mp else None)):
            qty = int(r.usage.get(key, 0))
            out.append(CostLine(usage=CostUsage(unit=unit, quantity=qty, measured=True),
                                unit_price_usd=per / 1e6 if per is not None else None,
                                estimate_usd=round(qty * per / 1e6, 6) if per is not None else None, **common))
    return out


def tiering(prices: Prices, runs: list[RunRecord], stages: dict[tuple[str, str], list[StageRecord]],
            models: dict[str, str], owner_sub: str) -> list[TieringRow]:
    """Calls per call type and model, over the package owner's asks: another user's asks (refused at the tools,
    for one) are in the ledger, never in the tier counts."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for r in runs:
        if r.caller_sub != owner_sub:
            continue
        key = r.model_key or next((k for k, mid in models.items() if mid == r.model_id), "MODEL_ANSWER")
        row = rows.setdefault((CALL_TYPES.get(key, key), r.model_id), {"key": key, "calls": 0, "in": 0, "out": 0,
                                                                         "usd": 0.0, "priced": True})
        row["calls"] += 1
        row["in"] += int(r.usage.get("inputTokens", 0))
        row["out"] += int(r.usage.get("outputTokens", 0))
        if r.model_id in prices.models:
            row["usd"] += prices.model_cost(r.model_id, r.usage)
        else:
            row["priced"] = False
    out = [TieringRow(call_type=ct, model_key=v["key"], model_id=mid, calls=v["calls"], tokens_in=v["in"],
                      tokens_out=v["out"], estimate_usd=round(v["usd"], 6) if v["priced"] else None,
                      source="RunRecord usage of the package owner's asks (one row per ask; an ask is one "
                             "agent loop of Converse calls)")
           for (ct, mid), v in sorted(rows.items())]
    tie_breaks = sum(int((rec.metrics or {}).get("tieBreak") or 0) for recs in stages.values() for rec in recs
                     if rec.stage_id.value == "classify")
    if "MODEL_COMPACT" in models:
        out.append(TieringRow(call_type="classify tie-break", model_key="MODEL_COMPACT",
                              model_id=models["MODEL_COMPACT"], calls=tie_breaks, tokens_in=0, tokens_out=0,
                              estimate_usd=None,
                              source="classify StageRecord metric tieBreak (tokens not logged)"))
    return out


def group(lines: list[CostLine], by: str) -> list[CostGroup]:
    def key_of(line: CostLine) -> str:
        return {"docType": line.doc_type or ("ask" if line.stage_id == "ask" else "unknown"),
                "stage": line.stage_id, "lane": line.pipeline_version, "service": line.service}[by]

    groups: dict[str, dict[str, Any]] = defaultdict(lambda: {"usd": 0.0, "svc": defaultdict(float), "n": 0,
                                                             "unpriced": 0})
    for line in lines:
        g = groups[key_of(line)]
        g["n"] += 1
        if line.estimate_usd is not None:
            g["usd"] += line.estimate_usd
            g["svc"][line.service] += line.estimate_usd
        else:
            g["unpriced"] += 1
    return [CostGroup(key=k, estimate_usd=round(v["usd"], 6), lines=v["n"], unpriced_lines=v["unpriced"],
                      by_service={s: round(x, 6) for s, x in sorted(v["svc"].items())})
            for k, v in sorted(groups.items())]


def package_runs(deps: Deps, package_id: str) -> tuple[list[RunRecord], bool]:
    """The package's newest ``MAX_RUNS`` run records, oldest first, and whether older runs were left out."""
    rows = store.query_all(deps.table("runs"), IndexName="byPackage",
                           KeyConditionExpression=Key("packageId").eq(package_id))
    summaries = sorted((RunSummary.load(r) for r in rows), key=lambda s: (s.created_at, s.run_id))
    truncated = len(summaries) > MAX_RUNS
    out = []
    for summary in summaries[-MAX_RUNS:]:
        data = store.read_json(deps, summary.s3_key)
        if data is not None:
            out.append(RunRecord.load(data))
    return out, truncated


def withhold(view: CostView) -> CostView:
    """RP_PUBLIC_METRICS=off: the same view with every dollar field null."""
    return view.model_copy(update={
        "published": False,
        "withheld": ["every dollar amount: RP_PUBLIC_METRICS=off"],
        "groups": [g.model_copy(update={"estimate_usd": None, "by_service": {}}) for g in view.groups],
        "per_document": [d.model_copy(update={"estimate_usd": None}) for d in view.per_document],
        "tiering": [t.model_copy(update={"estimate_usd": None}) for t in view.tiering],
        "lines": [ln.model_copy(update={"estimate_usd": None, "unit_price_usd": None}) for ln in view.lines],
    })


@router.route("GET", COST)
def cost(req: Request) -> Reply:
    package_id = req.query.get("packageId")
    if not package_id:
        raise bad_request("packageId is required")
    by = req.query.get("groupBy") or "docType"
    if by not in GROUP_BY:
        raise bad_request(f"groupBy must be one of {', '.join(GROUP_BY)}")
    lane = req.query.get("lane")
    deps = get_deps()
    pkg = require_package(deps, req.caller, package_id)
    if lane and lane not in pkg.pipeline_versions:
        raise bad_request(f"{package_id} has no lane {lane}")
    prices = load_prices(deps)
    lanes = [lane] if lane else list(pkg.pipeline_versions)
    lines: list[CostLine] = []
    durations: list[StageDuration] = []
    per_doc: list[CostPerDocument] = []
    stages: dict[tuple[str, str], list[StageRecord]] = {}
    for doc in store.query_documents(deps, package_id):
        if not can_read_doc(deps, req.caller, doc.doc_id):
            continue
        for pv in lanes:
            recs = store.stage_records(deps, doc.doc_id, pv)
            if not recs:
                continue
            stages[(doc.doc_id, pv)] = recs
            durations += [StageDuration(doc_id=doc.doc_id, pipeline_version=pv, stage_id=r.stage_id.value,
                                        engine=r.engine.value if r.engine else None, duration_ms=r.duration_ms,
                                        started_at=r.started_at) for r in recs if r.duration_ms is not None]
            doc_lines = stage_lines(prices, package_id, doc.doc_id, doc.doc_type, pv, recs)
            lines += doc_lines
            per_doc.append(CostPerDocument(doc_id=doc.doc_id, doc_type=doc.doc_type, lane=pv, pages=doc.page_count,
                                           estimate_usd=round(sum(x.estimate_usd or 0.0 for x in doc_lines), 6)))
    all_runs, truncated = package_runs(deps, package_id)
    runs = [r for r in all_runs if r.pipeline_version in lanes]
    lines += ask_lines(prices, runs)
    models = {k: m.model_id for k, m in deps.pipeline(lanes[0]).models.models.items()}
    refs = {k: v.source for k, v in prices.services.items()} | {k: v.source for k, v in prices.models.items()}
    view = CostView(
        package_id=package_id, lane=lane, group_by=by, published=True,
        prices=CostPrices(source=PRICES_KEY, as_of=prices.as_of, refs=refs), groups=group(lines, by),
        per_document=per_doc, tiering=tiering(prices, runs, stages, models, pkg.owner_sub), stages=durations,
        lines=lines, truncated=truncated,
        notes=["Lambda compute and Titan V2 embedding calls are not estimated in this build.",
               "Docling seconds are allocated to the document: the Fargate service bills each running task.",
               *([f"Only the newest {MAX_RUNS} runs are read: older asks are not in the ledger."] if truncated
                 else [])])
    return Reply(view if metrics_published() else withhold(view))

"""Local logged runs: golden questions asked of each lane's local outputs with the v1 agent.

Same prompt (``prompts/answer_v1.txt`` via the lane config), same model (``MODEL_ANSWER`` through
Bedrock Converse), same runner (``rp_agent.runner.AskRunner``: citation validator, region lookups, golden
check, run record) and the same tool code. Only preprocessing differs between the lanes, as in v1; what
differs from the deployed stack is where the tools' services run (see ``gateway.py``).

Each run writes ``runs/local/{runId}.json`` (a ``RunRecord`` plus a ``provenance`` block that says
"local run · Bedrock") and ``runs/local/events/{runId}.jsonl`` (the SSE event stream, for replay).
``runs/local/index.json`` lists every run with its golden-set score, cost and the representative run of
each (question, lane).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from pathlib import Path
from typing import Any

from evidence_core.config import LocalConfigSource, load_pipeline
from evidence_core.hashing import corpus_hash
from evidence_core.models.answer import AskEvent, AskRequest, RunRecord
from evidence_core.models.golden import GoldenEntry, load_golden, normalise_amount
from evidence_core.prices import Prices, load_prices
from pydantic import TypeAdapter

REPO = Path(__file__).resolve().parents[2]
RUNS_DIR = REPO / "runs" / "local"
PROVENANCE_LABEL = "local run · Bedrock"
LANES = ("1.0", "2.3.1")
_EVENT = TypeAdapter(AskEvent)

# The two analysts of the demo (the mock world's User A and User B). User A is each local build's package owner
# (``world.meta``); User B is in another group, as the generator's package ACLs say.
USER_B = ("b5d09f13-0000-4000-8000-00000000000b", "user-b", ["markets-analyst"])
# How a run as User B reaches the agent: the API refuses B at the package check (``POST /ask`` -> 403) before any
# model call, so the scoping run invokes the agent directly with B's identity, as a caller with a valid token could
# invoke the AgentCore Runtime, to show what the gateway-injected scope alone does.
USER_B_ENTRY = "agent runtime invoked directly as User B (bypasses the API's package check; tools still scoped)"


@cache
def prices() -> Prices:
    return load_prices(LocalConfigSource(root=REPO))


def cost_usd(model_id: str, usage: dict[str, int]) -> float:
    """List price × the run's reported token usage (``config/prices.yaml``)."""
    return prices().model_cost(model_id, usage) if model_id else 0.0


def guard_usd(model_id: str, usage: dict[str, int]) -> float:
    """What the batch budget counts: never below ``guardPerMTok`` (Opus is guarded at ~5x Sonnet)."""
    return prices().guard_cost(model_id, usage) if model_id else 0.0


def tool_spec_sha(specs: dict[str, dict[str, Any]], names: Sequence[str]) -> str:
    """sha256 of the exact tool specs sent to Converse for ``names`` (name, description, JSON schema)."""
    sent = [specs[n] for n in sorted(names) if n in specs]
    return hashlib.sha256(json.dumps(sent, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass
class LocalRunStore:
    """``RunStore`` for local runs: corpus hash from the world's Documents table, records on disk. Each record
    also carries ``toolSpecSha``, the hash of the tool specs the model was given (lane parity checks it)."""

    documents: list[dict[str, Any]]
    out: Path
    provenance: dict[str, Any]
    tool_specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    written: list[RunRecord] = field(default_factory=list)

    def corpus_hash(self, package_id: str) -> str:
        return corpus_hash(d["sha256"] for d in self.documents
                           if d["packageId"] == package_id and d.get("sha256") and d.get("uploadState") == "IN_BRONZE")

    def write(self, record: RunRecord) -> None:
        self.out.mkdir(parents=True, exist_ok=True)
        body = record.dump() | {"toolSpecSha": tool_spec_sha(self.tool_specs, record.tools_enabled),
                                "provenance": self.provenance}
        (self.out / f"{record.run_id}.json").write_text(json.dumps(body, indent=1, ensure_ascii=False))
        self.written.append(record)


# --- lane parity: the lanes may differ only in preprocessing ------------------------------------------------

PARITY_EXEMPT = ("preprocessing",)
_EXEMPT_KEYS = {f"blockShas.{b}" for b in PARITY_EXEMPT}
UNRECORDED = "unrecorded"
# Tool schema changes the RunRecords cannot show for runs logged before ``toolSpecSha`` was recorded.
TOOL_SCHEMA_CHANGES = (
    "2026-11-11: graph_hop gained template coupon_terms_v1 (models/tools.py 00:27Z; contracts/generated/"
    "mcp_tools.json last regenerated 01:15Z, after that day's runs at 00:37-00:49Z). Which schema those runs "
    "were given is not recorded.",
)


def _parity(values: dict[str, dict[str, Any]]) -> dict[str, Any]:
    lanes = sorted(values)
    keys = sorted({k for v in values.values() for k in v})
    differing = [k for k in keys if len({json.dumps(values[pv].get(k), sort_keys=True) for pv in lanes}) > 1]
    return {"lanes": lanes, "checked": keys, "differing": differing, "ok": not differing}


def config_parity(lanes: Sequence[str], loader: Callable[[str], Any] = load_pipeline,
                  tool_specs: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Before asking: model, prompt, tool specs and every config block but preprocessing are identical across
    the lanes."""
    values = {}
    for pv in lanes:
        lp = loader(pv)
        values[pv] = {"modelId": lp.config.generation.model_id, "promptSha": lp.prompt_sha,
                      **{f"blockShas.{b}": sha for b, sha in lp.block_shas.items() if b not in PARITY_EXEMPT}}
        if tool_specs is not None:
            names = [t.split("@", 1)[0] for t in lp.config.agent.tools]  # as rp_agent.runner enables them
            values[pv]["toolSpecSha"] = tool_spec_sha(tool_specs, names)
    return _parity(values) | {"source": "lane configs (load_pipeline)"}


def _profile(r: dict[str, Any]) -> str:
    return r.get("generationProfile") or "default"


def record_parity(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """After asking: the logged RunRecords agree with each other within a lane and across lanes, except in
    preprocessing (and so configSha). A lane with two values for one field fails.

    Runs with another generation profile (SC-10 ``frontier``, SC-13 ``compact``) are checked on the **model axis**
    instead: against the same lane's default runs, every recorded field must be identical except ``modelId`` (and
    ``modelKey``), which must differ (``modelAxis``). They never enter the lane comparison.

    ``toolSpecSha`` (the specs the model was given) is checked like the rest. A record from before it was logged
    reads ``unrecorded``: ``ok`` then means "no difference among what was recorded", and ``complete`` is False
    with ``unverified`` naming what the records cannot show."""
    groups: dict[tuple[str, str], dict[str, set[str]]] = {}
    unrecorded = 0
    for r in records:
        unrecorded += not r.get("toolSpecSha")
        fields = {"modelId": r["modelId"], "promptSha": r["promptSha"], "toolsEnabled": sorted(r["toolsEnabled"]),
                  "toolSpecSha": r.get("toolSpecSha") or UNRECORDED,
                  **{f"blockShas.{b}": sha for b, sha in r["blockShas"].items()}}
        group = groups.setdefault((r["pipelineVersion"], _profile(r)), {})
        for k, v in fields.items():
            group.setdefault(k, set()).add(json.dumps(v, sort_keys=True))
    mixed = sorted(f"{pv}{'' if prof == 'default' else '/' + prof}:{k}" for (pv, prof), g in groups.items()
                   for k, vs in g.items() if len(vs - {json.dumps(UNRECORDED)}) > 1)
    unrec = json.dumps(UNRECORDED)
    lane_values = {pv: {k: "|".join(sorted(vs - {unrec}) or [unrec]) for k, vs in g.items() if k not in _EXEMPT_KEYS}
                   for (pv, prof), g in groups.items() if prof == "default"}
    if any(v.get("toolSpecSha") == unrec for v in lane_values.values()):  # cannot compare what one lane never logged
        for v in lane_values.values():
            v.pop("toolSpecSha", None)
    out = _parity(lane_values)
    axis = []
    for (pv, prof), g in sorted(groups.items()):
        base = groups.get((pv, "default"))
        if prof == "default" or base is None:
            continue
        keys = sorted(set(g) | set(base))
        differing = [k for k in keys if g.get(k) != base.get(k)]
        unknown = [k for k in differing if k == "toolSpecSha" and any(UNRECORDED in v for v in (g.get(k) or set()) |
                                                                      (base.get(k) or set()))]
        axis.append({"lane": pv, "profile": prof, "modelIds": sorted(json.loads(v) for v in g["modelId"]),
                     "defaultModelIds": sorted(json.loads(v) for v in base["modelId"]),
                     "differing": [k for k in differing if k not in unknown], "unverified": unknown,
                     "ok": [k for k in differing if k not in unknown] == ["modelId"]})
    unverified = ["toolSpecSha"] if unrecorded else []
    return out | {"mixedWithinLane": mixed, "ok": out["ok"] and not mixed and all(a["ok"] for a in axis),
                  "modelAxis": axis, "complete": not unverified,
                  "unverified": unverified, "toolSpecUnrecorded": unrecorded,
                  "notes": list(TOOL_SCHEMA_CHANGES) if unrecorded else [], "runs": len(records),
                  "source": "runs/local RunRecords"}


def write_summary(out: Path) -> list[dict[str, Any]]:
    """Recompute the N-of-N summary of ``index.json`` from its rows (no model calls)."""
    path = out / "index.json"
    body = json.loads(path.read_text())
    body["summary"] = summarise(body.get("runs", []))
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False))
    return body["summary"]


def write_parity(out: Path) -> dict[str, Any]:
    """Recompute, over every logged run, the record parity, the config parity of every lane in the index and
    the provenance of every build; backfill each row's ``build`` from its RunRecord. Stored in ``index.json``
    (no model calls)."""
    records = [json.loads(p.read_text()) for p in sorted(out.glob("r-*.json"))]
    parity = record_parity(records)
    path = out / "index.json"
    body = json.loads(path.read_text())
    body["laneParity"] = parity
    lanes = sorted({r["pipelineVersion"] for r in records} | {r["pipelineVersion"] for r in body.get("runs", [])})
    if lanes:
        from .driver import gateway_tool_specs

        body["configParity"] = config_parity(lanes, tool_specs=gateway_tool_specs())
    by_run = {r["runId"]: r for r in records}
    by_build: dict[str, dict[str, Any]] = {}
    for r in records:
        prov = r.get("provenance") or {}
        if prov.get("build"):
            by_build.setdefault(prov["build"], {k: v for k, v in prov.items() if k != "build"})
    for row in body.get("runs", []):
        rec = by_run.get(row.get("runId") or "")
        if rec and not row.get("build"):
            row["build"] = (rec.get("provenance") or {}).get("build")
    body.pop("provenanceDetail", None)  # one batch's provenance; replaced by every build's
    body["provenanceByBuild"] = dict(sorted(by_build.items()))
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False))
    return parity


def outcome_row(gold: GoldenEntry, pv: str, repeat: int, events: Sequence[Any], *, profile: str = "default",
                caller: str = "a") -> dict[str, Any]:
    """Score one run with the eval harness's own functions (``eval.run.outcome_from_events``)."""
    from eval.run import outcome_from_events

    o = outcome_from_events(gold, pv, events, repeat=repeat)
    run = next((e for e in events if getattr(e, "type", None) == "run"), None)
    start = next((e for e in events if getattr(e, "type", None) == "run_start"), None)
    usage = run.usage if run else {}
    model_id = run.model_id if run else (start.model_id if start else "")
    return {"runId": o.run_id, "qid": gold.qid, "pipelineVersion": pv, "repeat": repeat,
            "question": gold.question, "expected": gold.answer, "total": o.total,
            "answerCorrect": o.answer_correct, "checkMatch": o.check_match,
            "citationValid": o.citation.valid if o.citation else None,
            "citationCoversGold": o.citation.covers_gold if o.citation else None,
            "top1": o.top1_unit_id, "recallAt1": o.recall_at1, "error": o.error,
            "latencyMs": o.latency_ms, "usage": usage, "modelId": model_id or None,
            "generationProfile": profile, "caller": caller,
            "costUsd": round(cost_usd(model_id, usage), 5), "guardUsd": round(guard_usd(model_id, usage), 5)}


def representative(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The first run whose total is the most common total for this (question, lane)."""
    ok = [r for r in rows if r.get("runId")]
    if not ok:
        return None
    key = Counter(normalise_amount(r["total"]) if r.get("total") else None for r in ok).most_common(1)[0][0]
    return next(r for r in ok if (normalise_amount(r["total"]) if r.get("total") else None) == key)


def cohort(r: dict[str, Any]) -> tuple[str, str]:
    """(generation profile, caller): the gate's N of N counts only ("default", "a"); the other cohorts (SC-10's
    bigger model, SC-13's compact tier, SC-7's User B) are summarised beside it, never mixed in."""
    return r.get("generationProfile") or "default", r.get("caller") or "a"


def summarise(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = {}
    for r in rows:
        groups.setdefault((r["qid"], r["pipelineVersion"], *cohort(r)), []).append(r)
    out = []
    for (qid, pv, profile, caller), rs in sorted(groups.items()):
        rep = representative(rs)
        logged = [r for r in rs if r.get("runId")]  # an agent error writes no RunRecord: counted apart
        totals = Counter(r.get("total") or "—" for r in logged)
        models = sorted({r["modelId"] for r in rs if r.get("modelId")})
        out.append({"qid": qid, "pipelineVersion": pv, "generationProfile": profile, "caller": caller,
                    "modelIds": models, "costUsd": round(sum(r.get("costUsd") or 0.0 for r in rs), 5),
                    "n": len(logged), "errors": len(rs) - len(logged),
                    "correct": sum(1 for r in rs if r.get("answerCorrect")),
                    "citationsValid": sum(1 for r in rs if r.get("citationValid")),
                    "totals": dict(totals.most_common()), "representativeRunId": rep["runId"] if rep else None,
                    "expected": rs[0]["expected"]})
    return out


def write_index(out: Path, rows: list[dict[str, Any]], meta: dict[str, Any]) -> Path:
    path = out / "index.json"
    body = {"provenance": PROVENANCE_LABEL, "generatedBy": "services/local_agent", **meta,
            "summary": summarise(rows), "runs": rows}
    path.write_text(json.dumps(body, indent=1, ensure_ascii=False))
    return path


def load_index(out: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = out / "index.json"
    if not path.exists():
        return [], {}
    body = json.loads(path.read_text())
    meta = {k: v for k, v in body.items() if k not in ("summary", "runs", "provenance", "generatedBy")}
    return body.get("runs", []), meta


def table(summary: list[dict[str, Any]]) -> str:
    lines = ["| question | lane | profile · caller | correct | every claim opens a region | totals | agent errors "
             "| cost (list) | representative run |", "|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        totals = ", ".join(f"{t} ×{n}" for t, n in s["totals"].items())
        who = f"{s.get('generationProfile', 'default')} · {s.get('caller', 'a')}"
        lines.append(f"| {s['qid']} (exp {s['expected']}) | {s['pipelineVersion']} | {who} "
                     f"| {s['correct']} of {s['n']} "
                     f"| {s['citationsValid']} of {s['n']} | {totals} | {s.get('errors', 0)} "
                     f"| ${s.get('costUsd', 0):.4f} | {s['representativeRunId']} |")
    return "\n".join(lines)


@dataclass
class Plan:
    entries: list[GoldenEntry]
    lanes: Sequence[str]
    repeat_for: Callable[[GoldenEntry], int]
    generation_profile: str = "default"  # AskRequest.generationProfile (model swap for these runs only)
    caller: str = "a"  # "a" = the package owner (world meta), "b" = USER_B

    def asks(self) -> list[tuple[GoldenEntry, str, int]]:
        """Round-robin by repeat, so a budget stop leaves every (question, lane) with the same N or N-1."""
        most = max((self.repeat_for(g) for g in self.entries), default=0)
        return [(g, pv, r) for r in range(most) for g in self.entries for pv in self.lanes if r < self.repeat_for(g)]


async def run_plan(plan: Plan, *, build: Path, out: Path, profile: str | None, budget_usd: float,
                   log: Callable[[str], None] = print) -> list[dict[str, Any]]:
    """Ask every (question, lane, repeat) in ``plan`` and append to ``out/index.json``."""
    import boto3
    from local_pipeline.recorded import RECORDED_DIR, RecordedTitan, Recording, aws_client_factory
    from local_pipeline.world import open_world
    from rp_agent.runner import AskRunner, CallerIdentity

    from .driver import LocalStrandsDriver, gateway_tool_specs
    from .driver import gateway_tool_specs as _specs
    from .gateway import Caller, LocalGateway
    from .index import LABEL as INDEX_LABEL

    parity = config_parity(plan.lanes, tool_specs=_specs())
    if not parity["ok"]:
        raise RuntimeError(f"lanes differ beyond preprocessing: {', '.join(parity['differing'])}")
    rows, meta = load_index(out)
    spent_before = sum(r.get("costUsd") or 0.0 for r in rows)
    spent = 0.0  # this batch, at the guard price; ``budget_usd`` caps it, the index keeps the running list total
    # reserve one more run's guard cost, so the guard stops before it would cross the budget, not after: the most
    # expensive logged run of this cohort, else a default-profile run priced at this model's guard price
    qids = {g.qid for g in plan.entries}
    same = [r for r in rows if cohort(r) == (plan.generation_profile, plan.caller) and r["qid"] in qids]
    probe_model = _profile_model(plan)
    reserve = max((r.get("guardUsd") or r.get("costUsd") or 0.0 for r in same), default=0.0) or max(
        (guard_usd(probe_model, r.get("usage") or {}) for r in rows
         if cohort(r) == ("default", "a") and r["qid"] in qids), default=0.25)
    golden = load_golden(REPO / "eval" / "golden.jsonl")
    titan_rec = Recording.open(RECORDED_DIR / "titan-v2.json",
                               "bedrock-runtime:InvokeModel amazon.titan-embed-text-v2:0")
    session = boto3.Session(profile_name=profile or None, region_name="us-east-1")
    run_info = json.loads((build / "run.json").read_text())
    with open_world(build, passthrough=("bedrock-runtime",)) as world:
        world_pkg = world.meta.get("packageId")
        foreign = sorted({g.qid for g in plan.entries if world_pkg and g.package_id and g.package_id != world_pkg})
        if foreign:
            raise RuntimeError(f"{build} holds {world_pkg}; these questions belong to another package: "
                               f"{', '.join(foreign)} (pass that package's --build)")
        titan = RecordedTitan(titan_rec, aws_client_factory("bedrock-runtime", profile))
        gateway = LocalGateway(world=world, bedrock=titan, abox=build / "graph" / "abox.nq").install()
        caller = (Caller(sub=USER_B[0], groups=list(USER_B[2])) if plan.caller == "b" else
                  Caller(sub=world.meta.get("ownerSub", "local-dev-owner-0000"),
                         groups=list(world.meta.get("groups", ["lending-analyst"]))))
        provenance = {
            "mode": "local-run", "label": PROVENANCE_LABEL,
            "note": "Asked on a developer machine against the local pipeline's outputs; not the deployed AWS stack.",
            "build": str(build.relative_to(REPO)),
            "model": "Amazon Bedrock Converse (real)", "embeddings": RecordedTitan.LABEL,
            "index": INDEX_LABEL, "graph": "pyoxigraph in-process (NOT Amazon Neptune)",
            "stores": "moto (S3, DynamoDB) loaded from the run's world snapshot",
            "piiGate": run_info.get("piiGate"), "extraction": run_info.get("docling"),
            "scanPages": run_info.get("scanPages"), "phaseBEmbeddings": run_info.get("phaseBEmbeddings"),
            "toolTransport": "tool Lambda handlers in-process (local gateway; identity injected like the interceptor)",
        }
        if plan.generation_profile != "default":
            provenance["generationProfile"] = (f"{plan.generation_profile}: answer model swapped for these runs only "
                                               "(AskRequest.generationProfile); lane config unchanged")
        if plan.caller == "b":
            provenance["identity"] = USER_B_ENTRY
        specs = gateway_tool_specs()
        store = LocalRunStore(documents=world.table_items("documents"), out=out, provenance=provenance,
                              tool_specs=specs)
        runner = AskRunner(
            pipeline_loader=load_pipeline,
            driver_factory=lambda spec: LocalStrandsDriver(spec, gateway=gateway, caller=caller,
                                                           boto_session=session, tool_specs=specs),
            run_store=store, golden=golden, gateway_url="local://gateway", identity_mode="local-gateway",
        )
        identity = CallerIdentity(sub=caller.sub, groups=caller.groups, authorization="Bearer local")
        try:
            for gold, pv, r in plan.asks():
                if spent + reserve > budget_usd:
                    log(f"batch budget reached (${spent:.2f} + ${reserve:.2f} reserve > ${budget_usd:.2f}); "
                        f"stopping before {gold.qid} {pv} #{r}")
                    break
                req = AskRequest(question=gold.question, package_id=gold.package_id or "pkg-lending-01",
                                 pipeline_version=pv, generation_profile=plan.generation_profile)
                raw = [ev async for ev in runner.run(req, identity)]
                events = [_EVENT.validate_python(e) for e in raw]
                row = outcome_row(gold, pv, r, events, profile=plan.generation_profile, caller=plan.caller) | {
                    "build": provenance["build"]}
                if row["runId"]:
                    (out / "events").mkdir(parents=True, exist_ok=True)
                    (out / "events" / f"{row['runId']}.jsonl").write_text(
                        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in raw))
                rows.append(row)
                spent += row["guardUsd"]
                reserve = max(reserve, row["guardUsd"])
                log(f"{gold.qid} {pv:>5} {plan.generation_profile}/{plan.caller} #{r}: total {row['total']!s:>12} "
                    f"correct {row['answerCorrect']!s:5} cites {row['citationValid']!s:5} {row['latencyMs']} ms "
                    f"${row['costUsd']:.4f} list / ${row['guardUsd']:.4f} guard (batch guard ${spent:.2f})"
                    f"{' ERROR ' + row['error'] if row['error'] else ''}")
                write_index(out, rows, meta | {"updatedAt": datetime.now(UTC).isoformat(timespec="seconds"),
                                               "spentUsd": round(spent_before + spent, 4)})
        finally:
            titan_rec.save()
            gateway.close()
    if (out / "index.json").exists():
        write_parity(out)
    return rows


def _profile_model(plan: Plan) -> str:
    from evidence_core.models.answer import GENERATION_PROFILE_MODEL_KEYS

    lp = load_pipeline(plan.lanes[0])
    key = GENERATION_PROFILE_MODEL_KEYS.get(plan.generation_profile)
    return lp.models.model_id(key) if key else lp.config.generation.model_id


def run(plan: Plan, **kwargs: Any) -> list[dict[str, Any]]:
    return asyncio.run(run_plan(plan, **kwargs))

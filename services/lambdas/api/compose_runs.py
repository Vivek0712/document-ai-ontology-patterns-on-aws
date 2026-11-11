"""Showcase composers over logged runs (``GET /showcases/{id}/payload``), registered by ``routes/showcases.py``.

``sc-7`` Identity and scoping (``?packageId=&question=``): the same question asked by the package owner and by
other users, each run with the identity the gateway injected, what ``search_units`` returned under it (unit count,
the filter clause it ran with) and the answer; the access decisions of each run (``routes/access.py``); and the
region the owner's answer cites, which the UI then requests as the other user (the 403 comes from the region
service, never from this payload).

``sc-10`` A bigger model does not fix it (``?packageId=&question=&lane=1.0``): the lane's runs of the question by
the package owner, grouped by generation profile (``default`` vs ``frontier``), with the config diff of the two
representative RunRecords (only ``modelId`` may differ), the sha256 of the text each run retrieved, and per run the
total, the agent's golden check (when the question has one), how many claims open a region, status and usage.
Outcomes are read from the runs, never written here; a profile with no runs is an empty group.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any

from boto3.dynamodb.conditions import Key
from evidence_core.ids import short
from evidence_core.models.answer import RunRecord, RunSummary
from evidence_core.models.golden import normalise_amount, normalise_question
from evidence_core.models.inspect import ShowcasePayload, SourceRef
from evidence_core.s3keys import run_key

from . import store
from .auth import Caller
from .deps import Deps
from .http import bad_request, not_found

PAYLOAD_VERSION = 1
MAX_RUNS = 200


def payload_sha(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def question_runs(deps: Deps, package_id: str, question: str, lane: str | None = None) -> tuple[list[RunRecord], bool]:
    """The newest ``MAX_RUNS`` logged runs of ``question`` (normalised) in the package, oldest first (records from
    S3), and whether older runs were left out (the payload says so as ``truncated``)."""
    want = normalise_question(question)
    rows = store.query_all(deps.table("runs"), IndexName="byPackage",
                           KeyConditionExpression=Key("packageId").eq(package_id))
    matching = sorted((s for s in (RunSummary.load(row) for row in rows)
                       if normalise_question(s.question) == want and not (lane and s.pipeline_version != lane)),
                      key=lambda s: (s.created_at, s.run_id))
    out = []
    for s in matching[-MAX_RUNS:]:
        data = store.read_json(deps, s.s3_key or run_key(s.run_id))
        if data is not None:
            out.append(RunRecord.load(data))
    return out, len(matching) > MAX_RUNS


def retrieved_sha(run: RunRecord) -> str:
    return hashlib.sha256("\n\x1e".join(run.retrieved_text).encode()).hexdigest()


def representative(runs: list[RunRecord]) -> RunRecord | None:
    """The first ok run with the most common total (as the run index and the UI pick it)."""
    ok = [r for r in runs if r.status == "ok"]
    if not ok:
        return None
    totals = Counter(normalise_amount(r.answer.total) if r.answer and r.answer.total else None for r in ok)
    modal = totals.most_common(1)[0][0]
    return next(r for r in ok if (normalise_amount(r.answer.total) if r.answer and r.answer.total else None) == modal)


def run_dot(run: RunRecord) -> dict[str, Any]:
    a = run.answer
    return {"runId": run.run_id, "createdAt": run.created_at, "modelId": run.model_id, "status": run.status,
            "error": run.error, "total": a.total if a else None, "answer": a.answer if a else None,
            "check": run.check.dump() if run.check else None, "claims": len(a.claims) if a else 0,
            "resolvableClaims": sum(1 for c in a.claims if c.resolvable) if a else 0,
            "chips": [{"text": c.text, "label": c.label, "resolvable": c.resolvable, "reason": c.reason}
                      for c in (a.claims if a else [])],
            "retrievedSha": retrieved_sha(run), "retrievedUnits": [u.unit_id for u in run.retrieved],
            "usage": run.usage, "latencyMs": run.latency_ms}


def config_diff(a: RunRecord, b: RunRecord) -> dict[str, Any]:
    """The fields a comparison must hold equal, read from two RunRecords: identical keys, the ones that differ, and
    the ones a record never logged (``unverified``: e.g. ``toolSpecSha`` on runs from before it was recorded), which
    are neither identical nor differing."""
    def fields(r: RunRecord) -> dict[str, Any]:
        return {"modelId": r.model_id, "promptSha": r.prompt_sha, "configSha": r.config_sha,
                "toolsEnabled": sorted(r.tools_enabled), "toolSpecSha": r.tool_spec_sha, "corpusHash": r.corpus_hash,
                **{f"blockShas.{k}": v for k, v in sorted(r.block_shas.items())}}

    fa, fb = fields(a), fields(b)
    keys = sorted(set(fa) | set(fb))
    unverified = [k for k in keys if fa.get(k) is None or fb.get(k) is None]
    checked = [k for k in keys if k not in unverified]
    return {"identical": [k for k in checked if fa[k] == fb[k]],
            "differing": [{"key": k, "a": fa[k], "b": fb[k]} for k in checked if fa[k] != fb[k]],
            "unverified": [{"key": k, "a": fa.get(k), "b": fb.get(k)} for k in unverified],
            "a": a.run_id, "b": b.run_id}


def _wrap(showcase_id: str, package_id: str, runs: list[RunRecord], data: dict[str, Any],
          provenance: dict[str, Any], extra_sources: list[SourceRef] | None = None) -> ShowcasePayload:
    sources = [SourceRef(kind="s3", key=run_key(r.run_id)) for r in runs] + (extra_sources or [])
    return ShowcasePayload(showcase_id=showcase_id, version=PAYLOAD_VERSION, mode="live", package_id=package_id,
                           run_ids=[r.run_id for r in runs], payload_sha=payload_sha(data), data=data,
                           sources=sources, provenance=provenance)


# --- sc-10 -----------------------------------------------------------------------------------------------------

def compose_sc10(deps: Deps, caller: Caller, package_id: str, query: dict[str, str]) -> ShowcasePayload:
    question = (query.get("question") or "").strip()
    lane = query.get("lane") or "1.0"
    if not question:
        raise bad_request("question is required (the question as asked)")
    pkg = store.get_package(deps, package_id)
    assert pkg is not None  # the route checked the package ACL
    logged, truncated = question_runs(deps, package_id, question, lane)
    runs = [r for r in logged if r.caller_sub == pkg.owner_sub]
    if not runs:
        raise not_found(f"No logged runs of that question on lane {lane} in {package_id}.")
    groups: dict[str, list[RunRecord]] = {"default": [], "frontier": []}
    for r in runs:
        groups.setdefault(r.generation_profile, []).append(r)
    reps = {p: representative(rs) for p, rs in groups.items()}
    diff = config_diff(reps["default"], reps["frontier"]) if reps.get("default") and reps.get("frontier") else None
    data = {
        "question": question, "lane": lane, "packageId": package_id,
        "profiles": {p: {"modelIds": sorted({r.model_id for r in rs}),
                         "modelKeys": sorted({r.model_key for r in rs if r.model_key}),
                         "n": sum(1 for r in rs if r.status == "ok"), "errors": sum(1 for r in rs if r.status != "ok"),
                         "representativeRunId": reps[p].run_id if reps.get(p) else None,
                         "retrievedShas": sorted({retrieved_sha(r) for r in rs if r.status == "ok"}),
                         "runs": [run_dot(r) for r in rs]} for p, rs in groups.items()},
        "configDiff": diff,
        "sameRetrievedText": bool(reps.get("default") and reps.get("frontier")
                                  and retrieved_sha(reps["default"]) == retrieved_sha(reps["frontier"])),
    }
    return _wrap("sc-10", package_id, runs, data,
                 {"$.data.profiles": "measured", "$.data.configDiff": "measured",
                  "$.data.sameRetrievedText": "derived"})


# --- sc-7 ------------------------------------------------------------------------------------------------------

def search_calls(run: RunRecord) -> list[dict[str, Any]]:
    out = []
    for c in run.tool_calls:
        if c.tool != "search_units":
            continue
        echo = (c.output or {}).get("query") or {}
        out.append({"callId": c.call_id, "status": c.status, "units": len((c.output or {}).get("units") or []),
                    "index": echo.get("index"), "filter": echo.get("filter"), "error": c.error})
    return out


def compose_sc7(deps: Deps, caller: Caller, package_id: str, query: dict[str, str]) -> ShowcasePayload:
    from .routes.access import access_rows

    question = (query.get("question") or "").strip()
    if not question:
        raise bad_request("question is required (the question as asked)")
    pkg = store.get_package(deps, package_id)
    assert pkg is not None
    logged, truncated = question_runs(deps, package_id, question)
    runs = [r for r in logged if r.generation_profile == "default"]
    owner = [r for r in runs if r.caller_sub == pkg.owner_sub]
    others = [r for r in runs if r.caller_sub != pkg.owner_sub]
    if not owner or not others:
        raise not_found("SC-7 needs logged runs of the question by the package owner and by another user.")
    picked: list[RunRecord] = []
    users = []
    for role, rs in (("owner", owner), ("other", others)):
        by_lane: dict[str, RunRecord] = {}
        for pv in sorted({r.pipeline_version for r in rs}):  # the representative run per lane (else its first run)
            lane_runs = [r for r in rs if r.pipeline_version == pv]
            by_lane[pv] = representative(lane_runs) or lane_runs[0]
        picked += by_lane.values()
        users.append({
            "role": role, "callerSub6": short(rs[0].caller_sub), "groups": rs[0].groups,
            "runs": {pv: run_dot(r) | {"search": search_calls(r), "toolCalls": len(r.tool_calls),
                                       "injected": (r.tool_calls[0].injected if r.tool_calls else None)}
                     for pv, r in sorted(by_lane.items())},
            "n": {pv: sum(1 for r in rs if r.pipeline_version == pv) for pv in sorted(by_lane)}})
    cited = next(((c.unit_id, c.field_id) for r in owner if r.answer for c in r.answer.claims if c.resolvable), None)
    data = {"question": question, "packageId": package_id, "users": users,
            "access": {r.run_id: [d.dump() for d in access_rows(deps, r)] for r in picked},
            "regionProbe": {"unitId": cited[0], "fieldId": cited[1]} if cited else None}
    return _wrap("sc-7", package_id, picked, data,
                 {"$.data.users": "measured", "$.data.access": "measured", "$.data.regionProbe": "derived"},
                 [SourceRef(kind="ddb", key=f"rp-access-log/{r.run_id}") for r in picked])

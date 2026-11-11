"""Package rollup (``rp-status-stream``): DocStatus stage + lane records -> ``Package.lanes`` and
``Package.counters`` per pipeline version."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from evidence_core.models.api import Counters, LaneDocCounts, LaneRollup


def compute_rollup(items: list[dict[str, Any]], pipeline_versions: list[str]
                   ) -> tuple[dict[str, LaneRollup], dict[str, Counters]]:
    lanes: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)  # pv -> docId -> lane item
    stages: dict[str, dict[str, dict[str, dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
    for it in items:
        rt = it.get("recordType")
        pv, doc = it.get("pipelineVersion"), it.get("docId")
        if not pv or not doc:
            continue
        if rt == "LANE":
            lanes[pv][doc] = it
        elif rt == "STAGE":
            stages[pv][doc][it.get("stageId")] = it

    rollups: dict[str, LaneRollup] = {}
    counters: dict[str, Counters] = {}
    for pv in pipeline_versions:
        docs = lanes.get(pv, {})
        c = LaneDocCounts(total=len(docs))
        cnt = Counters()
        expected = 0
        for doc_id, lane in docs.items():
            state = lane.get("state", "PENDING")
            key = state.lower()
            if key in ("pending", "running", "succeeded", "failed", "skipped"):
                setattr(c, key, getattr(c, key) + 1)
            st = stages[pv].get(doc_id, {})
            units = _out(st, "units", "units")
            held = _out(st, "pii_gate", "held")
            indexed = _out(st, "index", "indexed")
            triples = _out(st, "graph", "triples")
            cnt.units += units
            cnt.held += held
            cnt.indexed += indexed
            cnt.triples += triples
            if state != "SKIPPED":
                passed = _out(st, "pii_gate", "passed") if "pii_gate" in st and st["pii_gate"].get("output") else None
                expected += passed if passed is not None else max(units - held, 0)
        if c.total == 0:
            state = "EMPTY"
        elif c.running or c.pending:
            state = "RUNNING" if (c.running or c.succeeded or c.failed) else "PENDING"
        elif c.failed and not c.succeeded:
            state = "FAILED"
        elif c.failed:
            state = "PARTIAL"
        else:
            state = "SUCCEEDED"
        active = c.total - c.skipped
        ready = active > 0 and c.succeeded == active and cnt.indexed == expected
        rollups[pv] = LaneRollup(state=state, ready=ready, indexed=cnt.indexed, expected=expected, docs=c)
        counters[pv] = cnt
    return rollups, counters


def _out(stages: dict[str, dict[str, Any]], stage_id: str, key: str) -> int:
    rec = stages.get(stage_id) or {}
    if rec.get("state") != "SUCCEEDED":
        return 0
    return int((rec.get("output") or {}).get(key) or 0)

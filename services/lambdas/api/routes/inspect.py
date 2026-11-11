"""v2 inspection of a logged run's tool calls (SC-4 funnel, SC-5 hop path).

- ``GET /inspect/filters?runId=&callId=``: the ``search_units`` call's logged filter, counted. Three ``_count``
  queries on the lane's index: the scope (the run's package and lane, documents the *caller's* principals may
  read; the lane index is shared by every package, so never ``match_all``), the logged filter, and the logged
  filter with the BM25 sub-query's match clause. ``denseTopN`` and ``bm25TopN`` are the ``subQueryK`` caps applied
  to those counts (``counts.derived`` names them); ``fusedK`` is the logged output. Counts only: no other user's
  ids, text or counts leave the route. They are taken now (``countedAt``), not at the run's time.
  The counterfactual (post-filtering an unfiltered top K) is not built; it is not computed here.
- ``GET /runs/{runId}/calls/{callId}/path``: the nodes and edges a logged ``graph_hop`` call's rows bound
  (``graph_paths``); no graph is queried.

Both apply the package ACL of the run first (``runs.load_run``).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from evidence_core.models.answer import RunRecord, ToolCallRecord
from evidence_core.models.inspect import FilterCounts, FilterInspection, GraphNeighbourhood, SourceRef
from evidence_core.retrieval import bm25_query
from evidence_core.s3keys import run_key

from ..deps import get_deps
from ..graph_paths import path_from_rows
from ..http import Reply, Request, bad_request, not_found, not_ready
from . import INSPECT_FILTERS, RUN_CALL_PATH, router
from .runs import load_run


def _call(run: RunRecord, call_id: str, tool: str) -> ToolCallRecord:
    call = next((c for c in run.tool_calls if c.call_id == call_id), None)
    if call is None:
        raise not_found(f"Run {run.run_id} has no tool call {call_id}.")
    if call.tool != tool:
        raise bad_request(f"Tool call {call_id} is {call.tool}, not {tool}.")
    if call.status != "ok":
        raise bad_request(f"Tool call {call_id} did not succeed ({call.status}); there is nothing to inspect.")
    return call


def scope_query(principals: list[str], package_id: str, pipeline_version: str) -> dict[str, Any]:
    """The funnel's first bar: the run's package and lane, readable by the caller (the lane index is shared)."""
    return {"bool": {"filter": [{"terms": {"allowedPrincipals": list(principals)}},
                                {"term": {"packageId": package_id}}, {"term": {"pipelineVersion": pipeline_version}}]}}


@router.route("GET", INSPECT_FILTERS)
def inspect_filters(req: Request) -> Reply:
    run_id, call_id = req.query.get("runId"), req.query.get("callId")
    if not run_id or not call_id:
        raise bad_request("runId and callId are required")
    deps = get_deps()
    run = load_run(deps, req.caller, run_id)
    call = _call(run, call_id, "search_units")
    echo = call.output.get("query") or {}
    index, flt = echo.get("index"), echo.get("filter")
    if not index or not isinstance(flt, dict):
        raise bad_request(f"Tool call {call_id} logged no query echo (index and filter).")
    text = str(call.input.get("query") or "")
    if deps.aoss is None:
        raise not_ready("No index endpoint is configured for this API (RP_AOSS_ENDPOINT).")
    sub_k = int(echo.get("subQueryK") or 0)
    scope = scope_query(req.caller.principals, run.package_id, run.pipeline_version)
    counted_at = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    try:
        total = deps.aoss.count(index, scope)
        after = deps.aoss.count(index, {"bool": {"filter": [flt]}})
        bm25 = deps.aoss.count(index, bm25_query(text, flt, sub_k)["query"]) if text else 0
    except RuntimeError as exc:
        raise not_ready(f"Could not count {index}: {exc}") from exc
    counts = FilterCounts(index_total=total, after_filter=after, bm25_matches=bm25, dense_top_n=min(sub_k, after),
                          bm25_top_n=min(sub_k, bm25), fused_k=len(call.output.get("units") or []))
    return Reply(FilterInspection(
        run_id=run.run_id, call_id=call_id, package_id=run.package_id, pipeline_version=run.pipeline_version,
        index=index, query_text=text, filter=flt, k=int(echo.get("k") or 0), sub_query_k=sub_k,
        weights={k: float(v) for k, v in (echo.get("weights") or {}).items()},
        normalization=str(echo.get("normalization") or ""), counts=counts,
        counted_by=getattr(deps.aoss, "label", type(deps.aoss).__name__), counted_at=counted_at, scope=scope,
        sources=[SourceRef(kind="s3", key=run_key(run.run_id)),
                 SourceRef(kind="aoss", key=f"{index}/_count", at=counted_at)],
    ))


@router.route("GET", RUN_CALL_PATH)
def run_call_path(req: Request) -> Reply:
    deps = get_deps()
    run = load_run(deps, req.caller, req.param("runId"))
    call = _call(run, req.param("callId"), "graph_hop")
    out = call.output
    path: GraphNeighbourhood = path_from_rows(
        str(out.get("template") or call.input.get("template") or ""), list(out.get("rows") or []),
        list(out.get("graphs") or []), sparql=out.get("sparql"), latency_ms=out.get("latencyMs"),
        run_id=run.run_id, call_id=call.call_id)
    return Reply(path)

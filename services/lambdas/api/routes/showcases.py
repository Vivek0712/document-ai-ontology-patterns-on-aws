"""GET /showcases, GET /showcases/{showcaseId}/payload: server-side showcase composers.

A composer reads only through the store functions the other routes use (after the package ACL) and returns a
``ShowcasePayload``. This iteration serves the composer the browser cannot build from v1 routes:

``sc-3`` (The footnote becomes a property, ``?packageId=&security=``): the trade confirmation whose
``fin:SecurityIdentifier`` field prints ``security``, among the documents this caller's DocAcl allows (others are
skipped, never revealed); its coupon field and the raised marker that links it to its footnote (eu_builder
``markerLinks``); the classify stage's output; and ``graph_hop coupon_terms_v1`` run through ``rp-graph-hop`` with an
API-built injected context (``Deps.invoke_graph_hop``: allow-listed template, checked parameter, the caller's sub and
groups from the authorizer's claims). The step event's properties come from the graph's rows, never from parsing
text here.

``sc-7`` (Identity and scoping) and ``sc-10`` (A bigger model does not fix it) compose logged runs
(``compose_runs.py``). The SC-0/1/6/9 payloads are composed in the browser from v1 routes until their composers move
here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from evidence_core import s3keys
from evidence_core.enums import StageId
from evidence_core.ids import new_run_id
from evidence_core.models.inspect import (
    CouponTermsData,
    MarkerLink,
    ShowcaseCatalog,
    ShowcaseComposer,
    ShowcasePayload,
    SourceRef,
)
from evidence_core.models.units import Unit

from .. import store
from ..acl import can_read_doc, require_package
from ..auth import Caller
from ..compose_runs import compose_sc7, compose_sc10
from ..deps import Deps, check_showcase_template, get_deps
from ..graph_paths import path_from_rows
from ..http import Reply, Request, bad_request, not_found
from ..sources import read_jsonl_ref
from . import SHOWCASE_PAYLOAD, SHOWCASES, router

EVIDENCE_LANE = "2.3.1"
SECURITY_CONCEPT = "fin:SecurityIdentifier"
COUPON_CONCEPT = "fin:CouponRate"
PAYLOAD_VERSION = 1

COMPOSERS: dict[str, ShowcaseComposer] = {
    "sc-3": ShowcaseComposer(
        showcase_id="sc-3", title="The footnote becomes a property", params=["packageId", "security"],
        reads=["Gold units (2.3.1)", "classify stage record", "graph_hop coupon_terms_v1 via rp-graph-hop"]),
    "sc-7": ShowcaseComposer(
        showcase_id="sc-7", title="Identity and scoping", params=["packageId", "question"],
        reads=["rp-runs + run records (owner and other users)", "rp-access-log (or the run records' injected values)",
               "rp-access-log gate#{packageId}: the ask gate's refusals of another user's ask (no run of theirs)"]),
    "sc-10": ShowcaseComposer(
        showcase_id="sc-10", title="A bigger model does not fix it", params=["packageId", "question", "lane"],
        reads=["rp-runs + run records of the lane, by generation profile"]),
}


@router.route("GET", SHOWCASES)
def list_showcases(req: Request) -> Reply:
    return Reply(ShowcaseCatalog(items=list(COMPOSERS.values())))


@router.route("GET", SHOWCASE_PAYLOAD)
def showcase_payload(req: Request) -> Reply:
    showcase_id = req.param("showcaseId")
    compose = _COMPOSE.get(showcase_id)
    if compose is None:
        raise not_found(f"No server-side composer for {showcase_id}; it is composed in the browser.")
    package_id = req.query.get("packageId")
    if not package_id:
        raise bad_request("packageId is required")
    deps = get_deps()
    require_package(deps, req.caller, package_id)
    return Reply(compose(deps, req.caller, package_id, req.query))


def payload_sha(data: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# --- sc-3 ------------------------------------------------------------------------------------------------------

def _terms_unit(deps: Deps, caller: Caller, package_id: str,
                security: str) -> tuple[Any, Unit, SourceRef] | None:
    """(document, unit, receipt) of the 2.3.1 Gold unit whose security identifier field prints ``security``, in a
    document the caller's DocAcl allows."""
    want = security.strip().lower()
    for doc in store.query_documents(deps, package_id):
        if doc.doc_type not in (None, "doc:TradeConfirmation") or not can_read_doc(deps, caller, doc.doc_id):
            continue
        got = read_jsonl_ref(deps, s3keys.gold_units_key(package_id, doc.doc_id, EVIDENCE_LANE))
        if got is None:
            continue
        rows, ref = got
        for raw in rows:
            unit = Unit.load(raw)
            if any(f.concept == SECURITY_CONCEPT and f.value.strip().lower() == want for f in unit.fields):
                return doc, unit, ref
    return None


def compose_sc3(deps: Deps, caller: Caller, package_id: str, query: dict[str, str]) -> ShowcasePayload:
    security = (query.get("security") or "").strip()
    if not security:
        raise bad_request("security is required (a security identifier as printed, e.g. XS-2031)")
    try:
        check_showcase_template("coupon_terms_v1", {"security": security})
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    found = _terms_unit(deps, caller, package_id, security)
    if found is None:
        raise not_found(f"No trade confirmation you can read in {package_id} prints security {security!r} in a "
                        "2.3.1 unit.")
    doc, unit, units_ref = found
    coupon = next((f for f in unit.fields if f.concept == COUPON_CONCEPT), None)
    raw_link = next((lk for lk in unit.provenance.build.get("markerLinks") or []
                     if coupon is not None and lk.get("fieldId") == coupon.field_id), None)
    link = (MarkerLink.from_build(unit.unit_id, raw_link, {m.member_id: m for m in unit.members})
            if raw_link is not None else None)
    classify = next((r for r in store.stage_records(deps, doc.doc_id, EVIDENCE_LANE)
                     if r.stage_id is StageId.CLASSIFY), None)
    run_id = new_run_id()
    graph = deps.invoke_graph_hop("coupon_terms_v1", {"security": security}, caller_sub=caller.sub,
                                  groups=list(caller.groups), pipeline_version=EVIDENCE_LANE,
                                  package_id=package_id, run_id=run_id)
    rows = graph.get("rows") or []
    step = next((r for r in rows if str(r.get("unit", "")).endswith(unit.unit_id)), rows[0] if rows else None)
    parse = "ok" if step else ("no-link" if link is None else "failed")
    data = CouponTermsData(
        security=security, doc_id=doc.doc_id, file_name=doc.file_name, unit_id=unit.unit_id,
        coupon_field=coupon.dump() if coupon else None, marker_link=link,
        classify=({"stageId": classify.stage_id.value, "state": classify.state.value, "engine":
                   classify.engine.value if classify.engine else None, "headline": classify.headline,
                   "output": classify.output, "durationMs": classify.duration_ms} if classify else None),
        graph={k: graph.get(k) for k in ("template", "sparql", "graphs", "columns", "rows", "rowCount", "operands",
                                         "note", "latencyMs", "error")} | {"runId": run_id},
        step_event=step, step_event_parse=parse,
        path=path_from_rows("coupon_terms_v1", rows, list(graph.get("graphs") or []), sparql=graph.get("sparql"),
                            latency_ms=graph.get("latencyMs")) if rows else None,
    ).dump()
    sources = [units_ref, SourceRef(kind="ddb", key=f"rp-doc-status/{doc.doc_id}/STAGE#{EVIDENCE_LANE}#classify"),
               SourceRef(kind="neptune", key=f"graph_hop coupon_terms_v1 · {', '.join(graph.get('graphs') or [])}")]
    return ShowcasePayload(
        showcase_id="sc-3", version=PAYLOAD_VERSION, mode="live", package_id=package_id, run_ids=[],
        payload_sha=payload_sha(data), data=data, sources=sources,
        provenance={"$.data.couponField": "measured", "$.data.classify": "measured", "$.data.graph": "measured",
                    "$.data.stepEvent": "measured", "$.data.markerLink": "derived"},
    )


_COMPOSE: dict[str, Callable[[Deps, Caller, str, dict[str, str]], ShowcasePayload]] = {
    "sc-3": compose_sc3, "sc-7": compose_sc7, "sc-10": compose_sc10}

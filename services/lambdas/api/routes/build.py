"""GET /documents/{docId}/lanes/{pv}/build: the evidence-unit build of one document lane, replayed (SC-2).

Read from the lane's Silver ``build_report.json`` (eu_builder: phase counts, phase B cosine scores against tau,
marker links) and its Silver ``units.jsonl`` (which block became which member of which unit, with its box). Every
block is listed once with the phase that placed it; a phase B block below tau is listed with its best score and the
seed it lost, then the residual unit it joined. No block text is returned: roles, boxes and scores only, so a unit
the PII gate held exposes nothing here that its outline does not.

Lanes without an evidence-unit build (1.0, fixed chunks) answer 404.
"""

from __future__ import annotations

from typing import Any

from evidence_core import s3keys
from evidence_core.models.inspect import (
    BuildAllocation,
    BuildInvariants,
    BuildReplay,
    BuildUnitSummary,
    MarkerLink,
)
from evidence_core.models.units import Unit

from ..acl import require_doc
from ..deps import get_deps
from ..http import Reply, Request, bad_request, not_found
from ..sources import read_json_ref, read_jsonl_ref
from . import DOC_BUILD, router

_PHASES = ("A", "marker", "B", "C")


@router.route("GET", DOC_BUILD)
def get_build(req: Request) -> Reply:
    deps = get_deps()
    doc_id, pv = req.param("docId"), req.param("pv")
    access = require_doc(deps, req.caller, doc_id)
    try:
        lp = deps.pipeline(pv)
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise bad_request(f"unknown pipeline version {pv!r}") from exc
    if lp.config.preprocessing.units == "none":
        raise not_found(f"Lane {pv} builds fixed chunks; it has no evidence-unit build.")
    pkg = access.package_id
    report = read_json_ref(deps, s3keys.build_report_key(pkg, doc_id, pv))
    units = read_jsonl_ref(deps, s3keys.silver_units_key(pkg, doc_id, pv))
    if report is None or units is None:
        raise not_found(f"No evidence-unit build for {doc_id} on lane {pv} yet.")
    return Reply(compose_build(report[0], [Unit.load(u) for u in units[0]], package_id=pkg,
                               sources=[report[1], units[1]]))


def compose_build(report: dict[str, Any], units: list[Unit], *, package_id: str,
                  sources: list[Any]) -> BuildReplay:
    """Join the build report's scored allocations to the members the units actually hold."""
    tau = float(report.get("tau") or 0.0)
    scored = {a["pointer"]: a for a in report.get("allocations", []) if a.get("phase") == "B" and a.get("pointer")}
    allocations: list[BuildAllocation] = []
    links: list[MarkerLink] = []
    for u in units:
        for m in u.members:
            if m.block_ref is None:
                continue
            phase = m.phase if m.phase in _PHASES else "C"
            b = scored.get(m.block_ref.pointer)
            score = m.alloc_score if phase == "B" else (b.get("cosine") if b else None)
            allocations.append(BuildAllocation(
                block_pointer=m.block_ref.pointer, member_id=m.member_id, role=m.role.value,
                page=m.bbox.page if m.bbox else None, bbox=m.bbox, phase=phase, score=score,
                tau=tau if (phase == "B" or b) else None, allocated=phase != "C", unit_id=u.unit_id,
                candidate_unit_id=(b.get("unit") if b else None),
            ))
        by_member = {m.member_id: m for m in u.members}
        links.extend(MarkerLink.from_build(u.unit_id, link, by_member)
                     for link in u.provenance.build.get("markerLinks") or [])
    for a in allocations:  # a marker member says whether it crossed a page break
        if a.phase == "marker":
            a.cross_page = next((lk.cross_page for lk in links
                                 if lk.unit_id == a.unit_id and lk.footnote_member == a.member_id), None)
    summaries = [BuildUnitSummary(unit_id=u.unit_id, unit_type=u.unit_type.value, members=len(u.members),
                                  fields=len(u.fields), pages=u.pages,
                                  merged_tables=int(u.provenance.build.get("mergedTables") or 0)) for u in units]
    inv = report.get("invariants")
    invariants = None
    if isinstance(inv, dict) and "mode" in inv:
        invariants = BuildInvariants.load(inv)
    elif isinstance(inv, dict):  # 2.3.1: I1/I2 deferred to lane 2.4.0
        invariants = BuildInvariants(mode="off", note="; ".join(f"{k}: {v}" for k, v in sorted(inv.items())))
    return BuildReplay(
        doc_id=report.get("docId") or (units[0].doc_id if units else ""), package_id=package_id,
        pipeline_version=report.get("pipelineVersion") or (units[0].pipeline_version if units else ""),
        built_at=report.get("builtAt"), embed_model=str(report.get("embedModel") or "unknown"), tau=tau,
        phase_b=report.get("phaseB"), phase_counts={k: int(v) for k, v in (report.get("phaseCounts") or {}).items()},
        merged_tables=sum(s.merged_tables for s in summaries),
        derived_roles={k: int(v) for k, v in (report.get("derivedRoles") or {}).items()},
        allocations=allocations, marker_links=links, units=summaries, invariants=invariants, sources=sources,
    )

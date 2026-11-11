"""Graph paths from logged ``graph_hop`` rows (SC-5 hop step, SC-3 graph step before ``/graph/neighbourhood``).

A row of an approved template is a solution of that template's WHERE clause, so the triple patterns the template
binds are facts of the graph the run read. ``SHAPES`` lists, per template, the patterns to draw: subject column,
predicate, object column, and the class a column's IRIs were matched as. Only bound values are drawn; no graph is
queried and nothing is inferred beyond what the template matched.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from evidence_core import iris
from evidence_core.models.inspect import GraphEdge, GraphNeighbourhood, GraphNode

TBOX_RP = "urn:rp:graph:tbox:rp"
TBOX_FIBO = "urn:rp:graph:tbox:fibo"
MAX_NODES = 120


@dataclass(frozen=True)
class Shape:
    edges: tuple[tuple[str, str, str], ...]  # (subject column | "=curie", predicate curie, object column | "=curie")
    types: dict[str, str] = field(default_factory=dict)  # column -> class curie the template matched it as
    focus: str = ""  # column of the node the path is about


SHAPES: dict[str, Shape] = {
    "qi_sum_w2_box1_v1": Shape(
        edges=(("=fin:QualifyingIncome", "fin:operandConcept", "=fin:W2WagesBox1"),
               ("field", "fin:concept", "=fin:W2WagesBox1"),
               ("field", "fin:amount", "wagesBox1"),
               ("field", "fin:taxYear", "taxYear"),
               ("field", "fin:borrower", "borrower"),
               ("field", "prov:wasDerivedFrom", "unit")),
        types={"field": "fin:FieldValue", "unit": "doc:EvidenceUnit"}, focus="field"),
    "field_lookup_v1": Shape(
        edges=(("field", "fin:concept", "concept"),
               ("field", "rp:label", "label"),
               ("field", "fin:amount", "amount"),
               ("field", "fin:text", "text"),
               ("field", "fin:period", "period"),
               ("field", "prov:wasDerivedFrom", "unit")),
        types={"field": "fin:FieldValue", "unit": "doc:EvidenceUnit"}, focus="field"),
    "concept_by_label_v1": Shape(
        edges=(("concept", "skos:prefLabel", "prefLabel"),
               ("concept", "rp:docType", "docType")),
        types={"concept": "skos:Concept"}, focus="concept"),
    "unit_lineage_v1": Shape(
        edges=(("unit", "prov:wasDerivedFrom", "region"),
               ("unit", "rp:inDocument", "doc"),
               ("unit", "rp:unitType", "unitType")),
        types={"unit": "doc:EvidenceUnit", "region": "doc:PageRegion"}, focus="unit"),
    "coupon_terms_v1": Shape(
        edges=(("bond", "rp:name", "security"),
               ("bond", "fibo-sec-dbt-dbti:hasInterestPaymentTerms", "terms"),
               ("terms", "fin:hasStepEvent", "event"),
               ("terms", "prov:wasDerivedFrom", "field"),
               ("field", "fin:concept", "concept"),
               ("field", "rp:value", "value"),
               ("field", "prov:wasDerivedFrom", "unit"),
               ("event", "fin:newRate", "newRate"),
               ("event", "fin:effectiveFromYear", "effectiveFromYear"),
               ("event", "fin:triggerRating", "triggerRating"),
               ("event", "prov:wasDerivedFrom", "region")),
        types={"bond": "fibo-sec-dbt-bnd:StepUpBond", "terms": "fibo-sec-dbt-bnd:SteppedCouponTerms",
               "event": "fin:RatingTriggeredStepEvent", "field": "fin:FieldValue", "unit": "doc:EvidenceUnit",
               "region": "doc:PageRegion"},
        focus="bond"),
}


def _is_iri(value: str) -> bool:
    return value.startswith(("urn:", "http://", "https://"))


def _kind(iri: str) -> str:
    if iri.startswith("urn:rp:unit:"):
        return "unit"
    if iri.startswith("urn:rp:field:"):
        return "field"
    if iri.startswith("urn:rp:region:"):
        return "region"
    if iri.startswith("urn:rp:ont:") or iri.startswith("https://spec.edmcouncil.org/"):
        return "class"
    return "individual"


def _graph_of(iri: str, abox: str) -> str:
    if iri.startswith("https://spec.edmcouncil.org/"):
        return TBOX_FIBO
    if iri.startswith("urn:rp:ont:"):
        return TBOX_RP
    return abox


def _label(iri: str) -> str:
    """Short, readable: ``unit … eu-04``, ``field f-box1``, ``fin:CouponRate``, ``security xs-2031``."""
    for prefix, curie in iris.PREFIXES.items():
        if iri.startswith(curie):
            return f"{prefix}:{iri[len(curie):]}"
    if iri.startswith("urn:rp:field:"):
        return f"field {iri.rpartition('/')[2]}"
    if iri.startswith("urn:rp:unit:"):
        return f"unit {iri.rpartition(':')[2]}"
    if iri.startswith("urn:rp:region:"):
        return f"region {iri.removeprefix('urn:rp:region:').rpartition(':')[2]}"
    head, _, tail = iri.removeprefix("urn:rp:").partition(":")
    return f"{head} {tail.rpartition(':')[2]}" if tail else iri


_DOC_HASH = re.compile(r"-[0-9a-f]{6}$")


def _doc_of(iri: str) -> str | None:
    """The document slug of a unit, field or region IRI (``w2-2025-maya-fictus``: the doc id without its hash)."""
    for prefix in ("urn:rp:field:", "urn:rp:unit:", "urn:rp:region:"):
        if iri.startswith(prefix):
            return _DOC_HASH.sub("", iri.removeprefix(prefix).partition(":")[0]) or None
    return None


def _disambiguate(nodes: dict[str, GraphNode]) -> None:
    """Two nodes of one graph never share a label: a unit, field or region label that collides gets its
    document (``field f-box1 · w2-2025-maya-fictus``), so the audience can tell one W-2's operand from the other."""
    by_label: dict[str, list[GraphNode]] = {}
    for n in nodes.values():
        by_label.setdefault(n.label, []).append(n)
    for same in by_label.values():
        if len(same) < 2:
            continue
        for n in same:
            doc = _doc_of(n.iri)
            if doc:
                n.label = f"{n.label} · {doc}"


def path_from_rows(template: str, rows: list[dict[str, Any]], graphs: list[str], *, sparql: str | None = None,
                   latency_ms: int | None = None, run_id: str | None = None,
                   call_id: str | None = None) -> GraphNeighbourhood:
    shape = SHAPES.get(template)
    abox = next((g for g in graphs if g.startswith("urn:rp:graph:abox:")), graphs[-1] if graphs else "")
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    truncated = False

    def node(iri: str, *, kind: str | None = None, label: str | None = None, types: list[str] | None = None) -> str:
        nonlocal truncated
        if iri not in nodes:
            if len(nodes) >= MAX_NODES:
                truncated = True
                return ""
            nodes[iri] = GraphNode(iri=iri, kind=kind or _kind(iri), label=label or _label(iri),  # type: ignore[arg-type]
                                   types=types or [], graph=_graph_of(iri, abox))
        return iri

    def term(row: dict[str, Any], ref: str, subject: str | None, pred: str) -> str:
        if ref.startswith("="):
            return node(iris.expand(ref[1:]))
        value = row.get(ref)
        if value is None or value == "":
            return ""
        value = str(value)
        if _is_iri(value):
            typed = shape.types.get(ref) if shape else None
            return node(value, types=[iris.expand(typed)] if typed else [])
        return node(f"literal:{subject}|{pred}|{value}", kind="literal", label=value)

    focus = ""
    if shape is not None:
        for row in rows:
            for s_ref, pred, o_ref in shape.edges:
                s = term(row, s_ref, None, pred)
                if not s or nodes[s].kind == "literal":
                    continue
                o = term(row, o_ref, s, pred)
                if not o:
                    continue
                p = iris.expand(pred)
                edges.setdefault((s, p, o), GraphEdge(s=s, p=p, o=o, label=pred, graph=nodes[s].graph))
            focus = focus or str(row.get(shape.focus) or "")
    _disambiguate(nodes)
    return GraphNeighbourhood(focus=focus, depth=1, nodes=list(nodes.values()), edges=list(edges.values()),
                              truncated=truncated, template=template, sparql=sparql, latency_ms=latency_ms,
                              source="logged-rows", run_id=run_id, call_id=call_id)

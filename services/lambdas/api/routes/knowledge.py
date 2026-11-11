"""Knowledge routes (W-09 Ontology explorer, W-11 Graph).

- ``GET /ontology/classes?module=`` · ``GET /ontology/terms/{iri}``: the TBox as built (``config/ontology/{ver}/
  tbox.json``, ``ontology/rpont/tbox_export.py``), for the ontology version the evidence-units lane binds. A term's
  ``usedBy`` counts, per package the caller can read, the 2.3.1 fields that carry the concept (from the Gold
  units the caller may read; measured, computed at request time, terms page only).
- ``GET /graph/neighbourhood?iri=&depth=&packageId=&pv=``: the IRI's depth-1 or depth-2 neighbourhood in the TBox
  and the package's ABox for that lane, from ``rp-graph-hop`` running the API-only template ``neighbourhood_v1``
  (allow-listed with an IRI pattern; the caller's identity from the authorizer, never the request). Lane 1.0 binds
  no ontology: it answers 200 with no nodes and says so.
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import unquote

from evidence_core import s3keys
from evidence_core.ids import new_run_id
from evidence_core.iris import abox_graph, expand
from evidence_core.models.inspect import GraphEdge, GraphNeighbourhood, GraphNode
from evidence_core.models.knowledge import OntologyModule, OntologyTerm, OntologyTermList

from .. import store
from ..acl import can_read_doc, require_package
from ..auth import Caller
from ..deps import Deps, check_showcase_template, get_deps
from ..http import Reply, Request, bad_request, not_found
from ..sources import read_jsonl_ref
from . import GRAPH_NEIGHBOURHOOD, ONTOLOGY_CLASSES, ONTOLOGY_TERM, router

EVIDENCE_LANE = "2.3.1"
MAX_NODES = 150
_TBOX_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
TBOX_CACHE_SECONDS = 300.0


def tbox(deps: Deps) -> tuple[str, dict[str, Any]]:
    """(key, tbox.json) for the ontology version 2.3.1 binds (cached 5 minutes per container)."""
    version = deps.pipeline(EVIDENCE_LANE).config.ontology_version
    if not version:
        raise not_found(f"Lane {EVIDENCE_LANE} binds no ontology.")
    key = f"config/ontology/{version}/tbox.json"
    hit = _TBOX_CACHE.get(key)
    if hit and time.monotonic() - hit[0] < TBOX_CACHE_SECONDS:
        return key, hit[1]
    try:
        text, _ = deps.source().read_text(key)
    except Exception as exc:  # noqa: BLE001 - missing file locally or missing object on S3
        raise not_found(f"No TBox export at {key}; run `make ontology-check` and `make seed`.") from exc
    body = json.loads(text)
    _TBOX_CACHE[key] = (time.monotonic(), body)
    return key, body


@router.route("GET", ONTOLOGY_CLASSES)
def ontology_classes(req: Request) -> Reply:
    deps = get_deps()
    key, body = tbox(deps)
    module = req.query.get("module")
    modules = [OntologyModule.load(m) for m in body["modules"]]
    if module and module not in {m.module for m in modules}:
        raise bad_request(f"unknown module {module!r}; one of {', '.join(m.module for m in modules)}")
    items = [OntologyTerm.load(t) for t in body["terms"] if not module or t["module"] == module]
    return Reply(OntologyTermList(ontology_version=body["ontologyVersion"], module=module, modules=modules,
                                  items=items, source=key, sha256=body.get("nquadsSha256")))


def used_by(deps: Deps, caller: Caller, concept: str) -> dict[str, int]:
    """Per package the caller can read: the 2.3.1 Gold fields with this concept, in documents the caller's DocAcl
    allows (measured from the Gold units, at request time)."""
    out: dict[str, int] = {}
    for pkg in store.list_packages_for(deps, caller.principals):
        if EVIDENCE_LANE not in pkg.pipeline_versions:
            continue
        n = 0
        for doc in store.query_documents(deps, pkg.package_id):
            if not can_read_doc(deps, caller, doc.doc_id):
                continue
            got = read_jsonl_ref(deps, s3keys.gold_units_key(pkg.package_id, doc.doc_id, EVIDENCE_LANE))
            rows = got[0] if got else []
            n += sum(1 for u in rows for f in u.get("fields") or [] if f.get("concept") == concept)
        if n:
            out[pkg.package_id] = n
    return out


@router.route("GET", ONTOLOGY_TERM)
def ontology_term(req: Request) -> Reply:
    deps = get_deps()
    _key, body = tbox(deps)
    want = unquote(req.param("iri"))
    try:
        iri = expand(want)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    term = next((t for t in body["terms"] if t["iri"] == iri or t["curie"] == want), None)
    if term is None:
        raise not_found(f"No term {want} in ontology {body['ontologyVersion']}.")
    out = OntologyTerm.load(term)
    return Reply(out.model_copy(update={"used_by": used_by(deps, req.caller, out.curie)}))


ABOX_KINDS = (("urn:rp:unit:", "unit"), ("urn:rp:field:", "field"), ("urn:rp:region:", "region"))


def node_kind(iri: str, tbox_kinds: dict[str, str], literal: bool) -> str:
    """GraphNode kind: a literal, an ABox unit/field/region by its IRI scheme, a TBox class or concept by the TBox
    export, else an individual."""
    if literal:
        return "literal"
    for prefix, kind in ABOX_KINDS:
        if iri.startswith(prefix):
            return kind
    return "class" if tbox_kinds.get(iri) in ("class", "concept") else "individual"


def short_label(iri: str) -> str:
    return iri.rsplit("#", 1)[-1].rsplit("/", 1)[-1].rsplit(":", 1)[-1] or iri


def neighbourhood_from_rows(focus: str, depth: int, rows: list[dict[str, Any]], *, tbox_kinds: dict[str, str],
                            graphs: list[str], abox: str | None, sparql: str | None,
                            latency_ms: int | None) -> GraphNeighbourhood:
    """``neighbourhood_v1`` rows ``{s, p, o, sLabel?, oLabel?, pLabel?, oIsLiteral, hop}`` -> nodes and edges, one
    edge per (s, p, o) (label variants collapse), capped at ``MAX_NODES`` nodes (``truncated``). A node's graph is
    the ABox for unit/field/region IRIs and literals hanging off them, else the TBox."""
    nodes: dict[str, GraphNode] = {}
    edges: dict[tuple[str, str, str], GraphEdge] = {}
    truncated = False
    tbox = ", ".join(g for g in graphs if g != abox)

    def graph_of(iri: str) -> str:
        return abox or "" if iri.startswith(tuple(p for p, _ in ABOX_KINDS)) else tbox

    def add(iri: str, label: Any, literal: bool, owner: str) -> str | None:
        key = f"literal:{owner}:{iri}" if literal else iri
        if key in nodes:
            return key
        if len(nodes) >= MAX_NODES:
            return None
        nodes[key] = GraphNode(iri=key, kind=node_kind(iri, tbox_kinds, literal),  # type: ignore[arg-type]
                               label=str(iri if literal else (label or short_label(iri)))[:120],
                               graph=graph_of(owner if literal else iri))
        return key

    add(focus, None, False, focus)
    for r in rows:
        s, p, o = str(r["s"]), str(r["p"]), str(r["o"])
        literal = str(r.get("oIsLiteral")).lower() in ("true", "1")
        sk, ok = add(s, r.get("sLabel"), False, s), add(o, r.get("oLabel"), literal, s)
        if sk is None or ok is None:
            truncated = True
            continue
        edges.setdefault((sk, p, ok), GraphEdge(s=sk, p=p, o=ok, label=str(r.get("pLabel") or short_label(p)),
                                                graph=graph_of(s)))
    return GraphNeighbourhood(focus=focus, depth=depth, nodes=list(nodes.values()), edges=list(edges.values()),
                              truncated=truncated, template="neighbourhood_v1", sparql=sparql, latency_ms=latency_ms,
                              source="neptune")


@router.route("GET", GRAPH_NEIGHBOURHOOD)
def graph_neighbourhood(req: Request) -> Reply:
    raw_iri, package_id = req.query.get("iri"), req.query.get("packageId")
    pv = req.query.get("pv") or EVIDENCE_LANE
    if not raw_iri or not package_id:
        raise bad_request("iri and packageId are required")
    depth = req.int_query("depth", 1, lo=1, hi=2)
    deps = get_deps()
    pkg = require_package(deps, req.caller, package_id)
    if pv not in pkg.pipeline_versions:
        raise bad_request(f"{package_id} has no lane {pv}")
    try:
        iri = expand(raw_iri)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    params = {"iri": iri} | ({"depth": "2"} if depth == 2 else {})
    try:
        check_showcase_template("neighbourhood_v1", params)
    except ValueError as exc:
        raise bad_request(str(exc)) from exc
    if not deps.pipeline(pv).config.graph_enabled:
        return Reply(GraphNeighbourhood(focus=iri, depth=depth, nodes=[], edges=[], truncated=False,
                                        template="neighbourhood_v1", source="neptune",
                                        sparql=f"# lane {pv} binds no ontology: there is no graph to query"))
    out = deps.invoke_graph_hop("neighbourhood_v1", params, caller_sub=req.caller.sub, groups=list(req.caller.groups),
                                pipeline_version=pv, package_id=pkg.package_id, run_id=new_run_id())
    if out.get("error"):
        err = out["error"]
        raise bad_request(f"graph: {err.get('message') if isinstance(err, dict) else err}")
    _key, body = tbox(deps)
    kinds = {t["iri"]: t["kind"] for t in body["terms"]}
    return Reply(neighbourhood_from_rows(iri, depth, list(out.get("rows") or []), tbox_kinds=kinds,
                                         graphs=list(out.get("graphs") or []), abox=abox_graph(pkg.package_id, pv),
                                         sparql=out.get("sparql"), latency_ms=out.get("latencyMs")))

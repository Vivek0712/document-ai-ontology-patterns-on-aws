"""rp-graph-admin (VPC, SigV4): graph housekeeping the laptop cannot do directly.

Ops (event ``{"op": ...}``):
- ``drop_package {packageId}``: DROP the ABox graph of every pipeline version (DELETE /packages, reset.sh);
- ``drop_graph {graph}``: DROP one ``urn:rp:graph:`` graph;
- ``counts``: triples per named graph (seed/smoke checks).
"""

from __future__ import annotations

import os
from typing import Any

from evidence_core import PIPELINE_VERSIONS
from evidence_core.ids import PACKAGE_ID_RE
from evidence_core.iris import abox_graph
from evidence_core.logs import get_logger
from evidence_core.sparql import SparqlClient, bindings_to_rows

log = get_logger("rp.graph_admin")
_CLIENT: Any = None

GRAPH_PREFIX = "urn:rp:graph:"


def client() -> Any:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SparqlClient(endpoint=os.environ["RP_NEPTUNE_ENDPOINT"],
                               region=os.environ.get("AWS_REGION", "us-east-1"), timeout=120)
    return _CLIENT


def use_client(c: Any) -> None:
    global _CLIENT
    _CLIENT = c


def counts(sparql: Any) -> dict[str, int]:
    res = sparql.query("SELECT ?g (COUNT(*) AS ?n) WHERE { GRAPH ?g { ?s ?p ?o } } GROUP BY ?g ORDER BY ?g")
    _, rows = bindings_to_rows(res)
    return {r["g"]: int(r["n"]) for r in rows}


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    op = event.get("op")
    sparql = client()
    if op == "drop_package":
        package_id = event.get("packageId", "")
        if not PACKAGE_ID_RE.match(package_id):
            raise ValueError(f"invalid packageId {package_id!r}")
        before = counts(sparql)
        graphs = [abox_graph(package_id, pv) for pv in PIPELINE_VERSIONS]
        for g in graphs:
            sparql.update(f"DROP SILENT GRAPH <{g}>")
        dropped = {g: before.get(g, 0) for g in graphs}
        log.info("package graphs dropped", extra={"rp": {"packageId": package_id, "dropped": dropped}})
        return {"op": op, "packageId": package_id, "graphs": len(graphs), "triples": sum(dropped.values()),
                "dropped": dropped}
    if op == "drop_graph":
        graph = event.get("graph", "")
        if not graph.startswith(GRAPH_PREFIX) or any(c in graph for c in "<> {}\"\n"):
            raise ValueError(f"refusing to drop {graph!r}")
        sparql.update(f"DROP SILENT GRAPH <{graph}>")
        return {"op": op, "graph": graph}
    if op == "counts":
        return {"op": op, "graphs": counts(sparql)}
    raise ValueError(f"unknown op {op!r}; expected drop_package, drop_graph or counts")

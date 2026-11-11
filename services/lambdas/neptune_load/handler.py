"""rp-neptune-load (VPC, SigV4): load the TBox named graphs for one ontology version.

Event: ``{"ontologyVersion": "1.2.0", "graphs": ["fibo", "doco", "rp"], "replace": true}``
(``graphs`` defaults to all three). Reads ``config/ontology/{ver}/nquads/{graph}.nq`` from
the data bucket, drops the target graph when ``replace`` (one ontology version is loaded at
a time), inserts in batches, then counts the triples per graph. The laptop cannot reach
Neptune, so ``scripts/seed.sh`` invokes this function.
"""

from __future__ import annotations

import os
from typing import Any

from evidence_core import s3keys
from evidence_core.iris import TBOX_GRAPHS
from evidence_core.logs import get_logger
from evidence_core.sparql import SparqlClient

from lambdas.neptune_load.nquads import insert_batches, parse
from lambdas.tools._lib.stores import stores

log = get_logger("rp.neptune_load")
_CLIENT: Any = None


def client() -> Any:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SparqlClient(endpoint=os.environ["RP_NEPTUNE_ENDPOINT"],
                               region=os.environ.get("AWS_REGION", "us-east-1"), timeout=120)
    return _CLIENT


def use_client(c: Any) -> None:
    global _CLIENT
    _CLIENT = c


def count_triples(sparql: Any, graph: str) -> int:
    res = sparql.query(f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{graph}> {{ ?s ?p ?o }} }}")
    rows = res.get("results", {}).get("bindings", [])
    return int(rows[0]["n"]["value"]) if rows else 0


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    version = event.get("ontologyVersion")
    if not version:
        raise ValueError("ontologyVersion is required")
    names = event.get("graphs") or list(TBOX_GRAPHS)
    unknown = [g for g in names if g not in TBOX_GRAPHS]
    if unknown:
        raise ValueError(f"unknown TBox graphs {unknown}; known: {list(TBOX_GRAPHS)}")
    replace = bool(event.get("replace", True))
    sparql = client()
    st = stores()
    report: dict[str, Any] = {"ontologyVersion": version, "graphs": {}}
    for name in names:
        graph = TBOX_GRAPHS[name]
        key = s3keys.tbox_nquads_key(version, name)
        text = st.read_text(key)
        if text is None:
            raise FileNotFoundError(f"s3://{st.bucket}/{key} not found; run make ontology-check and deploy config")
        if replace:
            sparql.update(f"DROP SILENT GRAPH <{graph}>")
        inserted = 0
        for update, n in insert_batches(parse(text), default_graph=graph):
            sparql.update(update)
            inserted += n
        total = count_triples(sparql, graph)
        report["graphs"][name] = {"graph": graph, "key": key, "inserted": inserted, "triples": total}
        log.info("graph loaded", extra={"rp": report["graphs"][name]})
    return report

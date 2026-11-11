"""Local stand-in for Amazon Neptune (pyoxigraph) and the end-to-end graph check of ``make units --until ready``.

``OxiSparql`` has the ``query``/``update`` surface of ``evidence_core.sparql.SparqlClient``, so the real
``graph_write`` SPARQL UPDATEs (delete previous, insert batches) run against a real SPARQL 1.1 engine, and the
real ``graph_hop`` tool Lambda (ACL check, approved template from ``ontology/dist``, FROM clause, total and
operands) answers the headline question over the ABox the pipeline wrote. Labelled as NOT Neptune everywhere.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from evidence_core import iris

NOT_NEPTUNE = "pyoxigraph in-process (NOT Amazon Neptune)"


class OxiSparql:
    """pyoxigraph-backed SparqlClient (SPARQL JSON results), dev/test only."""

    def __init__(self) -> None:
        import pyoxigraph as ox

        self._ox = ox
        self.store = ox.Store()
        self.updates = 0
        self.queries = 0

    @classmethod
    def available(cls) -> bool:
        try:
            import pyoxigraph  # noqa: F401
        except ImportError:
            return False
        return True

    def load_nquads(self, text: str) -> None:
        self.store.load(text.encode("utf-8"), format=self._ox.RdfFormat.N_QUADS)

    def query(self, sparql: str) -> dict[str, Any]:
        self.queries += 1
        res = self.store.query(sparql)
        return json.loads(res.serialize(format=self._ox.QueryResultsFormat.JSON))

    def update(self, sparql: str) -> None:
        self.updates += 1
        try:
            self.store.update(sparql)
        except SyntaxError as exc:  # show the offending line, as Neptune's error would
            m = re.match(r"error at (\d+):(\d+)", str(exc))
            line = sparql.splitlines()[int(m.group(1)) - 1] if m else ""
            raise SyntaxError(f"{str(exc).splitlines()[0][:200]} in: {line[:300]}") from exc

    def graph_quads(self, graph: str) -> int:
        return len(list(self.store.quads_for_pattern(None, None, None, self._ox.NamedNode(graph))))


def load_tbox(client: OxiSparql, repo_root: Path, ontology_version: str) -> list[str]:
    """Load the built TBox N-Quads (``make ontology-check``); returns the files loaded."""
    nq_dir = repo_root / "ontology" / "dist" / ontology_version / "nquads"
    loaded = []
    for name in ("fibo", "doco", "rp"):
        path = nq_dir / f"{name}.nq"
        if path.exists():
            client.load_nquads(path.read_text(encoding="utf-8"))
            loaded.append(str(path.relative_to(repo_root)))
    return loaded


def ask_graph(client: OxiSparql, *, package_id: str, pipeline_version: str, caller_sub: str, groups: list[str],
              template: str = "qi_sum_w2_box1_v1", params: dict[str, str] | None = None) -> dict[str, Any]:
    """Invoke the real ``rp-graph-hop`` handler as the Gateway would (identity injected)."""
    from lambdas.tools._lib import stores as stores_mod
    from lambdas.tools.graph_hop import handler as graph_hop

    graph_hop.use_client(client)
    stores_mod.use_stores(None)
    try:
        return graph_hop.handler({"template": template, "params": params or {}, "callerSub": caller_sub,
                                  "groups": groups, "pipelineVersion": pipeline_version, "packageId": package_id,
                                  "runId": "r-000000local0"})
    finally:
        graph_hop.use_client(None)
        stores_mod.use_stores(None)


def write_graph_check(client: OxiSparql, out: Path, *, package_id: str, owner_sub: str, groups: list[str],
                      tbox_files: list[str]) -> str:
    """Writes {out}/graph/abox.nq and qi_sum.json; returns a one-paragraph summary."""
    dest = out / "graph"
    dest.mkdir(parents=True, exist_ok=True)
    abox = iris.abox_graph(package_id, "2.3.1")
    ox = client._ox
    lines = sorted(f"{q} .\n" for q in client.store.quads_for_pattern(None, None, None, ox.NamedNode(abox)))
    (dest / "abox.nq").write_text("".join(lines), encoding="utf-8")
    results = {pv: ask_graph(client, package_id=package_id, pipeline_version=pv, caller_sub=owner_sub,
                             groups=groups) for pv in ("2.3.1", "1.0")}
    (dest / "qi_sum.json").write_text(json.dumps({"engine": NOT_NEPTUNE, "tbox": tbox_files,
                                                  "aboxQuads": len(lines), "results": results}, indent=1))
    r = results["2.3.1"]
    if "error" in r:
        return f"graph check ({NOT_NEPTUNE}): graph_hop error {r['error']}"
    ops = ", ".join(f"{o['label']} {o['value']} ({o['unitId']}#{o['fieldId']})" for o in r.get("operands", []))
    if not ops:  # the check template sums W-2 Box 1 wages: only the lending packages have operands
        return (f"graph check ({NOT_NEPTUNE}): {len(lines)} ABox quads in {abox}; graph_hop qi_sum_w2_box1_v1 "
                "skipped: this package has no W-2 Box 1 fields (the check template is lending-specific; expected "
                "for other use cases)")
    return (f"graph check ({NOT_NEPTUNE}): {len(lines)} ABox quads in {abox}; graph_hop qi_sum_w2_box1_v1 on "
            f"2.3.1 -> total {r.get('total')} = {ops or 'no operands'}; on 1.0 -> {results['1.0'].get('note')}")

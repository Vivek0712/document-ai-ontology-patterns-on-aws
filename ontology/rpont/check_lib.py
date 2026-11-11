"""Run the SPARQL templates on pyoxigraph over the built TBox plus a reference ABox.

The same rendered text graph_hop sends to Neptune runs here (``templates.render``), so a template that
passes ``make ontology-check`` is syntactically and semantically the one the agent uses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from evidence_core import iris
from pyoxigraph import Literal, NamedNode, RdfFormat, Store

from .abox import package_abox
from .templates import Template, load_templates, render


@dataclass
class QueryResult:
    template: str
    sparql: str
    graphs: list[str]
    columns: list[str]
    rows: list[dict[str, str]]
    total: str | None = None
    operands: list[dict[str, str]] = field(default_factory=list)


def tbox_store(dist_dir: Path) -> Store:
    store = Store()
    for key in iris.TBOX_GRAPHS:
        store.load(path=str(dist_dir / "nquads" / f"{key}.nq"), format=RdfFormat.N_QUADS)
    return store


def add_package(store: Store, gold: dict[str, Any], pipeline_version: str = "2.3.1") -> str:
    """Load the reference ABox for a package lane; returns its graph IRI."""
    store.extend(package_abox(gold, pipeline_version))
    return iris.abox_graph(gold["packageId"], pipeline_version)


def run_template(store: Store, template: Template, graphs: list[str], params: dict[str, str] | None = None
                 ) -> QueryResult:
    sparql = render(template, graphs, params)
    solutions = store.query(sparql)
    columns = [v.value for v in solutions.variables]
    rows: list[dict[str, str]] = []
    for sol in solutions:
        row = {}
        for c in columns:
            term = sol[c]
            if term is not None:
                row[c] = term.value if isinstance(term, Literal | NamedNode) else str(term)
        rows.append(row)
    result = QueryResult(template.template_id, sparql, graphs, columns, rows)
    if template.template_id == "qi_sum_w2_box1_v1":
        result.total = f"{sum((Decimal(r['wagesBox1']) for r in rows), Decimal('0')):.2f}" if rows else None
        result.operands = []
        for r in rows:
            unit_id, field_id = iris.field_ref_from_iri(r["field"])
            result.operands.append({"unitId": unit_id, "fieldId": field_id, "value": r["wagesBox1"],
                                    "concept": "fin:W2WagesBox1", "label": r["borrower"]})
    return result


def tbox_graphs() -> list[str]:
    return list(iris.TBOX_GRAPHS.values())


def load_gold(package_dir: Path) -> dict[str, Any]:
    return json.loads((package_dir / "gold.json").read_text(encoding="utf-8"))


def templates_for(dist_dir: Path) -> dict[str, Template]:
    return load_templates(dist_dir / "templates")

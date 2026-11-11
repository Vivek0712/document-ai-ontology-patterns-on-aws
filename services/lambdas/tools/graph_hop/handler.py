"""graph_hop@1: approved SPARQL templates over Neptune (SigV4, IAM auth, read-only role).

The model picks a template id and string parameters; graph_hop renders them as escaped
literals / validated IRIs (``_lib.templates``). ``FROM`` = the version's TBox graphs plus the
package ABox graph, chosen only after the package ACL check. Pipeline versions without an
ontology (1.0) have no graph: the tool answers with 0 rows and a note, without a query.
"""

from __future__ import annotations

import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol

from evidence_core import s3keys
from evidence_core.config import default_source
from evidence_core.iris import TBOX_GRAPHS, abox_graph, field_ref_from_iri
from evidence_core.models.tools import GraphHopArgs, GraphHopResult, GraphOperand
from evidence_core.sparql import SparqlClient, bindings_to_rows

from lambdas.tools._lib.errors import BadRequest, NotReady
from lambdas.tools._lib.runtime import ToolCall, tool_handler
from lambdas.tools._lib.stores import pipeline
from lambdas.tools._lib.templates import render

VALUE_COLUMNS = ("wagesBox1", "amount", "value")
TOTAL_TEMPLATES = {"qi_sum_w2_box1_v1": "wagesBox1"}


class QueryClient(Protocol):
    def query(self, sparql: str) -> dict[str, Any]: ...


_SPARQL: QueryClient | None = None
_TEMPLATE_CACHE: dict[tuple[str, str], str] = {}


def sparql_client() -> QueryClient:
    global _SPARQL
    if _SPARQL is None:
        endpoint = os.environ.get("RP_NEPTUNE_ENDPOINT")
        if not endpoint:
            raise NotReady("RP_NEPTUNE_ENDPOINT is not configured")
        _SPARQL = SparqlClient(endpoint=endpoint, region=os.environ.get("AWS_REGION", "us-east-1"))
    return _SPARQL


def use_client(client: QueryClient | None) -> None:
    global _SPARQL
    _SPARQL = client
    _TEMPLATE_CACHE.clear()


def load_template(ontology_version: str, template_id: str) -> str:
    key = (ontology_version, template_id)
    if key not in _TEMPLATE_CACHE:
        text, _ = default_source().read_text(s3keys.template_key(ontology_version, template_id))
        _TEMPLATE_CACHE[key] = text
    return _TEMPLATE_CACHE[key]


def _decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def operands_from_rows(rows: list[dict[str, Any]]) -> list[GraphOperand]:
    out: list[GraphOperand] = []
    for row in rows:
        iri = row.get("field")
        if not isinstance(iri, str) or not iri.startswith("urn:rp:field:"):
            continue
        unit_id, field_id = field_ref_from_iri(iri)
        raw = next((str(row[c]) for c in VALUE_COLUMNS if c in row), "")
        amount = _decimal(raw) if raw else None
        value = f"{amount:.2f}" if amount is not None else raw  # xsd:decimal comes back canonical ("98400")
        out.append(GraphOperand(unit_id=unit_id, field_id=field_id, value=value,
                                concept=row.get("concept"), label=row.get("borrower") or row.get("label")))
    return out


def compute_total(template: str, rows: list[dict[str, Any]]) -> str | None:
    col = TOTAL_TEMPLATES.get(template)
    if col is None:
        return None
    values = [_decimal(r.get(col)) for r in rows if col in r]
    if not values or any(v is None for v in values):
        return None
    return f"{sum(values, Decimal(0)):.2f}"  # type: ignore[arg-type]


@tool_handler("graph_hop")
def graph_hop(args: GraphHopArgs, call: ToolCall) -> GraphHopResult:
    t0 = time.monotonic()
    call.stores.require_package(call.ctx.package_id, call.principals)
    lp = pipeline(call.ctx.pipeline_version)
    onto = lp.config.preprocessing.ontology
    if onto == "none":
        return GraphHopResult(
            template=args.template, sparql="", graphs=[], columns=[], rows=[], row_count=0,
            note=f"no ABox for pipelineVersion {call.ctx.pipeline_version} (ontology: none)",
            latency_ms=int((time.monotonic() - t0) * 1000),
        )
    unknown = [g for g in onto.graphs if g not in TBOX_GRAPHS]
    if unknown:
        raise BadRequest(f"config names unknown TBox graphs {unknown}")
    graphs = [TBOX_GRAPHS[g] for g in onto.graphs] + [abox_graph(call.ctx.package_id, call.ctx.pipeline_version)]
    sparql = render(args.template, load_template(onto.version, args.template), graphs, dict(args.params))
    columns, rows = bindings_to_rows(sparql_client().query(sparql))
    total = compute_total(args.template, rows)
    return GraphHopResult(
        template=args.template,
        sparql=sparql,
        graphs=graphs,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        total=total,
        operands=operands_from_rows(rows),
        note=None if rows else "the template matched no facts in this package",
        latency_ms=int((time.monotonic() - t0) * 1000),
    )


handler = graph_hop

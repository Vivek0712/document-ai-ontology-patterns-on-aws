from __future__ import annotations

from decimal import Decimal

import pytest
from evidence_core.iris import abox_graph
from evidence_core.models.tools import GraphHopResult
from lambdas.tools._lib.errors import BadRequest
from lambdas.tools._lib.templates import render
from lambdas.tools._lib.testing import USER_B_SUB, injected
from lambdas.tools.graph_hop import handler as mod
from lambdas.tools.tests.oxi import OxiSparql

FIN = "urn:rp:ont:fin:"
RP = "urn:rp:ont:rp:"
XSD = "http://www.w3.org/2001/XMLSchema#"

QI_SUM = """PREFIX fin: <urn:rp:ont:fin:>
PREFIX rp: <urn:rp:ont:rp:>
SELECT ?borrower ?taxYear ?wagesBox1 ?unit ?field
{{FROM}}
WHERE {
  ?field fin:concept fin:W2WagesBox1 ; fin:amount ?wagesBox1 ; fin:taxYear ?taxYear ;
         fin:borrower ?p ; <http://www.w3.org/ns/prov#wasDerivedFrom> ?unit .
  ?p rp:name ?borrower .
  ?unit a <urn:rp:ont:doc:EvidenceUnit> .
  {{#taxYear}}FILTER(?taxYear = {{taxYear:year}}){{/taxYear}}
} ORDER BY ?borrower
"""

FIELD_LOOKUP = """SELECT ?field ?amount
{{FROM}}
WHERE { ?field fin:concept {{concept:iri}} ; fin:amount ?amount .
  {{#borrower}}?field fin:borrower ?p . ?p rp:name {{borrower}} .{{/borrower}} }
"""


def _facts(pkg: str, pv: str) -> str:
    g = f"<{abox_graph(pkg, pv)}>"
    lines = []
    for name, uid, amount in [("Maya Fictus", "w2-maya-aaaaaa:2.3.1:eu-01", "98400.00"),
                              ("Leo Example", "w2-leo-bbbbbb:2.3.1:eu-01", "71250.00")]:
        f = f"<urn:rp:field:{uid}/f-box1>"
        u = f"<urn:rp:unit:{uid}>"
        p = f"<urn:rp:party:{name.lower().replace(' ', '-')}>"
        lines += [
            f"{f} <{FIN}concept> <{FIN}W2WagesBox1> {g} .",
            f'{f} <{FIN}amount> "{amount}"^^<{XSD}decimal> {g} .',
            f'{f} <{FIN}taxYear> "2025"^^<{XSD}gYear> {g} .',
            f"{f} <{FIN}borrower> {p} {g} .",
            f"{f} <http://www.w3.org/ns/prov#wasDerivedFrom> {u} {g} .",
            f"{u} <http://www.w3.org/1999/02/22-rdf-syntax-ns#type> <urn:rp:ont:doc:EvidenceUnit> {g} .",
            f'{p} <{RP}name> "{name}" {g} .',
        ]
    # a fact in another package's graph must never be visible
    lines.append(f'<urn:rp:field:x:2.3.1:eu-01/f-box1> <{FIN}concept> <{FIN}W2WagesBox1> '
                 f'<{abox_graph("pkg-other", "2.3.1")}> .')
    return "\n".join(lines) + "\n"


@pytest.fixture
def oxi(world, monkeypatch):
    client = OxiSparql()
    client.load_nquads(_facts("pkg-lending-01", "2.3.1"))
    mod.use_client(client)
    templates = {"qi_sum_w2_box1_v1": QI_SUM, "field_lookup_v1": FIELD_LOOKUP}
    monkeypatch.setattr(mod, "load_template", lambda ver, tid: templates[tid])
    yield client
    mod.use_client(None)


def test_qi_sum_total_and_operands(oxi):
    res = GraphHopResult.load(mod.handler({"template": "qi_sum_w2_box1_v1", "params": {}, **injected("2.3.1")}))
    assert res.row_count == 2 and res.total == "169650.00"
    assert res.columns == ["borrower", "taxYear", "wagesBox1", "unit", "field"]
    assert {(o.unit_id, o.field_id, o.value, o.label) for o in res.operands} == {
        ("w2-maya-aaaaaa:2.3.1:eu-01", "f-box1", "98400.00", "Maya Fictus"),
        ("w2-leo-bbbbbb:2.3.1:eu-01", "f-box1", "71250.00", "Leo Example"),
    }
    assert res.graphs[-1] == abox_graph("pkg-lending-01", "2.3.1")
    assert "urn:rp:graph:abox:pkg-other" not in res.sparql
    assert res.graphs[:3] == ["urn:rp:graph:tbox:fibo", "urn:rp:graph:tbox:doco", "urn:rp:graph:tbox:rp"]


def test_optional_section_and_year(oxi):
    res = GraphHopResult.load(mod.handler({"template": "qi_sum_w2_box1_v1", "params": {"taxYear": "2024"},
                                           **injected("2.3.1")}))
    assert res.row_count == 0 and res.total is None and res.note
    assert 'FILTER(?taxYear = "2024"^^<http://www.w3.org/2001/XMLSchema#gYear>)' in res.sparql


def test_param_is_a_literal_not_code(oxi):
    evil = 'Maya Fictus" } } DROP ALL ; SELECT * { ?s ?p "'
    res = GraphHopResult.load(mod.handler({"template": "field_lookup_v1",
                                           "params": {"concept": "fin:W2WagesBox1", "borrower": evil},
                                           **injected("2.3.1")}))
    assert res.row_count == 0
    assert oxi.updates == []
    assert '\\"' in res.sparql  # quote escaped inside the literal
    ok = GraphHopResult.load(mod.handler({"template": "field_lookup_v1",
                                          "params": {"concept": "fin:W2WagesBox1", "borrower": "Maya Fictus"},
                                          **injected("2.3.1")}))
    assert ok.row_count == 1 and ok.rows[0]["amount"] == "98400" and ok.operands[0].value == "98400.00"


def test_v1_has_no_graph(oxi):
    res = GraphHopResult.load(mod.handler({"template": "qi_sum_w2_box1_v1", **injected("1.0")}))
    assert res.row_count == 0 and res.note.startswith("no ABox for pipelineVersion 1.0")
    assert oxi.queries == []


def test_denied_and_validation(oxi):
    out = mod.handler({"template": "qi_sum_w2_box1_v1", **injected(sub=USER_B_SUB, groups=["markets-analyst"])})
    assert out["error"]["code"] == "forbidden" and oxi.queries == []
    assert mod.handler({"template": "free_sparql", **injected()})["error"]["code"] == "bad_request"
    out = mod.handler({"template": "field_lookup_v1", "params": {"concept": "<urn:x> } DROP ALL"}, **injected()})
    assert out["error"]["code"] == "bad_request"
    out = mod.handler({"template": "field_lookup_v1", "params": {"concept": "fin:X", "nope": "1"}, **injected()})
    assert out["error"]["code"] == "bad_request"


def test_render_rules():
    with pytest.raises(BadRequest):
        render("unit_lineage_v1", "SELECT * {{FROM}} WHERE { {{unitId:unit}} ?p ?o }", ["urn:g"],
               {"unitId": "not a unit id"})
    q = render("unit_lineage_v1", "SELECT * {{FROM}} WHERE { {{unitId:unit}} ?p ?o }", ["urn:g"],
               {"unitId": "w2-x-aaaaaa:2.3.1:eu-01"})
    assert "<urn:rp:unit:w2-x-aaaaaa:2.3.1:eu-01>" in q and "FROM <urn:g>" in q and q.startswith("PREFIX")
    with pytest.raises(BadRequest):
        render("concept_by_label_v1", "SELECT * WHERE { ?s ?p {{label}} }", ["urn:g"], {"label": "x"})


def test_coupon_terms_v1_reads_the_step_event_graph_write_wrote(world, monkeypatch):
    """The real template (ontology/templates) over the ABox ``ingest_lib.rdf`` writes for a marker-linked coupon."""
    from evidence_core.iris import abox_graph as graph_of
    from lambdas.ingest_lib.tests.test_markers_step_events import _abox, _terms_unit
    from lambdas.tools.tests.conftest import REPO

    unit = _terms_unit().model_copy(update={"package_id": "pkg-lending-01"})
    w, _ = _abox(unit)
    client = OxiSparql()
    # the ABox as graph_write writes it, into this test's package graph
    client.load_nquads(w.nquads().replace(f"<{graph_of('pkg-tradeconf-01', '2.3.1')}>",
                                          f"<{graph_of('pkg-lending-01', '2.3.1')}>"))
    mod.use_client(client)
    text = (REPO / "ontology" / "templates" / "coupon_terms_v1.rq").read_text()
    monkeypatch.setattr(mod, "load_template", lambda ver, tid: text)
    try:
        res = GraphHopResult.load(mod.handler({"template": "coupon_terms_v1", "params": {"security": "xs-2031"},
                                               **injected("2.3.1")}))
        none = GraphHopResult.load(mod.handler({"template": "coupon_terms_v1", "params": {"security": "XS-9999"},
                                                **injected("2.3.1")}))
    finally:
        mod.use_client(None)
    (row,) = res.rows
    # xsd:decimal comes back canonical ("6" on pyoxigraph); the printed text is the footnote's
    assert (row["security"], Decimal(row["newRate"]), row["effectiveFromYear"], row["triggerRating"],
            row["page"]) == ("XS-2031", Decimal("6.00"), "3", "BBB-", "2")
    assert row["value"] == "4.25%" and row["footnote"] == "3 6.00% from year 3 if rating < BBB-"
    assert [(o.field_id, o.value) for o in res.operands] == [("f-coupon", "4.25%")]
    assert none.row_count == 0


def test_every_tool_call_logs_who_invoked_it() -> None:
    """rp-api's showcase invokes are told apart from Gateway tool calls in the logs (the rp-api -> rp-graph-hop
    trust edge)."""
    from types import SimpleNamespace

    from lambdas.tools._lib.runtime import invocation_source

    api = SimpleNamespace(client_context=SimpleNamespace(custom={"rpSource": "rp-api"}))
    assert invocation_source(api, None) == "rp-api"
    assert invocation_source(api, "graph-hop___graph_hop") == "gateway"
    assert invocation_source(None, None) == "direct"

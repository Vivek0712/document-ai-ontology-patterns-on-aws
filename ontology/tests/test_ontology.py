"""Ontology build, vocab snapshot, templates and the qualifying-income computation on pyoxigraph."""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
from pyoxigraph import Literal, NamedNode, Quad

ONT = Path(__file__).resolve().parents[1]
REPO = ONT.parent
if str(ONT) not in sys.path:
    sys.path.insert(0, str(ONT))

from evidence_core import iris  # noqa: E402
from evidence_core.enums import CanonicalRole  # noqa: E402
from evidence_core.models.units import VocabSnapshot  # noqa: E402
from rpont.abox import package_abox  # noqa: E402
from rpont.build_lib import DIST_DIR, Manifest, build  # noqa: E402
from rpont.check_lib import add_package, load_gold, run_template, tbox_graphs, tbox_store, templates_for  # noqa: E402
from rpont.checks import run_all  # noqa: E402
from rpont.templates import TemplateError, load_templates, parse_template, render  # noqa: E402

SAMPLES = REPO / "data" / "samples"
XSD = "http://www.w3.org/2001/XMLSchema#"


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> Path:
    dist = tmp_path_factory.mktemp("dist")
    return build(dist_dir=dist).out_dir


@pytest.fixture()
def store(built):
    return tbox_store(built)


def test_every_check_passes(built):
    results = list(run_all(built, SAMPLES, REPO))
    failed = [r for r in results if not r[1]]
    assert not failed, failed
    assert len(results) >= 30


def test_build_is_deterministic_and_local_dist_is_current(built, tmp_path):
    """dist/ is a build output (gitignored); when a local build exists it must match the sources."""
    again = build(dist_dir=tmp_path).out_dir
    local = DIST_DIR / built.name
    for path in sorted(built.rglob("*")):
        if path.is_file():
            rel = path.relative_to(built)
            assert path.read_bytes() == (again / rel).read_bytes(), rel
            if local.exists():
                assert (local / rel).exists() and (local / rel).read_bytes() == path.read_bytes(), \
                    f"ontology/dist/{built.name}/{rel} is stale: run make ontology-check"


def test_version_matches_pipeline_config(built):
    assert built.name == Manifest.load().ontology_version == "1.2.0"


def test_vocab_snapshot_shape(built):
    vocab = VocabSnapshot.load(json.loads((built / "vocab.json").read_text()))
    assert vocab.source == "build" and vocab.ontology_version == "1.2.0"
    roles = {r.role: r for r in vocab.roles}
    assert set(roles) == set(CanonicalRole)
    assert "LAYOUT_TABLE" in roles[CanonicalRole.TABLE].engine_labels["textract"]
    assert "table" in roles[CanonicalRole.TABLE].engine_labels["docling"]
    assert roles[CanonicalRole.TABLE].doco == "doco:Table"
    assert roles[CanonicalRole.SECTION_HEADER].engine_labels["docling"] == ["section_header"]
    assert roles[CanonicalRole.KEY_VALUE].engine_labels["textract"] == ["KEY_VALUE_SET", "LAYOUT_KEY_VALUE"]
    assert roles[CanonicalRole.UNIT_LABEL].derived and roles[CanonicalRole.MARKER_LINK].derived
    assert not roles[CanonicalRole.TABLE].derived
    engine_codes = [c for r in vocab.roles for codes in r.engine_labels.values() for c in codes]
    assert len(engine_codes) == len(set(engine_codes)), "an engine label maps to two roles"
    w2 = next(t for t in vocab.doc_types if t.concept == "doc:W2")
    assert w2.short_label == "W-2" and w2.period_kind == "tax_year" and "Wage and Tax Statement" in w2.indicators
    box1 = next(f for f in vocab.field_concepts if f.concept == "fin:W2WagesBox1")
    assert "1 Wages, tips, other compensation" in box1.alt_labels and box1.notation == "1"
    assert box1.doc_type == "doc:W2" and box1.unit_group == "Wages (Boxes 1–6)"


def test_tbox_contains_qualifying_income_definition(store):
    prompt = (REPO / "prompts" / "answer_v1.txt").read_text()
    rows = list(store.query(
        "SELECT ?c WHERE { GRAPH <urn:rp:graph:tbox:rp> { <urn:rp:ont:fin:QualifyingIncome> "
        "<http://www.w3.org/2000/01/rdf-schema#comment> ?c } }"))
    assert len(rows) == 1
    assert rows[0]["c"].value in prompt, "fin:QualifyingIncome rdfs:comment must be the prompt's definition"


def test_qi_latest_tax_year_per_borrower(built, store):
    """An older W-2 for one borrower is ignored by default and selected by taxYear."""
    gold = load_gold(SAMPLES / "lending")
    graph = add_package(store, gold)
    g = NamedNode(graph)
    unit = NamedNode(iris.unit_iri("w2-2024-maya-fictus-000000:2.3.1:eu-01"))
    field = NamedNode(iris.field_iri("w2-2024-maya-fictus-000000:2.3.1:eu-01", "f-box1"))
    rdf_type = NamedNode("http://www.w3.org/1999/02/22-rdf-syntax-ns#type")
    store.extend([
        Quad(unit, rdf_type, NamedNode(iris.expand("doc:EvidenceUnit")), g),
        Quad(field, rdf_type, NamedNode(iris.expand("fin:FieldValue")), g),
        Quad(field, NamedNode(iris.expand("fin:concept")), NamedNode(iris.expand("fin:W2WagesBox1")), g),
        Quad(field, NamedNode(iris.expand("fin:amount")), Literal("90000.00", datatype=NamedNode(XSD + "decimal")), g),
        Quad(field, NamedNode(iris.expand("fin:taxYear")), Literal("2024", datatype=NamedNode(XSD + "gYear")), g),
        Quad(field, NamedNode(iris.expand("fin:borrower")), NamedNode(iris.party_iri("Maya Fictus")), g),
        Quad(field, NamedNode(iris.expand("prov:wasDerivedFrom")), unit, g),
    ])
    tpl = templates_for(built)["qi_sum_w2_box1_v1"]
    latest = run_template(store, tpl, [*tbox_graphs(), graph])
    assert latest.total == "169650.00"
    assert sorted(Decimal(o["value"]) for o in latest.operands) == [Decimal("71250"), Decimal("98400")]
    y2024 = run_template(store, tpl, [*tbox_graphs(), graph], {"taxYear": "2024"})
    assert y2024.total == "90000.00" and [r["borrower"] for r in y2024.rows] == ["Maya Fictus"]


def test_qi_operands_carry_lineage_ids(built, store):
    gold = load_gold(SAMPLES / "lending")
    graph = add_package(store, gold)
    qi = run_template(store, templates_for(built)["qi_sum_w2_box1_v1"], [*tbox_graphs(), graph])
    assert qi.columns == ["borrower", "taxYear", "wagesBox1", "unit", "field"]
    docs = {d["fileName"]: d["docId"] for d in gold["documents"]}
    for op, row in zip(qi.operands, qi.rows, strict=True):
        assert op["fieldId"] == "f-box1"
        assert row["unit"] == iris.unit_iri(op["unitId"])
        assert op["unitId"].split(":")[0] in docs.values()


def test_render_escapes_parameters_and_validates(built):
    tpl = templates_for(built)["field_lookup_v1"]
    evil = 'Maya" ) } ; DELETE WHERE { ?s ?p ?o } #'
    q = render(tpl, tbox_graphs(), {"concept": "fin:W2WagesBox1", "borrower": evil})
    assert '"Maya\\" ) } ; DELETE WHERE { ?s ?p ?o } #"' in q
    assert "{{" not in q
    with pytest.raises(TemplateError):
        render(tpl, tbox_graphs(), {"concept": "fin:W2WagesBox1", "nope": "x"})
    with pytest.raises(TemplateError):
        render(tpl, tbox_graphs(), {"borrower": "x"})
    with pytest.raises(TemplateError):
        render(tpl, ["urn:rp:graph:abox:pkg> } DROP ALL"], {"concept": "fin:W2WagesBox1"})


def test_rendered_query_runs_with_a_prepended_prefix_block(built, store):
    """graph_hop may prepend iris.sparql_prefix_block(); duplicate PREFIX lines must stay valid."""
    tpl = templates_for(built)["concept_by_label_v1"]
    q = iris.sparql_prefix_block() + render(tpl, tbox_graphs(), {"label": "Box 1"})
    assert [r["concept"].value for r in store.query(q)] == ["urn:rp:ont:fin:W2WagesBox1"]


def test_template_headers_parse():
    templates = load_templates(ONT / "templates")
    assert templates["qi_sum_w2_box1_v1"].optional == ("taxYear",)
    assert templates["field_lookup_v1"].required == ("concept",)
    assert templates["field_lookup_v1"].optional == ("borrower",)
    assert templates["unit_lineage_v1"].required == ("unitId",)
    assert templates["concept_by_label_v1"].required == ("label",)
    with pytest.raises(TemplateError):
        parse_template("bad", "# params:  x  required  test\nSELECT * {{FROM}} WHERE { ?s ?p {{y}} }")


def test_abox_skips_held_units_and_uses_the_lane_graph():
    gold = load_gold(SAMPLES / "lending")
    quads = package_abox(gold)
    text = "\n".join(str(q) for q in quads)
    assert "fin:BankAccountNumber" not in text and "urn:rp:ont:fin:BankAccountNumber" not in text
    assert all(q.graph_name.value == iris.abox_graph("pkg-lending-01", "2.3.1") for q in quads)

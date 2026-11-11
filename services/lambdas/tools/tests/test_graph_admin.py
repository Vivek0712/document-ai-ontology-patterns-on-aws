"""neptune_load, graph_admin and vocab_export against pyoxigraph (same SPARQL surface as Neptune)."""

from __future__ import annotations

import json

import pytest
from evidence_core import s3keys
from evidence_core.iris import abox_graph
from evidence_core.models.units import VocabSnapshot
from lambdas.graph_admin import handler as admin
from lambdas.neptune_load import handler as load
from lambdas.neptune_load.nquads import parse_line
from lambdas.tools._lib.testing import BUCKET
from lambdas.tools.tests.oxi import OxiSparql
from lambdas.vocab_export import handler as vocab

SKOS = "http://www.w3.org/2004/02/skos/core#"
RP = "urn:rp:ont:rp:"
DOC = "urn:rp:ont:doc:"
FIN = "urn:rp:ont:fin:"

# Same modelling as A's ontology/src/rp/roles.ttl (engine codes are notations in one scheme per engine).
RP_TBOX = f"""<{DOC}TableRole> <{SKOS}inScheme> <{DOC}RoleScheme> .
<{DOC}TableRole> <{RP}canonicalRole> "table" .
<{DOC}TableRole> <{SKOS}prefLabel> "Table"@en .
<{DOC}TableRole> <{SKOS}exactMatch> <http://purl.org/spar/doco/Table> .
<{DOC}DoclingLabelScheme> <{RP}engine> "docling" .
<{DOC}TextractLabelScheme> <{RP}engine> "textract" .
<{DOC}docling-table> <{SKOS}inScheme> <{DOC}DoclingLabelScheme> .
<{DOC}docling-table> <{SKOS}notation> "table" .
<{DOC}docling-table> <{SKOS}exactMatch> <{DOC}TableRole> .
<{DOC}textract-LAYOUT_TABLE> <{SKOS}inScheme> <{DOC}TextractLabelScheme> .
<{DOC}textract-LAYOUT_TABLE> <{SKOS}notation> "LAYOUT_TABLE" .
<{DOC}textract-LAYOUT_TABLE> <{SKOS}exactMatch> <{DOC}TableRole> .
<{DOC}W2> <{SKOS}inScheme> <{DOC}DocTypeScheme> .
<{DOC}W2> <{SKOS}prefLabel> "W-2 Wage and Tax Statement" .
<{DOC}W2> <{RP}shortLabel> "W-2" .
<{DOC}W2> <{RP}indicator> "Wage and Tax Statement" .
<{DOC}W2> <{RP}indicator> "Employer identification number" .
<{DOC}W2> <{RP}periodKind> "tax_year" .
<{FIN}W2WagesBox1> <{SKOS}inScheme> <{FIN}FieldConceptScheme> .
<{FIN}W2WagesBox1> <{SKOS}prefLabel> "Wages, tips, other compensation" .
<{FIN}W2WagesBox1> <{SKOS}altLabel> "1 Wages, tips, other compensation" .
<{FIN}W2WagesBox1> <{SKOS}altLabel> "Box 1" .
<{FIN}W2WagesBox1> <{SKOS}notation> "1" .
<{FIN}W2WagesBox1> <{RP}docType> <{DOC}W2> .
<{FIN}W2WagesBox1> <{RP}unitGroup> "Wages (Boxes 1–6)" .
"""


@pytest.fixture
def oxi(world):
    c = OxiSparql()
    for m in (load, admin, vocab):
        m.use_client(c)
    s3 = world.stores.s3
    s3.put_object(Bucket=BUCKET, Key=s3keys.tbox_nquads_key("1.2.0", "rp"), Body=RP_TBOX.encode())
    for g in ("fibo", "doco"):
        s3.put_object(Bucket=BUCKET, Key=s3keys.tbox_nquads_key("1.2.0", g),
                      Body=f'<urn:x:{g}> <{SKOS}prefLabel> "{g}" .\n'.encode())
    yield c
    for m in (load, admin, vocab):
        m.use_client(None)


def test_parse_line_handles_literals_and_graphs():
    q = parse_line('<urn:s> <urn:p> "a \\" b"@en <urn:g> .')
    assert q.graph == "urn:g" and q.obj == '"a \\" b"@en'
    q = parse_line('<urn:s> <urn:p> <urn:o> .')
    assert q.graph is None and q.obj == "<urn:o>"
    assert parse_line("# comment") is None
    with pytest.raises(ValueError):
        parse_line("<urn:s> <urn:p> .")


def test_load_then_export_vocab(world, oxi):
    report = load.handler({"ontologyVersion": "1.2.0"})
    assert report["graphs"]["rp"]["triples"] == RP_TBOX.count("\n")
    assert report["graphs"]["fibo"]["triples"] == 1
    again = load.handler({"ontologyVersion": "1.2.0", "graphs": ["rp"]})  # replace: no duplicates
    assert again["graphs"]["rp"]["triples"] == report["graphs"]["rp"]["triples"]

    out = vocab.handler({"ontologyVersion": "1.2.0"})
    assert out == {"ontologyVersion": "1.2.0", "roles": 1, "docTypes": 1, "fieldConcepts": 1,
                   "key": "config/ontology/1.2.0/vocab.json"}
    body = world.stores.s3.get_object(Bucket=BUCKET, Key=out["key"])["Body"].read()
    snap = VocabSnapshot.load(json.loads(body))
    assert snap.source == "neptune"
    role = snap.roles[0]
    assert role.concept == "doc:TableRole" and role.doco == "doco:Table"
    assert role.engine_labels == {"docling": ["table"], "textract": ["LAYOUT_TABLE"]}
    dt = snap.doc_types[0]
    assert dt.short_label == "W-2" and dt.period_kind == "tax_year" and len(dt.indicators) == 2
    fc = snap.field_concepts[0]
    assert fc.concept == "fin:W2WagesBox1" and fc.doc_type == "doc:W2" and fc.notation == "1"
    assert set(fc.alt_labels) == {"1 Wages, tips, other compensation", "Box 1"}


def test_graph_admin_drop_package(world, oxi):
    g = abox_graph("pkg-lending-01", "2.3.1")
    oxi.load_nquads(f"<urn:a> <urn:p> <urn:b> <{g}> .\n<urn:a> <urn:p> <urn:c> <{g}> .\n")
    assert admin.handler({"op": "counts"})["graphs"][g] == 2
    out = admin.handler({"op": "drop_package", "packageId": "pkg-lending-01"})
    assert out["triples"] == 2 and out["dropped"][g] == 2
    assert g not in admin.handler({"op": "counts"})["graphs"]
    with pytest.raises(ValueError):
        admin.handler({"op": "drop_graph", "graph": "http://example.com/g"})
    with pytest.raises(ValueError):
        admin.handler({"op": "drop_package", "packageId": "x> } DROP ALL"})

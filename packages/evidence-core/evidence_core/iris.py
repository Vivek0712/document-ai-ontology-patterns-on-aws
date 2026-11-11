"""Neptune named graphs, prefixes and the instance IRI scheme.

Named graphs
    urn:rp:graph:tbox:fibo                         FIBO subset           (ontology build, A)
    urn:rp:graph:tbox:doco                         DoCO                  (ontology build, A)
    urn:rp:graph:tbox:rp                           fin:, doc:, SKOS schemes (ontology build, A)
    urn:rp:graph:abox:{packageId}:{pv}             facts + lineage       (graph_write, B)
    urn:rp:graph:run:{runId}                       PROV-O run activity   (agent, C; optional)

TBox graphs carry ``owl:versionInfo`` = ontologyVersion on their ontology node; one
ontology version is loaded at a time.

Instance IRIs (ABox)
    urn:rp:pkg:{packageId}
    urn:rp:doc:{docId}
    urn:rp:unit:{unitId}
    urn:rp:field:{unitId}/{fieldId}               a fin:FieldValue with fin:amount etc.
    urn:rp:region:{unitId}/{fieldId|unit}/p{page} a doc:PageRegion with bbox literals
    urn:rp:party:{slug(name)}                     borrower / employer (fibo-be / fibo-fnd party)
    urn:rp:security:{slug(identifier)}            a bond named by its security identifier field (D2)
    urn:rp:terms:{unitId}/{fieldId}               its stepped coupon terms (from the coupon field)
    urn:rp:step:{unitId}/{fieldId}                the rating-triggered step event (from the linked footnote)
    urn:rp:run:{runId}
    urn:rp:activity:{docId}:{pv}:{stageId}        prov:Activity per stage execution

Core ABox predicates (written by graph_write, read by ontology/templates/*.rq; reference shape:
ontology/rpont/abox.py, checked against graph_write in tests/integration/test_graph_e2e.py):
    ?pkg   a doc:Package .
    ?doc   a doc:<DocType> ; rp:inPackage ?pkg ; rp:fileName "..." ; rp:sha256 "..." ;
           rp:sourceKey "bronze/..." .                       (typed only doc:<DocType>, never doc:Document)
    ?act   a doc:StageActivity ; rp:stageId "units" ; prov:used ?doc .
    ?unit  a doc:EvidenceUnit ; rp:unitType "table_panel" ; rp:inDocument ?doc ; rp:label "..." ;
           rp:pipelineVersion "2.3.1" ; rp:breadcrumb "..." ; prov:wasDerivedFrom ?region ;
           prov:wasGeneratedBy ?act .
    ?field a fin:FieldValue ; fin:concept fin:W2WagesBox1 ; fin:amount "98400.00"^^xsd:decimal ;
           (or fin:text "..." when the value is not a number) ;
           fin:currency "USD" ; fin:taxYear "2025"^^xsd:gYear ; fin:period "TY2025" ;
           fin:periodStart "2025-01-01"^^xsd:date ; fin:periodEnd "2025-12-31"^^xsd:date ;
           fin:borrower ?party ; fin:employer ?employer ; rp:fieldId "f-box1" ; rp:label "..." ;
           prov:wasDerivedFrom ?unit, ?fieldRegion .
    ?region a doc:PageRegion ; rp:page 1 ; rp:x0 50.0 ; rp:y0 216.0 ; rp:x1 306.0 ; rp:y1 284.0 ;
           prov:wasDerivedFrom ?doc .
    ?party rp:name "Maya Fictus" ; a fibo-fnd-aap-ppl:Person        (borrower)
    D2 step events (a coupon field whose raised marker links a footnote that parses as a rating trigger):
    ?bond  a fibo-sec-dbt-bnd:StepUpBond ; rp:name "XS-2031" ; prov:wasDerivedFrom ?unit, ?securityField ;
           fibo-sec-dbt-dbti:hasInterestPaymentTerms ?terms .
    ?terms a fibo-sec-dbt-bnd:SteppedCouponTerms ; fin:hasStepEvent ?event ; prov:wasDerivedFrom ?unit, ?couponField .
    ?event a fin:RatingTriggeredStepEvent ; fin:newRate "6.00"^^xsd:decimal ; fin:effectiveFromYear "3"^^xsd:integer ;
           fin:triggerRating "BBB-" ; rp:marker "3" ; rp:label "<footnote text>" ;
           prov:wasDerivedFrom ?unit, ?footnoteRegion .
    ?employer rp:name "..." ; a fibo-be-le-lp:BusinessEntity          (employer)

SPARQL returns xsd:decimal values in canonical form ("98400", not "98400.00"): format for display.
"""

from __future__ import annotations

from .ids import slugify

PREFIXES: dict[str, str] = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "skos": "http://www.w3.org/2004/02/skos/core#",
    "prov": "http://www.w3.org/ns/prov#",
    "doco": "http://purl.org/spar/doco/",
    "fibo-fnd-agr-ctr": "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/",
    "fibo-fnd-aap-ppl": "https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/People/",
    "fibo-be-le-lp": "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/",
    "fibo-fbc-dae-dbt": "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/",
    "fibo-sec-dbt-bnd": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/",
    "fibo-sec-dbt-dbti": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/DebtInstruments/",
    # The project's own namespaces (URN based, `rp` prefix; no domain is claimed).
    "fin": "urn:rp:ont:fin:",  # financial domain extension (W-2 fields, QualifyingIncome, ...)
    "doc": "urn:rp:ont:doc:",  # document types, roles, EvidenceUnit, PageRegion
    "rp": "urn:rp:ont:rp:",  # structural predicates (inDocument, page, x0, ...)
}

GRAPH_TBOX_FIBO = "urn:rp:graph:tbox:fibo"
GRAPH_TBOX_DOCO = "urn:rp:graph:tbox:doco"
GRAPH_TBOX_RP = "urn:rp:graph:tbox:rp"
TBOX_GRAPHS: dict[str, str] = {
    "fibo": GRAPH_TBOX_FIBO,
    "doco": GRAPH_TBOX_DOCO,
    "rp": GRAPH_TBOX_RP,
}


def sparql_prefix_block(names: list[str] | None = None) -> str:
    keys = names or list(PREFIXES)
    return "\n".join(f"PREFIX {k}: <{PREFIXES[k]}>" for k in keys) + "\n"


def expand(curie: str) -> str:
    """``fin:W2WagesBox1`` -> full IRI. Full IRIs pass through."""
    if curie.startswith(("http://", "https://", "urn:")):
        return curie
    prefix, _, local = curie.partition(":")
    if prefix not in PREFIXES:
        raise ValueError(f"unknown prefix in {curie!r}")
    return PREFIXES[prefix] + local


def abox_graph(package_id: str, pipeline_version: str) -> str:
    return f"urn:rp:graph:abox:{package_id}:{pipeline_version}"


def run_graph(run_id: str) -> str:
    return f"urn:rp:graph:run:{run_id}"


def package_iri(package_id: str) -> str:
    return f"urn:rp:pkg:{package_id}"


def doc_iri(doc_id: str) -> str:
    return f"urn:rp:doc:{doc_id}"


def unit_iri(unit_id: str) -> str:
    return f"urn:rp:unit:{unit_id}"


def field_iri(unit_id: str, field_id: str) -> str:
    return f"urn:rp:field:{unit_id}/{field_id}"


def region_iri(unit_id: str, page: int, field_id: str | None = None) -> str:
    return f"urn:rp:region:{unit_id}/{field_id or 'unit'}/p{page}"


def party_iri(name: str) -> str:
    return f"urn:rp:party:{slugify(name, 60)}"


def security_iri(identifier: str) -> str:
    return f"urn:rp:security:{slugify(identifier, 60)}"


def step_event_iri(unit_id: str, field_id: str) -> str:
    """The rating-triggered step event read from the footnote linked to ``field_id`` (D2)."""
    return f"urn:rp:step:{unit_id}/{field_id}"


def coupon_terms_iri(unit_id: str, field_id: str) -> str:
    return f"urn:rp:terms:{unit_id}/{field_id}"


def run_iri(run_id: str) -> str:
    return f"urn:rp:run:{run_id}"


def activity_iri(doc_id: str, pipeline_version: str, stage_id: str) -> str:
    return f"urn:rp:activity:{doc_id}:{pipeline_version}:{stage_id}"


def unit_id_from_iri(iri: str) -> str:
    if not iri.startswith("urn:rp:unit:"):
        raise ValueError(f"not a unit IRI: {iri!r}")
    return iri.removeprefix("urn:rp:unit:")


def field_ref_from_iri(iri: str) -> tuple[str, str]:
    """``urn:rp:field:{unitId}/{fieldId}`` -> (unitId, fieldId)."""
    if not iri.startswith("urn:rp:field:"):
        raise ValueError(f"not a field IRI: {iri!r}")
    unit_id, _, field_id = iri.removeprefix("urn:rp:field:").rpartition("/")
    return unit_id, field_id

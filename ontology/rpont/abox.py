"""Reference ABox for a generated package, built from the generator's gold regions.

This is the shape ``graph_write`` produces from Gold units (evidence_core/iris.py docstring),
written here from ground truth so the templates can be checked without running extraction. It is a
test and dev fixture: on AWS the ABox comes from the pipeline, never from gold.json.

Mapping from gold regions:
- every document                 -> ``urn:rp:doc:{docId}`` a doc:<DocType>, rp:inPackage, rp:fileName, rp:sha256
- each unit-level region         -> ``urn:rp:unit:{docId}:{pv}:eu-NN`` a doc:EvidenceUnit (+ its PageRegion);
                                    PII-held units are skipped (held units never leave Silver)
- each field region with concept -> ``urn:rp:field:{unitId}/{fieldId}`` a fin:FieldValue with amount or text,
                                    period, tax year, borrower/employer parties, lineage to unit + field region
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from evidence_core import iris
from evidence_core.ids import eu_local, unit_id
from pyoxigraph import Literal, NamedNode, Quad

XSD = "http://www.w3.org/2001/XMLSchema#"
RDF_TYPE = NamedNode(iris.PREFIXES["rdf"] + "type")
PERSON = NamedNode("https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/People/Person")
BUSINESS_ENTITY = NamedNode(iris.PREFIXES["fibo-be-le-lp"] + "BusinessEntity")
UNIT_KINDS = {"unit", "figure"}


def n(curie_or_iri: str) -> NamedNode:
    return NamedNode(iris.expand(curie_or_iri))


def dec(value: str) -> Literal:
    return Literal(value, datatype=NamedNode(XSD + "decimal"))


def _region_quads(subject: NamedNode, region: dict[str, Any], doc: NamedNode, g: NamedNode) -> list[Quad]:
    b = region["bbox"]
    out = [
        Quad(subject, RDF_TYPE, n("doc:PageRegion"), g),
        Quad(subject, n("rp:page"), Literal(str(b["page"]), datatype=NamedNode(XSD + "integer")), g),
        Quad(subject, n("prov:wasDerivedFrom"), doc, g),
    ]
    for k in ("x0", "y0", "x1", "y1"):
        out.append(Quad(subject, n(f"rp:{k}"), dec(f"{b[k]:.2f}"), g))
    return out


def package_abox(gold: dict[str, Any], pipeline_version: str = "2.3.1",
                 owner_sub: str = "7c41e2aa-0000-4000-8000-00000000000a") -> list[Quad]:
    """All ABox quads for one package lane, in ``urn:rp:graph:abox:{packageId}:{pv}``."""
    pkg_id = gold["packageId"]
    g = NamedNode(iris.abox_graph(pkg_id, pipeline_version))
    pkg = NamedNode(iris.package_iri(pkg_id))
    quads: list[Quad] = [Quad(pkg, RDF_TYPE, n("doc:Package"), g)]
    parties: dict[str, NamedNode] = {}

    def party(name: str, cls: NamedNode) -> NamedNode:
        node = NamedNode(iris.party_iri(name))
        if name not in parties:
            parties[name] = node
            quads.extend([Quad(node, RDF_TYPE, cls, g), Quad(node, n("rp:name"), Literal(name), g)])
        return node

    for d in gold["documents"]:
        doc_id = d["docId"]
        doc = NamedNode(iris.doc_iri(doc_id))
        quads += [
            Quad(doc, RDF_TYPE, n(d["docType"]), g),
            Quad(doc, n("rp:inPackage"), pkg, g),
            Quad(doc, n("rp:fileName"), Literal(d["fileName"]), g),
            Quad(doc, n("rp:sha256"), Literal(d["sha256"]), g),
            Quad(doc, n("rp:sourceKey"), Literal(f"bronze/{owner_sub}/{pkg_id}/{d['fileName']}"), g),
        ]
        activity = NamedNode(iris.activity_iri(doc_id, pipeline_version, "units"))
        quads += [Quad(activity, RDF_TYPE, n("doc:StageActivity"), g),
                  Quad(activity, n("rp:stageId"), Literal("units"), g),
                  Quad(activity, n("prov:used"), doc, g)]
        regions = d["regions"]
        units = sorted((r for r in regions if r["kind"] in UNIT_KINDS),
                       key=lambda r: (r["page"], r["bbox"]["y0"], r["bbox"]["x0"]))
        unit_ids: dict[str, str] = {}
        for i, r in enumerate(units, start=1):
            if r.get("pii") == "held":
                continue
            uid = unit_id(doc_id, pipeline_version, eu_local(i))
            unit_ids[r["regionId"]] = uid
            unit = NamedNode(iris.unit_iri(uid))
            region = NamedNode(iris.region_iri(uid, r["page"]))
            unit_type = r.get("extra", {}).get("unitType", "table_panel")
            quads += [
                Quad(unit, RDF_TYPE, n("doc:EvidenceUnit"), g),
                Quad(unit, n("rp:unitType"), Literal(unit_type), g),
                Quad(unit, n("rp:inDocument"), doc, g),
                Quad(unit, n("rp:pipelineVersion"), Literal(pipeline_version), g),
                Quad(unit, n("rp:label"), Literal(r.get("label") or uid), g),
                Quad(unit, n("rp:breadcrumb"), Literal(" › ".join([d["title"], r.get("label") or ""])), g),
                Quad(unit, n("prov:wasDerivedFrom"), region, g),
                Quad(unit, n("prov:wasGeneratedBy"), activity, g),
                *_region_quads(region, r, doc, g),
            ]
        for r in regions:
            if r["kind"] != "field" or not r.get("concept") or r.get("unitRef") not in unit_ids:
                continue
            uid = unit_ids[r["unitRef"]]
            fid = r["fieldId"]
            field = NamedNode(iris.field_iri(uid, fid))
            fregion = NamedNode(iris.region_iri(uid, r["page"], fid))
            quads += [
                Quad(field, RDF_TYPE, n("fin:FieldValue"), g),
                Quad(field, n("fin:concept"), n(r["concept"]), g),
                Quad(field, n("rp:fieldId"), Literal(fid), g),
                Quad(field, n("rp:label"), Literal(r.get("label") or fid), g),
                Quad(field, n("prov:wasDerivedFrom"), NamedNode(iris.unit_iri(uid)), g),
                Quad(field, n("prov:wasDerivedFrom"), fregion, g),
                *_region_quads(fregion, r, doc, g),
            ]
            if r.get("valueNumber") is not None:
                quads.append(Quad(field, n("fin:amount"), dec(r["valueNumber"]), g))
            else:
                quads.append(Quad(field, n("fin:text"), Literal(r.get("value") or ""), g))
            for key, pred in (("currency", "fin:currency"), ("period", "fin:period")):
                if r.get(key):
                    quads.append(Quad(field, n(pred), Literal(r[key]), g))
            for key, pred in (("periodStart", "fin:periodStart"), ("periodEnd", "fin:periodEnd")):
                if r.get(key):
                    quads.append(Quad(field, n(pred), Literal(r[key], datatype=NamedNode(XSD + "date")), g))
            if r.get("taxYear"):
                quads.append(Quad(field, n("fin:taxYear"), Literal(r["taxYear"], datatype=NamedNode(XSD + "gYear")),
                                  g))
            if r.get("borrower"):
                quads.append(Quad(field, n("fin:borrower"), party(r["borrower"], PERSON), g))
            if r.get("employer"):
                quads.append(Quad(field, n("fin:employer"), party(r["employer"], BUSINESS_ENTITY), g))
    return quads


def nquads(quads: Iterable[Quad]) -> str:
    return "".join(sorted(f"{q} .\n" for q in quads))

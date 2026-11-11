"""ABox N-Quads for ``graph_write`` (``evidence_core.iris``).

Per document lane, into ``urn:rp:graph:abox:{packageId}:{pv}``:
document typed with its ``doc:`` class; every Gold unit as ``doc:EvidenceUnit`` with
``prov:wasDerivedFrom`` its page regions; every field as ``fin:FieldValue`` with ``fin:concept``,
``fin:amount`` (xsd:decimal), period literals, ``fin:borrower`` / ``fin:employer`` and
``prov:wasDerivedFrom`` the unit and its field region; regions with page + bbox literals.

D2 step events: a coupon field (``fin:CouponRate``) whose raised marker links a footnote member
(``provenance.build.markerLinks``, eu_builder) is read deterministically, no model: the footnote text after
its marker must match ``STEP_EVENT_RE`` ("6.00% from year 3 if rating < BBB-"). A match writes a
``fin:RatingTriggeredStepEvent`` with ``fin:newRate``, ``fin:effectiveFromYear`` and ``fin:triggerRating``,
derived from the footnote's own region, hung off ``fibo-sec-dbt-bnd:SteppedCouponTerms`` of a
``fibo-sec-dbt-bnd:StepUpBond`` named by the unit's ``fin:SecurityIdentifier`` field. No match writes
nothing for that link and counts it in ``QuadWriter.step_event_failures``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from evidence_core import iris
from evidence_core.bbox import BBox
from evidence_core.models.units import Unit

from .periods import period_bounds

XSD = iris.PREFIXES["xsd"]
RDF_TYPE = f"<{iris.PREFIXES['rdf']}type>"
PROV = iris.PREFIXES["prov"]

# Concepts whose string value names a party. A vocab flag would be better (contract gap).
BORROWER_CONCEPT = re.compile(r"(Employee|Borrower|AccountHolder)Name$")
EMPLOYER_CONCEPT = re.compile(r"(Employer)Name$")
# Party classes (reference shape: ontology/rpont/abox.py).
PARTY_CLASS = {"borrower": "fibo-fnd-aap-ppl:Person", "employer": "fibo-be-le-lp:BusinessEntity"}
COUPON_CONCEPT = "fin:CouponRate"
SECURITY_CONCEPT = "fin:SecurityIdentifier"
# (D2): deterministic, no LLM
STEP_EVENT_RE = re.compile(r"^(?P<rate>\d+\.\d+)% from year (?P<year>\d+) if rating < (?P<rating>[A-Z]{1,3}[+-]?)$")


def _iri(value: str) -> str:
    return f"<{iris.expand(value)}>"


def _lit(value: str, datatype: str | None = None) -> str:
    esc = value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r")
    return f'"{esc}"^^<{XSD}{datatype}>' if datatype else f'"{esc}"'


@dataclass
class QuadWriter:
    graph: str
    lines: list[str] = field(default_factory=list)
    step_events: int = 0
    step_event_failures: int = 0

    def add(self, s: str, p: str, o: str) -> None:
        self.lines.append(f"{s} {p} {o} <{self.graph}> .")

    def triples_text(self) -> str:
        """Same statements as N-Triples (for ``INSERT DATA { GRAPH <g> { ... } }``)."""
        suffix = f" <{self.graph}> ."
        return "\n".join(line[: -len(suffix)] + " ." for line in self.lines)

    def nquads(self) -> str:
        return "\n".join(self.lines) + ("\n" if self.lines else "")


def _region(w: QuadWriter, iri: str, box: BBox, derived_from: str) -> None:
    w.add(iri, RDF_TYPE, _iri("doc:PageRegion"))
    w.add(iri, _iri("rp:page"), _lit(str(box.page), "integer"))
    for k in ("x0", "y0", "x1", "y1"):
        w.add(iri, _iri(f"rp:{k}"), _lit(f"{getattr(box, k):.2f}", "decimal"))
    w.add(iri, f"<{PROV}wasDerivedFrom>", derived_from)


def build_abox(units: list[Unit], *, package_id: str, pipeline_version: str, doc_id: str, file_name: str,
               source_sha: str, doc_type: str | None, stage_activity: str, source_key: str | None = None,
               stage_id: str = "units") -> tuple[QuadWriter, int]:
    """Returns the quads and the number of facts (fields) written.

    ``stage_activity`` is the activity that generated the units (the ``units`` stage), typed
    ``doc:StageActivity`` with ``rp:stageId`` and ``prov:used`` the document.
    """
    w = QuadWriter(iris.abox_graph(package_id, pipeline_version))
    doc = f"<{iris.doc_iri(doc_id)}>"
    pkg = f"<{iris.package_iri(package_id)}>"
    w.add(pkg, RDF_TYPE, _iri("doc:Package"))
    # Typed only with its doc:<DocType> (a subclass of doc:Document); doc:Document when unclassified.
    w.add(doc, RDF_TYPE, _iri(doc_type) if doc_type else _iri("doc:Document"))
    w.add(doc, _iri("rp:inPackage"), pkg)
    w.add(doc, _iri("rp:fileName"), _lit(file_name))
    w.add(doc, _iri("rp:sha256"), _lit(source_sha))
    if source_key:
        w.add(doc, _iri("rp:sourceKey"), _lit(source_key))
    activity = f"<{stage_activity}>"
    w.add(activity, RDF_TYPE, _iri("doc:StageActivity"))
    w.add(activity, _iri("rp:stageId"), _lit(stage_id))
    w.add(activity, f"<{PROV}used>", doc)

    parties: dict[str, str] = {}
    for u in units:
        for f in u.fields:
            if f.concept and BORROWER_CONCEPT.search(f.concept):
                parties.setdefault("borrower", f.value)
            elif f.concept and EMPLOYER_CONCEPT.search(f.concept):
                parties.setdefault("employer", f.value)
    if "borrower" not in parties:
        for u in units:  # breadcrumb party (second crumb) as a fallback
            if len(u.breadcrumb) >= 2 and u.fields:
                parties["borrower"] = u.breadcrumb[1]
                break
    party_iris = {}
    for role, name in parties.items():
        p = f"<{iris.party_iri(name)}>"
        party_iris[role] = p
        w.add(p, RDF_TYPE, _iri(PARTY_CLASS[role]))
        w.add(p, _iri("rp:name"), _lit(name))

    facts = 0
    for u in units:
        unit = f"<{iris.unit_iri(u.unit_id)}>"
        w.add(unit, RDF_TYPE, _iri("doc:EvidenceUnit"))
        w.add(unit, _iri("rp:unitType"), _lit(u.unit_type.value))
        w.add(unit, _iri("rp:inDocument"), doc)
        w.add(unit, _iri("rp:pipelineVersion"), _lit(pipeline_version))
        w.add(unit, _iri("rp:label"), _lit(u.label))
        if u.breadcrumb:
            w.add(unit, _iri("rp:breadcrumb"), _lit(" › ".join(u.breadcrumb)))
        w.add(unit, f"<{PROV}wasGeneratedBy>", activity)
        for box in u.bboxes:
            region = f"<{iris.region_iri(u.unit_id, box.page)}>"
            _region(w, region, box, doc)
            w.add(unit, f"<{PROV}wasDerivedFrom>", region)
        for f in u.fields:
            fld = f"<{iris.field_iri(u.unit_id, f.field_id)}>"
            w.add(fld, RDF_TYPE, _iri("fin:FieldValue"))
            w.add(fld, _iri("rp:fieldId"), _lit(f.field_id))
            w.add(fld, _iri("rp:label"), _lit(f.label))
            w.add(fld, _iri("rp:value"), _lit(f.value))
            w.add(fld, f"<{PROV}wasDerivedFrom>", unit)
            if f.concept:
                w.add(fld, _iri("fin:concept"), _iri(f.concept))
            if f.value_number is not None:
                w.add(fld, _iri("fin:amount"), _lit(f.value_number, "decimal"))
            else:
                w.add(fld, _iri("fin:text"), _lit(f.value))
            if f.currency:
                w.add(fld, _iri("fin:currency"), _lit(f.currency))
            if f.period:
                w.add(fld, _iri("fin:period"), _lit(f.period))
                if f.period.startswith("TY"):
                    w.add(fld, _iri("fin:taxYear"), _lit(f.period[2:], "gYear"))
                bounds = period_bounds(f.period)
                if bounds:
                    w.add(fld, _iri("fin:periodStart"), _lit(bounds[0], "date"))
                    w.add(fld, _iri("fin:periodEnd"), _lit(bounds[1], "date"))
            if "borrower" in party_iris:
                w.add(fld, _iri("fin:borrower"), party_iris["borrower"])
            if "employer" in party_iris:
                w.add(fld, _iri("fin:employer"), party_iris["employer"])
            if f.bbox is not None:
                region = f"<{iris.region_iri(u.unit_id, f.bbox.page, f.field_id)}>"
                _region(w, region, f.bbox, doc)
                w.add(fld, f"<{PROV}wasDerivedFrom>", region)
            facts += 1
        for link in u.provenance.build.get("markerLinks") or []:
            _step_event(w, u, link, doc)
    return w, facts


def parse_step_event(footnote: str, marker: str | None) -> dict[str, str] | None:
    """``3 6.00% from year 3 if rating < BBB-`` (marker 3) -> {rate: 6.00, year: 3, rating: BBB-}."""
    text = " ".join(footnote.split())
    if marker and text.startswith(marker):
        text = text[len(marker):].strip()
    m = STEP_EVENT_RE.match(text)
    return m.groupdict() if m else None


def _step_event(w: QuadWriter, u: Unit, link: dict[str, object], doc: str) -> None:
    fields = {f.field_id: f for f in u.fields}
    coupon = fields.get(str(link.get("fieldId") or ""))
    if coupon is None or coupon.concept != COUPON_CONCEPT:
        return
    member = next((m for m in u.members if m.member_id == link.get("footnoteMember")), None)
    parsed = parse_step_event(member.text or "", str(link.get("marker") or "")) if member else None
    if member is None or parsed is None or member.bbox is None:
        w.step_event_failures += 1
        return
    unit = f"<{iris.unit_iri(u.unit_id)}>"
    prov = f"<{PROV}wasDerivedFrom>"
    terms = f"<{iris.coupon_terms_iri(u.unit_id, coupon.field_id)}>"
    event = f"<{iris.step_event_iri(u.unit_id, coupon.field_id)}>"
    region = f"<{iris.region_iri(u.unit_id, member.bbox.page, member.member_id)}>"
    _region(w, region, member.bbox, doc)
    w.add(event, RDF_TYPE, _iri("fin:RatingTriggeredStepEvent"))
    w.add(event, _iri("fin:newRate"), _lit(parsed["rate"], "decimal"))
    w.add(event, _iri("fin:effectiveFromYear"), _lit(parsed["year"], "integer"))
    w.add(event, _iri("fin:triggerRating"), _lit(parsed["rating"]))
    w.add(event, _iri("rp:marker"), _lit(str(link.get("marker") or "")))
    w.add(event, _iri("rp:label"), _lit(" ".join((member.text or "").split())))
    w.add(event, prov, unit)
    w.add(event, prov, region)
    w.add(terms, RDF_TYPE, _iri("fibo-sec-dbt-bnd:SteppedCouponTerms"))
    w.add(terms, _iri("fin:hasStepEvent"), event)
    w.add(terms, prov, unit)
    w.add(terms, prov, f"<{iris.field_iri(u.unit_id, coupon.field_id)}>")
    security = next((f for f in u.fields if f.concept == SECURITY_CONCEPT and f.value.strip()), None)
    if security is not None:
        bond = f"<{iris.security_iri(security.value.strip())}>"
        w.add(bond, RDF_TYPE, _iri("fibo-sec-dbt-bnd:StepUpBond"))
        w.add(bond, _iri("rp:name"), _lit(security.value.strip()))
        w.add(bond, _iri("fibo-sec-dbt-dbti:hasInterestPaymentTerms"), terms)
        w.add(bond, prov, unit)
        w.add(bond, prov, f"<{iris.field_iri(u.unit_id, security.field_id)}>")
    w.step_events += 1


def delete_doc_update(graph: str, doc_id: str) -> str:
    """SPARQL UPDATE removing this document's previous statements from the lane graph."""
    g, d = f"<{graph}>", f"<{iris.doc_iri(doc_id)}>"
    inp, prov = _iri("rp:inDocument"), f"<{PROV}wasDerivedFrom>"
    return (
        f"DELETE {{ GRAPH {g} {{ ?r ?rp ?ro }} }} WHERE {{ GRAPH {g} {{ ?r {prov} {d} . ?r ?rp ?ro }} }};\n"
        f"DELETE {{ GRAPH {g} {{ ?f ?fp ?fo }} }} "
        f"WHERE {{ GRAPH {g} {{ ?u {inp} {d} . ?f {prov} ?u . ?f ?fp ?fo }} }};\n"
        f"DELETE {{ GRAPH {g} {{ ?u ?up ?uo }} }} WHERE {{ GRAPH {g} {{ ?u {inp} {d} . ?u ?up ?uo }} }};\n"
        f"DELETE WHERE {{ GRAPH {g} {{ {d} ?dp ?do }} }}"
    )


def count_doc_triples_query(graph: str, doc_id: str) -> str:
    g, d = f"<{graph}>", f"<{iris.doc_iri(doc_id)}>"
    inp, prov = _iri("rp:inDocument"), f"<{PROV}wasDerivedFrom>"
    return (
        f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH {g} {{ "
        f"{{ ?s ?p ?o . FILTER(?s = {d}) }} UNION {{ ?s {inp} {d} . ?s ?p ?o }} UNION "
        f"{{ ?u {inp} {d} . ?s {prov} ?u . ?s ?p ?o }} UNION {{ ?s {prov} {d} . ?s ?p ?o }} }} }}"
    )


def insert_batches(w: QuadWriter, batch_size: int = 500) -> list[str]:
    suffix = f" <{w.graph}> ."
    triples = [line[: -len(suffix)] + " ." for line in w.lines]
    out = []
    for i in range(0, len(triples), batch_size):
        body = "\n".join(triples[i:i + batch_size])
        out.append(f"INSERT DATA {{ GRAPH <{w.graph}> {{\n{body}\n}} }}")
    return out

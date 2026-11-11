"""TBox export for the ontology explorer: ``dist/{ver}/tbox.json``.

Read back from the N-Quads the build just wrote (the bytes ``make seed`` loads into Neptune), so the explorer and
the graph cannot disagree. One row per named class, concept, property or individual with its labels (each with the
triple that states it), its definition verbatim, its hierarchy and its domain and range. Blank nodes (OWL
restrictions, RDF lists) are not terms and are left out.

    python -m rpont.tbox_export ontology/dist/1.2.0      # what ontology/build.py runs after the N-Quads
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import pyoxigraph as ox

RDF = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
RDFS = "http://www.w3.org/2000/01/rdf-schema#"
OWL = "http://www.w3.org/2002/07/owl#"
SKOS = "http://www.w3.org/2004/02/skos/core#"
PROV = "http://www.w3.org/ns/prov#"

KINDS: dict[str, str] = {  # rdf:type -> term kind (first match in this order wins)
    f"{OWL}Class": "class", f"{RDFS}Class": "class", f"{SKOS}Concept": "concept",
    f"{OWL}ObjectProperty": "object_property", f"{OWL}DatatypeProperty": "datatype_property",
    f"{OWL}AnnotationProperty": "annotation_property", f"{RDF}Property": "object_property",
    f"{OWL}NamedIndividual": "individual",
}
LABELS = {f"{SKOS}prefLabel": "prefLabel", f"{SKOS}altLabel": "altLabel", f"{SKOS}notation": "notation",
          f"{RDFS}label": "label"}
DEFINITIONS = (f"{RDFS}comment", f"{SKOS}definition")
STRUCTURAL = {f"{RDF}type", f"{RDFS}subClassOf", f"{SKOS}broader", f"{RDFS}domain", f"{RDFS}range",
              f"{RDFS}isDefinedBy", *LABELS, *DEFINITIONS}
MAX_VALUES = 12  # per "other" predicate; the explorer shows a sample, the graph holds the rest


def _prefixes() -> list[tuple[str, str]]:
    from evidence_core.iris import PREFIXES

    return sorted(PREFIXES.items(), key=lambda kv: -len(kv[1]))


def curie(iri: str, prefixes: list[tuple[str, str]]) -> str:
    for name, ns in prefixes:
        if iri.startswith(ns) and len(iri) > len(ns):
            return f"{name}:{iri[len(ns):]}"
    return iri


def module_of(c: str) -> str:
    """``fin:QualifyingIncome`` -> ``fin``; every FIBO prefix -> ``fibo``; an unprefixed IRI -> ``other``."""
    prefix = c.split(":", 1)[0] if ":" in c and not c.startswith(("http", "urn")) else "other"
    return "fibo" if prefix.startswith("fibo-") else prefix


def _value(term: Any, prefixes: list[tuple[str, str]]) -> str:
    if isinstance(term, ox.NamedNode):
        return curie(term.value, prefixes)
    return str(term.value)


def _quote(term: Any, prefixes: list[tuple[str, str]]) -> str:
    return curie(term.value, prefixes) if isinstance(term, ox.NamedNode) else json.dumps(str(term.value),
                                                                                         ensure_ascii=False)


def export(dist: Path) -> dict[str, Any]:
    prefixes = _prefixes()
    store = ox.Store()
    for path in sorted((dist / "nquads").glob("*.nq")):
        store.load(path=str(path), format=ox.RdfFormat.N_QUADS)
    by_subject: dict[str, list[ox.Quad]] = defaultdict(list)
    for q in store:
        if isinstance(q.subject, ox.NamedNode):
            by_subject[q.subject.value].append(q)
    terms = []
    for iri, quads in sorted(by_subject.items()):
        types = [q.object.value for q in quads if q.predicate.value == f"{RDF}type"]
        kind = next((KINDS[t] for t in KINDS if t in types), None)
        if kind is None:
            continue
        c = curie(iri, prefixes)
        labels, others = [], defaultdict(list)
        for q in quads:
            p = q.predicate.value
            if p in LABELS:
                labels.append({"value": str(q.object.value), "scheme": LABELS[p],
                               "triple": f"{c} {curie(p, prefixes)} {_quote(q.object, prefixes)}"})
            elif p not in STRUCTURAL and not isinstance(q.object, ox.BlankNode):
                others[curie(p, prefixes)].append(_value(q.object, prefixes))

        def objs(pred: str, quads: list[ox.Quad] = quads) -> list[str]:
            return sorted({curie(q.object.value, prefixes) for q in quads
                           if q.predicate.value == pred and isinstance(q.object, ox.NamedNode)})

        pref = next((lb["value"] for lb in labels if lb["scheme"] == "prefLabel"), None)
        label = pref or next((lb["value"] for lb in labels if lb["scheme"] == "label"), None)
        definition = next((str(q.object.value) for p in DEFINITIONS for q in quads if q.predicate.value == p), None)
        terms.append({
            "iri": iri, "curie": c, "kind": kind, "module": module_of(c), "graph": quads[0].graph_name.value,
            "label": label, "labels": sorted(labels, key=lambda lb: (lb["scheme"], lb["value"])),
            "definition": definition, "subClassOf": objs(f"{RDFS}subClassOf"), "broader": objs(f"{SKOS}broader"),
            "domain": objs(f"{RDFS}domain"), "range": objs(f"{RDFS}range"),
            "properties": {p: sorted(set(vs))[:MAX_VALUES] for p, vs in sorted(others.items())},
        })
    modules: dict[str, dict[str, Any]] = {}
    for t in terms:
        m = modules.setdefault(t["module"], {"module": t["module"], "graph": t["graph"], "classes": 0, "concepts": 0,
                                             "properties": 0})
        if t["kind"] != "individual":
            m[{"class": "classes", "concept": "concepts"}.get(t["kind"], "properties")] += 1
    manifest = json.loads((dist / "manifest.json").read_text())
    inputs = hashlib.sha256(b"".join(p.read_bytes() for p in sorted((dist / "nquads").glob("*.nq")))).hexdigest()
    return {"ontologyVersion": manifest["ontologyVersion"], "generatedAt": manifest.get("generatedAt"),
            "source": "ontology/rpont/tbox_export.py over nquads/*.nq", "nquadsSha256": inputs,
            "modules": sorted(modules.values(), key=lambda m: m["module"]), "terms": terms}


def write(dist: Path) -> Path:
    path = dist / "tbox.json"
    path.write_text(json.dumps(export(dist), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1])
    out = write(target)
    body = json.loads(out.read_text())
    print(f"{out}: {len(body['terms'])} terms in {len(body['modules'])} modules")

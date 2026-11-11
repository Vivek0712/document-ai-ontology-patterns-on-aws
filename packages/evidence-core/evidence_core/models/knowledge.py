"""Knowledge contracts (W-09 Ontology explorer, W-11 Graph). Mirrored in contracts/api.ts.

- ``GET /ontology/classes?module=`` -> ``OntologyTermList`` and ``GET /ontology/terms/{iri}`` -> ``OntologyTerm``,
  read from ``config/ontology/{ver}/tbox.json``, the TBox export ``ontology/build.py`` writes from the same N-Quads
  ``make seed`` loads into Neptune (so the explorer shows exactly what the graph holds).
- ``GET /graph/neighbourhood?iri=&depth=&packageId=&pv=`` -> ``GraphNeighbourhood`` (models/inspect.py): the
  template ``neighbourhood_v1`` run by ``rp-graph-hop`` with an API-built injected context. The template is
  API-only: it is not in the agent's ``graph_hop`` tool schema.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ..base import RpModel

TermKind = Literal["class", "concept", "object_property", "datatype_property", "annotation_property", "individual"]


class TermLabel(RpModel):
    value: str
    scheme: Literal["prefLabel", "altLabel", "notation", "label"]
    triple: str  # the triple in Turtle short form, e.g. 'fin:W2WagesBox1 skos:altLabel "Box 1"'


class OntologyTerm(RpModel):
    iri: str
    curie: str
    kind: TermKind
    module: str  # fin | doc | rp | fibo | doco | prov | skos
    graph: str  # the TBox named graph that defines it
    label: str | None = None
    labels: list[TermLabel] = Field(default_factory=list)
    definition: str | None = None  # rdfs:comment or skos:definition, verbatim
    sub_class_of: list[str] = Field(default_factory=list)
    broader: list[str] = Field(default_factory=list)
    domain: list[str] = Field(default_factory=list)
    range: list[str] = Field(default_factory=list)
    properties: dict[str, list[str]] = Field(default_factory=dict)  # other predicates -> objects (CURIEs/literals)
    used_by: dict[str, int] | None = None  # packageId -> fields with this concept the caller can read (measured)


class OntologyModule(RpModel):
    module: str
    graph: str
    classes: int
    concepts: int
    properties: int


class OntologyTermList(RpModel):
    ontology_version: str
    module: str | None = None
    modules: list[OntologyModule]
    items: list[OntologyTerm]
    source: str  # "config/ontology/1.2.0/tbox.json"
    sha256: str | None = None

"""rp-vocab-export (VPC, SigV4): rebuild the vocab snapshot from the TBox graphs in Neptune.

Writes ``units.VocabSnapshot`` (``source: "neptune"``) to ``config/ontology/{ver}/vocab.json``
(or ``event.key``) so ``classify`` and ``eu_builder`` read the vocabulary the graph holds,
pinned to an ontology version. Event: ``{"ontologyVersion": "1.2.0", "write": true}``.

TBox modelling this export relies on (ontology/src/rp/*.ttl and ontology/rpont/build_lib.py; checked by
tests/integration/test_knowledge_plane.py against the built vocab.json):
  roles        ?c skos:inScheme doc:RoleScheme ; rp:canonicalRole "table" ; skos:prefLabel ?l ;
               [skos:exactMatch | skos:closeMatch doco:X] ; [rp:derived true]
  engine codes ?code skos:inScheme ?scheme ; skos:notation "LAYOUT_TABLE" ; skos:exactMatch ?c .
               ?scheme rp:engine "textract"          (one label scheme per engine)
  docTypes     ?c skos:inScheme doc:DocTypeScheme ; skos:prefLabel ; rp:shortLabel ;
               [skos:altLabel*] ; rp:indicator* ; [rp:periodKind "tax_year"]
  fields       ?c skos:inScheme fin:FieldConceptScheme ; skos:prefLabel ; skos:altLabel* ;
               [skos:notation] ; rp:docType ?docType ; [rp:datatype "decimal"] ; [rp:unitGroup "..."]
"""

from __future__ import annotations

import json
import os
from typing import Any

from evidence_core import s3keys
from evidence_core.iris import PREFIXES, TBOX_GRAPHS, sparql_prefix_block
from evidence_core.logs import get_logger
from evidence_core.models.status import iso
from evidence_core.models.units import VocabDocType, VocabFieldConcept, VocabRole, VocabSnapshot
from evidence_core.sparql import SparqlClient, bindings_to_rows

from lambdas.tools._lib.stores import stores

log = get_logger("rp.vocab_export")
_CLIENT: Any = None

_FROM = "\n".join(f"FROM <{g}>" for g in TBOX_GRAPHS.values())

Q_ROLES = sparql_prefix_block() + f"""SELECT ?c ?role ?pref ?docoExact ?docoClose ?engine ?el ?derived
{_FROM}
WHERE {{
  ?c skos:inScheme doc:RoleScheme ; rp:canonicalRole ?role ; skos:prefLabel ?pref .
  OPTIONAL {{ ?c skos:exactMatch ?docoExact . FILTER(STRSTARTS(STR(?docoExact), "{PREFIXES['doco']}")) }}
  OPTIONAL {{ ?c skos:closeMatch ?docoClose . FILTER(STRSTARTS(STR(?docoClose), "{PREFIXES['doco']}")) }}
  OPTIONAL {{ ?code skos:exactMatch ?c ; skos:inScheme ?scheme ; skos:notation ?el . ?scheme rp:engine ?engine }}
  OPTIONAL {{ ?c rp:derived ?derived }}
}} ORDER BY ?c"""

Q_ENGINES = sparql_prefix_block() + f"""SELECT DISTINCT ?engine
{_FROM}
WHERE {{ ?scheme rp:engine ?engine }} ORDER BY ?engine"""

Q_DOCTYPES = sparql_prefix_block() + f"""SELECT ?c ?pref ?short ?alt ?ind ?period
{_FROM}
WHERE {{
  ?c skos:inScheme doc:DocTypeScheme ; skos:prefLabel ?pref .
  OPTIONAL {{ ?c rp:shortLabel ?short }}
  OPTIONAL {{ ?c skos:altLabel ?alt }}
  OPTIONAL {{ ?c rp:indicator ?ind }}
  OPTIONAL {{ ?c rp:periodKind ?period }}
}} ORDER BY ?c"""

Q_FIELDS = sparql_prefix_block() + f"""SELECT ?c ?pref ?alt ?notation ?docType ?datatype ?group
{_FROM}
WHERE {{
  ?c skos:inScheme fin:FieldConceptScheme ; skos:prefLabel ?pref ; rp:docType ?docType .
  OPTIONAL {{ ?c skos:altLabel ?alt }}
  OPTIONAL {{ ?c skos:notation ?notation }}
  OPTIONAL {{ ?c rp:datatype ?datatype }}
  OPTIONAL {{ ?c rp:unitGroup ?group }}
}} ORDER BY ?c"""


def curie(iri: str) -> str:
    for prefix in ("fin", "doc", "rp", "doco"):
        ns = PREFIXES[prefix]
        if iri.startswith(ns):
            return f"{prefix}:{iri[len(ns):]}"
    return iri


def _group(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        out.setdefault(r["c"], []).append(r)
    return out


def _distinct(rows: list[dict[str, Any]], col: str) -> list[str]:
    seen: list[str] = []
    for r in rows:
        v = r.get(col)
        if v is not None and v not in seen:
            seen.append(v)
    return seen


def build_snapshot(sparql: Any, ontology_version: str) -> VocabSnapshot:
    roles = []
    engines = [str(r["engine"]) for r in bindings_to_rows(sparql.query(Q_ENGINES))[1]]
    for c, rows in _group(bindings_to_rows(sparql.query(Q_ROLES))[1]).items():
        engine_labels: dict[str, list[str]] = {e: [] for e in engines}
        for r in rows:
            if r.get("engine") and r.get("el") and r["el"] not in engine_labels.setdefault(r["engine"], []):
                engine_labels[r["engine"]].append(r["el"])
        engine_labels = {e: sorted(v) for e, v in engine_labels.items()}
        doco = _distinct(rows, "docoExact") or _distinct(rows, "docoClose")
        roles.append(VocabRole(role=rows[0]["role"], concept=curie(c), pref_label=rows[0]["pref"],
                               doco=curie(doco[0]) if doco else None, engine_labels=engine_labels,
                               derived=str(rows[0].get("derived", "false")).lower() == "true"))
    doc_types = []
    for c, rows in _group(bindings_to_rows(sparql.query(Q_DOCTYPES))[1]).items():
        period = _distinct(rows, "period")
        doc_types.append(VocabDocType(concept=curie(c), pref_label=rows[0]["pref"],
                                      short_label=(_distinct(rows, "short") or [rows[0]["pref"]])[0],
                                      alt_labels=_distinct(rows, "alt"), indicators=_distinct(rows, "ind"),
                                      period_kind=period[0] if period else "none"))
    fields = []
    for c, rows in _group(bindings_to_rows(sparql.query(Q_FIELDS))[1]).items():
        notation = _distinct(rows, "notation")
        datatype = _distinct(rows, "datatype")
        group = _distinct(rows, "group")
        fields.append(VocabFieldConcept(concept=curie(c), pref_label=rows[0]["pref"], alt_labels=_distinct(rows, "alt"),
                                        notation=notation[0] if notation else None, doc_type=curie(rows[0]["docType"]),
                                        datatype=datatype[0] if datatype else "decimal",
                                        unit_group=group[0] if group else None))
    return VocabSnapshot(ontology_version=ontology_version, generated_at=iso(), source="neptune", roles=roles,
                         doc_types=doc_types, field_concepts=fields)


def client() -> Any:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = SparqlClient(endpoint=os.environ["RP_NEPTUNE_ENDPOINT"],
                               region=os.environ.get("AWS_REGION", "us-east-1"), timeout=60)
    return _CLIENT


def use_client(c: Any) -> None:
    global _CLIENT
    _CLIENT = c


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    version = event.get("ontologyVersion")
    if not version:
        raise ValueError("ontologyVersion is required")
    snap = build_snapshot(client(), version)
    out: dict[str, Any] = {"ontologyVersion": version, "roles": len(snap.roles), "docTypes": len(snap.doc_types),
                           "fieldConcepts": len(snap.field_concepts)}
    if not (snap.roles and snap.doc_types and snap.field_concepts):
        raise RuntimeError(f"incomplete vocabulary in Neptune for {version}: {out}; is the TBox loaded?")
    if event.get("write", True):
        st = stores()
        key = event.get("key") or s3keys.vocab_key(version)
        st.s3.put_object(Bucket=st.bucket, Key=key, Body=json.dumps(snap.dump(), indent=2).encode(),
                         ContentType="application/json")
        out["key"] = key
    log.info("vocab exported", extra={"rp": out})
    return out

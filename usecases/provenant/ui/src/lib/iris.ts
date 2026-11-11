/**
 * IRI ↔ CURIE for display. Mirrors `evidence_core/iris.py` `PREFIXES` (a test compares the two), so a graph
 * node reads `fibo-sec-dbt-bnd:StepUpBond`, never a 70-character IRI.
 */
export const PREFIXES: Record<string, string> = {
  rdf: "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
  rdfs: "http://www.w3.org/2000/01/rdf-schema#",
  owl: "http://www.w3.org/2002/07/owl#",
  xsd: "http://www.w3.org/2001/XMLSchema#",
  skos: "http://www.w3.org/2004/02/skos/core#",
  prov: "http://www.w3.org/ns/prov#",
  doco: "http://purl.org/spar/doco/",
  "fibo-fnd-agr-ctr": "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/",
  "fibo-fnd-aap-ppl": "https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/People/",
  "fibo-be-le-lp": "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/",
  "fibo-fbc-dae-dbt": "https://spec.edmcouncil.org/fibo/ontology/FBC/DebtAndEquities/Debt/",
  "fibo-sec-dbt-bnd": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/",
  "fibo-sec-dbt-dbti": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/DebtInstruments/",
  fin: "urn:rp:ont:fin:",
  doc: "urn:rp:ont:doc:",
  rp: "urn:rp:ont:rp:",
};

/** `urn:rp:ont:fin:CouponRate` → `fin:CouponRate`; an IRI with no known namespace is returned as is. */
export function curie(iri: string): string {
  for (const [p, ns] of Object.entries(PREFIXES)) if (iri.startsWith(ns)) return `${p}:${iri.slice(ns.length)}`;
  return iri;
}

/** `fin:CouponRate` → ["fin", "CouponRate"]; a label with no prefix → ["", label]. */
export function splitCurie(label: string): [string, string] {
  const m = /^([a-z][\w-]*):(\S.*)$/.exec(label);
  return m && !label.includes(" ") ? [m[1], m[2]] : ["", label];
}

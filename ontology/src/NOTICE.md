# Third-party ontology sources

The files under `fibo/`, `doco/` and `w3c/` are unmodified copies of the upstream files listed below. `build.py`
adds one version node per named graph (`<urn:rp:graph:tbox:…> owl:versionInfo "1.2.0"`) to the built N-Quads; the
copies here are not edited.

| Graph | Files | Upstream, pinned | License |
|---|---|---|---|
| `urn:rp:graph:tbox:fibo` | `fibo/FND/Agreements/Contracts.rdf`, `fibo/FND/AgentsAndPeople/People.rdf`, `fibo/BE/LegalEntities/LegalPersons.rdf`, `fibo/FBC/DebtAndEquities/Debt.rdf`, `fibo/SEC/Debt/DebtInstruments.rdf`, `fibo/SEC/Debt/Bonds.rdf` | EDM Council FIBO, GitHub `edmcouncil/fibo` tag `master_2026Q3`, commit `2d73435a4419b66b7c9f44cb9763e35fe8ac7890` (downloaded 2026-11-03) | MIT, Copyright (c) 2020 Enterprise Data Management Council: `fibo/LICENSE` |
| `urn:rp:graph:tbox:doco` | `doco/doco.ttl` | DoCO, the Document Components Ontology (SPAR Ontologies), `http://purl.org/spar/doco`, version 1.4.0, version IRI `doco:2026-06-25` | CC BY 4.0. Attribution: David Shotton and Silvio Peroni, SPAR Ontologies |
| `urn:rp:graph:tbox:rp` | `w3c/skos.rdf` | W3C SKOS Simple Knowledge Organization System, namespace document 2009-08-18 (`https://www.w3.org/2009/08/skos-reference/skos.rdf`) | W3C Software and Document License |
| `urn:rp:graph:tbox:rp` | `w3c/prov-o.ttl` | W3C PROV-O, Recommendation 2013-04-30 (`https://www.w3.org/ns/prov-o.ttl`) | W3C Software and Document License |

Only a subset of FIBO is included: the modules that hold the classes Provenant uses
(`fibo-sec-dbt-bnd:StepUpBond`, `SteppedCouponTerms`, `BondCoupon`; `fibo-sec-dbt-dbti:StepEvent`;
`fibo-fnd-aap-ppl:Person`; `fibo-be-le-lp:BusinessEntity`). Their `owl:imports` are not followed: no inference is
relied on, and templates use explicit property paths.

`rp/*.ttl` are Provenant's own files (`fin:`, `doc:`, `rp:` under `urn:rp:ont:`), covered by the repository license.
All instance data is fictitious.

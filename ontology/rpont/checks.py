"""The checks behind ``make ontology-check`` (also called from the pytest suite)."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from decimal import Decimal
from pathlib import Path

from evidence_core import iris
from evidence_core.config import LocalConfigSource, load_pipeline
from evidence_core.enums import CanonicalRole
from evidence_core.models.units import VocabSnapshot

from .check_lib import add_package, load_gold, run_template, tbox_graphs, tbox_store, templates_for
from .templates import TEMPLATE_IDS

LENDING_TOTAL = "169650.00"
LENDING_OPERANDS = {"Maya Fictus": "98400.00", "Arjun Fictus": "71250.00"}
REQUIRED_TBOX_CLASSES = (
    "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/StepUpBond",
    "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/SteppedCouponTerms",
    "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/DebtInstruments/StepEvent",
    "https://spec.edmcouncil.org/fibo/ontology/FND/AgentsAndPeople/People/Person",
    "https://spec.edmcouncil.org/fibo/ontology/BE/LegalEntities/LegalPersons/BusinessEntity",
    "http://purl.org/spar/doco/Table",
    "http://purl.org/spar/doco/Footnote",
    "http://www.w3.org/ns/prov#Entity",
    "http://www.w3.org/2004/02/skos/core#Concept",
)

Check = tuple[str, bool, str]


def _close(a: float, b: float, tol: float = 0.01) -> bool:
    return abs(a - b) <= tol


def run_all(dist: Path, samples: Path, repo: Path) -> Iterator[Check]:
    version = dist.name
    templates = templates_for(dist)
    store = tbox_store(dist)
    tbox = tbox_graphs()

    # ---- config, templates, vocab
    lp = load_pipeline("2.3.1", LocalConfigSource(repo))
    yield ("config ontology version", lp.config.ontology_version == version,
           f"v2.3.1 binds {lp.config.ontology_version}, dist is {version}")
    lp10 = load_pipeline("1.0", LocalConfigSource(repo))
    yield ("v1.0 binds no ontology", not lp10.config.graph_enabled, f"graph_enabled={lp10.config.graph_enabled}")
    yield ("templates present", set(templates) == set(TEMPLATE_IDS), ", ".join(sorted(templates)))

    vocab = VocabSnapshot.load(json.loads((dist / "vocab.json").read_text(encoding="utf-8")))
    roles = {r.role for r in vocab.roles}
    yield ("vocab roles cover CanonicalRole", roles == set(CanonicalRole),
           f"{len(roles)} roles; missing {sorted(set(CanonicalRole) - roles)}")
    concepts = {f.concept for f in vocab.field_concepts}
    doc_types = {t.concept for t in vocab.doc_types}
    used_concepts: set[str] = set()
    used_types: set[str] = set()
    for gold_path in sorted(samples.glob("*/gold.json")):
        gold = json.loads(gold_path.read_text(encoding="utf-8"))
        # A new use case's gold.json may carry only the package fields, or documents without regions yet.
        for d in gold.get("documents", []):
            if d.get("docType"):
                used_types.add(d["docType"])
            used_concepts |= {r["concept"] for r in d.get("regions", []) if r.get("concept")}
    yield ("vocab covers sample field concepts", used_concepts <= concepts,
           f"{len(used_concepts)} used; missing {sorted(used_concepts - concepts)}")
    yield ("vocab covers sample doc types", used_types <= doc_types,
           f"{sorted(used_types)}; missing {sorted(used_types - doc_types)}")
    labels: dict[str, str] = {}
    clashes = []
    for f in vocab.field_concepts:
        for lab in [f.pref_label, *f.alt_labels]:
            key = " ".join(lab.lower().split())
            if key in labels and labels[key] != f.concept:
                clashes.append(f"{lab!r}: {labels[key]} / {f.concept}")
            labels[key] = f.concept
        if f.pref_label in f.alt_labels:
            clashes.append(f"{f.concept} prefLabel repeated as altLabel")
    yield ("field labels unambiguous and disjoint", not clashes, "; ".join(clashes) or "ok")

    # ---- TBox stamps and pinned classes
    for key, g in iris.TBOX_GRAPHS.items():
        ask = (f'ASK FROM <{g}> {{ <{g}> <http://www.w3.org/2002/07/owl#versionInfo> "{version}" }}')
        yield (f"version stamp {key}", bool(store.query(ask)), g)
    missing = [c for c in REQUIRED_TBOX_CLASSES
               if not store.query(f"ASK {{ GRAPH ?g {{ <{c}> ?p ?o }} }}")]
    yield ("pinned TBox classes present", not missing, f"missing {missing}" if missing else "ok")
    sub = store.query(
        "ASK { GRAPH ?g { <urn:rp:ont:fin:RatingTriggeredStepEvent> "
        "<http://www.w3.org/2000/01/rdf-schema#subClassOf> "
        "<https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/DebtInstruments/StepEvent> } }")
    yield ("fin:RatingTriggeredStepEvent subClassOf FIBO StepEvent", bool(sub), "")

    # ---- ABox: lending sample
    lending = load_gold(samples / "lending")
    abox = add_package(store, lending, "2.3.1")
    graphs = [*tbox, abox]
    qi = run_template(store, templates["qi_sum_w2_box1_v1"], graphs)
    got = {r["borrower"]: f"{Decimal(r['wagesBox1']):.2f}" for r in qi.rows}
    yield ("qi_sum_w2_box1_v1 lending total", qi.total == LENDING_TOTAL and got == LENDING_OPERANDS,
           f"total {qi.total}; operands {got}")
    yield ("qi_sum_w2_box1_v1 tax year", {r["taxYear"] for r in qi.rows} == {"2025"},
           str(sorted({r["taxYear"] for r in qi.rows})))
    qi_y = run_template(store, templates["qi_sum_w2_box1_v1"], graphs, {"taxYear": "2025"})
    yield ("qi_sum_w2_box1_v1 taxYear=2025", qi_y.total == LENDING_TOTAL, f"total {qi_y.total}")
    qi_none = run_template(store, templates["qi_sum_w2_box1_v1"], graphs, {"taxYear": "2026"})
    yield ("qi_sum_w2_box1_v1 taxYear=2026 has no W-2", qi_none.rows == [], f"{len(qi_none.rows)} rows")

    gold_box1 = {}
    for d in lending["documents"]:
        if d["docType"] == "doc:W2":
            r = next(x for x in d["regions"] if x["regionId"] == "w2-box1")
            gold_box1[r["borrower"]] = (d["fileName"], r["bbox"])
    for op in qi.operands:
        fl = run_template(store, templates["field_lookup_v1"], graphs,
                          {"concept": "fin:W2WagesBox1", "borrower": op["label"]})
        rows = [r for r in fl.rows if r["field"] == iris.field_iri(op["unitId"], op["fieldId"])]
        file_name, gb = gold_box1[op["label"]]
        ok = len(rows) == 1 and rows[0]["fileName"] == file_name and all(
            _close(float(rows[0][k]), gb[k]) for k in ("x0", "y0", "x1", "y1")) and int(rows[0]["page"]) == gb["page"]
        yield (f"operand {op['label']} -> field region", ok,
               f"{op['unitId']}/{op['fieldId']} page {rows[0]['page'] if rows else '?'}")

    lineage_unit = qi.operands[0]["unitId"] if qi.operands else ""
    ul = run_template(store, templates["unit_lineage_v1"], graphs, {"unitId": lineage_unit})
    doc_sha = {d["fileName"]: d["sha256"] for d in lending["documents"]}
    ok = bool(ul.rows) and all(r["sha256"] == doc_sha[r["fileName"]] and r.get("stageId") == "units"
                               and r["package"] == iris.package_iri(lending["packageId"]) for r in ul.rows)
    yield ("unit_lineage_v1 unit -> region -> source", ok,
           f"{len(ul.rows)} row(s) for {lineage_unit}: {ul.rows[0]['fileName'] if ul.rows else '-'}")

    ytd = run_template(store, templates["field_lookup_v1"], graphs, {"concept": "fin:PayslipGrossPayYtd"})
    yield ("payslip YTD facts exist and are not operands",
           len(ytd.rows) == 2 and not {r["field"] for r in ytd.rows} & {r["field"] for r in qi.rows},
           ", ".join(f"{r.get('borrower')} {r.get('amount')} {r.get('period')}" for r in ytd.rows))

    held = run_template(store, templates["field_lookup_v1"], graphs, {"concept": "fin:BankAccountNumber"})
    deposits = run_template(store, templates["field_lookup_v1"], graphs, {"concept": "fin:BankDepositAmount"})
    yield ("held account header absent, deposits present", not held.rows and len(deposits.rows) == 3,
           f"account-number facts {len(held.rows)}; deposit facts {len(deposits.rows)}")

    # ---- labels -> concepts
    probes: list[tuple[str, str]] = [
        ("1 Wages, tips, other compensation", "urn:rp:ont:fin:W2WagesBox1"),
        ("wages,  tips, other COMPENSATION", "urn:rp:ont:fin:W2WagesBox1"),
        ("Box 3", "urn:rp:ont:fin:W2SocialSecurityWagesBox3"),
        ("Total income for a lending package", "urn:rp:ont:fin:QualifyingIncome"),
        ("Coupon", "urn:rp:ont:fin:CouponRate"),
    ]
    for label, expected in probes:
        cl = run_template(store, templates["concept_by_label_v1"], tbox, {"label": label})
        found = sorted({r["concept"] for r in cl.rows})
        yield (f"concept_by_label_v1 {label!r}", found == [expected], ", ".join(found) or "no concept")

    # ---- neighbourhood (API-only, W-09/W-11): the concept's TBox triples and the package's fields that carry it
    box1 = "urn:rp:ont:fin:W2WagesBox1"
    nb = run_template(store, templates["neighbourhood_v1"], [*tbox, abox], {"iri": box1})
    incoming = {r["s"] for r in nb.rows if r.get("o") == box1 and r["s"].startswith("urn:rp:field:")}
    labels = {r["o"] for r in nb.rows if r.get("s") == box1 and r["p"].endswith("altLabel")}
    yield ("neighbourhood_v1 fin:W2WagesBox1", "Box 1" in labels and len(incoming) == 2,
           f"{len(nb.rows)} rows; altLabels {sorted(labels)}; fields in lending {len(incoming)}")
    nb2 = run_template(store, templates["neighbourhood_v1"], [*tbox, abox], {"iri": box1, "depth": "2"})
    yield ("neighbourhood_v1 depth 2 adds the second hop", any(r.get("hop") == "2" for r in nb2.rows) and
           len(nb2.rows) > len(nb.rows), f"{len(nb2.rows)} rows")
    nb0 = run_template(store, templates["neighbourhood_v1"], tbox, {"iri": box1})
    yield ("neighbourhood_v1 without package scope: TBox only", not any(r["s"].startswith("urn:rp:field:")
                                                                        for r in nb0.rows), f"{len(nb0.rows)} rows")

    # ---- lanes and scope
    v10 = run_template(store, templates["qi_sum_w2_box1_v1"], [*tbox, iris.abox_graph(lending["packageId"], "1.0")])
    yield ("v1.0 lane: no ABox, zero rows", v10.rows == [], f"{len(v10.rows)} rows")
    no_scope = run_template(store, templates["qi_sum_w2_box1_v1"], tbox)
    yield ("caller without package scope: zero rows", no_scope.rows == [], f"{len(no_scope.rows)} rows")

    # ---- second lending package (seeded)
    lending2 = load_gold(samples / "lending-02")
    abox2 = add_package(store, lending2, "2.3.1")
    qi2 = run_template(store, templates["qi_sum_w2_box1_v1"], [*tbox, abox2])
    expected2 = lending2["expected"]["qualifyingIncome"]
    yield ("qi_sum_w2_box1_v1 lending-02", qi2.total == expected2, f"total {qi2.total}, expected {expected2}")
    iso = run_template(store, templates["qi_sum_w2_box1_v1"], [*tbox, abox])
    yield ("package graphs isolated", iso.total == LENDING_TOTAL, f"lending total with both loaded: {iso.total}")


def failures(results: list[Check]) -> list[Check]:
    return [r for r in results if not r[1]]


RunAll = Callable[[Path, Path, Path], Iterator[Check]]

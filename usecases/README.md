# Use cases

A use case is an application built from the repository's building blocks for one domain: its documents, its
ontology (document types, field concepts, governed definitions), its pipeline configuration, its prompt, its
golden questions and, if it needs one, its UI.

| Use case | Domain | What it shows |
|---|---|---|
| [Provenant](provenant/README.md) | Lending files, quarterly reports, bond trade confirmations (fictitious) | Two pipeline versions side by side through one agent; every figure in an answer opens the page region it came from |

To build your own, follow [Build your own use case](../docs/quickstarts/10-build-your-own-use-case.md). In this
release the domain files of a use case still live at the repository root, so one checkout holds one domain. The
next section lists them.

## What is shared and what is Provenant

The building blocks do not know about lending, quarterly reports or bonds. Provenant's domain knowledge is in
data files, plus a few small code spots. In this release (v0.1) those files still sit at the repository root,
next to the shared code, so they are easy to mistake for part of the toolkit. This is the exact split.

**Provenant's files** (replace or extend these for your own domain):

| What | Where |
|---|---|
| Document types (W-2, payslip, bank statement, quarterly report, trade confirmation) | `ontology/src/rp/doctypes.ttl` |
| Field concepts and printed labels, `fin:QualifyingIncome`, step events | `ontology/src/rp/fin.ttl` |
| FIBO subset | `ontology/src/fibo/` |
| Domain SPARQL templates | `ontology/templates/qi_sum_w2_box1_v1.rq`, `ontology/templates/coupon_terms_v1.rq` |
| The template ids registered in code | `GRAPH_TEMPLATES` (`packages/evidence-core/evidence_core/models/tools.py`), `TEMPLATE_SPECS` (`services/lambdas/tools/_lib/templates.py`), `TOTAL_TEMPLATES` (`services/lambdas/tools/graph_hop/handler.py`) |
| Answer prompt, with the qualifying-income definition | `prompts/answer_v1.txt` |
| Golden questions | `eval/golden.jsonl` |
| Fictitious document generator and samples | `data/` |
| Logged agent runs and recorded AWS responses | `runs/local/` |
| Pipeline version values (ontology version, PII rule name), models, list prices | `config/pipelines/`, `config/models.yaml`, `config/prices.yaml` |
| Amazon Cognito groups and demo users | `USER_GROUPS` in `infra/lib/names.ts`, `scripts/lib/rp_ops/auth.py` |
| Domain rules still in code | Period forms and party names (`services/lambdas/ingest_lib/periods.py`); the coupon step-event rule and party links (`services/lambdas/ingest_lib/rdf.py`); currency and "in millions" forms (`UNIT_LABEL_RE` in `services/lambdas/eu_builder/builder.py`) |
| The recorded COA session (optional governed-metric cross-check) | `runs/local/coa/<date>/`, read by `ontology/rpont/coa_snapshot.py` |
| The web UI | `usecases/provenant/ui/` |

**Shared building blocks** (use as they are): `packages/evidence-core/`, the stage handlers and libraries in
`services/lambdas/` (apart from the code spots above), `services/agent/`, `services/local_pipeline/`,
`services/local_agent/`, the generic ontology files (`ontology/src/rp/doc.ttl`, `ontology/src/rp/roles.ttl`,
DoCO, SKOS, PROV-O), the generic SPARQL templates (`field_lookup_v1`, `concept_by_label_v1`, `unit_lineage_v1`,
`neighbourhood_v1`), the ontology build tooling (`ontology/rpont/`), the evaluation harness (`eval/run.py`,
`eval/metrics.py`), `infra/` and `scripts/`.

**Why the names say `rp`.** `rp` is the reference pipeline's resource and namespace prefix. It is on AWS
resource names, environment variables, IRIs and the `ontology/src/rp/` folder. Renaming it changes deployed
resource names and every IRI in the graph, so it stays in v0.1 (see the note in the root README).

**What changes next.** The next release moves Provenant's files into a "domain pack" under
`usecases/provenant/`, selected by one setting, and reads the SPARQL template registry and the code-spot rules
from data. Until then, one checkout holds one domain, and the simplest path for your own use case is a fork.

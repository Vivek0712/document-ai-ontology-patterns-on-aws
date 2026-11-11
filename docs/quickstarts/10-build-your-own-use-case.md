# Build your own use case

This guide takes you from a folder of your own PDFs to cited answers over them, using the same pipeline, ontology
tooling, agent and evaluation harness as the Provenant use case. It ends with a worked sketch in a different
domain: supplier invoices.

| | |
|---|---|
| **Time** | 1–2 hours for a first pass over a handful of documents |
| **Needs** | [Quickstart 1](01-run-locally.md) done. [Quickstart 2](02-deploy-to-aws.md) to deploy |
| **Cost** | Steps 1–7 run locally at no cost. Step 8 (agent runs) uses Amazon Bedrock. Step 10 deploys to AWS |

> **Use fictitious or properly approved data while you experiment.** Do not put real personal data into a
> sandbox. If you need realistic documents, the generator in `data/docgen/` shows how to build fictitious ones
> with known gold regions.

## How much is configuration, and how much is code

Most of a new use case is data: documents, ontology files, YAML and a golden set. A few domain rules are still
code in this release. The table tells you which is which, so you know where to look.

| You want to change | Where | Kind |
|---|---|---|
| Documents | A folder of PDFs, plus an optional `gold.json` with the package id, name and allowed groups | Data |
| Document types | `ontology/src/rp/doctypes.ttl`: a SKOS concept with `rp:indicator` phrases | Data |
| Field concepts and their printed labels | `ontology/src/rp/fin.ttl`: a concept with `skos:prefLabel` and `skos:altLabel` | Data |
| Layout roles, engine label mapping | `ontology/src/rp/roles.ttl` | Data |
| Unit rules (τ, marker link, residual size, review threshold) | `preprocessing.units` in `config/pipelines/v2.3.1.yaml` | Data |
| PII rule (entity types, score, digit thresholds) | `preprocessing.piiGate` in the pipeline config | Data |
| Governed definitions the agent must use | `fin.ttl` (definition, operand concept) and `prompts/answer_v1.txt` | Data |
| Golden questions | `eval/golden.jsonl` | Data |
| Models | `config/models.yaml` | Data |
| Currency and "in millions" label forms | `UNIT_LABEL_RE` in `services/lambdas/eu_builder/builder.py` | Code |
| Period forms and party names | `services/lambdas/ingest_lib/periods.py` | Code |
| Footnote-to-property rules, party links in the graph | `services/lambdas/ingest_lib/rdf.py` | Code |
| A new approved SPARQL template | The `.rq` file (data) plus its id in three registries (code, one line each) | Both |
| Access groups on AWS | `USER_GROUPS` in `infra/lib/names.ts`, demo users in `scripts/lib/rp_ops/auth.py` | Code |

In this release the repository holds **one domain at a time**: the pipeline configs, prompt and ontology are
shared by everything in the checkout. The simplest path is a fork for your use case. A planned "domain pack"
layout will let several use cases live side by side, each in its own folder.

## Step 1. Bring your documents

Put 3–5 representative PDFs in one folder, for example `data/samples/invoices/`. Mix what you really receive:
digital and scanned, one page and several, clean and messy. Add a `gold.json` to name the package:

```json
{"packageId": "pkg-invoices-01", "name": "Supplier invoices · September", "allowedGroups": ["ap-analyst"]}
```

Package ids must match `pkg-` followed by lowercase letters, digits and hyphens. Without `gold.json`, the local
runner uses the lending defaults. These three fields are all that `make ontology-check` and `make e2e-local` need.
You add a `documents` list in step 9.

## Step 2. See what the naive lane and the evidence-unit lane make of them

```bash
make units PACKAGE=data/samples/invoices
```

Open `build/units/diff.md` before you change anything. It shows, per document, the chunks, the units, the fields
found, and which values the naive lane separated from their labels. The `## <file> (<docId>)` headings give you
the document ids you need later. At this point classification will be wrong or weak, because the ontology does not
know your document types yet. That is expected.

## Step 3. Define your document types

Add a concept to `ontology/src/rp/doctypes.ttl`. Indicator phrases are words printed on every document of that
type, and rarely on others:

```turtle
doc:Invoice a owl:Class, skos:Concept ;
    rdfs:subClassOf doc:Document ;
    skos:inScheme doc:DocTypeScheme ;
    skos:prefLabel "Supplier invoice" ;
    skos:altLabel "Tax invoice", "Commercial invoice" ;
    rp:shortLabel "Invoice" ;
    rp:periodKind "statement_period" ;
    rp:indicator "Invoice number", "Invoice date", "Due date", "Bill to", "Amount due", "Remit to" ;
    skos:definition "A request for payment issued by a supplier for goods or services delivered." .
```

`rp:periodKind` picks how the period is read: `tax_year`, `pay_period`, `statement_period` (a month such as
`2026-09`), `quarter`, or `none`. Other period forms need a change in `periods.py`.

## Step 4. Define your field concepts, with the labels as printed

Add the fields you want to answer questions about to `ontology/src/rp/fin.ttl`. List every label variant you
have seen on real documents as `skos:altLabel`. This is where most of the extraction quality comes from.

```turtle
fin:InvoiceTotalDue a skos:Concept ; skos:inScheme fin:FieldConceptScheme ;
    skos:prefLabel "Invoice total due" ;
    skos:altLabel "Amount due", "Total due", "Balance due", "Invoice total" ;
    rp:docType doc:Invoice ; rp:datatype "decimal" ; rp:unitGroup "Totals" ;
    skos:definition "The amount the supplier asks to be paid for this invoice, including tax." .

fin:InvoiceNumber a skos:Concept ; skos:inScheme fin:FieldConceptScheme ;
    skos:prefLabel "Invoice number" ;
    skos:altLabel "Invoice No.", "Invoice #", "Inv. no." ;
    rp:docType doc:Invoice ; rp:datatype "text" ; rp:unitGroup "Header" .

fin:InvoiceDueDate a skos:Concept ; skos:inScheme fin:FieldConceptScheme ;
    skos:prefLabel "Invoice due date" ;
    skos:altLabel "Due date", "Payment due", "Pay by" ;
    rp:docType doc:Invoice ; rp:datatype "text" ; rp:unitGroup "Header" .
```

About the `fin:` prefix: in this release, field concepts live in `fin:FieldConceptScheme` and use the `fin:`
prefix, which the generic templates expect. Read it as "field concept", whatever your domain. A namespace of your
own (for example `inv:`) also needs its prefix registered in `packages/evidence-core/evidence_core/iris.py`
(`PREFIXES`) and in your templates; check it with `make ontology-check` before relying on it.

If a definition matters (a figure people argue about), make it explicit, as `fin:QualifyingIncome` does:

```turtle
fin:OpenPayables a owl:Class ;
    rdfs:subClassOf fin:DefinedQuantity ;
    rdfs:label "Open payables" ;
    skos:altLabel "Total amount due across this package" ;
    rdfs:comment "Open payables means the sum of the invoice total due over every invoice in the package. Credit notes are not included." ;
    fin:operandConcept fin:InvoiceTotalDue ;
    rp:computedBy "open_payables_sum_v1" .
```

Then build and check:

```bash
make ontology-check
make units PACKAGE=data/samples/invoices
```

`ontology-check` fails when a check does not hold, for example when two field concepts share a printed label or a
concept has no document type. In the new `diff.md`, your documents should now be classified, and your fields
should carry concepts.

The checks in `ontology/rpont/checks.py` also include Provenant's own expectations (its sample document types,
the lending total of 169,650.00). Keep the Provenant ontology files while you add yours, and those checks keep
passing. If you remove them, replace those checks with equivalent ones for your domain: they are your regression
tests for the knowledge plane.

When you change the ontology for real, raise `ontologyVersion` in `ontology/src/manifest.json` and
`preprocessing.ontology.version` in `config/pipelines/v2.3.1.yaml` together.

## Step 5. Tune units and the PII rule

Look at `build/units/v2.3.1/*.build_report.json` for documents whose units look wrong. Most fixes are config:

```yaml
# config/pipelines/v2.3.1.yaml
preprocessing:
  units: { tau: 0.40, phaseB: cosine, markerLink: hard-rule, residual: section_text, maxResidualTokens: 400,
           fieldBbox: true, bboxFrame: pdf-points-top-left, reviewMinConfidence: 0.80 }
  piiGate: { engine: comprehend, action: hold, rule: rp-pii-v1, minScore: 0.80,
             entityTypes: [BANK_ACCOUNT_NUMBER, BANK_ROUTING, CREDIT_DEBIT_NUMBER],
             unmaskedMinDigits: { BANK_ACCOUNT_NUMBER: 8, BANK_ROUTING: 9, CREDIT_DEBIT_NUMBER: 12 } }
```

Invoices often print the supplier's bank details on a remittance slip. Holding that unit keeps the account number
out of search while the totals stay searchable. Check the entity types Amazon Comprehend supports in the
[`DetectPiiEntities` documentation](https://docs.aws.amazon.com/comprehend/latest/dg/how-pii.html), and give each
type you add a digit threshold.

Keep `config/pipelines/v1.0.yaml` in step: the two versions may differ only in `preprocessing`, and a test checks
that. If your currency or "in thousands" forms are not covered by `UNIT_LABEL_RE`, extend it in
`services/lambdas/eu_builder/builder.py`.

## Step 6. Run both lanes to `ready` and query the graph

```bash
make e2e-local PACKAGE=data/samples/invoices E2E_OUT=build/e2e-invoices PII=local
```

`E2E_OUT` keeps your build apart from the lending build in `build/e2e`, which `make xray-fixtures`,
`make mock-world` and `make local-ask` read by default. To ask questions against your build, pass
`BUILD=build/e2e-invoices` to `make local-ask`.

The run ends with the lending check line. For a package with no W-2s, it says
`graph_hop qi_sum_w2_box1_v1 skipped: this package has no W-2 Box 1 fields`. That is expected: the check template
is the lending one.

Every field becomes a `fin:FieldValue` in `build/e2e-invoices/graph/abox.nq`, with its concept, amount or text,
period and lineage. You can ask the graph right away with the generic templates, without writing any SPARQL:

| Question | Template and parameters |
|---|---|
| "What is the amount due on each invoice?" | `field_lookup_v1`, `concept = fin:InvoiceTotalDue` |
| "Which concept is 'Balance due'?" | `concept_by_label_v1`, `label = Balance due` |
| "Where did this unit come from?" | `unit_lineage_v1`, `unitId = …` |

For a governed total, add a template such as `ontology/templates/open_payables_sum_v1.rq`. Copy the header
comment block and the `{{FROM}}` line from `qi_sum_w2_box1_v1.rq`, return one row per operand with an `?amount`,
`?unit` and `?field` column, and register its id:

- `GRAPH_TEMPLATES` in `packages/evidence-core/evidence_core/models/tools.py`;
- `TEMPLATE_SPECS` in `services/lambdas/tools/_lib/templates.py` (its required and optional parameters);
- `TOTAL_TEMPLATES` in `services/lambdas/tools/graph_hop/handler.py` (`{"open_payables_sum_v1": "amount"}`), so the
  tool sums the operands.

Then `make contracts` (regenerates the MCP tool schema) and `make ontology-check` (runs every template).

## Step 7. Write golden questions

Write 10–20 questions across the types you care about (`table`, `prose`, `figure`, `footnote`), each tied to the
region where the answer is printed. Append them to `eval/golden.jsonl`:

```json
{"qid": "q-inv-01", "type": "table", "question": "What is the amount due on invoice INV-0042?",
 "answer": "12,480.00", "answerPattern": "(?<![\\d,])\\$?12,?480(?:\\.00)?(?!\\.?\\d)",
 "packageId": "pkg-invoices-01", "docId": "<docId from diff.md>", "fileName": "invoice_0042.pdf",
 "page": 1, "goldBbox": {"page": 1, "x0": 410.0, "y0": 612.0, "x1": 540.0, "y1": 630.0},
 "headline": true, "aliases": ["How much is due on INV-0042?"]}
```

Boxes are in PDF points, top-left origin, 1-based pages: the same frame as the unit and field boxes in your build
output, so you can copy a field's `bbox` from the units file once you have checked it on the page.

`make data` regenerates `eval/golden.jsonl` from the Provenant generator. Once you add your own questions, do
not run `make data`, or keep your questions in a separate file and pass it with `--golden`.

Score retrieval offline:

```bash
make units PACKAGE=data/samples/invoices
.venv/bin/python eval/run.py run --mode local --units-dir build/units --package-id pkg-invoices-01
```

## Step 8. Ask the agent, N of N (Amazon Bedrock)

Update the definitions section of `prompts/answer_v1.txt` for your domain: the assistant's role and the governed
definitions it must apply. Keep the output contract (the JSON with `claims`) as it is; the citation validator
depends on it. Then:

```bash
make local-ask QID=q-inv-01 REPEAT=5 BUDGET=0.5 BUILD=build/e2e-invoices
make local-ask-summary
```

Compare the two lanes. If the evidence-unit lane is not clearly better on your documents, look at `diff.md` and
the build reports before you change the model or the prompt. Most RAG failures start in preprocessing.

## Step 9. Read the X-ray

The X-ray shows what the naive lane saw on each page: its chunks drawn over the page, next to your evidence units
and field boxes, with held PII regions filled. The export reads the list of documents from the package's
`gold.json`, so add one entry per file (take the `docId` from `build/e2e-invoices/diff.md` or `run.json`).
`fileName` and `docId` are enough; `docType` and `regions` are optional:

```json
{"packageId": "pkg-invoices-01", "name": "Supplier invoices · September", "allowedGroups": ["ap-analyst"],
 "documents": [{"fileName": "invoice_0042.pdf", "docId": "<docId from diff.md>"}]}
```

Then build it for your package and open it in the UI's mock mode:

```bash
PYTHONPATH=services .venv/bin/python scripts/xray_fixtures.py --package data/samples/invoices --build build/e2e-invoices
make mock-world
cd usecases/provenant/ui && npm run dev:mock      # Workbench → Units
```

Until your package has golden questions with logged runs (steps 7 and 8), the export says
`no logged naive run in --runs: answerNumbers left out`. The X-ray itself is complete.

Use it to explain your results to people who do not read JSON.

The mock world and the guided showcases were built around the Provenant packages. If `make mock-world` stops
with an error for your package, the X-ray export itself is still in `usecases/provenant/ui/mock-public/mock/xray/<packageId>/` (page
renders and one JSON file per page), and on AWS the workbench shows your package directly.

## Step 10. Deploy

When the local results hold up:

1. Add your access groups to `USER_GROUPS` in `infra/lib/names.ts`, and adjust the demo users in
   `scripts/lib/rp_ops/auth.py` if you use them.
2. Deploy as in [Quickstart 2](02-deploy-to-aws.md). The deploy copies `config/`, `prompts/` and the ontology build
   to the data bucket, and the seed step loads your TBox into Amazon Neptune.
3. Create a package in the UI, upload your PDFs, and wait for both lanes to be ready.
4. Run the golden set against the stack and keep the result as your baseline:

   ```bash
   .venv/bin/python eval/run.py run --mode ask --package-id <your package id> --qid q-inv-01 --repeat 5
   ```

From then on, every change is a new pipeline version, measured against that baseline with `eval/run.py compare`.

## Worked sketch: supplier invoices

A summary of the decisions for an accounts-payable team that wants "How much do we owe supplier X, and where does
each figure come from?"

| Decision | Choice |
|---|---|
| Documents | Supplier invoices (mostly digital, some scanned), credit notes, remittance slips |
| Document types | `doc:Invoice`, `doc:CreditNote`, each with 5–6 indicator phrases |
| Field concepts | `fin:InvoiceNumber`, `fin:InvoiceDate`, `fin:InvoiceDueDate`, `fin:SupplierName`, `fin:InvoiceSubtotal`, `fin:InvoiceTax`, `fin:InvoiceTotalDue`, `fin:CreditNoteAmount`, each with the label variants your suppliers print |
| Evidence units | The line-item table with its "amounts in EUR" label and footnotes; the totals block as a field group; terms and conditions as section text |
| Footnote that becomes a property | "2 % discount if paid within 10 days" next to the total. A rule in `rdf.py`, like the coupon step-up, writes `fin:EarlyPaymentDiscount` with a rate and a day count |
| Governed definition | `fin:OpenPayables`: the sum of `fin:InvoiceTotalDue` minus `fin:CreditNoteAmount` for one supplier, computed by `open_payables_sum_v1` |
| PII rule | Hold units with an unmasked `BANK_ACCOUNT_NUMBER` or `BANK_ROUTING` (remittance slips) |
| Metadata filters | Package per supplier and month; groups `ap-analyst` and `ap-approver` |
| Golden questions | 15: totals per invoice (table), due dates (table), discount terms (footnote), supplier address (prose), one open-payables total per supplier (headline) |
| Success measure | RegionHit@1 on table and footnote questions, and N-of-N citation validity on the headline question, both lanes |

The same steps work for insurance claims (claim form, adjuster report, policy schedule; "covered amount" as the
governed definition; policy exclusions as footnotes that become properties) and for clinical or regulatory forms.

## Next

- Read the pattern quickstarts for the detail behind each step:
  [extraction](03-layout-aware-extraction.md), [units](04-evidence-units.md),
  [ontology](05-ontology-binding-and-sparql.md), [filters](06-metadata-and-filtered-hybrid-search.md),
  [agent](07-agent-resolve-retrieve-hop-cite.md), [PII](08-pii-gate.md), [evaluation](09-evaluation-harness.md).
- See how Provenant composes the same pieces: [`usecases/provenant/README.md`](../../usecases/provenant/README.md).

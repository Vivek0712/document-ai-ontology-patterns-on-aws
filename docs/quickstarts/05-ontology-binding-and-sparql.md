# Quickstart 5: ontology binding and SPARQL templates (pattern 3)

**Pattern:** decide the meaning before you extract. Document types, field concepts, printed label variants and
governed definitions live in an ontology. Ingestion binds each document and each field to it, and writes facts
to a knowledge graph. A footnote that changes the meaning of a number becomes a typed property in the graph, not
a sentence a model may or may not read.

| | |
|---|---|
| **Plane** | Ingestion → knowledge |
| **Time** | 20 minutes |
| **Needs** | [Quickstart 1](01-run-locally.md) done, with Docling installed |
| **Cost** | None locally. On AWS: Amazon Neptune, and Amazon Bedrock for the occasional classification tie-break |

Why this pattern exists and how to adopt it without the rest of the pipeline: [methodology page](../methodology/03-ontology-driven-classification.md).

## The problem

"What is the total income across this package?" sounds like a retrieval question. It is a definition question.
For a lending package, total income means *qualifying income*: the sum of W-2 Box 1 wages for the latest tax year,
per borrower. Payslips and bank deposits corroborate it and are not added. A model that searches text will find
several plausible numbers (Box 1, Box 3, Box 5, year-to-date gross pay, deposits) and may add the wrong ones.

The trade confirmations carry a second trap. The coupon reads `4.25%` on page 1, with a small raised `3`. Footnote
3 on page 2 says `6.00% from year 3 if rating < BBB-`. A chunk with the coupon and no footnote gives a wrong
answer that looks right.

## The pattern

1. **Type documents from the ontology.** Each document type is a SKOS concept with indicator phrases
   (`rp:indicator`). The `classify` stage scores the extracted text against them. Only when the top two types are
   closer than `minMargin` (0.15) does a small model (Amazon Nova 2 Lite) break the tie, and it may only choose
   among the candidates it is shown.
2. **Bind fields to concepts by their printed labels.** A field concept lists the labels that appear on real
   documents as `skos:altLabel` (`"1 Wages, tips, other compensation"`, `"Box 1"`). A new label variant is a
   vocabulary change, never a code change.
3. **Write facts, with lineage.** `graph_write` turns every field into a `fin:FieldValue` with its concept, amount,
   period and party, linked with PROV-O to the unit and the page region it came from.
4. **The footnote becomes a property.** When a footnote is linked to a field by its marker
   ([pattern 2](04-evidence-units.md)) and its text matches a known rule, the pipeline writes a
   `fin:RatingTriggeredStepEvent` (a subclass of FIBO's `StepEvent`) with `fin:newRate`,
   `fin:effectiveFromYear` and `fin:triggerRating`.
5. **Govern definitions once.** `fin:QualifyingIncome` states its operand concept (`fin:W2WagesBox1`) and the
   template that computes it (`qi_sum_w2_box1_v1`). The answer prompt quotes the same definition.
6. **Query with approved templates only.** The agent's `graph_hop` tool runs a fixed set of parameterized SPARQL
   templates. The model picks a template id and string parameters. The tool escapes them as literals, checks the
   caller's access to the package, and only then adds that package's graph to `FROM`.

The ontology uses a pinned subset of [FIBO](https://spec.edmcouncil.org/fibo/) (MIT), the
[DoCO](http://purl.org/spar/doco) document components ontology (CC BY 4.0), and W3C
[SKOS](https://www.w3.org/2004/02/skos/) and [PROV-O](https://www.w3.org/TR/prov-o/). See
`ontology/src/NOTICE.md` for sources and licenses.

## See it

### Build and check the ontology

```bash
make ontology-check
ls ontology/dist/1.2.0/
```

`ontology/dist/1.2.0/` holds the TBox as N-Quads per named graph, `vocab.json` (the snapshot the ingestion stages
read: document types and indicators, field concepts and labels, layout roles), the rendered templates, and the
governance record for the qualifying-income definition.

### The governed definition and its operand

```bash
grep -n -A 7 "^fin:QualifyingIncome" ontology/src/rp/fin.ttl
grep -n -A 4 "^fin:W2WagesBox1" ontology/src/rp/fin.ttl
```

### The governance record, and the optional COA snapshot

`make ontology-check` also writes `ontology/dist/1.2.0/governance/qualifying_income.json`: the definition as
built (label, comment, superclasses, operand concept, the template that computes it), the sha256 of the Turtle
source and of the template, and the check results. By default (`COA_MODE=snapshot`) it adds a `coa` section read
from a **recorded** session with the
[AWS Context Ontology Accelerator (COA)](https://github.com/aws/context-ontology-accelerator) in
`runs/local/coa/2026-11-11/`. Nothing calls COA:

```bash
python3 -c "import json; c=json.load(open('ontology/dist/1.2.0/governance/qualifying_income.json'))['coa']; s=c['snapshot']; print(c['mode'], c['snapshotAt']); print(s['ontology']['label'], '· matches Git:', s['ontology']['matchesGitSource']); x=s['crossCheck']; print(x['metric'], 'tier', x['tier'], 'confidence', x['confidence'], x['value'], f\"{x['identicalRuns']} of {x['runs']} identical\")"
```

```text
snapshot 2026-11-11
uploaded, not steward-reviewed · matches Git: True
qualifying_income tier 1 confidence 1.0 169650.00 5 of 5 identical
```

The vocabulary was uploaded to COA as Turtle, so COA did not steward-review it. COA's metric is a SQL query run by
Amazon Athena over a table of the same W-2 Box 1 amounts: an independent computation of the same definition.
`make ontology-check COA_MODE=off` writes the record without the snapshot. The use case README explains every
field: [Governed meaning and the COA cross-check](../../usecases/provenant/README.md#governed-meaning-and-the-coa-cross-check).

### The answer, computed by the graph

After `make e2e-local PII=local`, `build/e2e/graph/qi_sum.json` holds the template, the exact SPARQL that ran,
the graphs in `FROM`, the rows and the total:

```bash
python3 -c "import json; r=json.load(open('build/e2e/graph/qi_sum.json'))['results']['2.3.1']; print(r['total']); [print(o) for o in r['operands']]"
```

```text
169650.00
{'unitId': 'w2-2025-arjun-fictus-546450:2.3.1:eu-02', 'fieldId': 'f-box1', 'value': '71250.00', 'label': 'Arjun Fictus'}
{'unitId': 'w2-2025-maya-fictus-827d37:2.3.1:eu-02', 'fieldId': 'f-box1', 'value': '98400.00', 'label': 'Maya Fictus'}
```

The total is computed by the tool from the graph rows, not by the model. Each operand names its unit and field,
which carry the page box.

### The footnote as a property

Build the trade confirmations through to the graph:

```bash
PYTHONPATH=services .venv/bin/python -m local_pipeline.run \
  --package data/samples/tradeconf --out build/graph-tradeconf --until ready --pii local
```

Then query the graph the pipeline wrote, with pyoxigraph (installed by `make venv`):

```bash
.venv/bin/python - <<'EOF'
from pyoxigraph import RdfFormat, Store

store = Store()
store.load(path="build/graph-tradeconf/graph/abox.nq", format=RdfFormat.N_QUADS)
query = """
PREFIX fin: <urn:rp:ont:fin:>
SELECT ?event ?newRate ?fromYear ?trigger WHERE {
  GRAPH ?g {
    ?event a fin:RatingTriggeredStepEvent ;
           fin:newRate ?newRate ; fin:effectiveFromYear ?fromYear ; fin:triggerRating ?trigger .
  }
} ORDER BY ?event
"""
for row in store.query(query):
    print(row["event"].value, row["newRate"].value, row["fromYear"].value, row["trigger"].value)
EOF
```

```text
urn:rp:step:trade-confirmation-xs2031-9af5c5:2.3.1:eu-04/f-coupon-rate-at-issue 6 3 BBB-
urn:rp:step:trade-confirmation-xs2032-5f0fa4:2.3.1:eu-04/f-coupon-rate-at-issue 5.75 3 BBB-
urn:rp:step:trade-confirmation-xs2033-cb595e:2.3.1:eu-04/f-coupon-rate-at-issue 5.5 2 BB+
```

"Which rating event triggers the XS-2031 coupon step-up?" is now a graph lookup with a page region behind it.
The approved template for it is `ontology/templates/coupon_terms_v1.rq`.

In the UI (mock mode), **Showcases → The footnote becomes a property** shows the same chain: classification, the
marker link across the page break, the text turned into properties, and the graph path.

## The approved templates

| Template | Parameters | Answers |
|---|---|---|
| `qi_sum_w2_box1_v1` | `taxYear`, `borrower` (optional) | Qualifying-income operands; `graph_hop` sums them into `total` |
| `field_lookup_v1` | `concept` (required), `borrower`, `taxYear` | Values of one field concept, with unit and region |
| `concept_by_label_v1` | `label` (required) | Which concept a printed label means |
| `unit_lineage_v1` | `unitId` (required) | Where a unit came from: document, regions, stage activity |
| `coupon_terms_v1` | `security` (required) | Coupon at issue and the step event read from its footnote |

Each `.rq` file starts with a comment header (`template`, `purpose`, `params`, `columns`, `graphs`, `notes`).
`{{FROM}}` is replaced by one `FROM <graph>` line per graph the caller may read.

## Read the code

| What | Where |
|---|---|
| Document types and indicators | `ontology/src/rp/doctypes.ttl` |
| Field concepts, printed labels, governed definitions, step events | `ontology/src/rp/fin.ttl` |
| Document, unit, region and activity classes; layout roles | `ontology/src/rp/doc.ttl`, `ontology/src/rp/roles.ttl` |
| Named graphs and pinned upstream sources | `ontology/src/manifest.json`, `ontology/src/NOTICE.md` |
| Build, vocab export, template rendering, checks | `ontology/rpont/`, `ontology/build.py`, `ontology/check.py` |
| Classification | `services/lambdas/ingest_lib/classifier.py`, `services/lambdas/classify/` |
| Facts and lineage written to the graph (incl. the step-event rule) | `services/lambdas/ingest_lib/rdf.py`, `services/lambdas/graph_write/` |
| IRIs and prefixes | `packages/evidence-core/evidence_core/iris.py` |
| The `graph_hop` tool and template checks | `services/lambdas/tools/graph_hop/handler.py`, `services/lambdas/tools/_lib/templates.py` |

## Prove it

```bash
.venv/bin/python -m pytest ontology/tests services/lambdas/ingest_lib/tests/test_markers_step_events.py \
  services/lambdas/tools/tests/test_graph_hop.py tests/integration
```

## On AWS

The TBox is loaded into Amazon Neptune (serverless, IAM database authentication) by the seed step. `graph_write`
writes each package and pipeline version into its own named graph, `urn:rp:graph:abox:{packageId}:{pv}`. The
`graph_hop` function runs inside the VPC with a read-only IAM policy; only `graph_write` and the admin function can
write. Pipeline versions without an ontology (1.0) have no graph, and the tool says so without running a query.

## Reuse it in your application

- **Start with the definitions your users argue about.** "Total income", "net exposure", "claim amount". Put the
  definition, its operand concepts and the template that computes it in the ontology.
- **Bind by printed labels.** Collect the label variants that actually appear on your documents and list them as
  `skos:altLabel`. That is where most extraction quality comes from.
- **Approve queries, do not generate them.** A small set of parameterized templates is easier to test, review and
  secure than free-form SPARQL written by a model.
- **Today, adding a template is data plus one line of code in three places:** the `.rq` file, and its id in
  `GRAPH_TEMPLATES` (`packages/evidence-core/evidence_core/models/tools.py`), `TEMPLATE_SPECS`
  (`services/lambdas/tools/_lib/templates.py`) and, if it returns a sum, `TOTAL_TEMPLATES`
  (`services/lambdas/tools/graph_hop/handler.py`). Then run `make contracts` to regenerate the MCP tool schema.
- **Footnote rules are code today.** The step-event pattern is `STEP_EVENT_RE` in `ingest_lib/rdf.py`. Add your own
  rule beside it, and keep it deterministic: no model writes facts into the graph.

## Next

[Quickstart 6: metadata and filters inside the query](06-metadata-and-filtered-hybrid-search.md)

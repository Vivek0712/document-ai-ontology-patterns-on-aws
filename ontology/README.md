# ontology

The knowledge-plane vocabulary: the TBox loaded into Amazon Neptune, the vocabulary snapshot the ingestion stages
read, and the approved SPARQL templates the agent's `graph_hop` tool runs (pattern 3).

```bash
make ontology-check      # build ontology/dist/<version>/, run every check and template, record the governed definition
```

## Sources

| Path | What |
|---|---|
| `src/manifest.json` | The ontology version (bound by `config/pipelines/v2.3.1.yaml`) and the files in each named graph |
| `src/rp/doc.ttl` | Document, evidence unit, page region, stage activity; structural predicates |
| `src/rp/doctypes.ttl` | Document types with `rp:indicator` phrases for classification |
| `src/rp/roles.ttl` | Canonical layout roles (aligned to DoCO), with the Docling and Amazon Textract label sets mapped by `skos:exactMatch` |
| `src/rp/fin.ttl` | `fin:FieldValue` and its predicates, field concepts with printed labels as `skos:altLabel`, governed definitions such as `fin:QualifyingIncome`, `fin:RatingTriggeredStepEvent` (a FIBO `StepEvent`) |
| `src/fibo/`, `src/doco/`, `src/w3c/` | Pinned, unmodified upstream copies: a FIBO subset (MIT), DoCO (CC BY 4.0), SKOS and PROV-O (W3C Software and Document License). See `src/NOTICE.md` |
| `templates/*.rq` | Approved SPARQL templates, each with a comment header (`template`, `purpose`, `params`, `columns`, `graphs`) |
| `rpont/` | Build, vocabulary export, template rendering, reference ABox, checks, governance record |

## Build output (`dist/<version>/`, not committed)

`nquads/` (one file per named graph), `vocab.json`, `tbox.json`, `templates/`, `governance/` and `manifest.json`.
The AWS deployment copies it to the data bucket under `config/ontology/<version>/`; the seed step loads the
N-Quads into Neptune.

## Templates

| Template | Parameters |
|---|---|
| `qi_sum_w2_box1_v1` | `taxYear`, `borrower` (optional) |
| `field_lookup_v1` | `concept` (required), `borrower`, `taxYear` |
| `concept_by_label_v1` | `label` (required) |
| `unit_lineage_v1` | `unitId` (required) |
| `coupon_terms_v1` | `security` (required) |
| `neighbourhood_v1` | `iri` (required), `depth`; used by the REST API only, not by the agent |

`{{FROM}}` becomes one `FROM <graph>` line per graph the caller may read; every `{{param}}` is bound as an escaped
literal.

## Changing it

- New label variants, document types and field concepts are data changes: edit the Turtle, run
  `make ontology-check`.
- Raise `ontologyVersion` in `src/manifest.json` and `preprocessing.ontology.version` in
  `config/pipelines/v2.3.1.yaml` together when you publish a change.
- A new template needs its `.rq` file and its id registered in the tool code; see
  [Quickstart 5](../docs/quickstarts/05-ontology-binding-and-sparql.md).
- `rpont/checks.py` includes the Provenant sample expectations (for example the lending total 169,650.00). They
  are the regression tests for the knowledge plane.

## Tests

```bash
.venv/bin/python -m pytest ontology
```

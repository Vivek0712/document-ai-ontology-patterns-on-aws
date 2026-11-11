# lambdas

Everything that runs as an AWS Lambda function: the ingestion stages, the shared ingestion library, the
evidence-unit builder, the agent's MCP tools, the Amazon Bedrock AgentCore Gateway interceptor, the REST API and the X-ray.
The import namespace is `lambdas`; each handler is `lambdas.<folder>.handler.handler`. The local runners import
the same code.

## Reusable building blocks

| Folder | What it is | Pattern |
|---|---|---|
| `ingest_lib/` | The canonical layout model (`layout.py`), Docling and Amazon Textract adapters (`docling_layout.py`, `textract_layout.py`), docling-serve client, raised-glyph detection, vocabulary snapshot and classifier, period detection, PII rule (`pii.py`), embedder, OpenSearch client, N-Quads writer (`rdf.py`), the naive chunker, page rendering | 1, 3, 4, governance |
| `eu_builder/` | Evidence-unit builder (`builder.py`: roles, phases A, B, C, marker link, table merge, build report) and fields with boxes (`fields.py`) | 2 |
| `tools/` | MCP tools for the agent: `search_units` (hybrid search, filter inside both sub-queries), `graph_hop` (approved SPARQL templates, access check first), `get_region`, `doc_status`; shared code in `_lib/` | 4, 5 |
| `interceptor/` | Amazon Bedrock AgentCore Gateway REQUEST interceptor: re-validates the bearer token and overwrites `callerSub`, `groups`, `pipelineVersion`, `packageId`, `runId` in every tool call. `tokens.py` verifies Amazon Cognito access tokens | 5, governance |

## Ingestion stages (AWS Step Functions state machine `rp-ingest`)

`register`, `route`, `text_layer`, `docling_call`, `textract_page`, `classify`, `naive_chunk`, `eu_builder`,
`pii_gate`, `promote`, `embed_index`, `graph_write`, `mark_ready`, plus `stage_status` (status writes for states that are not a stage function, such as opening and closing the parallel extract) and
`status_stream` (package roll-up from the DynamoDB stream). Each handler is thin: it reads its inputs, calls
`ingest_lib` or `eu_builder`, writes its outputs to S3 and its stage record to DynamoDB through the
`stage_handler` decorator in `evidence-core`.

## Application functions (Provenant)

| Folder | What it is |
|---|---|
| `api/` | The REST API behind Amazon API Gateway: packages, documents, stage inspection, units, lineage, artifacts, pipelines, runs, governance, and `POST /ask` |
| `xray/` | The X-ray: what the naive lane "saw" on a page, drawn back onto it. An inspection, never an extraction |
| `get_region_http/` | Presigned page image and boxes for a citation, after the document access check |
| `upload_url/` | Presigned upload to Bronze |
| `graph_admin/`, `neptune_load/`, `vocab_export/` | Graph administration, TBox loading, vocabulary export |

## Packaging

`scripts/build_lambdas.sh` builds one code asset for every function and dependency layers per requirements file
in `requirements/`, without Docker (`pip --platform manylinux… --only-binary=:all:`, Python 3.12, x86_64). AWS CDK
reads the result from `build/`.

## Tests

```bash
.venv/bin/python -m pytest services/lambdas
```

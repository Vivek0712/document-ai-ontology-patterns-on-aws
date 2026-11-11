# services

The pipeline runtime. The same handler code runs as AWS Lambda functions behind AWS Step Functions on AWS, and
in-process on your laptop through the local runners.

| Folder | What it is | Pattern | Guide |
|---|---|---|---|
| [`lambdas/`](lambdas/README.md) | Ingestion stage handlers, the shared ingestion library, the evidence-unit builder, the MCP tools, the AgentCore Gateway interceptor, the REST API and the X-ray | 1–5 | [Quickstarts 3–8](../docs/quickstarts/README.md) |
| [`agent/`](agent/README.md) | The Strands agent for Amazon Bedrock AgentCore Runtime, with the citation validator | 5 | [Quickstart 7](../docs/quickstarts/07-agent-resolve-retrieve-hop-cite.md) |
| [`local_pipeline/`](local_pipeline/README.md) | Runs the ingestion stages on a folder of PDFs, both pipeline versions, with no AWS account | all | [Quickstart 1](../docs/quickstarts/01-run-locally.md) |
| [`local_agent/`](local_agent/README.md) | Asks golden questions against a local build through the same tools; the model call goes to Amazon Bedrock | 5 | [Quickstart 7](../docs/quickstarts/07-agent-resolve-retrieve-hop-cite.md) |

The shared models live in [`packages/evidence-core`](../packages/evidence-core/README.md).

## Stages

Every document runs through these stages once per pipeline version. Each stage writes a stage record (state,
inputs and outputs with S3 keys and hashes, engine and version, config slice, errors, events).

| # | Stage | 1.0 (naive) | 2.3.1 (evidence units) | Handler |
|---|---|---|---|---|
| 1 | `register` | Register | Register | `lambdas/register` |
| 2 | `route` | skipped | Digital or scan, per page | `lambdas/route` |
| 3 | `extract` | pypdf text layer | Docling (digital), Amazon Textract (scan) | `lambdas/text_layer`, `docling_call`, `textract_page` |
| 4 | `classify` | skipped | SKOS indicators, Amazon Nova 2 Lite tie-break | `lambdas/classify` |
| 5 | `units` | Fixed 300-token chunks | Evidence units with fields | `lambdas/naive_chunk`, `eu_builder` |
| 6 | `pii_gate` | Amazon Comprehend | Amazon Comprehend | `lambdas/pii_gate` |
| 7 | `promote` | Gold units and page images | Gold units and page images | `lambdas/promote` |
| 8 | `index` | Titan V2 + OpenSearch Serverless | Titan V2 + OpenSearch Serverless | `lambdas/embed_index` |
| 9 | `graph` | skipped (no ontology) | Facts and lineage to Amazon Neptune | `lambdas/graph_write` |
| 10 | `ready` | Searchable check | Searchable check | `lambdas/mark_ready` |

## Tests

```bash
.venv/bin/python -m pytest services
```

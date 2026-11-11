# local_pipeline

Runs the real ingestion stage handlers on a folder of PDFs, for both pipeline versions, on your laptop. No AWS
account is needed. This is the fastest way to see what each pattern does to your documents.

```bash
make units                                   # data/samples/lending → build/units, up to the units stage
make e2e-local PII=local                     # through ready, graph answer included → build/e2e
PYTHONPATH=services .venv/bin/python -m local_pipeline.run --package <folder of PDFs> --out build/mine --until ready --pii local
PYTHONPATH=services .venv/bin/python -m local_pipeline.run --help
```

| Option | Default | Meaning |
|---|---|---|
| `--package` | `data/samples/lending` | Folder of PDFs; an optional `gold.json` names the package and its groups |
| `--out` | `build/units` | Output folder |
| `--until` | `units` | `ready` also runs the PII gate, promotion, indexing, the graph write and the graph check |
| `--pii` | `local` | `aws` uses Amazon Comprehend, recorded in `runs/local/recorded/comprehend.json` and replayed offline |
| `--bedrock` | off | Amazon Titan Text Embeddings V2 for phase B and the index, recorded in `runs/local/recorded/titan-v2.json` (not in the repository: the first run on a fresh clone calls Amazon Bedrock) |
| `--docling` | `auto` | `local` requires Docling; `fallback` forces the labeled pypdfium2 fallback |
| `--synthetic` | off | Generates small synthetic PDFs when the folder has none |

Note: the `make e2e-local` target passes `--pii aws` unless you set `PII=local`.

## What it writes

`diff.md` and `diff.json` (chunks versus units), `v1.0/` and `v2.3.1/` (units as JSON Lines, build reports),
`status/` (every stage record and event), `run.json` (the engines that ran, with stand-ins named), and with
`--until ready`: `graph/abox.nq`, `graph/qi_sum.json` and `world/` (the emulated S3 objects, DynamoDB items and
index documents).

## What is emulated

| AWS | Local |
|---|---|
| AWS Step Functions | `runner.py` calls the same handlers with the same envelopes |
| Amazon S3, Amazon DynamoDB | moto, in-process |
| docling-serve on AWS Fargate | Docling in-process, or the labeled fallback |
| Amazon Textract | Docling OCR shaped as a Textract response (`local_textract.py`), labeled "NOT Amazon Textract" |
| Amazon Titan Text Embeddings V2 | A deterministic hash embedder, or real Titan with `--bedrock` (recorded) |
| Amazon Comprehend | A regex stand-in, or real Comprehend with `--pii aws` (recorded) |
| Amazon OpenSearch Serverless | An in-memory index with the same fused scoring |
| Amazon Neptune | pyoxigraph (`graph_check.py`), labeled "NOT Amazon Neptune" |

No AWS resource is created. With `--pii aws` or `--bedrock`, a recording miss calls AWS with your default
credentials (or `AWS_PROFILE`); the recordings hold hashes, scores, offsets and vectors, never the input text.

## Optional: Docling

```bash
.venv/bin/pip install -r services/local_pipeline/requirements-docling.txt     # about 1.4 GB
```

## Tests

```bash
.venv/bin/python -m pytest services/local_pipeline
```

Guide: [Quickstart 1](../../docs/quickstarts/01-run-locally.md).

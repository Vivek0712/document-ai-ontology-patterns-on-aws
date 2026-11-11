# evidence-core

Shared contracts for every plane: the data models, the stage registry, the S3 key layout, the config loader, bounding
boxes, IRIs and the retrieval filter. Every other component imports it, on AWS Lambda, in Amazon Bedrock AgentCore
Runtime and in the local runners alike. It depends only on `pydantic` and `PyYAML`.

```bash
.venv/bin/pip install -e packages/evidence-core      # make venv does this for you
```

```python
from evidence_core.retrieval import build_filter, bm25_query, dense_query, fuse
from evidence_core.models.units import Unit
from evidence_core.bbox import BBox, coverage
```

## Modules

| Module | What it holds |
|---|---|
| `models/` | Pydantic models for units and fields (`units.py`), stage records and envelopes (`status.py`), stage outputs, tool arguments and results (`tools.py`), answers, claims and run records (`answer.py`), the golden set (`golden.py`), API shapes |
| `base.py` | `RpModel`: snake_case in Python, camelCase in JSON, DynamoDB and S3; unknown keys ignored on read |
| `stages.py`, `enums.py` | The stage registry per pipeline version and the shared enumerations |
| `config.py` | Pipeline config loader (`config/pipelines/*.yaml`), model keys, config hashes, `PiiGateConfig` |
| `s3keys.py` | The Bronze, Silver, Gold, config and runs key layout |
| `ids.py` | Package, document, unit and run ids (`doc_id_for(packageId, fileName)`) |
| `bbox.py` | One frame (PDF points, top-left origin, 1-based pages); conversions from Docling and Amazon Textract; coverage and IoU |
| `retrieval.py` | Index mapping, `build_filter` (inside both sub-queries), `dense_query`, `bm25_query`, min-max fusion |
| `iris.py`, `sparql.py` | IRIs and prefixes for the knowledge graph, SPARQL helpers |
| `status.py` | `StatusWriter` and the `stage_handler` decorator every stage uses |
| `ddb.py`, `hashing.py`, `masking.py`, `logs.py`, `prices.py`, `env.py` | DynamoDB mapping, hashes (config, prompt, corpus), digit masking, structured logs, list prices, environment |
| `export.py` | Writes `contracts/generated/` (`make contracts`): stage registry, MCP tool specs, index mapping, table schema, JSON Schemas |

## Rules

- **Generated contracts must not drift.** After changing a model, run `make contracts` and commit the result. A
  test fails when `contracts/generated/` differs from the code.
- **Models are the API.** The UI's TypeScript types (`contracts/api.ts`) mirror them, and a test checks the
  mirror.

## Tests

```bash
.venv/bin/python -m pytest packages/evidence-core
```

Pattern 4 in practice: [Quickstart 6](../../docs/quickstarts/06-metadata-and-filtered-hybrid-search.md).

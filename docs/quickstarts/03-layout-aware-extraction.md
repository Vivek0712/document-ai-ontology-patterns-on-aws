# Quickstart 3: layout-aware extraction (pattern 1)

**Pattern:** read the page as regions with roles and boxes, not as a flat stream of text. Route each page to the
engine that can read it, then normalize every engine into one layout model that keeps a pointer back to the
engine's own output.

| | |
|---|---|
| **Plane** | Ingestion |
| **Time** | 15 minutes |
| **Needs** | [Quickstart 1](01-run-locally.md) done, Docling installed (step 3 there) |
| **Cost** | None locally. On AWS: Amazon Textract per page, AWS Fargate per task-hour |

Why this pattern exists and how to adopt it without the rest of the pipeline: [methodology page](../methodology/01-layout-aware-extraction.md).

## The problem

Most PDF text extractors return the text layer in drawing order. On a form, the drawing order is often not the
reading order. The sample W-2s were built that way on purpose: the labels are drawn first and the values
afterwards. Look at what a plain text extractor returns:

```bash
.venv/bin/python -c "from pypdf import PdfReader; print(PdfReader('data/samples/lending/w2_2025_maya_fictus.pdf').pages[0].extract_text())"
```

You get a block of labels followed by a run of numbers such as `6,600.00 105,000.00 105,000.00 98,400.00 …`.
Which one is Box 1, the wages? The text alone cannot tell you. A chunker that splits this text, and a model that
reads the chunk, have to guess. That is the first way enterprise RAG fails, and it fails before retrieval starts.

Scanned pages fail differently. They have no text layer at all, so the naive lane produces no chunk for them.

## The pattern

1. **Route by page.** Count the text-layer characters on each page. More than 50 means a digital page; 50 or
   fewer means a scan. The threshold is config (`scanTextCharsMax` in `config/pipelines/v2.3.1.yaml`).
2. **Pick the engine per route.** Digital pages go to [Docling](https://github.com/docling-project/docling)
   (run as docling-serve on AWS Fargate). Scanned pages go to
   [Amazon Textract](https://docs.aws.amazon.com/textract/latest/dg/how-it-works-analyzing.html)
   `AnalyzeDocument` with the `LAYOUT`, `TABLES` and `FORMS` features.
3. **Normalize into one canonical layout.** Every engine's output becomes the same `LayoutBlock` model: a role
   (title, section header, text, list item, table, key-value, caption, footnote, picture, page header or
   footer …), the text, and a bounding box in
   one frame (PDF points, top-left origin, 1-based pages).
4. **Keep the pointer back.** Each block keeps a reference into the engine artifact: a Docling JSON pointer
   (`#/texts/31`) or a Textract block id. Every field you extract later can be traced to the exact engine item.
5. **Map engine labels through the ontology, not through code.** Docling and Textract label sets are mapped to
   canonical roles with `skos:exactMatch` in `ontology/src/rp/roles.ttl`. Supporting a new label is a vocabulary
   change.

## See it

After `make e2e-local PII=local`:

```bash
# Every stage of the scanned payslip on both lanes, as the stage inspector shows it
python3 - <<'EOF'
import json
d = json.load(open("build/e2e/status/payslip-2026-09-arjun-fictus-scan-8ff645.json"))
for s in d["stages"]:
    print(f'{s["pipelineVersion"]:>6} {s["stageId"]:<9} {s["state"]:<10} {s.get("engine") or "":<16} {s.get("headline") or ""}')
EOF
```

An excerpt of the output:

```text
   1.0 extract   SUCCEEDED  text-layer       text layer · 0 chars · 1 empty
   1.0 units     SUCCEEDED  fixed-chunker    0 chunks · no text layer
 2.3.1 route     SUCCEEDED  pypdf            0 digital · 1 scan
 2.3.1 extract   SUCCEEDED  textract         Textract 00:04
 2.3.1 classify  SUCCEEDED  skos-indicators  Payslip · margin 1.00
 2.3.1 units     SUCCEEDED  eu-builder       3 units · 1 fields
```

The `engine` column names the engine role of the stage. Locally the `textract` role is served by Docling OCR
shaped as a Textract response, and `build/e2e/run.json` says so ("NOT Amazon Textract"). On AWS the same stage
calls Amazon Textract.

The engine artifacts themselves are in the world snapshot:

```bash
ls build/e2e/world/s3/silver/pkg-lending-01/w2-2025-maya-fictus-827d37/2.3.1/extract/             # docling.json
ls build/e2e/world/s3/silver/pkg-lending-01/payslip-2026-09-arjun-fictus-scan-8ff645/2.3.1/extract/  # textract/
```

In the UI (mock mode, [Quickstart 1](01-run-locally.md) step 8):

- **Showcases → Read the page.** The same W-2 as a flat text stream and as regions with roles and boxes.
- **Workbench → Units** with the X-ray layer. The naive lane's chunks drawn on the page. The what-if control
  re-chunks the page at 300, 200, 128 and 64 tokens, so you can see that a smaller chunk size does not fix the
  problem: the label and the value are not next to each other in the text layer at any size.

## Read the code

| What | Where |
|---|---|
| Router: text-layer characters per page → `digital` or `scan` | `services/lambdas/route/handler.py` |
| Canonical layout model (`LayoutBlock`, one frame) | `services/lambdas/ingest_lib/layout.py` |
| Docling JSON → canonical layout | `services/lambdas/ingest_lib/docling_layout.py` |
| Textract `AnalyzeDocument` → canonical layout (uses `amazon-textract-textractor`) | `services/lambdas/ingest_lib/textract_layout.py` |
| docling-serve client (task id, resume, heartbeat) | `services/lambdas/ingest_lib/docling_client.py` |
| Raised glyphs from the text layer (footnote markers) | `services/lambdas/ingest_lib/glyphs.py` |
| Stage handlers | `services/lambdas/docling_call/`, `services/lambdas/textract_page/`, `services/lambdas/text_layer/` (the naive lane) |
| The X-ray: what the naive lane saw on a page | `services/lambdas/xray/` |
| Engine label → role mapping | `ontology/src/rp/roles.ttl` |
| docling-serve on Fargate, the queue, the scale-from-zero floor | `infra/lib/ingest-stack.ts` |

## Prove it

```bash
.venv/bin/python -m pytest services/lambdas/ingest_lib/tests/test_docling.py \
  services/lambdas/ingest_lib/tests/test_ingest_lib.py services/lambdas/xray/tests/test_xray.py
```

## On AWS

- docling-serve runs on AWS Fargate behind an Amazon SQS queue. A shim function in AWS Lambda calls it with a
  AWS Step Functions task token and heartbeats while the conversion runs. The service scales from zero, so the first
  extraction after a pause waits for a task to start.
- Scanned pages go through a Step Functions Map state, one `AnalyzeDocument` call per page.
- The upstream docling-serve image is pinned by digest in `infra/cdk.json` (`rp:doclingImage`). Pin your own
  image the same way.

To try Amazon Textract layout analysis on one of your own scanned pages:

```bash
aws textract analyze-document \
  --document '{"S3Object":{"Bucket":"<your-bucket>","Name":"<your-scan>.png"}}' \
  --feature-types LAYOUT TABLES FORMS > textract.json
```

This is billed per page. See [Amazon Textract pricing](https://aws.amazon.com/textract/pricing/).

## Reuse it in your application

- **Keep one layout model.** Whatever engines you use, normalize them into one shape with one coordinate frame
  before anything downstream runs. Copy `layout.py` and the two adapters, or follow the same contract.
- **Add an engine** by writing a `*_layout.py` that returns the canonical model, plus a router entry. Nothing
  downstream changes.
- **Route by page, not by document.** Mixed documents (a digital statement with one scanned page) are common.
- **Keep the pointer back.** It costs a few bytes per block and it makes every later figure auditable.
- **Label stand-ins.** If you run a cheaper or local engine in development, record that in the run metadata, as
  `run.json` does here.

## Next

[Quickstart 4: evidence units](04-evidence-units.md)

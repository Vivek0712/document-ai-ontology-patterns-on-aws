# Quickstart 1: run it locally, no AWS account

In this quickstart you run the real ingestion code on your laptop. Five fictitious lending documents go
through two pipeline versions side by side:

- **1.0, the naive lane:** text-layer extraction and fixed 300-token chunks.
- **2.3.1, the evidence-unit lane:** layout-aware extraction, evidence units, ontology binding.

Then a SPARQL template answers "What is the total income across this package?" over the knowledge graph the
pipeline wrote. Each operand of the answer points to a field, a unit and a box on a page. Last, you open the web
UI in mock mode and walk through the same results.

| | |
|---|---|
| **Time** | About 5 minutes of your time, plus installs: roughly 5–10 minutes without Docling and 10–20 minutes with it (a 1.4 GB install and a first-run model download). These are estimates; your network decides. |
| **Cost** | None. Nothing in this guide calls AWS. |
| **You need** | Python 3.12, GNU Make, git, about 2 GB of free disk. For the UI: Node.js 22 and npm. |
| **Optional** | Docling for real layout extraction on your machine (about 1.4 GB more, see step 3). |

> All documents in this repository are fictitious. They were generated for this project ("NewAge Global
> Fintech", people with the surname "Fictus"). Every page carries the footer
> `Fictitious document · NewAge Global Fintech · for demonstration only`.

Run every command from the repository root.

## Step 1. Clone the repository

```bash
git clone https://github.com/Vivek0712/document-ai-ontology-patterns-on-aws.git
cd document-ai-ontology-patterns-on-aws
```

## Step 2. Create the Python environment

```bash
make venv PY=python3.12
```

This creates `.venv/` and installs the development requirements plus every component's requirement file
(ingestion, ontology tooling, agent, evaluation). Pass `PY=` with the path of any Python 3.12 interpreter if
`python3.12` is not on your `PATH`.

## Step 3. (Recommended) Install Docling

```bash
.venv/bin/pip install -r services/local_pipeline/requirements-docling.txt
```

[Docling](https://github.com/docling-project/docling) is the open-source layout model that the 2.3.1 lane uses
for digital pages. On AWS it runs as docling-serve on AWS Fargate. Locally it runs in-process. The first run
downloads the layout models.

You can skip this step. The pipeline still runs, but the 2.3.1 lane then uses a clearly labeled fallback
(`FALLBACK pypdfium2 ... text runs (docling not installed)`). The fallback has no tables and no layout roles, and
the scanned payslip produces no units, because local OCR also comes from Docling. The comparison in step 5 is
much weaker without it, and the graph answer in step 6 may be incomplete.

## Step 4. Build the ontology

```bash
make ontology-check
```

This builds the knowledge-plane inputs from the Turtle sources in `ontology/src/`. The first is the TBox: the
ontology's classes and properties (a FIBO subset, DoCO, and the project's `fin:`, `doc:` and `rp:`
vocabularies), written as an N-Quads file, a plain-text graph format with one statement per line. The others are
the vocabulary snapshot the ingestion stages read, and the SPARQL templates. The
[glossary](../methodology/README.md#glossary) explains these terms. It then runs every template against a reference graph and checks the
results. The output goes to `ontology/dist/1.2.0/`.

## Step 5. Compare chunks with evidence units

```bash
make units
```

Both pipeline versions run on `data/samples/lending/` up to the `units` stage. AWS is emulated in-process (moto
for Amazon S3 and Amazon DynamoDB), and the stage handlers are the same code that runs in AWS Lambda.

Each of the 10 lane runs (5 documents, 2 lanes) prints `STOPPED (stopped at units by design: --until units)`.
That is the expected result here: this step stops at `units` on purpose, and step 6 runs on to `ready`. Without
Docling, the evidence-unit lane of the scanned payslip prints `FAILED ... Docling (OCR stand-in) is not installed`
instead; install Docling (step 3) to read scanned pages. With Docling, PyTorch can print a `Redirects are currently
not supported in MacOs` warning and a `UserWarning` from `conv2d` while the models load. Both are harmless.

Open `build/units/diff.md`. With Docling installed, the summary table looks like this:

| document | v1.0 chunks | v2.3.1 units | fields (with concept) | units with region | concept fields detached in v1.0 | concept values missing in v1.0 |
|---|---|---|---|---|---|---|
| payslip_2026-09_arjun_fictus_scan.pdf | 0 | 3 | 1 (1) | 3/3 | - | f-payslip-net-pay-for-the-period |
| w2_2025_maya_fictus.pdf | 1 | 2 | 9 (9) | 2/2 | f-box12a, f-box3, f-box5, f-box1, … | - |

(Excerpt. Your counts can differ slightly with another Docling version.)

Read it like this:

- **The naive lane has no chunk for the scanned payslip.** The page has no text layer, so there is nothing to
  chunk. The evidence-unit lane routes that page to OCR.
- **Every W-2 value is "detached" in the naive lane.** The number is in the chunk, but its printed label is not
  beside it. A model reading that chunk has to guess which number is Box 1.
- **Every evidence unit has a region.** Each field carries the label, the value, an ontology concept such as
  `fin:W2WagesBox1` and a bounding box on the page.

## Step 6. Run both lanes to `ready` and ask the graph

```bash
make e2e-local PII=local
```

This runs the rest of the pipeline locally: the PII gate, promotion to Gold, indexing and the graph write. Then it
calls the real `graph_hop` agent tool with the approved template `qi_sum_w2_box1_v1` over the graph the pipeline
just wrote. The last line of the output is one long line. Here it is wrapped for readability:

```text
graph check (pyoxigraph in-process (NOT Amazon Neptune)): 1082 ABox quads in urn:rp:graph:abox:pkg-lending-01:2.3.1;
  graph_hop qi_sum_w2_box1_v1 on 2.3.1 -> total 169650.00 =
  Arjun Fictus 71250.00 (w2-2025-arjun-fictus-546450:2.3.1:eu-02#f-box1),
  Maya Fictus 98400.00 (w2-2025-maya-fictus-827d37:2.3.1:eu-02#f-box1);
  on 1.0 -> no ABox for pipelineVersion 1.0 (ontology: none)
```

The quad count is 1082 with Docling and lower without it (the scanned payslip adds no units). The total and the
two operands are the same either way.

The answer, 169,650.00, is the sum of W-2 Box 1 for both borrowers. Box 1 differs from Boxes 3 and 5 on these
W-2s because of a 401(k) deferral, which is exactly the trap the documents were built to set. Each operand
names the unit and field it came from, so you can open the box on the page. The naive lane has no graph at all.

`PII=local` matters. The Makefile default is `PII=aws`, which replays recorded Amazon Comprehend responses from
`runs/local/recorded/` and calls Amazon Comprehend for anything that was not recorded. With `PII=local`, a regex
stand-in replaces Comprehend and the run never needs credentials.

## Step 7. Look at what was written

| Path | What it holds |
|---|---|
| `build/e2e/diff.md`, `diff.json` | Chunks versus units, per document and field |
| `build/e2e/v1.0/*.units.jsonl` | The naive lane's chunks |
| `build/e2e/v2.3.1/*.units.jsonl` | Evidence units: type, label, text, pages, boxes, fields, concepts, period, PII flag, access list, provenance |
| `build/e2e/v2.3.1/*.build_report.json` | How the unit builder placed each block (phases, scores, marker links) |
| `build/e2e/status/*.json` | Every stage record and event, as the stage inspector shows them |
| `build/e2e/graph/abox.nq`, `qi_sum.json` | The graph the pipeline wrote, and the template, SPARQL, rows and operands of the answer |
| `build/e2e/world/` | A snapshot of the emulated AWS world: S3 objects with version ids, DynamoDB items, index documents |
| `build/e2e/run.json` | Which engine ran each step, with every stand-in named as one |

To print one evidence unit and its fields:

```bash
head -n 2 build/e2e/v2.3.1/w2-2025-maya-fictus-827d37.units.jsonl | tail -n 1 | python3 -m json.tool | head -n 60
```

## Step 8. Open the UI in mock mode

The UI is a React app. In mock mode it serves responses that the real API code produced over your local
builds, so no AWS account is needed.

```bash
make xray-fixtures        # page renders and the naive lane's chunks drawn on each lending page
make mock-world           # real API responses over build/e2e*/world and the logged agent runs in runs/local
cd usecases/provenant/ui && npm ci && npm run dev:mock
```

`make mock-world` ends with a summary line. With only the lending package built, it adds one more line,
``skipped (their package has no build here; run `make e2e-packages` for the full world): 15 askReplays``. That is
expected: those are the logged runs of the three other sample packages. Without Docling, `make xray-fixtures`
also prints `skipped` for the scanned payslip.

Open <http://localhost:5188>. Mock mode signs you in as the demo user A and shows a `MOCK` badge in the top bar.
If port 5188 is taken, run on another port: `npm run dev:mock -- --port 5189`.

The mock replays the package's ingestion from the moment the page loads, as if you had just uploaded it. The
Pipeline board shows the stages running. **Ask unlocks when both lanes are ready, about 30 to 45 seconds after the
page loads.** Until then, the Ask screen says that the lanes are still indexing.

What to look at:

- **Home → Start the story.** One guided tour: the same upload and the same agent, and only one answer can
  show where its figures come from.
- **Showcases.** Fifteen guided walk-throughs, among them one per pattern: *Read the page*, *Evidence units*,
  *The footnote becomes a property*, *Filters inside the query* and *Resolve, retrieve, hop, cite*, plus *PII
  gate*, *Governed meaning* (with the recorded COA cross-check) and *Follow the number*. Use Space to play or
  pause and the arrow keys to step.
- **Workbench → Units.** The X-ray view: the naive lane's chunks drawn over the page, next to the evidence
  units and their field boxes.
- **Workbench → Ask.** Logged agent runs for each golden question on both lanes, with citation chips. A chip
  opens the page region behind the figure.

The Ask screens replay runs logged in `runs/local/` with Amazon Bedrock. To make new ones, see
[Quickstart 7](07-agent-resolve-retrieve-hop-cite.md).

To build the other three sample packages (quarterly reports, trade confirmations, a second lending package)
before `make mock-world`, run `make e2e-packages`. This target is **not offline on a fresh clone**:

- It embeds units with Amazon Titan Text Embeddings V2 through Amazon Bedrock. The recorded embedding responses
  (`runs/local/recorded/titan-v2.json`, about 4.5 MB) are not part of the repository, so a fresh clone calls
  Amazon Bedrock and needs credentials. The embedding cost for the three packages is well under USD 0.01 at list
  price (an estimate).
- It replays the recorded Amazon Comprehend responses in `runs/local/recorded/comprehend.json`. If your inputs
  differ from the recording (for example a different Docling version), it calls Amazon Comprehend for the
  missing responses.

The second lending package, `lending-02`, is the one behind the before-and-after table in the
[root README](../../README.md#before-and-after-on-the-same-question).

For the **complete mock world** (every showcase on every package, and the Evaluate screen), add the X-ray of
each extra package and the re-scored logged runs, then export the world again:

```bash
make e2e-packages
for p in quarterly tradeconf lending-02; do
  PYTHONPATH=services .venv/bin/python scripts/xray_fixtures.py --package data/samples/$p --build build/e2e-$p
done
make eval-logged       # re-scores the logged runs in runs/local into eval/results/ (no model calls)
make mock-world
```

With only the lending package, the showcases that play on another package say that their data is missing.

## Step 9. (Optional) Run the tests

```bash
.venv/bin/python -m pytest
```

The Python suite covers the shared models, every stage handler, the unit builder, the agent tools, the
interceptor, the citation validator, the ontology build and the evaluation metrics. It runs offline.

On a fresh clone, some tests are skipped, and the summary line says how many:

- 27 tests are skipped until the Lambda code and layers are packaged. Run `make build-lambdas` (no Docker needed),
  then run the tests again.
- 8 more are skipped when Docling is not installed (step 3).

`pyproject.toml` already passes `-q`. Adding another `-q` hides the summary line.

## What is real here, and what is a stand-in

| Concern | On AWS | In this quickstart |
|---|---|---|
| Orchestration | AWS Step Functions | An in-process runner that calls the same handlers with the same inputs |
| Storage | Amazon S3, Amazon DynamoDB | moto, in-process |
| Layout, digital pages | docling-serve on AWS Fargate | Docling in-process, or the labeled fallback |
| Layout, scanned pages | Amazon Textract `AnalyzeDocument` | Docling OCR shaped as a Textract response, labeled "NOT Amazon Textract" |
| Embeddings | Amazon Titan Text Embeddings V2 | A deterministic hash embedder (not semantic), labeled "NOT Titan" |
| PII detection | Amazon Comprehend `DetectPiiEntities` | A regex stand-in (`PII=local`) |
| Vector and keyword index | Amazon OpenSearch Serverless | An in-memory index with the same fused scoring |
| Knowledge graph | Amazon Neptune | pyoxigraph in-process, labeled "NOT Amazon Neptune" |

`build/e2e/run.json` records which of these ran. The code never presents a stand-in as the AWS service.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make venv` fails with `python3.12: No such file` | Install Python 3.12, or pass its path: `make venv PY=/path/to/python3.12` |
| `no PDFs in data/samples/lending` | Regenerate the samples: `make data` (deterministic, byte-identical to the checked-in files) |
| `diff.md` says `FALLBACK pypdfium2` | Docling is not installed. Do step 3, then run the step again |
| `make e2e-packages` asks for AWS credentials | Expected on a fresh clone: it calls Amazon Bedrock for embeddings. See step 8 |
| The run asks for AWS credentials | You left out `PII=local`. Run `make e2e-local PII=local` |
| `make mock-world` says `no world snapshot` | Run `make e2e-local PII=local` first |
| Port 5188 is in use | The UI dev server uses a fixed port. Stop the other process, or run on another port: `npm run dev:mock -- --port 5189` |
| Sign-in in mock mode fails with `Mock world not found` (or `Unexpected token '<'`) | The mock world is missing or `make mock-world` failed. Run `make mock-world` from the repository root (step 8), then reload |
| Workbench → Ask says the lanes are still indexing | Expected for about 30 to 45 seconds after the page loads: the mock replays ingestion. Watch the Pipeline board |
| `make units` prints `STOPPED` for every lane | Expected: step 5 stops at `units` on purpose. Step 6 runs to `ready` |
| `make mock-world` prints `skipped ... askReplays` | Expected with only the lending package. Build the others with `make e2e-packages` (step 8) |
| Tests are skipped with `layer core not built` | Run `make build-lambdas`, then the tests again |

## Clean up

```bash
make clean
```

This removes `build/`, `usecases/provenant/ui/dist/` and the caches. The mock UI data in
`usecases/provenant/ui/mock-public/` stays until you delete it.

## Next

- [Quickstart 2: deploy to AWS](02-deploy-to-aws.md)
- The pattern quickstarts, starting with [layout-aware extraction](03-layout-aware-extraction.md)
- [Build your own use case](10-build-your-own-use-case.md)

# Quickstart 9: the evaluation harness

**Pattern:** measure every pipeline version against a golden set of questions whose answers are tied to a region
on a page. Report per question type and per version, with `n`, never as one accuracy figure. Ask each question
several times (N of N), because one lucky run proves little. Block a new pipeline version when any measured rate
regresses.

| | |
|---|---|
| **Plane** | All three |
| **Time** | 15 minutes |
| **Needs** | [Quickstart 1](01-run-locally.md) done |
| **Cost** | `local` and `logged` modes: none. `search` mode: a few AWS Lambda and Amazon OpenSearch Serverless calls. `ask` mode and `make local-ask`: Amazon Bedrock tokens |

Why the evaluation works this way, and how to apply it to your own pipeline: [methodology page](../methodology/evaluation.md).

## The golden set

`eval/golden.jsonl` holds 50 questions over the four fictitious packages: 15 table, 12 prose, 10 figure and 13
footnote questions. Each line looks like this (shortened):

```json
{"qid": "q-lend-01", "type": "table", "question": "What is the total income across this package?",
 "answer": "169,650.00", "answerPattern": "(?<![\\d,])\\$?169,?650(?:\\.00)?(?!\\.?\\d)",
 "packageId": "pkg-lending-01", "docId": "w2-2025-maya-fictus-827d37", "fileName": "w2_2025_maya_fictus.pdf",
 "page": 1, "goldBbox": {"page": 1, "x0": 309.0, "y0": 100.33, "x1": 433.0, "y1": 125.57},
 "headline": true, "aliases": ["What is the total qualifying income across this package?"]}
```

| Field | Meaning |
|---|---|
| `type` | `table`, `prose`, `figure` or `footnote`. Metrics are reported per type |
| `answer`, `answerPattern` | The expected answer, and a regular expression that accepts its printed forms |
| `docId`, `page`, `goldBbox` | Where the answer is printed: the region a correct citation must cover |
| `corpusHash` | Ties the question to an exact set of documents |
| `aliases` | Paraphrases, for robustness checks |

The data generator writes the golden set together with the documents (`make data`), so every `goldBbox` is a
region the generator actually drew.

## The metrics

| Metric | Rule |
|---|---|
| **Recall@1** | The top-1 retrieved item contains the gold page and its text matches `answerPattern` |
| **RegionHit@1** | One of the top-1 item's boxes covers at least 50 % of the gold box. Items without a region (1.0 chunks) report `n = 0`, not a miss |
| **IoU@0.5** | A stricter secondary column: intersection over union of at least 0.5 |
| **Answer correctness** (`ask`) | Numeric answers compare the normalized total; others match `answerPattern` |
| **Citation validity** (`ask`) | At least one claim, every claim resolvable to a region, no uncited numbers after the retry |

Every result file carries the caption *"These numbers describe our synthetic documents, not your corpus."*

## Run it

### Offline, over a local build

```bash
make units
.venv/bin/python eval/run.py run --mode local --units-dir build/units
```

`local` mode is a development proxy: BM25 only, over the units `make units` wrote. It is not the production
retriever, and the result file says so. Use it to see quickly whether a preprocessing change moved the right unit
to the top.

### Re-score logged agent runs, offline

```bash
.venv/bin/python eval/run.py run --mode logged --runs runs/local
make local-ask-summary
```

`logged` mode re-scores every logged run from its event stream with the same scoring code as `ask` mode.
`make local-ask-summary` prints the N-of-N table. For the headline question on the lending package, the logged
runs in this repository read:

| Lane | Runs | Correct total | Valid citations |
|---|---|---|---|
| 1.0 (chunks) | 10 | 10 | 0 |
| 2.3.1 (evidence units) | 10 | 10 | 10 |

Read that table carefully. On these documents the model found the right total on both lanes. Only the
evidence-unit lane could show where every figure came from. Accuracy alone would have called the lanes equal.

### Against a deployed stack

```bash
.venv/bin/python eval/run.py run --mode search                               # retrieval only, no model call
.venv/bin/python eval/run.py run --mode ask --versions 1.0,2.3.1             # full answer path (= make eval)
.venv/bin/python eval/run.py run --mode ask --qid q-lend-01 --repeat 10      # N of N for one question
```

`search` mode invokes the production `search_units` function directly with a service principal: same fusion,
filters and K. `ask` mode signs in as the demo user and calls `POST /ask`, so it measures the whole path. Add
`--upload` to copy the result to the data bucket.

### N of N locally, with Amazon Bedrock

```bash
make local-ask QID=q-lend-01 REPEAT=10 BUDGET=1.0
make local-ask-summary
```

See [Quickstart 7](07-agent-resolve-retrieve-hop-cite.md) for what runs where.

### Compare two runs and block regressions

```bash
.venv/bin/python eval/run.py compare eval/results/<baseline>.json eval/results/<candidate>.json
```

`compare` prints the change per pipeline version, question type and metric, and exits with status 1 if any measured
rate went down. Put it in CI before you promote a new pipeline version. `--tolerance` allows a small drop if you
decide that is acceptable.

## Read the code

| What | Where |
|---|---|
| Metrics (pure functions, no AWS) | `eval/metrics.py` |
| Harness: modes, selection, scoring from events, compare | `eval/run.py` |
| Golden-set model and amount normalization | `packages/evidence-core/evidence_core/models/golden.py` |
| Local N-of-N runs and summary | `services/local_agent/harness.py` |
| Golden set and its generator | `eval/golden.jsonl`, `data/generate.py` |

## Prove it

```bash
.venv/bin/python -m pytest eval/tests data/tests/test_golden.py
```

## Reuse it in your application

- **Tie every golden answer to a region.** "The answer is 98,400.00" is weaker than "the answer is printed in this
  box on page 1". The second lets you measure retrieval and citation separately from generation.
- **Write 10 to 20 questions per document type before tuning anything,** and cover each question type you care
  about. Footnote and figure questions are where pipelines differ most.
- **Report per type, with `n`.** A single accuracy number hides the failures you need to see.
- **Run N of N.** Ask each important question several times at temperature 0 and report how many runs were
  correct and cited. Treat any disagreement between runs as a finding.
- **Gate promotion.** A new pipeline version is a new config. Run the same golden set on both, and let `compare`
  decide.

## Next

[Build your own use case](10-build-your-own-use-case.md)

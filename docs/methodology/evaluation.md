# Evaluation

Preprocessing changes are easy to argue about and hard to measure. This page describes how this repository
measures them, and what the logged local runs show, including where both pipelines still fail.

> These numbers describe the repository's synthetic documents, not your corpus. Run the same method on your own
> documents before you draw conclusions.

---

## Principles

1. **Use a control lane.** Run the old and new preprocessing side by side, with identical retrieval, prompt,
   model and agent settings. Here, `1.0` is the control and `2.3.1` is the candidate. A test enforces that their
   configs differ only in `preprocessing`.
2. **Use gold regions, not only gold answers.** Every golden question names the page and the gold bounding box
   that holds the answer. This lets you measure whether retrieval found the right **place**, not just text that
   happens to match.
3. **Report per question type and per lane, with `n`.** Do not compute a single accuracy figure. Table, prose,
   figure and footnote questions fail for different reasons.
4. **Separate "correct" from "proven".** Answer correctness and citation validity are different metrics. A
   correct number with no valid region is not a pass in a workflow that needs evidence.
5. **Repeat, and count errors apart.** Ask the same question several times and report N of N. Agent errors
   (timeouts, unparsable replies) are counted separately from wrong answers.
6. **Make regressions block.** Comparing two evaluation results exits non-zero when any measured rate drops.
   Use it as a gate before you promote a new pipeline version.

## The golden set

[`eval/golden.jsonl`](../../eval/golden.jsonl) holds 50 questions over the four fictitious packages:

| Type | Questions | Example |
|---|---|---|
| table (includes key-value fields) | 15 | "What is the total income across this package?" (169,650.00) |
| footnote | 13 | "Are the Q4 FY2025 revenue figures in Table 3 final?" (No. Q4 figures are preliminary.) |
| prose | 12 | "Does the XS-2031 step-up reverse if the rating later recovers?" |
| figure | 10 | Values printed on a titled chart |

Each entry has `qid`, `type`, `question`, `answer`, an `answerPattern` (a regular expression), `packageId`,
`docId`, `fileName`, `page`, `goldBbox`, a `headline` flag and `aliases`. The data generator draws every gold
box, so the boxes are exact. See [`data/`](../../data/).

## Metrics

Defined in [`eval/metrics.py`](../../eval/metrics.py). They are pure functions with no AWS calls, applied in the
same way to both lanes.

| Metric | Rule | Notes |
|---|---|---|
| **Recall@1** | The top-1 retrieved item's pages include the gold page **and** its text matches `answerPattern` | Works for chunks and units |
| **RegionHit@1** | One of the top-1 item's boxes on the gold page covers at least 50% of the gold box | Only items with a box count. Fixed chunks have none, so the control lane reports `n = 0`. That is the finding, not a gap in the metric |
| **IoU@0.5** | Maximum intersection-over-union with the gold box is at least 0.5 | Secondary. It under-counts units that are larger than the gold field |
| **Answer correctness** | Numeric answers: the normalized `total` (or the first amount in the text) equals the gold value. Text answers: `answerPattern` matches | Ask mode only |
| **Citation validity** | At least one claim, every claim resolvable to a region, and no uncited figures left after the retry. `coversGold` also records whether a cited box covers the gold box | Ask mode only |

Also report **mean characters per unit** next to retrieval metrics. Larger units can raise recall just by being
larger.

## Running it

```bash
# No AWS: BM25 over the units from `make units` (a proxy without the dense half; the result file says so)
.venv/bin/python eval/run.py run --mode local --units-dir build/units

# No new model calls: re-score the logged runs in runs/local from their event streams
.venv/bin/python eval/run.py run --mode logged --runs runs/local

# Against a deployed stack
.venv/bin/python eval/run.py run --mode search                          # production search_units, no model call
.venv/bin/python eval/run.py run --mode ask --versions 1.0,2.3.1        # full answer path, both lanes
.venv/bin/python eval/run.py run --mode ask --qid q-lend-01 --repeat 10 # repeat one question

# Gate: exit code 1 if any measured rate regresses
.venv/bin/python eval/run.py compare eval/results/<baseline>.json eval/results/<candidate>.json
```

Results are written to `eval/results/<evalRunId>.json`. Ask mode calls Amazon Bedrock and costs money. Run a
single question with `--qid` first.

For a guided walk through the harness, see [Quickstart 9: the evaluation harness](../quickstarts/09-evaluation-harness.md).

## What the logged local runs show

At the release cut, [`runs/local/index.json`](../../runs/local/index.json) summarized 137 runs (`make local-ask-summary`
prints the current figures). They were asked on a developer machine
against the local pipeline's outputs. Model calls were real (Amazon Bedrock Converse). The index, graph and
stores were local stand-ins: an in-memory k-NN and BM25 index instead of Amazon OpenSearch Serverless,
pyoxigraph instead of Amazon Neptune, and moto instead of Amazon S3 and Amazon DynamoDB. Estimated model spend for
all 137 runs was about USD 5.05 (token usage × the list prices in
[`config/prices.yaml`](../../config/prices.yaml)).

The table covers the main comparison only: the default answer model (Anthropic Claude Sonnet 4.6) and the user
who can read every package. `n` counts runs that returned an answer.

| Question type | `1.0` correct | `1.0` valid citations | `2.3.1` correct | `2.3.1` valid citations |
|---|---|---|---|---|
| table | 26 of 32 | 0 of 32 | 27 of 29 | 27 of 29 |
| footnote | 10 of 10 | 0 of 10 | 10 of 10 | 10 of 10 |
| prose | 8 of 8 | 0 of 8 | 8 of 8 | 5 of 8 |
| figure | 3 of 3 | 0 of 3 | 3 of 3 | 3 of 3 |
| agent errors | 1 | | 0 | |

How to read it:

- **The biggest difference is provability.** On these short, clean, synthetic documents, the control lane often
  produced the right text. It could never point to a region, because a fixed chunk has none.
- **Where the control lane was wrong, preprocessing was the cause.** On the `lending-02` headline question, every
  control-lane answer was 199,000.00. The model read W-2 Box 3 values from chunks in which labels and values had
  been pulled apart. The evidence-unit lane answered 188,090.00 from typed Box 1 fields, with page regions.
- **Both lanes still fail somewhere.** Neither lane answered `q-lend-04`, the year-to-date gross pay on the
  **scanned** payslip. Locally, scanned pages used Docling OCR as a stand-in for Amazon Textract, and that field
  was not extracted. Three prose answers on `2.3.1` (`q-lend-06` and `q-qr25-03`) were correct but had no valid
  citation.
- **Access control held.** A user without access to the lending package asked the headline question 4 times
  across both lanes. Every time, the tools returned `forbidden` and the agent said it could not answer.

### Limits of these runs

- They are **repeat runs on a subset** of the golden set (22 of 50 questions), chosen to exercise each scenario.
  The question sets differ slightly between lanes. Treat them as evidence about specific behaviors, not as a
  benchmark.
- They ran against **local** builds. Results on AWS can differ, for example because of real Amazon Textract on
  scanned pages, or OpenSearch Serverless scoring.
- The lane-parity check confirms that both lanes share the same retrieval, generation and agent config hashes,
  model and prompt. The tool-schema hash was not recorded for most of these runs, and the index says so.

## Evaluate your own pipeline

1. **Write 20–50 golden questions** over documents like yours. Cover each failure mode you care about (tables
   across pages, footnotes, scans, forms). For each, record the page and the gold box.
2. **Keep a control lane.** Run your current preprocessing and the candidate through the same retrieval and
   agent.
3. **Report per type and per lane**, with `n`, and with correctness and citation validity separate.
4. **Repeat the important questions** (for example 10 times each) and count agent errors apart.
5. **Gate promotion on `compare`.** No measured rate may regress.
6. **Read the failures.** Open the build report and the lineage for every wrong or uncited answer. The fix is
   usually in preprocessing.

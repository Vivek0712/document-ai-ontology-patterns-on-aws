# eval

The golden-set harness. It measures each pipeline version against questions whose answers are tied to a region
on a page, reports per question type and per version with `n`, and blocks a new version when a measured rate
regresses. Every result carries the caption *"These numbers describe our synthetic documents, not your corpus."*

```bash
.venv/bin/python eval/run.py run --mode local --units-dir build/units      # offline, BM25 over `make units` output
.venv/bin/python eval/run.py run --mode logged --runs runs/local           # offline, re-score logged agent runs
.venv/bin/python eval/run.py run --mode search                             # deployed: search_units only
.venv/bin/python eval/run.py run --mode ask --qid q-lend-01 --repeat 10    # deployed: the full answer path
.venv/bin/python eval/run.py compare eval/results/<baseline>.json eval/results/<candidate>.json
```

`make eval` runs `ask` mode against the deployed stack.

| File | What |
|---|---|
| `golden.jsonl` | 50 questions (table, prose, figure, footnote) with answer pattern, document, page and gold box. Written by the data generator (`make data`) |
| `metrics.py` | Pure functions: Recall@1, RegionHit@1, IoU@0.5, answer correctness, citation validity, comparison |
| `run.py` | Modes `local`, `logged`, `search`, `ask`; `compare` exits with status 1 on any regression |
| `results/` | Result files (not committed) |

Options of `run`: `--versions`, `--package-id`, `--qid` (repeatable), `--types`, `--headline-only`, `--limit`,
`--repeat`, `--user`, `--golden`, `--out`, `--upload`. See `eval/run.py --help`.

## Tests

```bash
.venv/bin/python -m pytest eval
```

Guide: [Quickstart 9](../docs/quickstarts/09-evaluation-harness.md).

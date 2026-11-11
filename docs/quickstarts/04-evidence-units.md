# Quickstart 4: evidence units (pattern 2)

**Pattern:** retrieve whole units of meaning, not fixed-size slices of text. An evidence unit is a table with
its caption, unit label and footnotes, a group of form fields, a figure with its title, or a section of prose.
Every unit keeps its page regions, and every field inside it keeps its label, value, concept and box.

| | |
|---|---|
| **Plane** | Ingestion |
| **Time** | 20 minutes |
| **Needs** | [Quickstart 1](01-run-locally.md) done, with Docling installed |
| **Cost** | None locally |

Why this pattern exists and how to adopt it without the rest of the pipeline: [methodology page](../methodology/02-evidence-units.md).

## The problem

Fixed-size chunking cuts wherever the token count says. It separates a table from the footnote that changes its
meaning, a value from its label, a figure from its caption, a table's first half from its second half on the
next page. Making the chunks smaller or larger moves the cut. It does not remove it.

## The pattern

The builder in this repository follows the evidence-unit approach described by Han
([arXiv:2604.00500](https://arxiv.org/abs/2604.00500)), with extensions noted below. It runs on the canonical
layout from [pattern 1](03-layout-aware-extraction.md).

| Phase | What happens |
|---|---|
| **0. Roles** | Engine labels are already mapped to canonical roles. Two roles are derived: unit labels ("USD millions") and footnotes (a block in the bottom band with a marker, or a block that opens with a raised marker glyph). Page headers and footers are left out. |
| **A. Seeds** | Every table, picture and key-value group is a seed. Tables split across pages are merged (part *n* ends in the bottom 8 % of its page, part *n+1* starts in the top 12 % of the next, same column count, column centers within 2 %). Captions and unit labels nearby attach to their seed. |
| **Marker link** | A hard rule, before phase B. A footnote whose marker appears as a raised glyph in a seed attaches to that seed, **also across pages**. (Extension in this repository; the paper's units are single-page.) Each link is recorded with the marker's glyph box and the field it follows. |
| **B. Allocation** | Each remaining body block joins the same-page seed with the highest cosine similarity of their embeddings, when the cosine is at least **τ = 0.40**. Layout proximity breaks exact ties and is recorded. |
| **C. Residual** | Blocks left over are grouped by section into `section_text` units of at most 400 tokens, split on paragraph boundaries. |
| **Fields** | Inside each unit, label and value pairs become fields with a bounding box, an ontology concept, a period and a normalized number. |

### The two checks, and what this build enforces

The pattern defines two invariants for a unit:

- **I1:** a chart or table is anchored to a heading or label.
- **I2:** a table and the chart drawn from it agree.

**This reference build records both invariants but does not enforce them.** The build report says
`"deferred"` for each, and the UI shows "not enforced in this lane". What the build does enforce today:

- the marker-link rule (a footnote never drifts away from the figure it qualifies);
- a bounding box on every field;
- a review-queue item for any field whose extraction confidence is below `reviewMinConfidence` (0.80).

If you need I1 and I2, they belong in the unit builder after phase C, and they should fail the stage or route the
unit to review.

## See it

### Units versus chunks for one W-2

```bash
python3 - <<'EOF'
import json
path = "build/e2e/v2.3.1/w2-2025-maya-fictus-827d37.units.jsonl"
for line in open(path):
    u = json.loads(line)
    print(f'{u["unitId"]}  {u["unitType"]}  "{u["label"]}"  pages={u["pages"]}')
    for f in u["fields"]:
        b = f.get("bbox")
        box = f'p{b["page"]} ({b["x0"]:.0f},{b["y0"]:.0f})-({b["x1"]:.0f},{b["y1"]:.0f})' if b else "no box"
        print(f'    {f["fieldId"]:<10} {f["value"]:>12}  {f.get("concept") or "-":<36} {box}')
EOF
```

You should see a `field_group` unit labeled `W-2 · Maya Fictus · Wages (Boxes 1–6)` with `f-box1` =
`98,400.00`, concept `fin:W2WagesBox1`, and its box on page 1. Compare with the naive lane's single chunk:

```bash
head -n 1 build/e2e/v1.0/w2-2025-maya-fictus-827d37.units.jsonl | python3 -c "import json,sys; print(json.load(sys.stdin)['text'])"
```

### A footnote on the next page

The trade confirmations put a footnote marker on the coupon rate on page 1 and the footnote itself on page 2.
Build them locally:

```bash
PYTHONPATH=services .venv/bin/python -m local_pipeline.run --package data/samples/tradeconf --out build/units-tradeconf
```

Then print the marker links the builder recorded:

```bash
python3 - <<'EOF'
import json
for line in open("build/units-tradeconf/v2.3.1/trade-confirmation-xs2031-9af5c5.units.jsonl"):
    u = json.loads(line)
    for link in u["provenance"].get("build", {}).get("markerLinks", []):
        print(u["unitId"], "pages", u["pages"], link["marker"], "->", link["fieldId"],
              "crossPage" if link["crossPage"] else "same page", "by", link["evidence"])
EOF
```

One line reads `… pages [1, 2] 3 -> f-coupon-rate-at-issue crossPage by raised glyph`: the coupon table and the
step-up footnote are one unit. [Quickstart 5](05-ontology-binding-and-sparql.md) turns that footnote into
properties in the graph.

### How each block was placed

```bash
python3 -m json.tool build/units-tradeconf/v2.3.1/trade-confirmation-xs2031-9af5c5.build_report.json | head -n 80
```

The build report lists `phaseCounts`, the derived roles, every allocation with its phase, cosine and τ (losing
candidates too), excluded blocks, review items and the invariants record.

Locally, phase B uses a deterministic hash embedder, which is not semantic. Add `--bedrock` to use Amazon Titan
Text Embeddings V2 instead. Responses are recorded in `runs/local/recorded/titan-v2.json`, so a rebuild with the
same inputs makes no call. The recordings file is not part of the repository (it is about 4.5 MB), so on a fresh clone the first run calls Amazon Bedrock and needs credentials; later runs with the same inputs replay it.

In the UI (mock mode), **Showcases → Evidence units** animates the phases on a quarterly report: seeds, the marker
link, phase B cosine bars against τ, the residual, and the checks as recorded.

## Configure it

All unit rules for a pipeline version live in its config, `config/pipelines/v2.3.1.yaml`:

```yaml
preprocessing:
  chunker: { type: evidence_units }
  units: { tau: 0.40, phaseB: cosine, markerLink: hard-rule, residual: section_text, maxResidualTokens: 400,
           fieldBbox: true, bboxFrame: pdf-points-top-left, reviewMinConfidence: 0.80 }
```

`config/pipelines/v1.0.yaml` differs from it only in the `preprocessing` block, and a test enforces that. The
retriever, K, prompt, model and agent are the same, so any difference in answers comes from preprocessing.

Two things are still code in this release: the currency and "in millions" label forms (`UNIT_LABEL_RE` in
`services/lambdas/eu_builder/builder.py`) and the amount pattern in `fields.py`. Change them there if your
documents use other forms.

## Read the code

| What | Where |
|---|---|
| The builder: roles, phases A, B, C, marker link, merge, build report | `services/lambdas/eu_builder/builder.py` |
| Fields with bounding boxes | `services/lambdas/eu_builder/fields.py` |
| Raised-glyph detection from the text layer | `services/lambdas/ingest_lib/glyphs.py` |
| The naive lane's fixed chunker | `services/lambdas/ingest_lib/chunker.py` |
| The unit model (`Unit`, fields, provenance) | `packages/evidence-core/evidence_core/models/units.py` |
| Chunk-versus-unit diff | `services/local_pipeline/diff.py` |

## Prove it

```bash
.venv/bin/python -m pytest services/lambdas/eu_builder/tests/test_builder.py \
  services/lambdas/ingest_lib/tests/test_markers_step_events.py services/local_pipeline/tests/test_lanes.py
```

## Reuse it in your application

- **Unit types are generic.** Tables, field groups, figures and section text exist in invoices, claims, clinical
  forms and contracts alike. Start with the builder as it is and look at `diff.md` for your documents.
- **Keep the region on everything.** A unit without a region cannot be cited to a page. That one property is what
  makes [pattern 5](07-agent-resolve-retrieve-hop-cite.md) possible.
- **Record decisions, not just results.** The build report shows why each block landed where it did. When a unit
  looks wrong, you can see whether the cause is the layout, the threshold or the marker rule.
- **Tune τ with evidence.** Change `tau` in a new pipeline version, rebuild, and compare with
  [the evaluation harness](09-evaluation-harness.md). Do not tune it by eye on one document.

## Next

[Quickstart 5: ontology binding and SPARQL templates](05-ontology-binding-and-sparql.md)

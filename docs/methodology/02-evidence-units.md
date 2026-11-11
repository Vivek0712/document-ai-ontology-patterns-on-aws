# Pattern 2 · Evidence units with invariants

**Rule:** build retrievable units around the things people cite (tables, figures, field groups), and keep each
one's caption, unit label and footnotes attached to it.

Plane: ingestion. Previous: [pattern 1](01-layout-aware-extraction.md). Next: [pattern 3](03-ontology-driven-classification.md).

---

## The problem

Fixed-size chunking cuts wherever the token count runs out. On business documents, that cut usually falls
through the thing that matters:

- A table loses its caption ("Table 3 · Total revenue") or its unit label ("USD millions"), so its numbers lose
  their meaning.
- A table that continues on the next page becomes two unrelated chunks.
- A footnote ("Q4 figures are preliminary.") lands in a different chunk from the figure it qualifies.
- On a trade confirmation, the coupon "4.25%³" sits on page 1, and footnote ³, "6.00% from year 3 if rating
  < BBB-", sits on page 2. No chunk holds both.

Bigger chunks do not fix this. They just make the cut less frequent and the context noisier.

## The principle

An **evidence unit** is built around a *seed*, which is something a person would cite. Related blocks attach to
the seed by explicit rules. The construction follows the preprint by Han,
[arXiv:2604.00500](https://arxiv.org/abs/2604.00500), and runs in three phases over the canonical layout from
pattern 1. Before phase A, a preparation step (phase 0 in the code) derives roles the engine did not label: unit
labels such as "USD millions", and footnotes (bottom band of the page, or text that opens with a raised marker).

| Phase | What happens |
|---|---|
| **A · Seeds** | Every table, picture and key-value group is a seed. A table that continues across a page break is merged. Captions and unit labels that sit close to a seed attach to it |
| **B · Allocation** | Every remaining body block joins the same-page seed with the highest cosine similarity of their embeddings, if that similarity is at least τ = 0.40 (the paper's default) |
| **C · Residual** | Blocks still unallocated are grouped by section into `section_text` units, split on paragraph boundaries |

Each unit carries its members (with their roles and boxes), its **fields** (labeled values, each with its own
box), a breadcrumb of section headings, and its page boxes.

**Invariants** are checks that a unit is still coherent after it is built. The paper defines two:

- **I1 · Anchoring.** A visual unit (a table or figure) must contain a textual anchor, such as a section header,
  unit label or title. Otherwise it is demoted to plain text.
- **I2 · Consistency.** A table and a chart merged into one panel must share at least 60% of their values.
  Otherwise the panel splits in two.

> **Status in this repository.** The reference build **records I1 and I2 but does not enforce them yet**. Every
> build report says `"invariants": {"I1": "deferred …", "I2": "deferred …"}`, and the UI shows the same. They
> are planned for a later pipeline version. What **is** enforced today:
>
> - The **marker link**, a hard rule that runs before phase B (described below).
> - **Field-level bounding boxes** on every field.
> - A **review queue** entry for every field below `reviewMinConfidence` (0.80).

### Extensions in this repository

- **Marker link, across pages.** A footnote whose marker appears in a seed's text attaches to that seed, also
  when the footnote is on a later page. The paper's units are single-page. A plain digit (the `3` in
  `Coupon 4.25%3` as the text layer reads it) links only when the seed prints that digit as a **raised glyph**,
  so ordinary numbers are not mistaken for markers. Each link is recorded on the unit with the marker's glyph
  box.
- **Derived roles.** `unit_label` ("USD millions" and similar) and `footnote` (bottom band plus marker) are
  derived when the engine does not label them.
- **Fields with their own boxes.** A citation points at a field (Box 1), not at the whole unit (the W-2's wages
  block).

## How it is implemented here

| Piece | File |
|---|---|
| The builder: phase 0 (derived roles), phases A, B and C, marker link, build report | [`services/lambdas/eu_builder/builder.py`](../../services/lambdas/eu_builder/builder.py) |
| Fields: engine key-value pairs, then table cells, then text lines ending in an amount, each mapped to an ontology concept | [`services/lambdas/eu_builder/fields.py`](../../services/lambdas/eu_builder/fields.py) |
| Unit, member, field and provenance models | [`packages/evidence-core/evidence_core/models/units.py`](../../packages/evidence-core/evidence_core/models/units.py) |
| Raised-glyph detection for markers | [`services/lambdas/ingest_lib/glyphs.py`](../../services/lambdas/ingest_lib/glyphs.py) |
| Settings, as data | `preprocessing.units` in [`config/pipelines/v2.3.1.yaml`](../../config/pipelines/v2.3.1.yaml): `tau: 0.40`, `phaseB: cosine`, `markerLink: hard-rule`, `maxResidualTokens: 400`, `fieldBbox: true`, `reviewMinConfidence: 0.80` |
| Stage handler (writes `units.jsonl`, `build_report.json` and review items) | [`services/lambdas/eu_builder/handler.py`](../../services/lambdas/eu_builder/handler.py) |
| The control lane's fixed chunker (300 tokens, 20% overlap) | [`services/lambdas/ingest_lib/chunker.py`](../../services/lambdas/ingest_lib/chunker.py) |

Phase B (allocation by cosine similarity) uses Amazon Titan Text Embeddings V2 on Amazon Bedrock. In local mode it uses a deterministic hash embedder,
which is **not semantic**, unless you pass `--bedrock`. `run.json` records which embedder ran.

## Adopt it in your own pipeline

The core idea fits in a few dozen lines on top of any layout model that has roles, boxes and reading order.
This standalone sketch shows seeds, caption attachment, the marker link and cosine allocation. The repository's
builder adds cross-page table merging, residual grouping, fields and the build report.

```python
from dataclasses import dataclass, field

SEED_ROLES = {"table", "picture", "key_value"}
ATTACH_ROLES = {"caption", "unit_label"}

@dataclass
class Unit:
    seed: "Block"
    members: list = field(default_factory=list)
    marker_links: list = field(default_factory=list)

def near(a, b, line_height=12.0, lines=1.5) -> bool:
    return a.page == b.page and abs(a.bbox.y0 - b.bbox.y1) <= lines * line_height

def build_units(blocks, embed, cosine, tau=0.40, raised_markers=None):
    blocks = [b for b in blocks if not b.excluded]
    units = [Unit(seed=b, members=[b]) for b in blocks if b.role in SEED_ROLES]
    free = [b for b in blocks if b.role not in SEED_ROLES]

    # Phase A: captions and unit labels attach to the nearest seed on the same page.
    for b in [b for b in free if b.role in ATTACH_ROLES]:
        target = next((u for u in units if near(b, u.seed, lines=3 if b.role == "unit_label" else 1.5)), None)
        if target:
            target.members.append(b)
            free.remove(b)

    # Marker link (hard rule): a footnote joins the seed that prints its marker as a raised glyph,
    # on any earlier page.
    for b in [b for b in free if b.role == "footnote"]:
        marker = b.text.strip()[:1]
        target = next((u for u in units
                       if u.seed.page <= b.page and marker in (raised_markers or {}).get(u.seed.block_id, set())),
                      None)
        if target:
            target.members.append(b)
            target.marker_links.append({"marker": marker, "footnote": b.block_id})
            free.remove(b)

    # Phase B: remaining body text joins the most similar same-page seed if cosine >= tau.
    for b in list(free):
        cands = [(cosine(embed(b.text), embed(u.seed.text)), u) for u in units if u.seed.page == b.page]
        best = max(cands, default=(0.0, None), key=lambda c: c[0])
        if best[1] is not None and best[0] >= tau:
            best[1].members.append(b)
            free.remove(b)

    # Phase C: whatever is left becomes section text, grouped by heading (not shown).
    return units, free
```

Then give every unit:

- the union of its members' boxes, per page;
- its fields, each with a box and a concept (see [pattern 3](03-ontology-driven-classification.md));
- the metadata from [pattern 4](04-metadata-and-filters-inside-the-query.md);
- a provenance record: engine, engine version, source object version, config hash.

If you add invariants, start them in **log-only** mode. Count violations per document and look at them before
you let a check change the unit set.

## Pitfalls

- **Treating every trailing digit as a marker.** `4.25%3` could be a footnote marker or a typo. Require evidence
  (a raised glyph, a superscript character, or the engine's label) before linking.
- **Allocating across pages in phase B.** Similarity alone will happily attach a paragraph from page 9 to a table
  on page 2. Keep allocation on the same page, and cross pages only with explicit rules such as table
  continuation and marker links.
- **Changing the unit set without a version.** Turning an invariant on changes which units exist, and so changes
  retrieval. Ship it as a new pipeline version, not as an edit to the current one.
- **Losing the units you held.** The PII gate holds some units. Keep every unit in Silver with its decision, so
  you can explain gaps later.
- **Over-reading local results.** Phase B with the hash embedder is deterministic but not semantic. Judge
  allocation quality with real embeddings.

## How to evaluate it

- **RegionHit@1:** the top-1 retrieved unit's box covers at least 50% of the gold box. Fixed chunks have no box,
  so this metric only exists for units, which is the point. See [evaluation](evaluation.md).
- **Answer correctness with valid citations**, per question type. Footnote and table questions are where units
  should help most.
- **Build report review.** Each `build_report.json` lists phase counts, every allocation with its score,
  derived roles, marker links, unknown engine labels and review items. Read it for a handful of documents
  before you trust aggregate numbers.
- **Unit size.** Report mean characters per unit next to retrieval scores. Larger units can raise recall just by
  being larger.

## Try it

```bash
make units PACKAGE=data/samples/tradeconf     # look for the coupon unit and its linked footnote
.venv/bin/python -m pytest services/lambdas/eu_builder/tests/test_builder.py \
                           services/lambdas/ingest_lib/tests/test_markers_step_events.py \
                           services/local_pipeline/tests/test_lanes.py
```

For a guided, hands-on walk through this pattern, see
[Quickstart 4: evidence units](../quickstarts/04-evidence-units.md).

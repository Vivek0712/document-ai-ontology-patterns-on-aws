# Pattern 3 · Ontology-driven classification

**Rule:** type documents and fields against a governed vocabulary, and turn what a footnote says into a property
of the thing it qualifies.

Planes: ingestion → knowledge. Previous: [pattern 2](02-evidence-units.md). Next: [pattern 4](04-metadata-and-filters-inside-the-query.md).

---

## The problem

Evidence units keep the right text together. They still do not say what the text **means**:

- "Box 1", "Wages, tips, other compensation" and "1 Wages, tips, other compensation" are the same field, printed
  three ways. A W-2 Box 1 is not Box 3, even though both are "wages".
- "Total income for this package" has a business definition: here, the sum of W-2 Box 1 wages for the latest tax
  year, per borrower. Payslips and bank deposits corroborate but are not added. If only the prompt knows this,
  every model and prompt change can quietly change the answer.
- A footnote such as "6.00% from year 3 if rating < BBB-" is a **rule about the coupon**. As text, it can only be
  found by luck. As a fact, it can be queried.

Classifying with a free-form model prompt ("what kind of document is this?") gives answers you cannot govern,
version or audit.

## The principle

Keep the meaning in a **versioned ontology** and make every classification point into it:

1. **Document types** are concepts with indicator phrases. Score the phrases deterministically. Use a model only
   to break a near tie, and only among the candidate concepts.
2. **Field concepts** are concepts whose `skos:prefLabel` and `skos:altLabel` are the labels **as printed**.
   Field typing is then label matching against the vocabulary, not guessing.
3. **Defined quantities** (such as qualifying income) are classes in the ontology. They name their operand
   concept and the query that computes them. The prompt quotes the same definition, and the ontology owns it.
4. **A footnote becomes a property.** When a footnote is linked to a field by its marker (pattern 2), parse it
   deterministically into typed properties on the field's subject. If it does not parse, write nothing and count
   the failure.
5. **Every fact keeps its region.** Each field and event in the graph is `prov:wasDerivedFrom` its unit and its
   page region.

Use public ontologies where they exist and extend them narrowly. This repository uses a subset of
[FIBO](https://spec.edmcouncil.org/fibo/) for financial concepts, [DoCO](http://purl.org/spar/doco) for document
components, and W3C [SKOS](https://www.w3.org/2004/02/skos/) and [PROV-O](https://www.w3.org/TR/prov-o/) for
vocabularies and provenance.

## How it is implemented here

| Piece | File |
|---|---|
| Document types with `rp:indicator` phrases (W-2, payslip, bank statement, quarterly report, trade confirmation) | [`ontology/src/rp/doctypes.ttl`](../../ontology/src/rp/doctypes.ttl) |
| Field concepts with printed labels, `fin:QualifyingIncome`, `fin:RatingTriggeredStepEvent` (a subclass of FIBO `StepEvent`) | [`ontology/src/rp/fin.ttl`](../../ontology/src/rp/fin.ttl) |
| Document model: `doc:Document`, `doc:EvidenceUnit`, `doc:PageRegion` | [`ontology/src/rp/doc.ttl`](../../ontology/src/rp/doc.ttl) |
| Pinned upstream vocabularies and their licenses | [`ontology/src/`](../../ontology/src/) (`fibo/`, `doco/`, `w3c/`, `NOTICE.md`) |
| Build: N-Quads per named graph, the vocab snapshot the stages read, SPARQL templates | [`ontology/build.py`](../../ontology/build.py), [`ontology/rpont/`](../../ontology/rpont/) |
| Checks: run every SPARQL template on pyoxigraph over a reference graph | [`ontology/check.py`](../../ontology/check.py) |
| Classifier: indicator scoring, Amazon Nova 2 Lite tie-break when the margin is below 0.15 | [`services/lambdas/ingest_lib/classifier.py`](../../services/lambdas/ingest_lib/classifier.py), [`services/lambdas/classify/handler.py`](../../services/lambdas/classify/handler.py) |
| Vocabulary lookup: printed label → concept, engine label → role | [`services/lambdas/ingest_lib/vocab.py`](../../services/lambdas/ingest_lib/vocab.py) |
| Graph writer: fields as `fin:FieldValue`, regions, parties, and the footnote step event | [`services/lambdas/ingest_lib/rdf.py`](../../services/lambdas/ingest_lib/rdf.py), [`services/lambdas/graph_write/handler.py`](../../services/lambdas/graph_write/handler.py) |
| IRIs and named graphs (`urn:rp:graph:tbox:*`, `urn:rp:graph:abox:{packageId}:{pipelineVersion}`) | [`packages/evidence-core/evidence_core/iris.py`](../../packages/evidence-core/evidence_core/iris.py) |
| Settings, as data | `preprocessing.ontology`, `classify` and `enrichment` in [`config/pipelines/v2.3.1.yaml`](../../config/pipelines/v2.3.1.yaml) |

### The footnote, end to end

On the fictitious trade confirmation for XS-2031:

1. Page 1 prints `Coupon 4.25%³`. Page 2 prints footnote ³: *"6.00% from year 3 if rating < BBB-"*.
2. Pattern 1 finds the raised `³` glyph. Pattern 2 links the footnote to the coupon's unit across the page break
   and records the link with the marker's box.
3. The graph writer matches the footnote text against a strict pattern and writes (IRIs shortened):

```turtle
<urn:rp:…/step-event>  a fin:RatingTriggeredStepEvent ;
    fin:newRate 6.00 ;
    fin:effectiveFromYear 3 ;
    fin:triggerRating "BBB-" ;
    prov:wasDerivedFrom <…unit>, <…region on page 2> .

<urn:rp:…/coupon-terms>  a fibo-sec-dbt-bnd:SteppedCouponTerms ;
    fin:hasStepEvent <urn:rp:…/step-event> .
```

4. The agent's `graph_hop` tool runs the `coupon_terms_v1` template. It returns the coupon at issue, the
   step-up rate, the year and the trigger, each with the page region it was read from.

No model reads the footnote. If the wording does not match, nothing is written and the failure is counted.

## Adopt it in your own pipeline

Start with SKOS. It is small, standard and enough for most classification needs.

```turtle
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix ex:   <urn:example:ont:> .

ex:InvoiceTotal a skos:Concept ;
    skos:prefLabel "Invoice total" ;
    skos:altLabel  "Total due", "Amount due", "Balance due" .     # labels exactly as printed

ex:Invoice a skos:Concept ;
    ex:indicator "Invoice number", "Bill to", "Payment terms" .   # phrases that identify the document type
```

Then classify with the vocabulary, not with a free-form prompt:

```python
import re

def normalize(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().casefold()

def classify(text: str, doc_types: dict[str, list[str]], min_margin: float = 0.15, tie_break=None):
    t = normalize(text)
    scores = {dt: sum(normalize(p) in t for p in phrases) / len(phrases) for dt, phrases in doc_types.items()}
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    (best, s1), (second, s2) = ranked[0], ranked[1] if len(ranked) > 1 else (None, 0.0)
    if tie_break and s1 - s2 < min_margin:
        best = tie_break(text, candidates=[best, second])     # the model may only choose a listed concept
    return best, scores

def field_concept(printed_label: str, alt_labels: dict[str, str]) -> str | None:
    """alt_labels maps normalized printed label -> concept IRI, built from skos:prefLabel/altLabel."""
    return alt_labels.get(normalize(printed_label))
```

For footnotes, write a strict parser per rule you care about, and emit triples only on a full match:

```python
STEP = re.compile(r"^(?P<rate>\d+\.\d+)% from year (?P<year>\d+) if rating < (?P<rating>[A-Z]{1,3}[+-]?)$")

def footnote_to_triples(footnote_text, subject_iri, region_iri):
    m = STEP.match(" ".join(footnote_text.split()))
    if not m:
        return []                                   # count it; never guess
    ev = f"{subject_iri}/step-event"
    return [(ev, "rdf:type", "ex:RatingTriggeredStepEvent"),
            (ev, "ex:newRate", m["rate"]), (ev, "ex:effectiveFromYear", m["year"]),
            (ev, "ex:triggerRating", m["rating"]), (ev, "prov:wasDerivedFrom", region_iri),
            (subject_iri, "ex:hasStepEvent", ev)]
```

On AWS, load the TBox and per-package ABox graphs into Amazon Neptune as named graphs, and query them with
parameterized SPARQL templates (see [pattern 5](05-resolve-retrieve-hop-assemble-cite.md)).

## Pitfalls

- **Labels that are not the printed labels.** If `altLabel` holds what you *think* the form says, matching
  fails silently. Copy labels from real documents.
- **Letting the model invent a type.** A tie-break model must choose among the candidates you show it. Log its
  choice and the scores.
- **Definitions in two places.** If the prompt and the ontology disagree, the answer depends on which one the
  model read last. Keep the definition in the ontology, quote it in the prompt, and record a hash of both.
- **Using a model to parse footnotes into facts.** It works on the examples you test and fails quietly on the
  rest. Use deterministic rules for facts. Use models where a person reviews the result.
- **Inference you did not ask for.** Write explicit property paths in templates. Do not rely on a reasoner being
  enabled on the graph store.
- **Unversioned vocabulary changes.** A new label is a new ontology version. Pin the version in the pipeline
  config, so you can tell which vocabulary produced a unit.

## How to evaluate it

- **Classification accuracy** per document type, and how often the tie-break was used (`tieBreakUsed` in each
  `classification.json`).
- **Field coverage:** the share of gold fields whose concept was assigned, and the unknown labels left over.
  `diff.md` lists fields "with concept" per document. The build report lists unknown engine labels.
- **Footnote facts:** step events written versus `step_event_failures`, per document.
- **Graph answers:** `make ontology-check` runs every template over a reference graph. `make e2e-local` runs
  `graph_hop` over the graph the pipeline actually wrote, and checks the headline total.
- **An independent computation of a governed definition.** A definition that matters (here "total income") can be
  computed a second way and compared. The Provenant use case records one such cross-check: the same vocabulary
  uploaded to the [AWS Context Ontology Accelerator](https://github.com/aws/context-ontology-accelerator) (as an
  uploaded ontology, not steward-reviewed), a COA metric written as SQL and run by Amazon Athena, and its answer
  (169,650.00) compared with the graph total for the same package. It is a dated recording, replayed offline; see
  [Governed meaning and the COA cross-check](../../usecases/provenant/README.md#governed-meaning-and-the-coa-cross-check).

## Try it

```bash
make ontology-check                      # build the TBox, vocab snapshot and templates; run the checks
make e2e-local PII=local                 # then read build/e2e/graph/qi_sum.json
.venv/bin/python -m pytest ontology/tests/test_ontology.py \
                           services/lambdas/ingest_lib/tests/test_markers_step_events.py \
                           tests/integration/test_knowledge_plane.py
```

For a guided, hands-on walk through this pattern, see
[Quickstart 5: ontology binding and SPARQL templates](../quickstarts/05-ontology-binding-and-sparql.md).

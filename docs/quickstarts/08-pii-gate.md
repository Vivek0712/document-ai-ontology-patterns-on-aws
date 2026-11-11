# Quickstart 8: the PII gate (governance)

**Pattern:** nothing reaches the searchable Gold layer without passing a PII gate. The gate runs per evidence unit,
after units are built and before anything is promoted, indexed or written to the graph. A held unit stays in
Silver. Its regions are filled black on every page image the system serves.

| | |
|---|---|
| **Plane** | Ingestion |
| **Time** | 15 minutes |
| **Needs** | [Quickstart 1](01-run-locally.md) done |
| **Cost** | None with the local stand-in. On AWS: Amazon Comprehend, billed per 100 characters with a 3-unit minimum per request ([pricing](https://aws.amazon.com/comprehend/pricing/)) |

## Why per unit, and why before promotion

If PII screening runs at query time, the sensitive text has already been embedded, indexed and possibly cached.
If it runs per document, one account number holds back a whole statement. Running it per evidence unit, before
promotion, holds only the unit that carries the identifier and keeps everything else usable. Because a unit
knows its regions, the same decision can black out the right boxes on the page image.

## The rule

The rule is named `rp-pii-v1` and is set in the `piiGate` block of each pipeline version:

```yaml
preprocessing:
  piiGate: { engine: comprehend, action: hold, rule: rp-pii-v1, minScore: 0.80 }
```

With the defaults in `PiiGateConfig` (`packages/evidence-core/evidence_core/config.py`):

1. Send the unit's text to Amazon Comprehend
   [`DetectPiiEntities`](https://docs.aws.amazon.com/comprehend/latest/dg/how-pii.html).
2. Keep entities of the configured types (`SSN`, `BANK_ACCOUNT_NUMBER`, `BANK_ROUTING`,
   `CREDIT_DEBIT_NUMBER`) with a score of at least `minScore` (0.80).
3. Count the digits in each matched span. A span with at least the type's threshold of digits is **unmasked**
   (9 for an SSN or routing number, 8 for an account number, 12 for a card number; 8 for any other type you add
   without a threshold).
4. Decide:
   - any unmasked hit → **held** (`piiFlag: held`);
   - only masked hits, such as a truncated `XXX-XX-0417` → **masked**, the unit passes;
   - no hits → **none**, the unit passes.

Decisions carry the entity type, score, offsets and digit count. They never carry the matched text, and the gate
never logs text.

What happens next:

- `promote` copies passed units to Gold and renders page images at 150 dpi with every held region filled black.
- Held units are never indexed and never written to the graph.
- The retriever excludes `piiFlag = held` in its filter anyway ([pattern 4](06-metadata-and-filtered-hybrid-search.md)).
- Silver objects are never served through a presigned URL. Previews of Bronze and Silver mask long digit runs.

## See it

The sample bank statement prints a full 14-digit account number in its header. That is the only full identifier
in the corpus. After `make e2e-local PII=local`:

```bash
python3 - <<'EOF'
import json, glob
for path in sorted(glob.glob("build/e2e/world/s3/silver/pkg-lending-01/*/*/pii_gate/decisions.json")):
    d = json.load(open(path))
    for dec in d["decisions"]:
        if dec["decision"] != "none":
            ents = [(e["type"], e["score"], e["digits"], "masked" if e["masked"] else "UNMASKED") for e in dec["entities"]]
            print(d["pipelineVersion"], dec["unitId"], dec["decision"], ents)
EOF
```

You should see the bank statement held on both lanes, with one unmasked `BANK_ACCOUNT_NUMBER` (14 digits). Look
at what is held:

- On 1.0 the held item is `…:1.0:c-01`, the statement's only chunk. The whole statement disappears from search.
- On 2.3.1 only the header unit `…:2.3.1:eu-02` is held. The balances, the chart and the transactions stay
  searchable.

That is the case for running the gate per evidence unit. With real Amazon Comprehend (below), the unit of the
scanned payslip that carries a full (fictitious, unissued) SSN is held as well.

With `PII=local`, a regex stand-in plays Comprehend's part (SSN patterns and 8 to 17 digit runs). To use real
Amazon Comprehend, run with `PII=aws`:

```bash
make e2e-local PII=aws
```

Responses are recorded in `runs/local/recorded/comprehend.json`, keyed by a hash of the request. The file holds
entity types, scores and offsets, never the text. A rebuild with the same inputs replays them with no AWS call.
`build/e2e/run.json` says which engine ran and how many calls were replayed versus sent.

In the UI (mock mode), **Showcases → PII gate** follows a unit from Bronze through the gate to Gold, and
**Governance → PII** lists every unit's decision with the masked Gold page. The mock data export refuses to
write if any held span appears anywhere in it.

## Read the code

| What | Where |
|---|---|
| The rule: windows, entity filter, digit thresholds, decision | `services/lambdas/ingest_lib/pii.py` |
| Rule configuration and defaults | `packages/evidence-core/evidence_core/config.py` (`PiiGateConfig`) |
| Gate stage | `services/lambdas/pii_gate/handler.py` |
| Promotion to Gold, held regions filled | `services/lambdas/promote/handler.py`, `services/lambdas/ingest_lib/render.py` |
| Regex stand-in for local runs | `services/lambdas/ingest_lib/testing.py` (`FakeComprehend`) |
| Recorded Comprehend responses | `services/local_pipeline/recorded.py`, `runs/local/recorded/comprehend.json` |

## Prove it

```bash
.venv/bin/python -m pytest services/lambdas/ingest_lib/tests/test_ingest_lib.py services/lambdas/api/tests/test_api_v2_governance.py
```

## Reuse it in your application

- **Choose entity types for your domain.** Amazon Comprehend detects many PII types (names, addresses, bank and
  card numbers, national identifiers and more). List the ones that must never be searchable, and give each a digit
  threshold if masking is common on your documents.
- **Hold, do not redact text in place.** A held unit stays whole in Silver for review. Downstream copies never
  contain it.
- **Fill regions on images, not just text.** If your UI shows page images, the gate's held boxes are what keep
  the identifier off the screen.
- **Keep a stand-in for development** and record real responses, so tests and demos are reproducible without
  sending text to a service each time.
- **The gate is one control, not the whole policy.** Combine it with access filters, encryption (this stack uses
  SSE-KMS on S3) and your organization's data-handling rules. Do not experiment with real personal data in a
  sandbox.

## Next

[Quickstart 9: the evaluation harness](09-evaluation-harness.md)

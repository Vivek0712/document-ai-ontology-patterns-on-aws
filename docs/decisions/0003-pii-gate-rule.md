# 0003. The PII gate holds units, not documents

**Status:** accepted

## Context

A document-level block (or a redact-and-re-enter loop) would hold every W-2, so nothing could be cited. The gate has
to be fine-grained enough to keep clean regions of a document usable.

## Decision

- The same gate runs in both lanes, on every unit (or chunk), between Silver and Gold.
- A unit is **held** when Amazon Comprehend `DetectPiiEntities` returns `SSN`, `BANK_ACCOUNT_NUMBER`,
  `BANK_ROUTING` or `CREDIT_DEBIT_NUMBER` with a score of at least 0.80 **and** the matched span is unmasked
  (at least 9 digits for an SSN, at least 8 for an account number). A masked span passes with `piiFlag: masked`.
- Held units never leave Silver. Gold page images have held regions filled, so the region service cannot show
  them, and the X-ray drops every token inside a held region.
- In the constructed data the bank statement's account header is the held example; the deposits table on the same
  page passes.

## Consequences

The gate is a rule, not a model: its decisions are reproducible and shown per unit (SC-6, W-12). Entity types
outside the list are not held.

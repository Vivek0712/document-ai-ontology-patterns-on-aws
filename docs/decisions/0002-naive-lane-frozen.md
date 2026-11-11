# 0002. The naive control lane is frozen; only preprocessing differs

**Status:** accepted

## Context

The claim this repository demonstrates is that most enterprise RAG failures start before retrieval. The comparison is only fair if the
two lanes differ in preprocessing alone.

## Decision

- Two pipeline versions run the same documents: `1.0` (naive) and `2.3.1` (evidence units).
- Only the `preprocessing` block of the pipeline config may differ. The agent, system prompt, model, tool set,
  retriever (hybrid dense + BM25, the same K and weights, every filter inside both sub-queries, never a
  post-filter), PII gate and generation settings are identical. Seeding refuses to run if the configs differ
  anywhere else.
- The `1.0` lane is frozen: `pypdf` `extract_text()` in its default content-stream-order mode, then fixed chunks of
  300 tokens with 20 % overlap. Scanned pages yield no text in this lane.
- Each run records `configSha`, `promptSha` and `toolSpecSha`, so lane parity is checked on the logged runs, not
  asserted.
- A changed model is a separate, labelled cohort (for example the frontier profile in SC-10); it never replaces the
  control.

## Consequences

Improvements to the naive lane are out of scope: they would change the control and break every comparison with
the logged runs.

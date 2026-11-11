# 0004. Evidence-unit invariants I1 and I2 are deferred to pipeline 2.4.0

**Status:** accepted, deferred

## Context

Two checks on evidence units were designed: **I1** anchors a chart or table to a heading or label (a visual unit
without a textual anchor is demoted to text) and **I2** checks that a table and its chart agree. Adding either to
pipeline `2.3.1` after its runs were logged would change its unit set and invalidate those runs.

## Decision

- `2.3.1` does not evaluate I1 or I2. Its build report records them as deferred to pipeline version `2.4.0`, with
  zero violations, and SC-2 shows exactly that ("I1 · not enforced in this lane").
- I1 and I2 arrive as a new pipeline version, `2.4.0`, never as a change to `2.3.1`.

## Consequences

The wording of the build report ("deferred (pipelineVersion 2.4.0, …)") is part of the recorded builds and stays
as written. Screens rephrase it.

# 0005. Replays show logged runs and recorded sessions only

**Status:** accepted

## Context

Most showcases replay earlier runs, and some show external services (COA) that this build does not call live. A
replay must never look like something it is not.

## Decision

- **Live or replay is always visible.** Every showcase frame carries a banner: "Replay of run r-… · recorded
  <date>" or "Live · <package> · <time>".
- **The before answer is never scripted.** Before and after come from logged runs, shown as N of N. Agent errors
  are counted beside N, never as wrong answers.
- **Hypotheses are tagged.** A mechanism inferred from the artifacts (for example the position-only pairing of a
  W-2 value with the wrong box) is drawn as `derived · hypothesis` until a logged answer confirms or refutes it.
- **Recorded sessions are dated snapshots.** The COA cross-check is a recorded session against a COA deployment
  (`runs/local/coa/<date>/`). Every value it shows is read from those files and tagged
  "COA snapshot · captured <date>"; latency is labelled as measured from the client (laptop → AWS Region).
  Uploaded Turtle is labelled "uploaded, not steward-reviewed": nothing claims COA reviewed or approved it. With
  `COA_MODE=off` the record carries no COA values and the screens say COA is off.
- **Unpublished metrics stay unpublished.** With `RP_PUBLIC_METRICS=off` (the default) the API withholds rates
  and dollar amounts; screens show per-question outcomes and usage counts only.

## Consequences

Showcases depend on recorded evidence. When a run or recording is missing, the screen says so instead of
filling the gap.

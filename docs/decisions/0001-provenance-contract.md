# 0001. Every number on screen carries its provenance

**Status:** accepted

## Context

The app exists to show where an answer comes from. A number without a source would undercut that, and a demo makes
it tempting to type a convenient value into a frame.

## Decision

- **Bound to data.** Every value a showcase or screen shows is read from a payload the API (or the mock world,
  which is recorded API responses) returned. Storyboards never type a value; tests scan the story files for money
  literals, run ids and question ids.
- **Tagged.** Every number has a provenance: `measured` (a logged run on AWS), `local run` (the local pipeline on a
  dev machine), `generator` (constructed data), `derived` (deterministic arithmetic over real artifacts),
  `snapshot` (a dated recording, for example "COA snapshot · captured 2026-11-11"), `estimate`, `what-if`,
  `counterfactual`, `design target` or `illustrative`. A panel renders the tag once (`ProvenanceScope`); a number
  whose provenance differs from its panel carries its own tag (`Num`). A number with neither throws in development.
- **Captions follow the data.** A caption that states an outcome is a function of the payload, with a variant for
  each outcome, so it cannot claim more than the data says.
- **Receipts.** Every showcase step lists the exact artifacts and payload paths it read.
- **Time is text.** Measured durations appear as text from stage records or run events; playback pacing is
  labelled and never implies speed.

## Consequences

A showcase cannot be finished before its evidence exists: a missing run shows as "not captured yet", not as a
placeholder value. The copy is sometimes longer than a slide would be.

# Provenant web UI

*Every answer, provenance included.*

The web workbench for the [Provenant use case](../README.md). It has 15 guided **showcases** that animate real
pipeline output and logged runs, a **workbench** for packages, the pipeline stages, evidence units and questions,
and reference screens for knowledge, governance, evaluation and cost. Every figure in an answer opens the page
region it came from. The rules the screens follow are recorded in [`docs/decisions/`](../../../docs/decisions/README.md).

React 18, Vite 6, TypeScript (strict), Tailwind CSS. Every version is pinned in `package.json`.

> All documents and people shown in the UI are fictitious ("NewAge Global Fintech", surname "Fictus").

## Run it

From the repository root, after the [local quickstart](../../../docs/quickstarts/01-run-locally.md):

```bash
make xray-fixtures          # page renders with the control lane's chunks drawn on each lending page
make mock-world             # real API responses over your local builds and the logged runs in runs/local
                            # (Quickstart 1, step 8, adds the other three packages for the complete mock world)
cd usecases/provenant/ui
npm ci
npm run dev:mock            # http://localhost:5188, no AWS account needed
```

Mock mode signs you in as demo user A and shows a `MOCK` badge in the top bar. The mock data in `mock-public/`
is generated on your machine from the real API code run over your local builds. It is not part of the
repository.

| Command | What it does |
|---|---|
| `npm run dev:mock` | Dev server on the mock world. The lending package replays its recorded ingest when a session opens |
| `npm run replay` | Mock mode with every package already ingested |
| `npm run dev` | Dev server against a deployed API. Needs `public/config.json`: copy `public/config.example.json`; on AWS the `rp-web` stack writes the real one |
| `npm run build` | Type check, then the production bundle in `dist/`. Mock code and fixtures are not included |
| `npm run build:mock` | The same bundle in mock mode (used by CI) |
| `npm run typecheck` | `tsc --noEmit` |
| `npm test` | Unit tests (Vitest). Tests that need the mock world skip when `RP_ALLOW_MISSING_FIXTURES=1` is set |
| `npm run e2e` | Playwright smoke, keyboard and accessibility tests in mock mode. Uses your installed Google Chrome |
| `npm run budget` | After `npm run build`: checks the gzip sizes of the bundle and the mock data against fixed budgets |

The dev server uses port 5188 (`strictPort`). Stop any other process on that port first.

## What to look at

- **Home → Start the story.** One guided tour: the same upload and the same agent, and only one lane can show
  where its figures come from.
- **Showcases.** Fifteen, grouped as the failure, the five patterns and the AWS planes: *Story tour*, *A bigger
  model does not add regions*, *Before and after*, *Read the page*, *Evidence units*, *The footnote becomes a
  property*, *Filters inside the query*, *Resolve, retrieve, hop, cite*, *Evaluate*, *Three planes*, *PII gate*,
  *Identity and scoping*, *Governed meaning*, *Follow the number* and *Cost and model tiering*. Space plays or
  pauses, the arrow keys step, **R** opens the receipts.
- **Workbench → Units.** The X-ray view: the control lane's chunks drawn over the page, next to the evidence
  units and their field boxes.
- **Workbench → Ask and Runs.** Logged agent runs for each golden question on both lanes, reported N of N, with
  citation chips that open the page region.
- **Trace.** Follow one figure from the answer back to the field, the unit, the layout blocks, the page region,
  the original upload (with its SHA-256), the index entry and the graph.
- **Knowledge → Governed meaning.** The definition of "total income" as governed in Git, its binding to each lane
  and the sentence of the prompt that uses it. With the recorded COA session (the default build), it also shows
  the uploaded vocabulary (*"uploaded, not steward-reviewed"*), COA's metric card (SQL run by Amazon Athena), the
  Tier 1 cross-check (169,650.00, equal to the pipeline's own total), the provenance hashes, the measured latency
  and the token rule, each tagged *"COA snapshot · captured 2026-11-11"*. See
  [the use case README](../README.md#governed-meaning-and-the-coa-cross-check).
- **Governance → PII gate, Access, Review.** Every unit's decision and the page as stored in Gold with held
  regions filled; the identity injected into each tool call; the review queue for low-confidence fields.
- **Evaluate and Cost.** Per-question outcomes on the golden set and usage per answer. Rates and dollar amounts
  appear only when the API is configured to publish them (`RP_PUBLIC_METRICS=on`).

## Live mode

`npm run dev`, or the build served by Amazon CloudFront, reads `/config.json`, signs in with Amazon Cognito (tokens
are kept in memory only) and calls Amazon API Gateway with the access token. See
[Quickstart 2](../../../docs/quickstarts/02-deploy-to-aws.md).

## Accessibility

The UI supports keyboard-only use (press `?` for the shortcut sheet) and reduced motion
(`prefers-reduced-motion`, or `?motion=reduced`). The Playwright accessibility suite scans every page in both
themes with axe-core and fails on any serious or critical violation.

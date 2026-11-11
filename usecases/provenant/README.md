# Provenant

*Every answer, provenance included.*

Provenant is a document workbench built from the building blocks in this repository. It is one use case, not
the product: it shows what the five patterns do together on documents that were built to make naive RAG fail.

You sign in, create a package, upload PDFs, and watch two pipeline versions process them side by side:

- **1.0, the naive lane:** text-layer extraction and fixed 300-token chunks.
- **2.3.1, the evidence-unit lane:** layout-aware extraction (Docling or Amazon Textract), evidence units, ontology
  binding, a knowledge graph.

Every stage of every document can be inspected. Then you ask a question on either lane, through the same agent,
and get an answer whose figures open the page region they came from. The two configs differ only in
`preprocessing`. Same retriever, same K, same prompt, same model, same agent.

> **All data is fictitious.** The documents were generated for this project: "NewAge Global Fintech" and people
> with the surname "Fictus". SSNs are in unissued ranges and mostly truncated, EINs use an unissued prefix, account
> numbers are invented. Every page carries the footer
> `Fictitious document · NewAge Global Fintech · for demonstration only`.

## The scenarios

| Package | Documents | What it is built to break | Headline question |
|---|---|---|---|
| `pkg-lending-01` | Two 2025 W-2s, two September 2026 payslips (one a skewed 150 dpi scan with no text layer), a two-page bank statement | W-2 values drawn after their labels, so the text layer separates them; Box 1 differs from Boxes 3 and 5 because of a 401(k) deferral; the scan has nothing for a text extractor to read; the statement header prints a full account number | "What is the total income across this package?" → **169,650.00** (98,400.00 + 71,250.00, W-2 Box 1 per borrower) |
| `pkg-lending-02` | The same kinds of documents for two other borrowers, with a titled balance chart | A figure whose values are printed only as chart labels | "What is the total income across this package?" → 188,090.00 |
| `pkg-quarterly-01` | Three quarterly reports (16 pages each) | Table 3's footnote "Q4 figures are preliminary"; Table 4 split across pages 15 and 16 with its footnote on 16; revenue by segment only in Figure 2 | "What was total revenue in Q3 FY2025?" → 4,890 |
| `pkg-tradeconf-01` | Three bond trade confirmations (2 pages each) | The coupon `4.25%` on page 1 carries a raised marker; footnote 3 on page 2 says `6.00% from year 3 if rating < BBB-` | "Which rating event triggers the XS-2031 coupon step-up?" → Issuer rating below BBB- |

The golden set, `eval/golden.jsonl`, has 50 questions across these packages (table, prose, figure and footnote
types), each tied to the page region where the answer is printed.

## How it composes the patterns

| Pattern | What Provenant uses | Where to see it |
|---|---|---|
| 1. Layout-aware extraction | The router sends the scanned payslip to Amazon Textract and every digital page to docling-serve on AWS Fargate; both become one layout model | Showcase *Read the page*; Workbench → Units (X-ray) |
| 2. Evidence units | W-2 boxes become one field group with a box per field; the trade confirmation's coupon table and its next-page footnote become one unit | Showcase *Evidence units* |
| 3. Ontology-driven classification | W-2, payslip, bank statement, quarterly report and trade confirmation typed from SKOS indicators; `fin:QualifyingIncome` defines "total income"; the coupon footnote becomes a `fin:RatingTriggeredStepEvent` (a FIBO `StepEvent`) | Showcase *The footnote becomes a property* |
| 4. Metadata, filters inside the query | Every unit carries package, version, doc type, period, PII flag and allowed principals; `search_units` filters inside both sub-queries | Showcase *Filters inside the query* |
| 5. Resolve, retrieve, hop, assemble, cite | The Strands agent on Amazon Bedrock AgentCore calls `search_units`, `graph_hop` (`qi_sum_w2_box1_v1`, `coupon_terms_v1`) and `get_region`; the validator keeps only cited figures | Showcases *Resolve, retrieve, hop, cite* and *Follow the number*; Workbench → Ask |
| Governance | The PII gate holds the bank statement header unit; its region is filled black on every page image; user B is refused user A's package | Showcases *PII gate* and *Identity and scoping*; Governance → PII, Access |
| Governed meaning | "Total income" is defined once, as `fin:QualifyingIncome` in the ontology in Git, with its SPARQL template; the optional COA snapshot cross-checks it (see below) | Showcase *Governed meaning*; Knowledge → Governed meaning |

## Where the domain lives

Provenant's domain knowledge is mostly data. In this release the files sit at the repository root, shared with
the toolkit:

| Domain piece | File |
|---|---|
| Document types and indicator phrases | `ontology/src/rp/doctypes.ttl` |
| Field concepts, printed labels, `fin:QualifyingIncome`, step events | `ontology/src/rp/fin.ttl` |
| FIBO subset (people, legal persons, debt, bonds) | `ontology/src/fibo/` |
| Domain SPARQL templates | `ontology/templates/qi_sum_w2_box1_v1.rq`, `coupon_terms_v1.rq` |
| Answer prompt, with the qualifying-income definition | `prompts/answer_v1.txt` |
| Pipeline versions, models, list prices | `config/pipelines/`, `config/models.yaml`, `config/prices.yaml` |
| Golden questions | `eval/golden.jsonl` |
| Fictitious document generator and samples | `data/` |
| Logged agent runs and recorded AWS responses | `runs/local/` |
| The recorded COA session | `runs/local/coa/2026-11-11/` |
| Cognito groups (`lending-analyst`, `markets-analyst`) | `infra/lib/names.ts` |
| Domain rules still in code | `services/lambdas/ingest_lib/periods.py` (period forms, party names), `services/lambdas/ingest_lib/rdf.py` (step-event rule, party links), `UNIT_LABEL_RE` in `services/lambdas/eu_builder/builder.py` |

This folder holds the React UI (`ui/`). A later release moves the domain files above into a "domain pack" folder
here, so other use cases can sit beside this one. [Build your own use case](../../docs/quickstarts/10-build-your-own-use-case.md)
walks through the same pieces for a different domain.

## Run it locally (no AWS account)

From the repository root, following [Quickstart 1](../../docs/quickstarts/01-run-locally.md):

```bash
make venv PY=python3.12
.venv/bin/pip install -r services/local_pipeline/requirements-docling.txt   # recommended, about 1.4 GB
make ontology-check
make e2e-local PII=local          # the lending package through `ready`, graph answer 169,650.00
make xray-fixtures
# optional, for every showcase on every package (e2e-packages needs Amazon Bedrock credentials on a fresh clone):
make e2e-packages
for p in quarterly tradeconf lending-02; do
  PYTHONPATH=services .venv/bin/python scripts/xray_fixtures.py --package data/samples/$p --build build/e2e-$p
done
make eval-logged
make mock-world
cd usecases/provenant/ui && npm ci && npm run dev:mock
```

Open <http://localhost:5188>. The `MOCK` badge in the top bar means every response comes from the real API code
run over your local builds and the logged runs, not from AWS.

| UI mode | Command (in `usecases/provenant/ui/`) | What it does |
|---|---|---|
| Mock | `npm run dev:mock` | The mock world, signed in as user A; the lending package replays its recorded ingest when a session opens |
| Replay | `npm run replay` | Mock mode with every package already ingested |
| Live | `npm run dev` | Against a deployed API; needs `public/config.json` (copy `config.example.json`; the `rp-web` stack writes the real one) |

`make e2e-packages` replays the recorded Amazon Comprehend responses in `runs/local/recorded/comprehend.json`. It
embeds with Amazon Titan Text Embeddings V2, whose recordings are not part of the repository, so on a fresh clone
it calls Amazon Bedrock (well under USD 0.01 at list price, an estimate) and needs credentials.

## Run it on AWS

Follow [Quickstart 2](../../docs/quickstarts/02-deploy-to-aws.md). In short:

```bash
scripts/verify_account.sh            # must end with "0 failed"
scripts/deploy.sh --upload           # about 30–45 minutes; uploads the lending sample at the end
scripts/smoke.sh --ask
```

Then sign in at the printed web URL:

| User | Group | Access |
|---|---|---|
| `user-a@example.com` | `lending-analyst` | Owns `pkg-lending-01`, which the seed step creates |
| `user-b@example.com` | `markets-analyst` | Denied `pkg-lending-01`; sees packages shared with `markets-analyst` |

In the local mock world, the quarterly-report and trade-confirmation packages are shared with both groups, as the
generator's `gold.json` files say. On AWS, create further packages in the UI and choose which of your groups to
share them with.

The password is generated into AWS Systems Manager Parameter Store as `/rp/demo/password` (SecureString), unless
you set `RP_DEMO_PASSWORD`.

A good first walk-through:

1. Upload the five lending PDFs (or use `--upload` above) and watch the stage board. Click any cell for the stage
   inspector.
2. Ask "What is the total income across this package?" on `1.0`, then on `2.3.1`.
3. On `2.3.1`, open each citation chip: the W-2 page with Box 1 outlined. On `1.0` there is no region to open.
4. Open Trace on a figure: field → unit → engine blocks → page region → page image → the uploaded object (key,
   version id, sha256) → index entry → graph → pipeline version → run.
5. Sign in as user B and open the same region link: 403.
6. Run `make pause` when you stop, and `make teardown` when you are done.

## The showcases

Fifteen guided, step-by-step animations built from real pipeline artifacts and logged runs. Space plays or
pauses, the arrow keys step, and **R** opens the receipts (the S3 keys, hashes and runs each step read). Every
number on screen carries its provenance (`measured`, `local run`, `generator`, `derived`, `snapshot`, `estimate`
and so on; see [decision 0001](../../docs/decisions/0001-provenance-contract.md)), and every replay says which
logged run or recording it shows ([decision 0005](../../docs/decisions/0005-honest-replays.md)).

| Showcase | Shows |
|---|---|
| SC-0 Story tour | Same upload, same agent: only one answer can show where its figures come from |
| SC-10 A bigger model does not add regions | Same chunks, a larger model: still no region to cite |
| SC-11 Before and after | Fix the representation; same retriever, same model. Four examples, one per package |
| SC-1 Read the page | Extraction returns regions with roles and boxes, not a flat text stream |
| SC-2 Evidence units | Seeds, marker links, phase B against τ, the residual, the checks as recorded |
| SC-3 The footnote becomes a property | A footnote on page 2 becomes typed coupon properties in the graph |
| SC-4 Filters inside the query | Unit metadata, the filter inside both sub-queries, the fusion, the funnel |
| SC-5 Resolve, retrieve, hop, cite | One logged run to scale: every tool call, the graph hop, the cited regions |
| SC-12 Evaluate | Top-1 and region hits by question type, per lane, from the golden set |
| SC-14 Three planes | Ingestion, knowledge and agent planes turn documents into governed answers |
| SC-6 PII gate | Bronze → Silver → gate → Gold, with held regions filled |
| SC-7 Identity and scoping | The identity the gateway injects into every tool call; user B's asks are refused |
| SC-8 Governed meaning | "Total income" defined once in Git, bound to both lanes, and cross-checked in a recorded COA session |
| SC-9 Follow the number | From a figure in the answer down to the uploaded bytes and the pipeline |
| SC-13 Cost and model tiering | Usage per answer and the model tiers, as configuration (amounts only when published) |

## Governed meaning and the COA cross-check

The definition of "total income" for a lending package is governed **in Git**: `fin:QualifyingIncome` in
`ontology/src/rp/fin.ttl` (the sum of W-2 Box 1 wages for the latest tax year, per borrower; payslips and bank
deposits corroborate and are never added) and the SPARQL template `qi_sum_w2_box1_v1` that computes it. `make
ontology-check` builds the ontology, runs the checks and writes the governed-definition record
(`ontology/dist/<version>/governance/qualifying_income.json`), which the API serves at
`GET /governance/definitions/qualifying_income`.

The record can also carry a **recorded session** with the
[AWS Context Ontology Accelerator (COA)](https://github.com/aws/context-ontology-accelerator), an AWS sample that
manages ontologies and governed metrics. The session was captured once against a COA deployment (COA v0.3.9,
us-east-1) on 2026-11-11 and is stored in [`runs/local/coa/2026-11-11/`](../../runs/local/coa/2026-11-11/): each
file is a captured request and response, with the principal's e-mail, COA object ids and presigned links
redacted (the placeholders are listed in `manifest.json`). The app's screens never call COA. To run a live COA
next to Provenant, the optional COA environment control spins one up from Settings → COA environment, seeds the
same metric and tears it down after 60 idle minutes: see
[Quickstart 11](../../docs/quickstarts/11-coa-environment.md).

What the snapshot shows, and what it does not:

- **The vocabulary was uploaded, not steward-reviewed.** `fin.ttl` was uploaded to COA as a user-uploaded ontology.
  COA has no steward review step for uploaded Turtle, so every screen labels it *"uploaded, not
  steward-reviewed"*. The record checks that the uploaded file is byte-identical to the one in Git (it is), and
  that COA's export kept all 359 of its triples.
- **The metric is SQL run by Amazon Athena.** COA's metric store defines `qualifying_income` as a Trino SQL
  query over a table of W-2 Box 1 amounts for the fictitious package, executed by Amazon Athena. It is a second,
  independent computation of the same definition, not a reasoner over the graph.
- **The cross-check.** COA's Tier 1 query for `pkg-lending-01` returned **169,650.00** with confidence 1.0, the
  same value in 5 of 5 REST calls and 3 of 3 MCP calls, through the resolver trace
  `metric_match → authorize (cedar) → firewall → execute (athena)`. That equals the pipeline's own graph total for
  the same package. The UI shows the check mark only when the two values are equal for the same `package_id`;
  otherwise it says "differs" or "not compared".
- **Latency is measured from a laptop.** The times shown are client wall-clock times from a laptop to us-east-1,
  labeled as such (for example a median of about 10.6 s for a Tier 1 query, of which about 1.9 s inside COA).
- **Tokens.** COA's MCP server accepted only an ID token issued to its own MCP client; the two other tokens tried
  were refused with HTTP 401.

Every COA value on screen is read from those files and tagged *"COA snapshot · captured 2026-11-11"*. To build
the record without the snapshot, run `make ontology-check COA_MODE=off`; every COA place in the UI then says
"COA is off in this build".

## What the logged runs are

`runs/local/` holds agent runs made with Amazon Bedrock over the local builds (`make local-ask`), about USD 5 of
model spend at list price in total. They are recorded model output over fictitious data. The UI replays them, and
`make local-ask-summary` and `eval/run.py run --mode logged` re-score them offline. The `recorded/` folder holds
Amazon Comprehend and Titan responses keyed by a hash of each request, without the input text, so local builds
are reproducible offline.

## Limits, stated plainly

- **A reference implementation, not a production system.** Use a sandbox account and fictitious or approved data.
- **Tested in `us-east-1` only.**
- **Evidence-unit invariants I1 and I2 are recorded, not enforced,** in this build. See
  [Quickstart 4](../../docs/quickstarts/04-evidence-units.md).
- **The COA cross-check is a recording.** It shows one session on one date. It does not call COA, and it does
  not claim that COA reviewed or approved the vocabulary.
- **Mock mode is the tested way to run the showcases.** In live mode, a few showcases read inspection routes that
  may not be deployed yet.
- **One domain per checkout** in this release. See [Build your own use case](../../docs/quickstarts/10-build-your-own-use-case.md).
- **Metrics describe the synthetic documents, not your corpus.** Every evaluation result carries that caption.

## Related reading

- [Methodology](../../docs/methodology/README.md): the five patterns, the provenance contract, evaluation
- [Architecture](../../docs/architecture.md): planes, stages, contracts, security model
- [Quickstarts](../../docs/quickstarts/README.md)

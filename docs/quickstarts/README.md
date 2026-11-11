# Quickstarts

Hands-on guides, from a first local run to your own use case on AWS. Each one lists the time, cost and
prerequisites at the top, and every command in it runs from the repository root.

| # | Guide | Needs AWS | What you get |
|---|---|---|---|
| 1 | [Run it locally, no AWS account](01-run-locally.md) | No | Both pipeline versions on five fictitious documents, the graph answer 169,650.00 with its page regions, the UI in mock mode |
| 2 | [Deploy to AWS](02-deploy-to-aws.md) | Yes | All three planes in your account; sign in, upload, ask, open the region behind each figure; pause and teardown; costs |
| 3 | [Layout-aware extraction](03-layout-aware-extraction.md) | No | Routing by page, Docling and Amazon Textract normalized into one layout model |
| 4 | [Evidence units](04-evidence-units.md) | No | Units with fields and boxes, a footnote linked across a page break, the build report |
| 5 | [Ontology binding and SPARQL templates](05-ontology-binding-and-sparql.md) | No | Document types and field concepts from SKOS, a governed definition, a footnote turned into graph properties |
| 6 | [Metadata and filters inside the query](06-metadata-and-filtered-hybrid-search.md) | No | The access filter inside both the vector and the keyword query, and the fused ranking |
| 7 | [The agent: resolve, retrieve, hop, assemble, cite](07-agent-resolve-retrieve-hop-cite.md) | Optional (Amazon Bedrock) | Logged agent runs on both lanes, the citation validator, AgentCore Gateway and Runtime |
| 8 | [The PII gate](08-pii-gate.md) | No | Per-unit holds before promotion, held regions filled on page images |
| 9 | [The evaluation harness](09-evaluation-harness.md) | Optional | Golden questions tied to regions, metrics per question type, N-of-N runs, regression gate |
| 10 | [Build your own use case](10-build-your-own-use-case.md) | Optional | Your documents, document types, field concepts, PII rule and golden questions, end to end, with an invoice example |
| 11 | [Spin COA up and down](11-coa-environment.md) | Optional (mock mode needs none) | A live AWS Context Ontology Accelerator environment controlled from Settings, torn down automatically after 60 idle minutes |

Suggested paths:

- **"Show me what it does."** Quickstart 1.
- **"I want to use one pattern in my own system."** Quickstart 1, then the pattern's quickstart and its
  [methodology page](../methodology/README.md).
- **"I want this running on AWS."** Quickstarts 1 and 2, then 7 and 9.
- **"I have my own documents."** Quickstart 1, then 10.
- **"I want to check the governed metric in a live COA."** Quickstarts 2 and 5, then 11.

## Conventions

- **Fictitious data.** Every sample document, name and number is fictitious ("NewAge Global Fintech", surname
  "Fictus"). Use fictitious or approved data when you try your own documents.
- **Stand-ins are labeled.** Local runs replace some AWS services with in-process stand-ins and record which ones
  in `build/*/run.json`. The guides say which is which.
- **Costs are estimates.** Figures come from public list prices for `us-east-1` and the sizes the stack
  configures, with links to the pricing pages. Check them before you deploy.
- **No account details in the repository.** Credentials come from the standard AWS credential chain. Set
  `AWS_PROFILE` only if you use a named profile, and keep account-specific settings in the git-ignored `.env`.
- **Two pipeline versions, called lanes.** A *lane* is one pipeline version processing the same documents.
  `1.0` is the control lane, also called the naive lane (text layer, fixed chunks). `2.3.1` is the evidence-unit
  lane. Their configs differ only in `preprocessing`, so any difference in answers comes from preprocessing.
  The [glossary](../methodology/README.md#glossary) defines the other terms the guides use.

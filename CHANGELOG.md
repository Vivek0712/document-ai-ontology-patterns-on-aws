# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0, a minor version may change
public interfaces; the entry will say so under **Changed**.

Pipeline versions (for example `1.0` and `2.3.1` in `config/pipelines/`) and the ontology version are
versioned separately from the repository. When a release changes one of them, the entry names the new
version.

## [Unreleased]

## [0.1.0] - 2026-11-11

First public release.

### Added

- Reusable building blocks for the five patterns:
  1. Layout-aware extraction: Docling and Amazon Textract adapters that produce one canonical layout
     model with page regions.
  2. Evidence units: units built from layout roles, each carrying its source regions, with recorded
     invariants.
  3. Ontology-driven classification: a FIBO- and DoCO-based vocabulary, so a footnote becomes a
     property of the figure it qualifies.
  4. Metadata at ingestion: document type, period, party and access metadata attached at ingestion and
     applied as filters inside every retrieval sub-query.
  5. Resolve, retrieve, hop, assemble, cite: agent tools (`search_units`, `get_region`, `graph_hop`,
     `doc_status`) exposed through Amazon Bedrock AgentCore Gateway, with citation checks.
- An offline local pipeline that runs the ingestion stages on a folder of PDFs without an AWS account.
- An AWS CDK app (TypeScript) with five stacks for the ingestion, knowledge and agent planes and a web
  front end.
- Provenant (*Every answer, provenance included.*), a reference use case built on the building blocks:
  fictitious lending, quarterly report and trade confirmation packages, golden questions, a web workbench with
  15 guided showcases, and logged runs for offline replay.
- A governed definition of "total income" (`fin:QualifyingIncome`) recorded by `make ontology-check`, with an
  optional, recorded cross-check against the AWS Context Ontology Accelerator (COA): a dated snapshot in
  `runs/local/coa/`, replayed and never called. `COA_MODE=off` builds the record without it.
- An optional, opt-in COA environment control (stacks `rp-coa-guard` and `rp-coa-control`, CDK context
  `rp:coaControl=true`): spin a live COA up and down from Settings → COA environment or `make coa-up` /
  `make coa-down`, re-seed the governed metric, and tear it down automatically after 60 idle minutes. Admins only
  (Cognito group `rp-admin`), audited, simulated in mock mode (Quickstart 11, decision record 0006).
- Decision records (`docs/decisions/`) for the rules the code relies on, and a REST API reference
  (`docs/reference/rest-api.md`).
- Quickstarts, pattern guides and operations docs.
- Repository hygiene: Apache-2.0 license, third-party notices, contributing guide, security policy,
  pre-commit hooks, gitleaks rules and CI that needs no AWS credentials.

### Known limitations

- Evidence-unit invariants I1 and I2 are recorded but not yet enforced.
- The offline PII stand-in (`PII=local`) is a few regular expressions for the fictitious samples. Use
  Amazon Comprehend for real documents.
- Cost figures in the docs are planning estimates based on public pricing pages; some unit prices in
  `config/prices.yaml` are marked `[verify]`. Check current pricing for your Region.
- Provenant's domain files (ontology, templates, prompt, golden set, sample generator) share the
  repository root with the building blocks. A domain-pack layout is planned.
- The COA environment control has passed its unit, infrastructure and UI tests and synthesizes, but has not yet
  run a full spin-up and tear-down against AWS. Treat it as a preview.
- The COA cross-check is one recorded session (2026-11-11). The vocabulary was uploaded to COA, not
  steward-reviewed there; the COA metric is SQL run by Amazon Athena; latencies were measured from a laptop.
- The `rp` resource and namespace prefix, and an earlier working name in hashed or recorded text, remain
  where renaming would change a hash, a recorded run or a deployed resource (see the root README).
- The Amazon Titan Text Embeddings V2 recordings are not shipped, so `make e2e-packages` and `--bedrock`
  builds call Amazon Bedrock on a fresh clone.
- The Amazon OpenSearch Serverless collection allows public network access (IAM and the data access
  policy still apply). Use a VPC endpoint for real data.
- `scripts/teardown.sh` keeps the AWS KMS key, the Lambda layer versions and the `rp-*` log groups.
- Tested in `us-east-1` only.

[Unreleased]: https://github.com/Vivek0712/document-ai-ontology-patterns-on-aws/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Vivek0712/document-ai-ontology-patterns-on-aws/releases/tag/v0.1.0

# scripts

Operator scripts for the AWS deployment, plus the fixture builders for the UI's mock mode. The shell scripts handle
order, prompts and logging; the AWS work runs in `lib/rp_ops` (boto3), which has unit tests against moto.

| Script | Make target | What it does |
|---|---|---|
| `verify_account.sh [--read-only]` | `make verify` | Identity and Region, each model listed and invoked once, Amazon Textract layout, Amazon Comprehend PII, OpenSearch Serverless, Neptune, Fargate and AgentCore reachability, quotas, local tools. `--read-only` makes no model, Textract or Comprehend call. Changes nothing |
| `build_lambdas.sh` | `make build-lambdas` | One code asset and dependency layers for every function, without Docker |
| `deploy.sh [--from STEP] [--only STEP] [--upload] [--skip-seed] [--yes]` | `make deploy` (similar) | build → bootstrap → core stacks → AgentCore → `rp-agent` → UI → `rp-web` → seed. Idempotent |
| `deploy_agentcore.sh` | `make deploy-agent` | Amazon Bedrock AgentCore Gateway (targets, REQUEST interceptor) and Runtime |
| `seed.sh [--upload] [--package-id ID]` | `make seed` | Demo groups and users, pipeline-version rows pinned to the deployed config versions, the TBox in Neptune, the vocabulary snapshot, the demo package; `--upload` also uploads the lending sample |
| `smoke.sh [--ask]` | `make smoke` | The user journey through the API, validated against the contract models; `--ask` adds one question per pipeline version |
| `prewarm.sh` | `make prewarm` | Neptune available, one healthy docling-serve task, provisioned concurrency on the tool functions, warm queries and model pings |
| `pause.sh [--wait]` | `make pause` | docling-serve to zero, provisioned concurrency removed, Neptune stopped |
| `resume.sh` | `make resume` | Starts Neptune (about 10 minutes) |
| `reset.sh [PACKAGE_ID] [--dry-run] [--include-runs] [--drop-package]` | `make reset` | Removes one package's objects, rows, index documents and graphs, so the journey can run again |
| `export_runs.sh` | `make export-runs` | Copies run records and evaluation results from AWS into `runs/` and `eval/results/` |
| `teardown.sh [--skip-export] [--keep-buckets] [--yes]` | `make teardown` | Export, delete AgentCore resources, `cdk destroy --all`, empty and delete the buckets. Asks twice |
| `xray_fixtures.py` | `make xray-fixtures` | X-ray export of a local build for the UI's mock mode |
| `mock_world.py` | `make mock-world` | The UI's mock world: real API responses over the local builds and logged runs |

## Settings

Every shell script reads a git-ignored `.env` at the repository root, if present.

| Variable | Meaning |
|---|---|
| `AWS_PROFILE` | Optional. Without it, the standard AWS credential chain is used |
| `AWS_REGION` | Defaults to `us-east-1`, the tested Region |
| `RP_EXPECTED_ACCOUNT` | If set, every script refuses to run in any other account |
| `RP_DEMO_PASSWORD` | Password for the demo users; otherwise generated into SSM `/rp/demo/password` |
| `RP_YES=1` | Skips confirmation prompts (automation only) |

Scripts print the account masked to its last four digits (`********9012`). Destructive scripts ask you to type a word before they act.

Each script prints its options with `--help`.

## Repository checks (`scripts/dev/`)

These run in pre-commit and CI. None of them calls AWS.

| Script | What it checks |
|---|---|
| `check-denylist.sh` | No file contains a string from a private denylist kept outside the repository (`DENYLIST`, `DENYLIST_FILE` or `~/.config/doc-ai-patterns/denylist.txt`). Prints file names only |
| `check-binary-metadata.py` | PDF and image metadata holds no home paths, e-mail addresses, 12-digit numbers or denylisted strings |
| `check-doc-links.py` | Relative links and anchors in the Markdown docs resolve, and every cited run id has a record in `runs/local/` |
| `test-gitleaks-rules.sh` | The project rules in `.gitleaks.toml` flag synthetic leaks (account ids, profile names, home paths) and pass placeholders |
| `check-release-placeholders.sh` | No release placeholder (the GitHub owner in clone URLs, the CODEOWNERS handles, the Code of Conduct contact, the release date) is left |

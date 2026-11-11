# Quickstart 2: deploy to AWS

This quickstart deploys all three planes into your own AWS account: ingestion, knowledge and agent. You then
sign in to the web UI, upload the fictitious lending package, watch both pipeline versions process it, ask a
question on each version, and open the page region behind each figure. At the end you pause or remove
everything.

| | |
|---|---|
| **Time** | About 30–45 minutes, mostly waiting for Amazon Neptune and Amazon OpenSearch Serverless to create |
| **Cost** | Roughly USD 10–18 for an 8-hour working day, and well under USD 1 for a paused day. These are estimates, see [Costs](#costs) |
| **You need** | An AWS account you can create resources in, the AWS CLI v2, Python 3.12, Node.js 22, GNU Make. No Docker |
| **Region** | `us-east-1` is the tested Region. See [Other Regions](#other-regions) |

> **Use a sandbox account.** This is a reference implementation, not a production system. It creates
> billable resources, two demo users and a public web endpoint protected by Amazon Cognito sign-in. Remove it
> with `make teardown` when you are done. All documents are fictitious.

Do [Quickstart 1](01-run-locally.md) first if you can. It installs the same tools and shows what each
pipeline step produces, without cost.

## What gets deployed

| Stack or step | What it creates |
|---|---|
| `rp-base` | AWS KMS key; versioned, encrypted S3 data bucket with Amazon EventBridge notifications; eight Amazon DynamoDB tables; a VPC with private isolated subnets and no NAT gateway (S3 and DynamoDB gateway endpoints, one AWS Step Functions interface endpoint); Lambda layers; the pipeline configs, prompts and ontology build copied to `s3://…/config/` |
| `rp-knowledge` | Amazon OpenSearch Serverless vector collection `rp-units`; Amazon Neptune Serverless cluster `rp-graph` with IAM authentication; graph loader and admin functions |
| `rp-ingest` | The `rp-ingest` state machine and its stage functions in AWS Lambda; an Amazon SQS queue with a dead-letter queue; docling-serve on AWS Fargate (upstream image pinned by digest, scales from zero); EventBridge rules that start two executions per upload, one per pipeline version |
| `rp-agent` | Amazon Cognito user pool with two groups; Amazon API Gateway REST API `rp-api`; the agent tool functions and the gateway interceptor; IAM roles for Amazon Bedrock AgentCore |
| AgentCore (script) | Amazon Bedrock AgentCore Gateway `rp-gateway` (MCP, with the tools as Lambda targets and a REQUEST interceptor) and AgentCore Runtime `rp_agent` (a Strands agent). `scripts/deploy_agentcore.sh` creates these, not AWS CDK |
| `rp-web` | S3 bucket for the UI behind Amazon CloudFront (origin access control), with `/api/*` routed to the REST API |

Two more stacks, `rp-coa-guard` and `rp-coa-control`, are opt-in (CDK context `rp:coaControl=true`) and are not
deployed by default. They control an optional live COA environment: see
[Quickstart 11](11-coa-environment.md).

Models, from `config/models.yaml`, all through Amazon Bedrock:

| Key | Model | Used for |
|---|---|---|
| `MODEL_ANSWER` | Anthropic Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`) | The agent's answers, both pipeline versions |
| `MODEL_COMPACT` | Amazon Nova 2 Lite (`us.amazon.nova-2-lite-v1:0`) | Tie-break in document classification |
| `EMBED_MODEL` | Amazon Titan Text Embeddings V2 (`amazon.titan-embed-text-v2:0`) | Unit and query embeddings |
| `MODEL_FRONTIER` | Anthropic Claude Opus 4.6 (`us.anthropic.claude-opus-4-6-v1`) | Optional bigger-model comparison only |

The `us.` model ids are cross-Region inference profiles. To use other models, change `config/models.yaml`.
Never hard-code a model id in code.

## Step 1. Point your shell at the right account

The scripts use the standard AWS credential chain. Set a named profile only if you use one.

```bash
export AWS_PROFILE=your-profile     # optional: leave unset to use your default credentials
export AWS_REGION=us-east-1
aws sts get-caller-identity
```

You can put the same settings in a `.env` file at the repository root. It is git-ignored, and every script
reads it. Two more settings are useful there:

```bash
# .env (never commit this file)
AWS_REGION=us-east-1
RP_EXPECTED_ACCOUNT=<your 12-digit account id>   # scripts refuse to run in any other account
# RP_DEMO_PASSWORD=...                           # optional: otherwise a password is generated into SSM
```

AWS CDK takes the account from your credentials (`CDK_DEFAULT_ACCOUNT`). Nothing in the repository names an
account. If you run `cdk` commands yourself, you can pin one with `-c rp:account=<account-id>`.

The scripts print the account masked to its last four digits (`********9012`) so it does not end up in screenshots
or logs.

## Step 2. Install the tools

```bash
make venv PY=python3.12
(cd infra && npm ci)
(cd usecases/provenant/ui && npm ci)
```

## Step 3. Check the account

```bash
scripts/verify_account.sh --read-only    # describe and list calls only, no model invocation
scripts/verify_account.sh                # also invokes each model once, Amazon Textract and Amazon Comprehend
```

The second run makes a handful of small paid calls. It must end with `0 failed` before you deploy. It checks the
caller identity and Region, that each model in `config/models.yaml` is listed and can be invoked, Textract
`LAYOUT`, Comprehend PII detection, OpenSearch Serverless, Neptune, Fargate and AgentCore reachability, and the
API Gateway and Lambda quotas.

A model `FAIL` usually means your account cannot invoke that model yet. Check model access in the Amazon Bedrock
console, or choose another model in `config/models.yaml`.

## Step 4. Deploy

```bash
scripts/deploy.sh
```

The script asks once, then runs these steps in order. It is idempotent: run it again after any change.

| Step | What it does |
|---|---|
| `build` | Packages the Lambda code and dependency layers without Docker (`scripts/build_lambdas.sh`), and builds the ontology (`make ontology-check`) |
| `bootstrap` | `cdk bootstrap`, only if the account and Region have no `CDKToolkit` stack |
| `core` | Deploys `rp-base`, `rp-knowledge`, `rp-ingest` |
| `agentcore` | `scripts/deploy_agentcore.sh`: AgentCore Gateway, its targets and interceptor, and AgentCore Runtime |
| `agent` | Deploys `rp-agent` |
| `ui` | Builds the UI (`make ui-build`) |
| `web` | Deploys `rp-web` |
| `seed` | `scripts/seed.sh`: demo users and groups, pipeline-version rows, the ontology in Neptune, the demo package |

Useful options:

```bash
scripts/deploy.sh --upload           # also upload the five sample PDFs at the end
scripts/deploy.sh --from agentcore   # resume after a failed step
scripts/deploy.sh --only web         # run one step
```

At the end the script prints the web URL and the API URL. You can read them again at any time:

```bash
aws ssm get-parameter --name /rp/web-url --query Parameter.Value --output text
aws ssm get-parameter --name /rp/api-url --query Parameter.Value --output text
```

`make deploy` followed by `make seed` does the same work without the step control.

## Step 5. Smoke-test the stack

```bash
scripts/smoke.sh          # the user journey through the API, no model calls
scripts/smoke.sh --ask    # adds the headline question on both pipeline versions (two model calls)
```

The smoke test signs in as user A, checks that an ID token is refused (only access tokens are accepted), compares
the two pipeline configs, checks that user B cannot read user A's package, and walks the stage inspector routes.
Every response is validated against the contract models.

## Step 6. Sign in, upload and ask

1. **Get the demo password.** The seed step generated it and stored it as a SecureString:

   ```bash
   aws ssm get-parameter --name /rp/demo/password --with-decryption --query Parameter.Value --output text
   ```

2. **Sign in** at the web URL as `user-a@example.com` (group `lending-analyst`).
3. **Upload** the five PDFs from `data/samples/lending/` into the seeded package `pkg-lending-01`, or run
   `scripts/seed.sh --upload`. Each file starts two Step Functions executions, one per pipeline version.
4. **Watch the stage board.** Rows are documents, columns are stage groups. Click any cell to open the stage
   inspector: state, inputs and outputs with their S3 keys and hashes, engine and version, the config slice the
   stage read, errors and the event timeline. The first 2.3.1 extraction waits a few minutes while docling-serve
   starts from zero. `make prewarm` avoids that wait next time.
5. **Ask** "What is the total income across this package?" on `1.0` and on `2.3.1`. The answer streams in with
   citation chips, the retrieved units and their scores, and the tool trace
   (`search_units` → `graph_hop` → `get_region`). The golden-set marker compares the answer with the expected
   169,650.00.
6. **Open a citation chip.** It shows the page image with the field outlined. Held PII is filled black. A 1.0
   chunk has no region to open.
7. **Sign in as `user-b@example.com`** (group `markets-analyst`) and open the same region link. It returns 403.

## Step 7. (Optional) Measure it

```bash
.venv/bin/python eval/run.py run --mode search                          # retrieval only, no model call
.venv/bin/python eval/run.py run --mode ask --qid q-lend-01 --repeat 3  # full answer path, both versions
```

See [Quickstart 9](09-evaluation-harness.md) for what the metrics mean and how to compare runs.

## Day to day: pause, resume, reset

```bash
make pause      # docling-serve to 0 tasks, provisioned concurrency off, Neptune stopped
make resume     # start Neptune again (about 10 minutes)
make prewarm    # Neptune up, one docling-serve task healthy, warm queries and model pings
make reset      # remove pkg-lending-01's documents, units, index entries and graph so you can upload again
```

`make pause` is the single most useful cost control. Run it at the end of every working day. Neptune restarts by
itself after seven days stopped, so run `make pause` again after a longer break. OpenSearch Serverless, DynamoDB,
S3, Lambda and Step Functions cost little or nothing while idle and stay up.

`scripts/reset.sh --dry-run` shows what a reset would delete.

## Remove everything

```bash
make teardown
```

The teardown script asks twice, once for the stacks and once for the data. It:

1. exports the logged runs and evaluation results into the repository (`runs/`, `eval/results/`);
2. deletes the AgentCore Gateway targets, Gateway and Runtime and their SSM parameters;
3. runs `cdk destroy --all`;
4. empties and deletes the retained data and UI buckets, every object version included;
5. lists anything `rp-*` that is left and deletes the demo password parameter.

Run `scripts/teardown.sh --skip-export` to skip step 1, or `--keep-buckets` to keep the data (the buckets then
keep billing for storage).

**What teardown leaves on purpose.** These cost cents to about USD 1 a month together (estimates):

| Left behind | Why | Remove it with |
|---|---|---|
| AWS KMS key `alias/rp-data` | Retained so data encrypted with it is never orphaned by accident. About USD 1 a month | `aws kms schedule-key-deletion --key-id alias/rp-data --pending-window-in-days 7` (the key stays billable until the 7-day window ends; delete only after the buckets are gone) |
| Log groups `/aws/lambda/rp-*` | Kept so you can read them after teardown. Storage is billed per GB-month | `aws logs delete-log-group --log-group-name <name>` for each name the script lists |
| Lambda layer versions `rp-*` | Retained by the stack. Layer storage counts toward the Lambda code storage quota | `aws lambda list-layer-versions --layer-name rp-<name>`, then `aws lambda delete-layer-version` |
| The AWS CDK bootstrap stack `CDKToolkit` and its asset bucket | Shared by every CDK app in the account and Region | Leave it if you use CDK elsewhere. Otherwise empty its bucket and delete the stack in the AWS CloudFormation console |

**Check that nothing is left.** In the AWS Resource Groups Tag Editor, search all resource types in your Region
for names that start with `rp-`. A day later, check AWS Cost Explorer grouped by service for this Region.

## Costs

All figures are **estimates** for `us-east-1`, based on public list prices and the sizes this stack configures.
They are not measured bills and not a quote. Prices change: check the linked pricing pages before you deploy, and
create an [AWS Budgets](https://docs.aws.amazon.com/cost-management/latest/userguide/budgets-create.html) alert
for your account.

| Item | How it is billed | 8-hour working day | Paused day | Pricing page |
|---|---|---|---|---|
| Amazon Neptune Serverless, 1 to 2.5 NCU | per NCU-hour while running, plus storage and I/O. At about USD 0.16 per NCU-hour (list price, unconfirmed): 1 NCU × 8 h ≈ USD 1.30; 2.5 NCU × 8 h ≈ USD 3.20 | about USD 1.30–3.20 (about USD 3.90 if left running all day at the 1 NCU minimum) | storage only (stopped) | [Neptune pricing](https://aws.amazon.com/neptune/pricing/) |
| Amazon OpenSearch Serverless, collection group with a 0 OCU minimum and 4 OCU maximum | per OCU-hour, plus storage | about USD 2–4 | close to 0 once scaled to zero | [OpenSearch Service pricing](https://aws.amazon.com/opensearch-service/pricing/) |
| AWS Fargate, docling-serve task with 4 vCPU and 16 GB | per vCPU-hour and GB-hour while a task runs (about USD 0.23 per hour) | about USD 1.90 | 0 | [Fargate pricing](https://aws.amazon.com/fargate/pricing/) |
| Amazon Bedrock: answers, embeddings, classification tie-break | per input and output token | about USD 3–8 for about 150 questions | 0 | [Amazon Bedrock pricing](https://aws.amazon.com/bedrock/pricing/) |
| Amazon Bedrock AgentCore Runtime and Gateway | consumption-based | small at this volume | 0 | [AgentCore pricing](https://aws.amazon.com/bedrock/agentcore/pricing/) |
| Amazon Textract, Amazon Comprehend, AWS Lambda, AWS Step Functions, Amazon DynamoDB, Amazon S3, Amazon API Gateway, Amazon CloudFront, AWS KMS, VPC interface endpoint | per request, page, GB or hour | under USD 1 | under USD 1 | [Textract](https://aws.amazon.com/textract/pricing/), [Comprehend](https://aws.amazon.com/comprehend/pricing/), [Lambda](https://aws.amazon.com/lambda/pricing/), [PrivateLink](https://aws.amazon.com/privatelink/pricing/) |
| **Total** | | **about USD 10–18** | **about USD 0.10–1** | |

Notes:

- **Per question.** In our logged local runs, one question on Claude Sonnet 4.6 cost about USD 0.04 in model
  tokens at list price (`make local-ask-summary` prints the totals). Your questions and documents will differ.
- **Textract** only sees pages without a text layer. The lending sample has one scanned page.
- **Comprehend** bills in 100-character units with a three-unit minimum per request.
- **OpenSearch Serverless floor.** If the collection is created without the zero-OCU minimum, the idle floor is
  about USD 5.80 per day. `scripts/verify_account.sh` reports which kind you have.
- `config/prices.yaml` holds the list prices the app uses for its own cost estimates, each with its source and
  the date it was checked. Entries marked `[verify]` have not been confirmed against the pricing page. The
  Neptune and OpenSearch Serverless rows above use the capacity the stack configures (`infra/lib/knowledge-stack.ts`)
  times the list price; recompute them if you change the capacity.
- **OpenSearch Serverless collection group.** The stack puts the collection in a collection group with a
  minimum of 0 and a maximum of 4 OCU for indexing and for search, so idle capacity can scale to zero. See the
  [Amazon OpenSearch Serverless developer guide](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless.html)
  for how capacity is billed.

## Other Regions

`us-east-1` is the only Region this stack has been tested in. Another Region needs at least:

- every model in `config/models.yaml` available there, with matching inference profile ids;
- Amazon Bedrock AgentCore, Amazon Neptune Serverless and Amazon OpenSearch Serverless available there;
- the CDK context `rp:region` and `rp:availabilityZones` (in `infra/cdk.json`) and `AWS_REGION` changed together.

Treat any other Region as untested.

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| `extract` stays RUNNING on 2.3.1 with "waiting for docling-serve (cold start …)" | ECS service `rp-docling`, queue `rp-docling` | Normal for a few minutes from zero. Run `make prewarm` before a session |
| `index` FAILED | Logs of `/aws/lambda/rp-embed-index`; the OpenSearch Serverless data access policy | Reprocess the document (`POST /documents/{docId}/reprocess`); the stage deletes its earlier index entries first |
| `graph` FAILED | Is Neptune stopped? | `make resume`, then reprocess the document |
| Ask returns `not_ready` | The readiness pill | Wait until both lanes show indexed |
| Ask returns 401 | An ID token was sent, or the token expired | Sign in again |
| A lane FAILED with no stage error | The Step Functions execution events | Read the execution's failure cause |
| A model check fails in `verify_account.sh` | Model access in the Amazon Bedrock console | Request access, or change `config/models.yaml` |

You can also inspect every stage directly in AWS: the `rp-ingest` executions in Step Functions, the
`rp-doc-status` table in DynamoDB, the `/aws/lambda/rp-<stage>` log groups (structured JSON with `docId`,
`pipelineVersion` and `stageId`), and the `silver/` and `gold/` prefixes of the data bucket.

## Next

- [Quickstart 7: the agent on Amazon Bedrock AgentCore](07-agent-resolve-retrieve-hop-cite.md)
- [Quickstart 9: evaluation](09-evaluation-harness.md)
- [Build your own use case](10-build-your-own-use-case.md)

# infra

The AWS CDK app (TypeScript) for all three planes. Five stacks, deployed in this order:

| Stack | What it holds |
|---|---|
| `rp-base` | AWS KMS key; versioned, SSE-KMS data bucket with Amazon EventBridge notifications; eight Amazon DynamoDB tables (from `contracts/generated/tables.json`); a VPC with public and private isolated subnets, no NAT gateway, S3 and DynamoDB gateway endpoints and one AWS Step Functions interface endpoint; Lambda layers; `config/`, `prompts/` and the ontology build copied to `s3://…/config/` |
| `rp-knowledge` | Amazon OpenSearch Serverless vector collection `rp-units` with its encryption, network and data access policies; Amazon Neptune Serverless cluster `rp-graph` (IAM database authentication); graph loader, admin and vocabulary export functions |
| `rp-ingest` | The stage functions; the `rp-docling` queue and dead-letter queue; docling-serve on AWS Fargate (upstream image pinned by digest, service discovery through AWS Cloud Map, scaling from zero on queue depth); the `rp-ingest` state machine (`lib/ingest-asl.ts`); EventBridge rules that start one execution per upload and pipeline version, and a failure backstop; the status roll-up from the DynamoDB stream |
| `rp-agent` | Amazon Cognito user pool `rp-users` (groups, an SRP client with no secret); Amazon API Gateway REST API `rp-api` (every method requires the access-token scope); API, tool, interceptor and region functions; IAM roles for Amazon Bedrock AgentCore Gateway and Runtime |
| `rp-web` | UI bucket behind Amazon CloudFront (origin access control), `/api/*` routed to the REST API, `/config.json` for the single-page app |

Amazon Bedrock AgentCore Gateway and Runtime are created by `scripts/deploy_agentcore.sh`, not by CDK.

Every Lambda function is declared once in `lib/functions.ts` (stack, layers, VPC, sizing, environment,
least-privilege access). API routes are declared once in `lib/api-routes.ts`, and a test checks they match the API
router.

## Commands

```bash
make build-lambdas                 # code asset and layers into build/ (no Docker)
cd infra && npm ci
npx cdk synth --all                # no AWS lookups at synth time (Availability Zones are set in cdk.json)
npm test                           # state machine and stack assertions (uses a stub build directory)
npx cdk deploy --all               # usually through scripts/deploy.sh, which handles order, bootstrap and seeding
```

The account comes from your credentials (`CDK_DEFAULT_ACCOUNT`). Nothing in this folder names an account. Set
`AWS_PROFILE` only if you use a named profile.

## Context keys (`cdk.json` or `-c key=value`)

| Key | Default | Meaning |
|---|---|---|
| `rp:account` | from credentials | Target account |
| `rp:region` | `us-east-1` | Target Region (the only tested one) |
| `rp:availabilityZones` | `us-east-1a`, `us-east-1b` | Availability Zones for the VPC |
| `rp:buildDir` | `../build` | Lambda build output |
| `rp:doclingImage` | pinned digest | Upstream docling-serve CPU image |
| `rp:neptuneEngineVersion` | `1.4.8.1` | Neptune engine version |
| `rp:devOrigins` | local Vite ports | Extra CORS origins for the UI dev server |
| `rp:agentRuntimeArn` | unset | When set, `POST /ask` streams straight from the AgentCore Runtime |
| `rp:askTimeoutMs` | `29000` | `/ask` integration timeout |
| `rp:aossAdminArns` | none | Operator role ARNs with write access to the indexes |

To switch `/ask` to streaming after the AgentCore deploy:

```bash
npx cdk deploy rp-agent -c rp:agentRuntimeArn="$(aws ssm get-parameter --name /rp/agent/runtime-arn --query Parameter.Value --output text)"
```

## Costs and clean-up

See [Quickstart 2](../docs/quickstarts/02-deploy-to-aws.md) for estimated costs, `make pause` and `make teardown`.
The data and UI buckets are retained on `cdk destroy`; `scripts/teardown.sh` empties and deletes them.

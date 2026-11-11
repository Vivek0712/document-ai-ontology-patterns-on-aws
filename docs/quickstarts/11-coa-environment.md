# Quickstart 11: spin COA up and down

**What this adds:** an optional, live
[AWS Context Ontology Accelerator (COA)](https://github.com/aws/context-ontology-accelerator) environment next to
Provenant, controlled from **Settings → COA environment** in the UI (or `make coa-up` / `make coa-down`), with an
automatic tear-down when nobody has used it for 60 minutes. Provenant does not need it: the *Governed meaning*
showcase and the Knowledge → Governed meaning screen replay a recorded COA session from `runs/local/coa/`. Bring
COA up only when you want to check the governed metric live.

| | |
|---|---|
| **Time** | Mock mode: 5 minutes. On AWS: about 15 minutes of setup once, then 40–60 minutes per spin-up and 20–30 minutes per tear-down, unattended |
| **Needs** | Mock mode: [Quickstart 1](01-run-locally.md) step 8 (the UI in mock mode). On AWS: [Quickstart 2](02-deploy-to-aws.md) done, and credentials that may create IAM roles for the one-time setup |
| **Cost** | Mock mode: none. On AWS: about **USD 1.3 per hour while COA is up** (estimate, see [Costs](#costs)); close to nothing while it is off |

> **Preview.** The control stacks pass their unit, infrastructure and UI tests, and the CDK app synthesizes
> them, but this release has not yet run a full spin-up and tear-down through them against AWS. Use a sandbox
> account, watch the first spin-up in AWS CodeBuild, and see [what can go wrong](#troubleshooting). The design
> and every permission are in [decision record 0006](../decisions/0006-coa-environment-lifecycle.md).

## Step 1. Try it in mock mode (no AWS)

Start the UI in mock mode as in [Quickstart 1, step 8](01-run-locally.md#step-8-open-the-ui-in-mock-mode), then
open **Settings → COA environment** (`/settings/environment`, also in the command palette). Every view there says
*Simulated · mock mode*: the panel runs the same rules and configuration as the real control, with time running
30 times faster.

- **Spin up** walks through the seven phases (queued, provisioning, toolchain, clone and setup, `make deploy-dev`,
  seed, done), then shows simulated console and REST endpoint URLs, the idle deadline and the cost estimate.
- **Tear down** asks for confirmation and names what it deletes and the hourly cost it stops.
- **Auto tear-down** is on by default: 60 idle minutes (15–480). *Keep alive 60 min* pushes the deadline out.
- Add `?coa=running`, `?coa=failed`, `?coa=partially_down` or `?coa=undeployed` to the URL to start in that state.

In the mock, User A is the simulated environment admin and User B reads only. In a deployment, only members of
the Cognito group `rp-admin` can act; everyone else sees the state read-only, without cost figures.

## Step 2. Enable the control on AWS (once)

The control is two opt-in stacks, off by default (CDK context `rp:coaControl`):

| Stack | What it creates |
|---|---|
| `rp-coa-guard` | The IAM permissions boundary `rp-coa-boundary`, which keeps everything COA creates away from Provenant's stacks, roles, user pool, key, tables, buckets and parameters |
| `rp-coa-control` | The AWS CodeBuild project `rp-coa-lifecycle` (the executor), the AWS Lambda function `rp-coa-control` (state machine, admin check, audit), the idle watcher (an Amazon EventBridge schedule, every 5 minutes), the DynamoDB table `rp-coa-env`, the CloudWatch alarm `rp-coa-env-needs-attention` and the Cognito group `rp-admin` |

COA deploys through **its own CDK bootstrap** (qualifier `rpcoa`, toolkit stack `CDKToolkit-rpcoa`), never through
the one Provenant's stacks use. Run the steps in this order:

```bash
make build-lambdas && make contracts

# 1. the permissions boundary that COA's bootstrap needs
(cd infra && npx cdk deploy rp-coa-guard -c rp:coaControl=true)

# 2. COA's own CDK bootstrap, with that boundary on its execution role (asks first; --print shows the command only)
scripts/coa/bootstrap.sh

# 3. everything else. rp-coa-control fails until step 2 has run: its deploy guard attaches to the new deploy role
(cd infra && npx cdk deploy rp-agent rp-coa-control -c rp:coaControl=true)

# 4. make yourself an environment admin, then sign out and in again so the token carries the group
aws cognito-idp admin-add-user-to-group \
  --user-pool-id "$(aws ssm get-parameter --name /rp/user-pool-id --query Parameter.Value --output text)" \
  --username <your-sign-in-email> --group-name rp-admin
```

Keep `-c rp:coaControl=true` on every later deploy (or set `"rp:coaControl": true` in `infra/cdk.json`).
Otherwise `rp-agent` drops the permission to forward requests and the panel says the control is not deployed.

COA reads its own deploy settings, including the e-mail address of its first admin user (`initialAdminEmail`),
from its SSM parameter `/coa/config`. Create it before the first spin-up as COA's deployment guide describes. This
repository never copies it. COA's deploy falls back to the IAM role `Admin` as the SageMaker Unified Studio admin
unless you store an admin principal in `/rp/coa-control/smus-admin-arns` (optional).

If COA is already running when the control first looks at the account, it is adopted as *Running* with auto
tear-down **off** and a notice. Turn auto tear-down on in the panel when you are happy for the watcher to delete it.

## Step 3. Spin up, use, tear down

From **Settings → COA environment**, or from a terminal:

| Command | What it does |
|---|---|
| `make coa-status` | State, current build and phases, idle deadline and endpoints; without the control stack, COA's own stacks |
| `make coa-up` | Asks, then starts the spin-up (40–60 minutes), including the seed |
| `make coa-down` | Asks, then starts the tear-down (20–30 minutes). **Deletes everything inside COA** |
| `make coa-seed` | Re-runs only the seed against the running COA |

A **spin-up** fetches COA at the commit pinned in [`config/coa.yaml`](../../config/coa.yaml) (v0.3.9; any other
commit is refused), installs COA's toolchain with every download checked against a pinned checksum, runs COA's
own `make deploy-dev`, and then seeds the governed-metric demo with `scripts/coa/seed.py`: the namespace, the
`fin.ttl` vocabulary, the Glue source over the fictitious W-2 Box 1 rows in `data/coa/w2_box1.csv`, the
`qualifying_income` metric, and a Tier 1 check that it returns 169,650.00, the same total as the pipeline's graph.

Each spin-up creates a new COA user pool, console URL and REST endpoint. The panel reads them from COA's stack
outputs; COA's admin gets a new temporary password by e-mail. A **tear-down** deletes the uploaded vocabulary, the
metric, the namespace and the Neptune and OpenSearch data. The seed's own bucket and Glue table stay (they cost
nothing) and the next spin-up seeds again.

The CLI calls the same control function when the stack is deployed, so it obeys the same rules and is audited as
`cli`. Without the stack, `make coa-up` and `make coa-down` run COA's make targets in a local checkout (`COA_DIR`,
default `~/coa`, at the pinned commit) after a typed confirmation. That needs COA's own toolchain on your machine
(JDK 21, Node.js 22, pnpm, uv, Maven and Docker).

### States

| State | Meaning | What to do |
|---|---|---|
| Off | No COA stacks. The watcher still checks every 15 minutes: a COA deployed by hand becomes *Running* (detected) and is watched like any other | Spin up when needed |
| Starting | The CodeBuild project is deploying; the panel shows its phases | Wait. Tearing down now needs *force*, which stops the build first |
| Running | COA is up, with its console URL and REST endpoint | Sign in to COA with the new temporary password. Press *Keep alive 60 min* before a long pause |
| Stopping | The destroy is running (after a forced stop it first waits up to 60 minutes for COA stacks still being created) | Wait |
| Partially down | Amazon Bedrock AgentCore holds the network interfaces of deleted runtimes for up to 8 hours | Nothing: the watcher retries every 30 minutes (at most 16 times). COA cannot be redeployed meanwhile |
| Failed (spin-up) | The deploy or the seed failed; the panel shows the phase, the message and a link to the build log | Read the log tail, then retry the spin-up or tear down what is left. Auto tear-down still applies |
| Failed (tear-down) | The destroy failed, left stacks behind, or ran out of retries. **Parts of COA may still be billing** | It is retried once after 30 minutes, then raises `rp-coa-env-needs-attention`. Press *Tear down again*; spin-up is refused until a tear-down succeeds |

## Auto tear-down

On by default, after **60 idle minutes** (15–480, in the *Auto tear-down* card). Every 5 minutes the watcher
looks at the latest of:

- successful requests to COA's REST API (API Gateway `Count − 4XXError`, so scanners and unauthenticated calls
  do not count),
- invocations of COA's AgentCore runtimes,
- Provenant calls that reach live COA (none today: the screens read the recording),
- the *Keep alive 60 min* button, and the moment COA became running.

Looking at the settings page is not activity. With auto tear-down on, COA is also torn down **10 hours** after it
came up whatever the activity (an open COA console tab keeps polling its API), unless *Keep alive* was pressed in
the last hour. The watcher never acts while a build runs or within 15 minutes of a state change. If CloudWatch
cannot be read, the panel shows the error; once that has lasted longer than the idle threshold, the watcher
decides on the activity it last saw. Its tear-downs are audited as `idle-watcher`.

## Where to look

| Where | What |
|---|---|
| CodeBuild project `rp-coa-lifecycle` | Builds, phases and logs (`/aws/codebuild/rp-coa-lifecycle`; e-mail addresses are masked) |
| DynamoDB table `rp-coa-env` | The state row: state, version, current build, activity and history |
| DynamoDB table `rp-access-log`, `runId = coa-environment` | Every allowed, refused and denied action, the watcher's actions and detections |
| CloudWatch alarm `rp-coa-env-needs-attention` | A failed tear-down, or activity unreadable for too long. It has no action: connect it to your own notification path if you want one |
| CloudFormation `coa-dev-*`, `CDKToolkit-rpcoa` | COA's own stacks and its CDK bootstrap |

## Costs

About **USD 1.3 per hour while COA is up**, from list prices in `us-east-1` (`coaEnvironment` in
[`config/prices.yaml`](../../config/prices.yaml); several rows are still marked `[verify]`):

| Component | USD per hour (estimate) |
|---|---|
| AWS Fargate ontology engine, 8 vCPU / 32 GB | 0.47 |
| Amazon Neptune `db.r8g.large` | 0.35 |
| Amazon OpenSearch Serverless collection | 0.24 |
| About ten interface VPC endpoints and a NAT gateway | 0.25 |

The panel shows admins the hourly rate and this session's hours times that rate, labeled *Estimate*. The session
clock stops when the destroy has run. *Partially down* costs close to nothing. After a failed tear-down, the panel
says what may still be billing. Each spin-up also uses CodeBuild minutes (`BUILD_GENERAL1_LARGE`, about an hour).
With the stacks enabled and COA off, the control itself costs well under USD 1 a month (the watcher's Lambda
invocations, one DynamoDB row and the alarm).

## Troubleshooting

| Symptom | Check | Fix |
|---|---|---|
| The panel says *Environment control isn't deployed* | Was the last deploy run with `-c rp:coaControl=true`? | Deploy `rp-agent rp-coa-control` with the flag, or use the CLI |
| *Spin up* and *Tear down* are disabled | Are you in the `rp-admin` group, and did you sign in again after joining it? | Step 2, item 4 |
| `rp-coa-control` fails to deploy | Did `scripts/coa/bootstrap.sh` run? | Run it, then deploy again |
| The spin-up fails with AccessDenied on a role | A role COA creates did not get the permissions boundary | Read the role name in the build log; decision record 0006 explains the boundary. Do not widen the boundary |
| The spin-up fails in the seed with AccessDenied on Cognito | COA's user pool lacks the `Project=provenant-coa` tag the seed's permission requires | Check the tags on COA's user pool |
| *Failed* after a tear-down | The build log, then CloudFormation `coa-dev-*` | Press *Tear down again*. If you clean up by hand, the watcher moves to *Off* once no COA stack is left |
| *Partially down* for hours | AgentCore network interfaces | Expected for up to 8 hours; the watcher keeps retrying |

## Next

- [Decision record 0006](../decisions/0006-coa-environment-lifecycle.md): the design, the executor's permissions
  and the residual risks this release accepts.
- [Governed meaning and the COA cross-check](../../usecases/provenant/README.md#governed-meaning-and-the-coa-cross-check):
  what the recorded session shows.
- [Quickstart 5](05-ontology-binding-and-sparql.md): the governed definition in Git that COA cross-checks.

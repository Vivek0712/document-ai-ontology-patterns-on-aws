# 0006. COA is spun up and down from Settings, and tears itself down when idle

**Status:** accepted

## Context

COA (the AWS Context Ontology Accelerator, v0.3.9, 18 `coa-dev-*` stacks) costs about $1.3 an hour while it is up:
an 8 vCPU / 32 GB Fargate ontology engine, Neptune `db.r8g.large`, an OpenSearch Serverless collection, about ten
interface VPC endpoints and a NAT gateway (`config/prices.yaml` `coaEnvironment`, list prices, `[verify]`). The demo
needs it only for a live cross-check; SC-8, SC-0 and W-10 already show a recorded session
(`runs/local/coa/2026-11-11`, decision 0005). Leaving it up between demos is the single biggest cost of the
project, and redeploying it by hand takes a laptop, JDK 21, Node 22, pnpm, uv, Maven and Docker for most of an hour.

## Decision

- **An opt-in stack, `rp-coa-control`** (CDK context `rp:coaControl=true`, default false). Without it the panel says
  "Environment control isn't deployed" and shows the CLI (`make coa-status | coa-up | coa-down`), which runs COA's own
  make targets locally after a typed confirmation.
- **CodeBuild is the executor** (`rp-coa-lifecycle`): COA's deploy is a long CDK app with Docker image assets, so it
  needs a privileged Docker host, 40–60 minutes and COA's toolchain. A Lambda cannot run it (15 minutes, no Docker);
  Step Functions would only wrap the same build. CodeBuild gives a privileged amd64 container
  (`aws/codebuild/standard:7.0`, `BUILD_GENERAL1_LARGE`), a 150-minute timeout, a concurrency limit of 1 and phase
  events. The build fetches the public COA repo at the pinned **commit** (`config/coa.yaml` `coa.commit`; the tag is
  only a label, and any other `HEAD` is refused), installs the pinned toolchain with every download checked against a
  pinned checksum, registers QEMU for COA's linux/arm64 images from an image pinned by digest, runs
  `SCL_PREFIX=coa make deploy-dev` with the CDK context the earlier deploy used, then re-seeds the governed-metric demo
  (`scripts/coa/seed.py`, the recorded session's calls in order, including the `coa:namespace` tag on the Glue database that
  COA's ownership check requires). Tear-down first waits (up to 60 min) for COA stacks still being created or updated
  (a forced stop of a spin-up leaves CloudFormation running), then runs COA's `scripts/destroy.sh` directly (`make
  destroy-dev` reports its exit 3 as 2), with GuardDuty's leftovers in COA's VPC included. A destroy that exits 0 but
  leaves `coa-dev-*` stacks reports `leftovers`. Every deploy and destroy line passes through an e-mail mask before
  it reaches the 30-day build log (COA's admin e-mail is a Cognito username, so stack events can print it).
- **COA's own CDK bootstrap.** The executor never uses the bootstrap Provenant's stacks use. COA deploys with
  qualifier `rpcoa` (written to the build's `~/.cdk.json`, with `@aws-cdk/core:permissionsBoundary`), set up once by
  `scripts/coa/bootstrap.sh` after stack `rp-coa-guard` has created the permissions boundary `rp-coa-boundary`. See
  "The executor's permissions" below.
- **One state machine, server-side** (`services/lambdas/coa_control`): states `off`, `starting`, `running`,
  `stopping`, `failed`, `partially_down`. One row in `rp-coa-env`, every write conditional on its `version`; the row
  is claimed before `StartBuild` (the conditional write is the lock), and CodeBuild's concurrency limit is the second
  lock. Up while stopping or partially down, and down while starting, are refused (409); down while starting is
  allowed only with an explicit `force` (the running build is stopped first). Repeats are no-ops, and an
  `idempotencyKey` makes a retried request a no-op. EventBridge mirrors every build state and phase change into the
  row, read back with `BatchGetBuilds`, so a missed or duplicated event changes nothing; the idle tick reconciles a
  build whose events were lost. A claim whose start was never bound (a lost `StartBuild` answer, a write race, an
  expired 5-minute idempotency token) is matched to its build by `RP_OP_ID` before anything is started again;
  `StartBuild` refusals that mean "not now" (the one concurrency slot still held by a stopped build) keep the claim
  pending for up to 20 minutes instead of failing it. A forced tear-down is refused while the spin-up's build has no
  id yet. Background writes (activity, timestamps) go to attributes the versioned write never touches, so a stale
  copy cannot overwrite them.
- **Admins only.** A Cognito group `rp-admin` (created by the opt-in stack) may act; everyone else sees the state read
  only, without cost figures. `rp-api` owns the routes and forwards the authorizer's sub and groups (never the
  body's) to `rp-coa-control`, which checks the group and writes one `rp-access-log` row (run `coa-environment`,
  injection point `coa-control`) for every allowed, refused and denied action, and for every automatic one (caller
  `idle-watcher`, also for detections). `scripts/coa_env.sh` invokes the same function as caller `cli`, with the role
  it reports from `sts get-caller-identity` in the audit text; IAM decided who may invoke (only `rp-api`'s role in
  this app, asserted by the infra tests).
- **Idle means nobody used COA.** Activity is the latest of: successful COA REST API requests (CloudWatch
  `AWS/ApiGateway` `Count − 4XXError`, `ApiName=coa-dev-api`, at least `minRequestsPerPeriod` per 5 minutes; scanners
  and unauthenticated calls are not activity), invocations of COA's AgentCore runtimes (`AWS/Bedrock-AgentCore`
  `Invocations`, runtimes `coa_dev_*` with data in the last 3 h, all of them, no truncation), Provenant calls that
  reach live COA (`op: touch`; none today, the screens read the recording) and the "Keep alive 60 min" button. The
  moment COA became running counts too. Viewing the settings page is not activity. The panel shows the count behind
  each source. Every 5 minutes the watcher tears COA down when auto tear-down is on (default, 60 minutes, 15–480), the
  state is running (or failed after a spin-up, which may have left billable stacks) and idle ≥ the threshold, never
  within 15 minutes of a state change and never while a build runs.
- **A hard cap.** With auto tear-down on, a session is torn down `maxSessionHours` (10) after it became running
  whatever the activity (an open COA console tab polls the API for ever), unless "Keep alive" was pressed within the
  last hour. Audited as "max session 10 h".
- **An unreadable activity source is not a reason to stay up for ever.** A failed CloudWatch read is recorded on the
  row and shown in the panel; no tear-down starts on it until it has lasted longer than the idle threshold, after
  which the watcher decides on the activity it last saw.
- **Off is checked against CloudFormation.** While off, the watcher (and a GET, at most every 15 minutes) looks for
  COA's network stack: COA deployed by hand becomes running (`detected`) and the idle rules apply to it.
- **Failures surface, and a failed tear-down is retried once.** A failed build goes to `failed` with its phase,
  CodeBuild's message and the log link, and the operation that failed. A failed tear-down (or one that left stacks,
  or ran out of ENI retries) is retried automatically once, 30 minutes later; after that it stays `failed`, raises the
  CloudWatch alarm `rp-coa-env-needs-attention` (metric `Provenant/CoaEnv` `NeedsAttention`, emitted by the watcher;
  no action of its own, so no new e-mail or SNS subscription), and spin-up is refused until a tear-down succeeds or
  the watcher finds no COA stack left. A failed seed leaves COA `running` with the error shown.
- **Adopting a running COA.** If COA is already running when the control first reads the account, it predates the
  control (possibly hand-seeded with an account-specific setup), so it is adopted with auto tear-down **off** and a
  notice until an admin turns it on.
- **ENIs.** AgentCore keeps a deleted runtime's network interfaces for up to 8 hours; COA's destroy exits 3 until
  then. The build reports that as `eni_pending` (an exported variable), the state is `partially_down`, and the watcher
  runs the destroy again every 30 minutes, at most 16 times, then gives up to `failed`. COA's preflight refuses a
  deploy over a network stack that is still deleting, so spin-up is refused meanwhile.
- **Cost is an estimate, for admins.** The panel shows $/h and this session's hours × that rate, tagged `Estimate`
  with `prices.yaml`'s date and sources. The session clock stops when the destroy has run (partially down has no
  charge of note; after a failed tear-down the panel says what may still bill). It is the one cost figure in the UI that is not behind
  `RP_PUBLIC_METRICS`: it is an operator's figure about infrastructure, not a published demo metric, and viewers do
  not get it.
- **Mock mode simulates it** (`usecases/provenant/ui/src/mocks/coaEnv.ts`, the same rules and config, time 30× faster) and every view
  says "Simulated · mock mode".

## Consequences

- **Data loss is the point of a tear-down.** Everything inside COA goes: the uploaded `fin.ttl`, the metric, the
  namespace, its source, Neptune and OpenSearch data. A spin-up re-seeds only the governed-metric demo. The seed's
  bucket (`coa-dev-provenant-<account>-<region>`) and Glue table are ours and stay; they cost nothing.
- **A new COA user pool on every deploy.** COA's admin (`initialAdminEmail` in COA's own SSM `/coa/config`, read by
  COA's CDK app, never copied here) gets a new temporary-password e-mail and signs in again. The seed signs in as its
  own user (`provenant-seed@example.com`, group `Admin`, random password never stored, disabled afterwards), so
  COA's `definedBy` on the metric is that user, not a person.
- **Endpoints change.** The console URL and REST endpoint are read from COA's stack outputs after each spin-up.
- **Up to 8 hours partially down**, during which the VPC, subnets and two security groups remain (no hourly charge of
  note) and COA cannot be redeployed under the same prefix.
- **A new COA release is a config change**: `coa.tag`, `coa.commit` and, when COA's toolchain moves, the versions and
  `executor.checksums` together.
- **arm64 images under QEMU** make the first spin-up slower than on an arm64 laptop; the 150-minute timeout leaves
  room. If it proves too slow, an ARM CodeBuild image with QEMU for the two amd64 images is the alternative.

### The executor's permissions

**COA's own bootstrap.** CloudFormation does the deploying through CDK bootstrap roles, but not the ones Provenant's
stacks use: COA gets qualifier `rpcoa` (toolkit stack `CDKToolkit-rpcoa`). Three things keep it away from Provenant:

1. **The permissions boundary `rp-coa-boundary`** (stack `rp-coa-guard`) is on the `rpcoa` CloudFormation execution
   role (`cdk bootstrap --custom-permissions-boundary`) and, through CDK's `@aws-cdk/core:permissionsBoundary`
   context, on every role COA's apps create. It allows everything except: creating a role or user without this same
   boundary, removing a boundary, editing the two guard policies, IAM user credentials, any IAM change to `role/rp-*`,
   `role/cdk-*`, `policy/rp-*`, `policy/cdk-*`, assuming `rp-*` or `cdk-*` roles, CloudFormation changes to `rp-*`,
   `CDKToolkit` and `CDKToolkit-rpcoa`, anything on Provenant's user pool and KMS key, anything on a resource carrying
   Provenant's `project` tag (`infra/lib/app.ts`), Provenant's tables, functions, buckets, state machines, rules, CodeBuild projects and
   `/rp/*` parameters by name, and writes to the default bootstrap's asset bucket and image repository.
2. **The deploy guard `rp-coa-deploy-guard`** (stack `rp-coa-control`) on the `rpcoa` deploy role denies
   CloudFormation changes to `rp-*` and both toolkit stacks. CloudFormation lets anyone who may update a stack use
   that stack's own service role, so without it the deploy role could reach Provenant through Provenant's stacks.
   The policy attaches by role name, so `rp-coa-control` cannot be deployed before `scripts/coa/bootstrap.sh`.
3. **The executor** may assume only the four `rpcoa` roles, and pass only the `rpcoa` execution role; it reads the
   default bootstrap bucket only for its own source bundle.

Everything else is the direct API calls of COA v0.3.9's `deploy-all.sh`, `deploy.sh`, `preflight-deploy.sh`,
`check-*.sh`, `destroy.sh` and its CDK app's synth-time reads, plus our seed and `lifecycle.sh`
(`infra/lib/coa-control-stack.ts`, `coaExecutorStatements`):

| Sid | Actions | Resources | Why |
|---|---|---|---|
| CdkBootstrapRoles | sts:AssumeRole | the four `cdk-rpcoa-*` roles | `cdk deploy` / `cdk destroy`, asset publishing, lookups |
| ReadCoaBootstrapVersion | ssm:GetParameter | `/cdk-bootstrap/rpcoa/version` | the CLI's bootstrap version check |
| DeleteCoaStacksDirectly | cloudformation:DeleteStack | `stack/coa-dev-*` | destroy.sh deletes the connector stacks (a separate CDK app) with the CLI |
| PassCfnExecRoleToCloudFormation | iam:PassRole (to cloudformation.amazonaws.com) | the `rpcoa` cfn-exec role | a direct DeleteStack uses the stack's own role |
| ReadCoaStacks | DescribeStacks, ListStackResources, DescribeStackEvents | `stack/coa-*`, `stack/CDKToolkit`, `stack/CDKToolkit-rpcoa` | deploy.sh, preflight (checks `CDKToolkit` exists), check-teardown-pending, check-vpc-limit, destroy.sh, seed |
| ListStacks | cloudformation:ListStacks | `*` (no resource level) | destroy.sh, lifecycle `settle` and `check` |
| ReadCoaParameters | ssm:GetParameter | `parameter/coa/*`, the optional SMUS-admin parameter | COA's CDK app reads `/coa/config`; the connector and seed read `/coa/*` |
| PreflightReads | Lambda account settings, Bedrock model and profile lists, service quotas, EC2/RAM/Glue/Cloud Map/Lake Formation describes and lists | `*` (no resource level) | preflight, the CDK app's AZ lookup, destroy.sh's discovery |
| GetRoles | iam:GetRole | `role/Admin`, `role/coa-dev-*` | SMUS admin fallback, destroy.sh |
| PublicEcrLogin | ecr-public:GetAuthorizationToken, sts:GetServiceBearerToken | `*` | preflight logs Docker in to public ECR |
| DeleteCoaRuntimes | bedrock-agentcore:Get/DeleteAgentRuntime | `runtime/coa_dev_*` | destroy.sh deletes runtimes before their stacks |
| DrainVkgServices | ecs:List/Describe/Update/DeleteService | `cluster/coa-dev-*`, `service/coa-dev-*/*` | destroy.sh drains VKG |
| CleanCloudMap | servicediscovery:DeregisterInstance, DeleteService | `service/*` (random ids) | destroy.sh, filtered by COA's namespace id |
| ReadDataZoneDomain | datazone:GetDomain | `domain/*` | destroy.sh checks the domain's name first |
| DeleteCoaDataZoneDomain | datazone:DeleteDomain | `domain/*` **tagged `Project=provenant-coa`** | destroy.sh force-deletes COA's domain; no other DataZone / SMUS domain |
| DeleteFederatedGlue | glue Get/DeleteConnection, Get/DeleteCatalog | `catalog/coadevds_*`, `connection/coadevds_*` | destroy.sh |
| LakeFormationCleanup | lakeformation:DeregisterResource, GrantPermissions, RevokePermissions | `*` (no resource level) | destroy.sh grants itself DROP to delete a federated catalog |
| DeleteCoaSecurityGroupsAndEndpoints | ec2:DeleteSecurityGroup, DeleteVpcEndpoints, CreateTags | SGs and endpoints tagged `aws:cloudformation:stack-name=coa-dev-*` | destroy.sh |
| DeleteGuardDutyLeftovers | ec2:DeleteVpcEndpoints, DeleteSecurityGroup | endpoints and SGs tagged `GuardDutyManaged=true` | destroy.sh with `SCL_DESTROY_DELETE_GUARDDUTY_ENDPOINT=1` (only in COA's VPC, after checking nothing else uses it) |
| DeleteDetachedEnis | ec2:DeleteNetworkInterface | `network-interface/*` | ENIs Glue/AgentCore detached but left (not tagged) |
| CheckCoaFunctions | lambda:GetFunction | `function:coa-dev-*` | destroy.sh |
| EmptyCoaBuckets | s3 List/DeleteObject(Version) | `coa-dev-*` buckets | destroy.sh empties a refilled access-log bucket |
| SeedBucket | s3 Create/Put…, ListBucket | `coa-dev-provenant-<account>-<region>` | seed: the CSV |
| SeedGlueTable | glue Get/Create/UpdateTable, Get/CreateDatabase, TagResource | the seed database and its tables | seed (incl. the `coa:namespace` tag) |
| SeedCoaUser | cognito-idp Admin{Create,Enable,Disable}User, AdminSetUserPassword, AdminAddUserToGroup | `userpool/*` **tagged `Project=provenant-coa`** | the pool id changes on every deploy; COA tags it from `SCL_PROJECT_TAG` |
| NeverProvenantUserPool | **Deny** cognito-idp:* | Provenant's `rp-users` pool | whatever the tag says |

`rp-coa-control` itself may start, stop, list and read builds of its one project, read CloudWatch metrics, describe
`coa-dev-*` stacks, read and write its table and put rows into `rp-access-log`. `rp-api` gains only
`lambda:InvokeFunction` on `rp-coa-control`, and only when the stack is enabled; no other role in the app may invoke
it (infra test).

**Supply chain.** The executor deploys with administrator-equivalent rights inside the boundary, so it runs only
pinned code: COA by commit, Node / Maven / uv by SHA-256/512 from the publishers' checksum files, pnpm by its npm
integrity, the privileged QEMU image by digest, COA's Python and Node dependencies from its lock files
(`uv sync --locked`, `pnpm install --frozen-lockfile`), and the seed's boto3 from COA's locked environment. Not pinned
by us: the AWS-managed CodeBuild image, Maven Central artifacts COA's Smithy build resolves (Maven checks their
published checksums), and container base images COA's own Dockerfiles name.

**Residual breadth, accepted:**

- Inside the boundary the `rpcoa` execution role is administrator: it can create and delete anything in the account
  that is not Provenant's, not the default bootstrap and not carrying Provenant's `project` tag, including resources of
  other projects in this account. The boundary names Provenant's resources; it cannot know about anything else.
- The `rpcoa` deploy role may change any stack except `rp-*` and the toolkit stacks (the bootstrap template's own
  policy is `*`).
- `lakeformation:GrantPermissions` / `RevokePermissions` / `DeregisterResource` on `*` (no resource-level permission).
- `servicediscovery:DeleteService` / `DeregisterInstance` on `service/*`: VKG's service ids are random and untagged;
  destroy.sh filters by COA's namespace id.
- `ec2:DeleteNetworkInterface` on `network-interface/*`: leftover Glue/AgentCore ENIs carry no tag and their VPC id
  changes on every deploy; destroy.sh deletes only detached ENIs in COA's VPC.
- `ec2:DeleteVpcEndpoints` / `DeleteSecurityGroup` on anything GuardDuty tagged `GuardDutyManaged=true`, in any VPC
  (GuardDuty recreates its own endpoint where monitoring is on); destroy.sh limits it to COA's VPC.
- The Cognito and DataZone conditions rely on COA tagging its user pool and domain with `Project=provenant-coa`
  (COA's `Tags.of(stack)` with `SCL_PROJECT_TAG`). If a deploy did not tag them, the seed or the domain delete fails
  visibly (AccessDenied in the build log); nothing broader is allowed instead.
- Whoever holds `lambda:InvokeFunction` on `rp-coa-control` (in practice an account admin) can act as `cli`; the
  operator in the audit row is what the CLI reported, not a verified identity. Such a principal could start the
  CodeBuild project directly anyway.
- None of this has run against AWS yet. If a role COA creates lacks the boundary (a construct the CDK context does
  not reach), CloudFormation refuses to create it and the first spin-up fails with that role in the log; the fix is
  then in how that construct is synthesised, not a wider boundary.

/**
 * rp-coa-guard and rp-coa-control (opt-in, context `rp:coaControl=true`): spin COA up and down from Settings → COA
 * environment, and tear it down when idle. Decision record: docs/decisions/0006-coa-environment-lifecycle.md.
 *
 * - rp-coa-guard: the permissions boundary of COA's own CDK bootstrap (qualifier `bootstrap.qualifier`). It sits on
 *   that bootstrap's CloudFormation execution role and, through CDK's permissions-boundary context, on every role COA
 *   creates; it denies Provenant's stacks, roles, user pool, key, data and the default bootstrap. Deployed before
 *   `scripts/coa/bootstrap.sh`, which needs it.
 *
 * - Table `rp-coa-env`: one row (pk `env`), written conditionally on its `version` (optimistic locking).
 * - Lambda `rp-coa-control`: the state machine. `rp-api` forwards /coa/environment to it; EventBridge sends it every
 *   `rp-coa-lifecycle` state and phase change, and an idle tick every `idle.tickMinutes`.
 * - CodeBuild `rp-coa-lifecycle` (the executor): clones COA at the pinned tag and runs `make deploy-dev` + the
 *   seed, or COA's destroy. Privileged (Docker image assets), amd64, 150 min, one build at a time.
 * - Cognito group `rp-admin` in `rp-users`: only its members may change the environment.
 * - Policy `bootstrap.deployGuardPolicy` on COA's bootstrap deploy role: no CloudFormation change to rp-* stacks or
 *   the toolkit stacks (a stack's own service role would otherwise be usable through it).
 * - Alarm `rp-coa-env-needs-attention` (no action: existing notification paths only) on `Provenant/CoaEnv`.
 *
 * IAM of the executor: COA's own CDK bootstrap roles for everything CloudFormation does, plus only the direct calls
 * COA's scripts and our seed make outside CloudFormation (`coaExecutorStatements`, each with its reason).
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as codebuild from 'aws-cdk-lib/aws-codebuild';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3assets from 'aws-cdk-lib/aws-s3-assets';
import { Construct } from 'constructs';
import { BaseResources } from './base-stack';
import { CoaControlConfig, REPO_ROOT, loadCoaControlConfig } from './names';
import { buildFunctions } from './rp-functions';

export interface CoaControlStackProps extends StackProps {
  base: BaseResources;
  userPoolId: string;
  repoRoot?: string;
}

export interface CoaGuardStackProps extends StackProps {
  base: BaseResources;
  userPoolId: string;
  repoRoot?: string;
}

/** CloudFormation changes (the deploy role's and the boundary's denies on stacks COA must never touch). */
export const CFN_WRITES = ['cloudformation:CreateChangeSet', 'cloudformation:ExecuteChangeSet', 'cloudformation:DeleteChangeSet',
  'cloudformation:CreateStack', 'cloudformation:UpdateStack', 'cloudformation:DeleteStack', 'cloudformation:RollbackStack',
  'cloudformation:ContinueUpdateRollback', 'cloudformation:CancelUpdateStack', 'cloudformation:UpdateTerminationProtection',
  'cloudformation:SetStackPolicy', 'cloudformation:CreateStackRefactor', 'cloudformation:ExecuteStackRefactor'];
const IAM_WRITES = ['iam:Create*', 'iam:Delete*', 'iam:Update*', 'iam:Put*', 'iam:Attach*', 'iam:Detach*', 'iam:Tag*',
  'iam:Untag*', 'iam:PassRole', 'iam:AddRoleToInstanceProfile', 'iam:SetDefaultPolicyVersion'];

/** Stacks COA's bootstrap may never change: Provenant's and both toolkit stacks. */
function protectedStacks(stack: Stack, cfg: CoaControlConfig): string[] {
  const { partition: p, region: r, account: a } = stack;
  return [`arn:${p}:cloudformation:${r}:${a}:stack/rp-*/*`, `arn:${p}:cloudformation:${r}:${a}:stack/CDKToolkit/*`,
    `arn:${p}:cloudformation:${r}:${a}:stack/${cfg.bootstrap.toolkitStackName}/*`];
}

/**
 * The permissions boundary of COA's bootstrap (`bootstrap.boundaryPolicy`): everything, except Provenant and the
 * default CDK bootstrap, and no role or user without this same boundary (so nothing COA deploys can step outside it).
 */
export function coaBoundaryStatements(stack: Stack, cfg: CoaControlConfig, refs: { userPoolArn: string; keyArn: string }):
iam.PolicyStatement[] {
  const { partition: p, region: r, account: a } = stack;
  const boundaryArn = `arn:${p}:iam::${a}:policy/${cfg.bootstrap.boundaryPolicy}`;
  const guardArn = `arn:${p}:iam::${a}:policy/${cfg.bootstrap.deployGuardPolicy}`;
  const allow = (sid: string, actions: string[], resources: string[]) =>
    new iam.PolicyStatement({ sid, actions, resources });
  const deny = (sid: string, actions: string[], resources: string[], conditions?: Record<string, unknown>) =>
    new iam.PolicyStatement({ sid, effect: iam.Effect.DENY, actions, resources, conditions });
  return [
    allow('AllowWithinTheBoundary', ['*'], ['*']),
    deny('DenyRolesOrUsersWithoutThisBoundary', ['iam:CreateRole', 'iam:PutRolePermissionsBoundary', 'iam:CreateUser',
      'iam:PutUserPermissionsBoundary'], ['*'], { StringNotEquals: { 'iam:PermissionsBoundary': boundaryArn } }),
    deny('DenyBoundaryRemoval', ['iam:DeleteRolePermissionsBoundary', 'iam:DeleteUserPermissionsBoundary'], ['*']),
    deny('DenyGuardPolicyEdits', ['iam:CreatePolicyVersion', 'iam:DeletePolicy', 'iam:DeletePolicyVersion',
      'iam:SetDefaultPolicyVersion'], [boundaryArn, guardArn]),
    deny('DenyIamUserCredentials', ['iam:CreateAccessKey', 'iam:CreateLoginProfile', 'iam:UpdateLoginProfile',
      'iam:AttachUserPolicy', 'iam:PutUserPolicy', 'iam:AddUserToGroup'], ['*']),
    deny('DenyProvenantAndBootstrapIam', IAM_WRITES, [`arn:${p}:iam::${a}:role/rp-*`, `arn:${p}:iam::${a}:role/cdk-*`,
      `arn:${p}:iam::${a}:policy/rp-*`, `arn:${p}:iam::${a}:policy/cdk-*`]),
    deny('DenyAssumingProvenantOrBootstrapRoles', ['sts:AssumeRole', 'sts:TagSession'],
      [`arn:${p}:iam::${a}:role/rp-*`, `arn:${p}:iam::${a}:role/cdk-*`]),
    deny('DenyProvenantStacks', CFN_WRITES, protectedStacks(stack, cfg)),
    deny('DenyProvenantUserPool', ['cognito-idp:*'], [refs.userPoolArn]),
    deny('DenyProvenantKey', ['kms:*'], [refs.keyArn]),
    deny('DenyProvenantTagged', ['*'], ['*'], { StringEquals: { 'aws:ResourceTag/project': 'regionproof' } }),
    deny('DenyProvenantByName', ['dynamodb:*', 'lambda:*', 's3:*', 'states:*', 'events:*', 'codebuild:*', 'ssm:PutParameter',
      'ssm:DeleteParameter', 'ssm:DeleteParameters', 'ssm:LabelParameterVersion'], [
      `arn:${p}:dynamodb:${r}:${a}:table/rp-*`, `arn:${p}:lambda:${r}:${a}:function:rp-*`, `arn:${p}:s3:::rp-*`,
      `arn:${p}:s3:::rp-*/*`, `arn:${p}:states:${r}:${a}:stateMachine:rp-*`, `arn:${p}:events:${r}:${a}:rule/rp-*`,
      `arn:${p}:codebuild:${r}:${a}:project/rp-*`, `arn:${p}:ssm:${r}:${a}:parameter/rp/*`]),
    deny('DenyDefaultBootstrapAssets', ['s3:PutObject*', 's3:DeleteObject*', 's3:PutBucket*', 's3:DeleteBucket*', 'ecr:*'], [
      `arn:${p}:s3:::cdk-hnb659fds-*`, `arn:${p}:s3:::cdk-hnb659fds-*/*`, `arn:${p}:ecr:${r}:${a}:repository/cdk-hnb659fds-*`]),
  ];
}

/** rp-coa-guard: the boundary only (it must exist before `scripts/coa/bootstrap.sh` runs). */
export class CoaGuardStack extends Stack {
  readonly boundary: iam.ManagedPolicy;

  constructor(scope: Construct, id: string, props: CoaGuardStackProps) {
    super(scope, id, props);
    const cfg = loadCoaControlConfig(props.repoRoot ?? REPO_ROOT);
    const userPoolArn = `arn:${this.partition}:cognito-idp:${this.region}:${this.account}:userpool/${props.userPoolId}`;
    this.boundary = new iam.ManagedPolicy(this, 'Boundary', {
      managedPolicyName: cfg.bootstrap.boundaryPolicy,
      description: `Permissions boundary of COA's CDK bootstrap (${cfg.bootstrap.qualifier}): no Provenant resource, no default bootstrap`,
      statements: coaBoundaryStatements(this, cfg, { userPoolArn, keyArn: props.base.key.keyArn }),
    });
    new CfnOutput(this, 'BoundaryPolicy', { value: this.boundary.managedPolicyName });
  }
}

/** The files the build needs from this repo (the rest of the build is COA's own checkout). */
export const BUNDLE_FILES = [
  'scripts/coa/lifecycle.sh',
  'scripts/coa/seed.py',
  'scripts/coa/metric_qualifying_income.json',
  'data/coa/w2_box1.csv',
  'ontology/src/rp/fin.ttl',
  'contracts/generated/coa_control.json',
];

/** Copies BUNDLE_FILES into a fresh directory (the asset hash follows their content only). */
export function stageBundle(repoRoot: string): string {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-coa-bundle-'));
  for (const rel of BUNDLE_FILES) {
    const src = path.join(repoRoot, rel);
    if (!fs.existsSync(src)) throw new Error(`${src} missing (run make contracts?)`);
    fs.mkdirSync(path.join(dir, path.dirname(rel)), { recursive: true });
    fs.copyFileSync(src, path.join(dir, rel));
  }
  return dir;
}

/** The buildspec: one lifecycle.sh step per phase; RP_RESULT carries ok | eni_pending | seed_failed | leftovers back,
 * RP_LEFTOVERS the stacks a tear-down left. */
export function lifecycleBuildSpec(cfg: CoaControlConfig): Record<string, unknown> {
  const run = (step: string) => `bash scripts/coa/lifecycle.sh ${step}`;
  return {
    version: '0.2',
    env: {
      shell: 'bash',
      variables: { RP_OPERATION: 'none', RP_RESULT_FILE: '/tmp/rp_result', RP_COA_WORKDIR: '/codebuild/output/coa' },
      'exported-variables': ['RP_RESULT', 'RP_LEFTOVERS'],
    },
    phases: {
      install: {
        'runtime-versions': { java: cfg.executor.toolchain.java, python: cfg.executor.toolchain.python },
        commands: [
          'case "$RP_OPERATION" in up|down) ;; *) echo "RP_OPERATION must be up or down"; exit 2 ;; esac',
          run('install'),
        ],
      },
      pre_build: { commands: [run('prepare')] },
      build: { commands: [run('$RP_OPERATION'), 'export RP_RESULT="$(cat "$RP_RESULT_FILE")"'] },
      post_build: {
        commands: [
          'if [ "$CODEBUILD_BUILD_SUCCEEDING" = 1 ] && [ "$RP_OPERATION" = up ]; then '
            + `${run('seed')}; export RP_RESULT="$(cat "$RP_RESULT_FILE")"; fi`,
          `if [ "$RP_OPERATION" = down ]; then ${run('check')}; export RP_RESULT="$(cat "$RP_RESULT_FILE" 2>/dev/null)"; `
            + 'export RP_LEFTOVERS="$(cat "$RP_RESULT_FILE.leftovers" 2>/dev/null)"; fi',
        ],
      },
    },
  };
}

/**
 * What the executor role may do. CloudFormation work goes through the CDK bootstrap roles (assumed); the rest is
 * exactly the direct API calls of COA v0.3.9's deploy-all.sh / deploy.sh / preflight-deploy.sh / check-*.sh /
 * destroy.sh and its CDK app's synth-time SDK reads, and of scripts/coa/seed.py. Scoped to COA's names where the
 * service allows it. Residual breadth: docs/decisions/0006.
 */
export function coaExecutorStatements(stack: Stack, cfg: CoaControlConfig, refs: { userPoolArn: string }): iam.PolicyStatement[] {
  const { partition: p, region: r, account: a } = stack;
  const sp = `${cfg.coa.prefix}-${cfg.coa.env}`; // coa-dev
  const q = cfg.bootstrap.qualifier; // COA's own bootstrap, never Provenant's (hnb659fds)
  const projectTag = { StringEquals: { 'aws:ResourceTag/Project': cfg.coa.deployContext.SCL_PROJECT_TAG } };
  const glueName = `${sp}-`.toLowerCase().replace(/[^a-z0-9]/g, '') + 'ds_'; // coadevds_ (lib/glue-catalog-name.sh)
  const runtimeName = sp.replace(/-/g, '_'); // coa_dev (AgentCore runtime names)
  const st = (sid: string, actions: string[], resources: string[], conditions?: Record<string, unknown>) =>
    new iam.PolicyStatement({ sid, actions, resources, conditions });
  return [
    // CDK: deploy/destroy (deploy role -> CloudFormation with the bounded cfn-exec role), asset publishing, lookups
    st('CdkBootstrapRoles', ['sts:AssumeRole'], ['deploy', 'file-publishing', 'image-publishing', 'lookup']
      .map((role) => `arn:${p}:iam::${a}:role/cdk-${q}-${role}-role-${a}-${r}`)),
    // the CLI checks the bootstrap version of COA's qualifier
    st('ReadCoaBootstrapVersion', ['ssm:GetParameter'], [`arn:${p}:ssm:${r}:${a}:parameter/cdk-bootstrap/${q}/version`]),
    // destroy.sh deletes the connector stacks (a separate CDK app) with `aws cloudformation delete-stack`;
    // CloudFormation then uses the stack's own role (the cfn-exec role), which the caller must be able to pass
    st('DeleteCoaStacksDirectly', ['cloudformation:DeleteStack'], [`arn:${p}:cloudformation:${r}:${a}:stack/${sp}-*/*`]),
    st('PassCfnExecRoleToCloudFormation', ['iam:PassRole'], [`arn:${p}:iam::${a}:role/cdk-${q}-cfn-exec-role-${a}-${r}`],
      { StringEquals: { 'iam:PassedToService': 'cloudformation.amazonaws.com' } }),
    // deploy-all.sh / deploy.sh / preflight / check-teardown-pending / check-vpc-limit / destroy.sh / seed.py /
    // lifecycle.sh settle + check; preflight checks that CDKToolkit exists, the CLI reads COA's own toolkit stack
    st('ReadCoaStacks', ['cloudformation:DescribeStacks', 'cloudformation:ListStackResources',
      'cloudformation:DescribeStackEvents'], [
      `arn:${p}:cloudformation:${r}:${a}:stack/${cfg.coa.prefix}-*/*`, // coa-dev-*, coa-integ-test-databases
      `arn:${p}:cloudformation:${r}:${a}:stack/CDKToolkit/*`,
      `arn:${p}:cloudformation:${r}:${a}:stack/${cfg.bootstrap.toolkitStackName}/*`]),
    st('ListStacks', ['cloudformation:ListStacks'], ['*']), // no resource-level permission
    // COA's CDK app reads /coa/config at synth (initialAdminEmail); the connector and seed read /coa/*;
    // lifecycle.sh reads the optional SMUS admin ARNs parameter
    st('ReadCoaParameters', ['ssm:GetParameter'], [`arn:${p}:ssm:${r}:${a}:parameter/${cfg.coa.prefix}/*`,
      `arn:${p}:ssm:${r}:${a}:parameter${cfg.coa.smusAdminArnsParameter}`]),
    // preflight checks and synth-time lookups (no resource-level permission on any of these)
    st('PreflightReads', ['lambda:GetAccountSettings', 'bedrock:ListInferenceProfiles', 'bedrock:ListFoundationModels',
      'servicequotas:GetServiceQuota', 'ec2:DescribeAvailabilityZones', 'ec2:DescribeVpcEndpointServices',
      'ec2:DescribeVpcs', 'ec2:DescribeInternetGateways', 'ec2:DescribeAddresses', 'ec2:DescribeSubnets',
      'ec2:DescribeSecurityGroups', 'ec2:DescribeNetworkInterfaces', 'ec2:DescribeVpcEndpoints',
      'ram:ListResources', 'glue:GetCatalogs', 'servicediscovery:ListNamespaces', 'servicediscovery:ListServices',
      'servicediscovery:ListInstances', 'servicediscovery:GetService', 'lakeformation:ListResources'], ['*']),
    // check-smus-admin-principal.sh falls back to the role Admin; destroy.sh resolves COA's roles
    st('GetRoles', ['iam:GetRole'], [`arn:${p}:iam::${a}:role/Admin`, `arn:${p}:iam::${a}:role/${sp}-*`]),
    // public ECR login for COA's image builds (preflight-deploy.sh)
    st('PublicEcrLogin', ['ecr-public:GetAuthorizationToken', 'sts:GetServiceBearerToken'], ['*']),
    // destroy.sh: AgentCore runtimes before their stacks (they hold ENIs)
    st('DeleteCoaRuntimes', ['bedrock-agentcore:GetAgentRuntime', 'bedrock-agentcore:DeleteAgentRuntime'],
      [`arn:${p}:bedrock-agentcore:${r}:${a}:runtime/${runtimeName}_*`]),
    // destroy.sh: VKG's ECS services (desired 0, then delete) before the stack
    st('DrainVkgServices', ['ecs:ListServices', 'ecs:DescribeServices', 'ecs:UpdateService', 'ecs:DeleteService'], [
      `arn:${p}:ecs:${r}:${a}:cluster/${sp}-*`, `arn:${p}:ecs:${r}:${a}:service/${sp}-*/*`]),
    // destroy.sh: Cloud Map services VKG registered (ids are random: no name to scope by)
    st('CleanCloudMap', ['servicediscovery:DeregisterInstance', 'servicediscovery:DeleteService'],
      [`arn:${p}:servicediscovery:${r}:${a}:service/*`]),
    // destroy.sh: force-delete the DataZone domain (cascades to RETAINed children CloudFormation cannot clear). It
    // reads the id from COA's SSM and checks the name first; IAM also requires COA's Project tag (SCL_PROJECT_TAG), so
    // no other DataZone / SageMaker Unified Studio domain in the account can be deleted
    st('ReadDataZoneDomain', ['datazone:GetDomain'], [`arn:${p}:datazone:${r}:${a}:domain/*`]),
    st('DeleteCoaDataZoneDomain', ['datazone:DeleteDomain'], [`arn:${p}:datazone:${r}:${a}:domain/*`], projectTag),
    // destroy.sh: federated Glue connections/catalogs COA's sources service created (coadevds_*)
    st('DeleteFederatedGlue', ['glue:GetConnections', 'glue:GetConnection', 'glue:DeleteConnection',
      'glue:GetCatalog', 'glue:DeleteCatalog'], [`arn:${p}:glue:${r}:${a}:catalog`,
      `arn:${p}:glue:${r}:${a}:catalog/${glueName}*`, `arn:${p}:glue:${r}:${a}:connection/${glueName}*`]),
    // destroy.sh: Lake Formation registrations and the DROP grant it gives itself (no resource-level permission)
    st('LakeFormationCleanup', ['lakeformation:DeregisterResource', 'lakeformation:GrantPermissions',
      'lakeformation:RevokePermissions'], ['*']),
    // destroy.sh: what AgentCore and Glue leave behind in COA's VPC
    st('DeleteCoaSecurityGroupsAndEndpoints', ['ec2:DeleteSecurityGroup', 'ec2:DeleteVpcEndpoints', 'ec2:CreateTags'],
      [`arn:${p}:ec2:${r}:${a}:security-group/*`, `arn:${p}:ec2:${r}:${a}:vpc-endpoint/*`],
      { StringLike: { 'aws:ResourceTag/aws:cloudformation:stack-name': `${sp}-*` } }),
    // destroy.sh (SCL_DESTROY_DELETE_GUARDDUTY_ENDPOINT=1): GuardDuty Runtime Monitoring's endpoint and security group
    // in COA's VPC, after the script checked that nothing else uses that VPC; GuardDuty tags them, CloudFormation not
    st('DeleteGuardDutyLeftovers', ['ec2:DeleteVpcEndpoints', 'ec2:DeleteSecurityGroup'],
      [`arn:${p}:ec2:${r}:${a}:security-group/*`, `arn:${p}:ec2:${r}:${a}:vpc-endpoint/*`],
      { StringEquals: { 'aws:ResourceTag/GuardDutyManaged': 'true' } }),
    st('DeleteDetachedEnis', ['ec2:DeleteNetworkInterface'], [`arn:${p}:ec2:${r}:${a}:network-interface/*`]),
    st('CheckCoaFunctions', ['lambda:GetFunction'], [`arn:${p}:lambda:${r}:${a}:function:${sp}-*`]),
    // destroy.sh empties a refilled access-log bucket before its stack goes
    st('EmptyCoaBuckets', ['s3:ListBucket', 's3:ListBucketVersions', 's3:DeleteObject', 's3:DeleteObjectVersion'], [
      `arn:${p}:s3:::${sp}-*`, `arn:${p}:s3:::${sp}-*/*`]),
    // seed.py: its own coa- bucket (COA's serve role reads coa-* buckets) with the fictitious CSV
    st('SeedBucket', ['s3:CreateBucket', 's3:PutBucketPublicAccessBlock', 's3:PutEncryptionConfiguration',
      's3:PutBucketTagging', 's3:ListBucket', 's3:PutObject'], [
      `arn:${p}:s3:::${cfg.seed.bucketPrefix}-${a}-${r}`, `arn:${p}:s3:::${cfg.seed.bucketPrefix}-${a}-${r}/*`]),
    // seed.py: the Glue database and table the governed metric's SQL names
    st('SeedGlueTable', ['glue:GetDatabase', 'glue:CreateDatabase', 'glue:CreateTable', 'glue:UpdateTable',
      'glue:GetTable', 'glue:TagResource'], [`arn:${p}:glue:${r}:${a}:catalog`,
      `arn:${p}:glue:${r}:${a}:database/${cfg.seed.glueDatabase}`,
      `arn:${p}:glue:${r}:${a}:table/${cfg.seed.glueDatabase}/*`]),
    // seed.py: its own COA principal in COA's user pool (the pool id changes on every deploy, so the pool is matched
    // by COA's Project tag), and never Provenant's pool
    st('SeedCoaUser', ['cognito-idp:AdminCreateUser', 'cognito-idp:AdminEnableUser', 'cognito-idp:AdminDisableUser',
      'cognito-idp:AdminSetUserPassword', 'cognito-idp:AdminAddUserToGroup'], [`arn:${p}:cognito-idp:${r}:${a}:userpool/*`],
    projectTag),
    new iam.PolicyStatement({ sid: 'NeverProvenantUserPool', effect: iam.Effect.DENY, actions: ['cognito-idp:*'],
      resources: [refs.userPoolArn] }),
  ];
}

export class CoaControlStack extends Stack {
  readonly table: dynamodb.Table;
  readonly project: codebuild.Project;
  readonly cfg: CoaControlConfig;

  constructor(scope: Construct, id: string, props: CoaControlStackProps) {
    super(scope, id, props);
    const { base } = props;
    const repoRoot = props.repoRoot ?? REPO_ROOT;
    const cfg = loadCoaControlConfig(repoRoot);
    this.cfg = cfg;

    // --- state row ------------------------------------------------------------------------------------------
    this.table = new dynamodb.Table(this, 'CoaEnv', {
      tableName: cfg.table.name,
      partitionKey: { name: cfg.table.pk, type: dynamodb.AttributeType.STRING },
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
      encryptionKey: base.key,
      pointInTimeRecoverySpecification: { pointInTimeRecoveryEnabled: true },
      removalPolicy: RemovalPolicy.DESTROY,
    });

    // --- admins -----------------------------------------------------------------------------------------------
    new cognito.CfnUserPoolGroup(this, 'AdminGroup', {
      userPoolId: props.userPoolId,
      groupName: cfg.adminGroup,
      description: 'Provenant environment admins: may spin COA up and down (Settings, COA environment)',
    });

    // --- executor ---------------------------------------------------------------------------------------------
    const bundle = new s3assets.Asset(this, 'LifecycleBundle', { path: stageBundle(repoRoot) });
    const logGroup = new logs.LogGroup(this, 'LifecycleLogs', {
      logGroupName: `/aws/codebuild/${cfg.executor.project}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const role = new iam.Role(this, 'LifecycleRole', {
      roleName: `${cfg.executor.project}-role`,
      assumedBy: new iam.ServicePrincipal('codebuild.amazonaws.com', {
        conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
      }),
      description: 'rp-coa-lifecycle: CDK bootstrap roles + the direct calls of COA deploy/destroy and the seed',
    });
    const userPoolArn = `arn:${this.partition}:cognito-idp:${this.region}:${this.account}:userpool/${props.userPoolId}`;
    for (const s of coaExecutorStatements(this, cfg, { userPoolArn })) role.addToPolicy(s);

    // COA's bootstrap deploy role may change COA's stacks only: through any other stack it could use that stack's
    // own service role (CloudFormation lets anyone who may update a stack use its role). Attaching by name makes this
    // stack fail until scripts/coa/bootstrap.sh has run, so the executor never exists without the guard.
    new iam.ManagedPolicy(this, 'DeployGuard', {
      managedPolicyName: cfg.bootstrap.deployGuardPolicy,
      description: `COA's CDK bootstrap (${cfg.bootstrap.qualifier}) deploy role: no change to Provenant or toolkit stacks`,
      roles: [iam.Role.fromRoleName(this, 'CoaDeployRole', `cdk-${cfg.bootstrap.qualifier}-deploy-role-${this.account}-${this.region}`)],
      statements: [new iam.PolicyStatement({ sid: 'DenyProvenantStacks', effect: iam.Effect.DENY, actions: CFN_WRITES,
        resources: protectedStacks(this, cfg) })],
    });
    this.project = new codebuild.Project(this, 'Lifecycle', {
      projectName: cfg.executor.project,
      description: `COA ${cfg.coa.tag} spin-up (make deploy-dev + seed) and tear-down (destroy) for Provenant`,
      role,
      source: codebuild.Source.s3({ bucket: bundle.bucket, path: bundle.s3ObjectKey }),
      environment: {
        buildImage: codebuild.LinuxBuildImage.fromCodeBuildImageId(cfg.executor.image),
        computeType: cfg.executor.computeType as codebuild.ComputeType,
        privileged: true, // COA's Docker image assets (and QEMU for its linux/arm64 images)
      },
      timeout: Duration.minutes(cfg.executor.timeoutMinutes),
      concurrentBuildLimit: cfg.executor.concurrency,
      buildSpec: codebuild.BuildSpec.fromObject(lifecycleBuildSpec(cfg)),
      logging: { cloudWatch: { logGroup } },
    });
    bundle.grantRead(role);

    // --- control function -----------------------------------------------------------------------------------
    const fns = buildFunctions(this, 'coaControl', base, {
      coaEnvTable: this.table.tableName,
      coaProject: cfg.executor.project,
    });
    const fn = fns.get(cfg.function);
    this.table.grantReadWriteData(fn);
    fn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'RunTheExecutor',
      actions: ['codebuild:StartBuild', 'codebuild:StopBuild', 'codebuild:BatchGetBuilds', 'codebuild:ListBuildsForProject'],
      resources: [this.project.projectArn],
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReadCoaActivity', // API Gateway Count and AgentCore Invocations (no resource-level permission)
      actions: ['cloudwatch:GetMetricData', 'cloudwatch:ListMetrics'],
      resources: ['*'],
    }));
    fn.addToRolePolicy(new iam.PolicyStatement({
      sid: 'ReadCoaOutputs', // console URL, REST endpoint, and whether COA's network stack exists
      actions: ['cloudformation:DescribeStacks'],
      resources: [`arn:${this.partition}:cloudformation:${this.region}:${this.account}:stack/${cfg.coa.prefix}-${cfg.coa.env}-*/*`],
    }));

    // --- triggers ---------------------------------------------------------------------------------------------
    new events.Rule(this, 'LifecycleEvents', {
      ruleName: 'rp-coa-lifecycle-events',
      description: 'rp-coa-lifecycle state and phase changes -> rp-coa-control (mirrors the build into rp-coa-env)',
      eventPattern: {
        source: ['aws.codebuild'],
        detailType: ['CodeBuild Build State Change', 'CodeBuild Build Phase Change'],
        detail: { 'project-name': [cfg.executor.project] },
      },
      targets: [new targets.LambdaFunction(fn, { retryAttempts: 4 })],
    });
    new events.Rule(this, 'IdleWatcher', {
      ruleName: 'rp-coa-idle-watcher',
      description: 'COA idle watcher: reconcile, retry a destroy waiting for ENIs, tear down when idle',
      schedule: events.Schedule.rate(Duration.minutes(cfg.idle.tickMinutes)),
      targets: [new targets.LambdaFunction(fn, { event: events.RuleTargetInput.fromObject({ op: 'tick' }),
        retryAttempts: 0 })],
    });

    // Failed (or an activity read failing longer than the idle threshold): an alarm with no action of its own, for the
    // console and any existing notification path; no new e-mail or SNS subscription
    new cloudwatch.Alarm(this, 'NeedsAttention', {
      alarmName: 'rp-coa-env-needs-attention',
      alarmDescription: 'COA environment needs an admin: a failed spin-up or tear-down (possibly still billing), or '
        + 'activity unreadable past the idle threshold. Settings, COA environment.',
      metric: new cloudwatch.Metric({ namespace: 'Provenant/CoaEnv', metricName: 'NeedsAttention',
        dimensionsMap: { Environment: `${cfg.coa.prefix}-${cfg.coa.env}` }, statistic: 'Maximum',
        period: Duration.minutes(cfg.idle.tickMinutes) }),
      threshold: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      evaluationPeriods: 1,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new CfnOutput(this, 'LifecycleProject', { value: this.project.projectName });
    new CfnOutput(this, 'ControlFunction', { value: fn.functionName });
    new CfnOutput(this, 'AdminGroupName', { value: cfg.adminGroup });
  }
}

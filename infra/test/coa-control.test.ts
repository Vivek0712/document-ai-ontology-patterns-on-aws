/**
 * rp-coa-control (context rp:coaControl): absent by default; when on, the executor, its least-privilege role, the
 * idle schedule, the event mirror, the admin group and rp-api's one new grant. Placeholder account, no credentials.
 */
import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ROUTES } from '../lib/api-routes';
import { ReferencePipelineStacks, buildApp } from '../lib/app';
import { loadBuildAssets } from '../lib/build-assets';
import { BUNDLE_FILES, lifecycleBuildSpec } from '../lib/coa-control-stack';
import { FUNCTIONS, specHandler } from '../lib/functions';
import { REPO_ROOT, loadCoaControlConfig } from '../lib/names';

function stubBuild(): string {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'rp-build-'));
  fs.mkdirSync(path.join(root, 'lambda', 'code', 'lambdas'), { recursive: true });
  fs.mkdirSync(path.join(root, 'lambda', 'code', 'evidence_core'), { recursive: true });
  fs.writeFileSync(path.join(root, 'lambda', 'code', 'lambdas', '__init__.py'), '');
  fs.mkdirSync(path.join(root, 'layers', 'core', 'python'), { recursive: true });
  fs.writeFileSync(path.join(root, 'layers', 'core', 'python', 'stub.py'), '');
  fs.writeFileSync(path.join(root, 'lambda', 'manifest.json'), JSON.stringify({
    codeSha: 'a'.repeat(64), codePath: 'lambda/code',
    layers: { core: { requirementsSha: 'b'.repeat(64), path: 'layers/core' } },
  }));
  return root;
}

function synth(context: Record<string, unknown> = {}): ReferencePipelineStacks {
  const app = new App({ context: { 'rp:account': '000000000000', 'rp:region': 'us-east-1', ...context } });
  return buildApp(app, { assets: loadBuildAssets(stubBuild()), uiDistDir: path.join(os.tmpdir(), 'no-ui') });
}

const cfg = loadCoaControlConfig(REPO_ROOT);
const off = synth();
const on = synth({ 'rp:coaControl': 'true' });
const t = Template.fromStack(on.coaControl!);
const g = Template.fromStack(on.coaGuard!);
const agentOn = Template.fromStack(on.agent);
const agentOff = Template.fromStack(off.agent);

type Stmt = { Sid?: string; Action: string | string[]; Resource: unknown; Condition?: unknown };
function statements(tpl: Template, roleLogicalPrefix: string): Stmt[] {
  // a large role overflows into AWS::IAM::ManagedPolicy (`<role>OverflowPolicy…`)
  const policies = { ...tpl.findResources('AWS::IAM::Policy'), ...tpl.findResources('AWS::IAM::ManagedPolicy') };
  return Object.entries(policies)
    .filter(([id]) => id.startsWith(roleLogicalPrefix))
    .flatMap(([, p]) => (p as any).Properties.PolicyDocument.Statement as Stmt[]);
}
const actions = (s: Stmt) => (Array.isArray(s.Action) ? s.Action : [s.Action]);
const resources = (s: Stmt) => JSON.stringify(s.Resource);

describe('off by default', () => {
  test('no rp-coa-control stack and rp-api says off without the invoke grant', () => {
    expect(off.coaControl).toBeUndefined();
    expect(off.coaGuard).toBeUndefined();
    agentOff.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'rp-api', Environment: { Variables: Match.objectLike({ RP_COA_CONTROL: 'off' }) },
    });
    const invokes = statements(agentOff, 'FnApiServiceRole').filter((s) => actions(s).includes('lambda:InvokeFunction'));
    expect(invokes.some((s) => resources(s).includes('rp-coa-control'))).toBe(false);
    const cdkJson = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'cdk.json'), 'utf8'));
    expect(cdkJson.context['rp:coaControl']).toBe(false);
  });

  test('the routes exist either way (GET answers "not deployed") and are Cognito-authorized', () => {
    const coa = ROUTES.filter((r) => r.path.startsWith('/coa/environment'));
    expect(coa.map((r) => `${r.method} ${r.path}`).sort()).toEqual([
      'GET /coa/environment', 'POST /coa/environment/down', 'POST /coa/environment/keep-alive',
      'POST /coa/environment/up', 'PUT /coa/environment/settings']);
    for (const tpl of [agentOff, agentOn]) {
      tpl.hasResourceProperties('AWS::ApiGateway::Method', { HttpMethod: 'PUT', AuthorizationType: 'COGNITO_USER_POOLS',
        AuthorizationScopes: ['aws.cognito.signin.user.admin'] });
    }
  });
});

describe('on (rp:coaControl=true)', () => {
  test('rp-api may invoke rp-coa-control by name and knows it is on', () => {
    agentOn.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'rp-api', Environment: { Variables: Match.objectLike({ RP_COA_CONTROL: 'on' }) },
    });
    const invokes = statements(agentOn, 'FnApiServiceRole').filter((s) => actions(s).includes('lambda:InvokeFunction'));
    expect(invokes.some((s) => resources(s).includes(':function:rp-coa-control'))).toBe(true);
  });

  test('the control function, its state table and admin group', () => {
    const spec = FUNCTIONS.find((f) => f.dir === 'coa_control')!;
    t.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'rp-coa-control', Handler: specHandler(spec), Runtime: 'python3.12',
      Environment: { Variables: Match.objectLike({ RP_COA_PROJECT: 'rp-coa-lifecycle', RP_CONFIG_SOURCE: 's3' }) },
    });
    t.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'rp-coa-env', KeySchema: [{ AttributeName: 'env', KeyType: 'HASH' }], BillingMode: 'PAY_PER_REQUEST',
      PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
    });
    t.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'rp-admin' });
    expect(cfg.adminGroup).toBe('rp-admin');
  });

  test('CodeBuild executor: privileged, amd64 standard image, 150 min, one build at a time, bundle source', () => {
    t.hasResourceProperties('AWS::CodeBuild::Project', {
      Name: 'rp-coa-lifecycle',
      Environment: Match.objectLike({ Image: 'aws/codebuild/standard:7.0', Type: 'LINUX_CONTAINER',
        PrivilegedMode: true, ComputeType: 'BUILD_GENERAL1_LARGE' }),
      TimeoutInMinutes: 150,
      ConcurrentBuildLimit: 1,
      Source: Match.objectLike({ Type: 'S3' }),
    });
    expect(BUNDLE_FILES).toContain('contracts/generated/coa_control.json');
  });

  test('buildspec: one lifecycle step per phase, RP_RESULT exported, seed only after a successful spin-up', () => {
    const spec = lifecycleBuildSpec(cfg) as any;
    expect(spec.env['exported-variables']).toEqual(['RP_RESULT', 'RP_LEFTOVERS']);
    expect(spec.phases.post_build.commands[1]).toContain('lifecycle.sh check');
    expect(spec.phases.post_build.commands[1]).toContain('export RP_LEFTOVERS=');
    expect(spec.phases.install['runtime-versions']).toEqual({ java: 'corretto21', python: '3.12' });
    expect(spec.phases.pre_build.commands).toEqual(['bash scripts/coa/lifecycle.sh prepare']);
    expect(spec.phases.build.commands[0]).toBe('bash scripts/coa/lifecycle.sh $RP_OPERATION');
    expect(spec.phases.post_build.commands[0]).toContain('CODEBUILD_BUILD_SUCCEEDING" = 1');
    expect(spec.phases.post_build.commands[0]).toContain('lifecycle.sh seed');
  });

  test('idle watcher every 5 minutes and the build-event mirror target the control function', () => {
    t.hasResourceProperties('AWS::Events::Rule', {
      Name: 'rp-coa-idle-watcher', ScheduleExpression: 'rate(5 minutes)',
      Targets: [Match.objectLike({ Input: JSON.stringify({ op: 'tick' }) })],
    });
    t.hasResourceProperties('AWS::Events::Rule', {
      Name: 'rp-coa-lifecycle-events',
      EventPattern: { source: ['aws.codebuild'], 'detail-type': ['CodeBuild Build State Change', 'CodeBuild Build Phase Change'],
        detail: { 'project-name': ['rp-coa-lifecycle'] } },
    });
    expect(cfg.idle.tickMinutes).toBe(5);
  });

  test('control function IAM: only its project, metrics reads, coa-dev-* stacks, its table', () => {
    const s = statements(t, 'FnCoaControl');
    const cb = s.find((x) => x.Sid === 'RunTheExecutor')!;
    expect(actions(cb).sort()).toEqual(['codebuild:BatchGetBuilds', 'codebuild:ListBuildsForProject', 'codebuild:StartBuild',
      'codebuild:StopBuild']);
    expect(resources(cb)).toContain('Lifecycle');
    expect(resources(s.find((x) => x.Sid === 'ReadCoaOutputs')!)).toContain(':stack/coa-dev-*/*');
    expect(s.some((x) => actions(x).some((a) => a.startsWith('iam:') || a === '*'))).toBe(false);
  });

  test('executor IAM: COA\'s own bootstrap roles for CloudFormation, direct calls scoped, nothing wildcard-all', () => {
    const s = statements(t, 'LifecycleRole');
    const bySid = Object.fromEntries(s.filter((x) => x.Sid).map((x) => [x.Sid!, x]));
    const assume = bySid.CdkBootstrapRoles;
    expect(actions(assume)).toEqual(['sts:AssumeRole']);
    for (const role of ['deploy', 'file-publishing', 'image-publishing', 'lookup']) {
      expect(resources(assume)).toContain(`cdk-${cfg.bootstrap.qualifier}-${role}-role-`);
    }
    expect(resources(assume)).not.toContain('cfn-exec');
    // never Provenant's bootstrap roles (its deploy role could change rp-* stacks); its asset bucket only to read the
    // build's own source bundle
    for (const x of s.filter((y) => resources(y).includes('hnb659fds'))) {
      expect(actions(x).every((act) => /^s3:(GetObject\*|GetBucket\*|List\*)$/.test(act))).toBe(true);
    }
    expect(resources(bySid.PassCfnExecRoleToCloudFormation)).toContain(`cdk-${cfg.bootstrap.qualifier}-cfn-exec-role-`);
    expect(bySid.PassCfnExecRoleToCloudFormation.Condition).toEqual({
      StringEquals: { 'iam:PassedToService': 'cloudformation.amazonaws.com' } });
    expect(resources(bySid.DeleteCoaStacksDirectly)).toContain(':stack/coa-dev-*/*');
    expect(resources(bySid.DeleteCoaRuntimes)).toContain(':runtime/coa_dev_*');
    expect(resources(bySid.DeleteFederatedGlue)).toContain('connection/coadevds_*');
    expect(bySid.DeleteCoaSecurityGroupsAndEndpoints.Condition).toEqual({
      StringLike: { 'aws:ResourceTag/aws:cloudformation:stack-name': 'coa-dev-*' } });
    expect(resources(bySid.SeedBucket)).toContain('coa-dev-provenant-');
    expect(resources(bySid.ReadCoaParameters)).toContain('parameter/coa/*');
    // no Action "*" anywhere, and no write action on Resource "*" except the documented Lake Formation cleanup
    for (const x of s) {
      expect(actions(x)).not.toContain('*');
      if (x.Resource === '*') {
        const writes = actions(x).filter((a) => !/:(Get|List|Describe|BatchGet)/.test(a) && a !== 'sts:GetServiceBearerToken'
          && a !== 'ecr-public:GetAuthorizationToken');
        expect({ sid: x.Sid, writes }).toEqual({ sid: x.Sid, writes: x.Sid === 'LakeFormationCleanup' ? writes : [] });
      }
    }
    // the Cognito admin calls: user pools tagged by COA's deploy, and never Provenant's own pool
    expect(resources(bySid.SeedCoaUser)).toContain(':userpool/*');
    expect(bySid.SeedCoaUser.Condition).toEqual({ StringEquals: { 'aws:ResourceTag/Project': 'provenant-coa' } });
    expect(cfg.coa.deployContext.SCL_PROJECT_TAG).toBe('provenant-coa');
    const never = bySid.NeverProvenantUserPool as Stmt & { Effect: string };
    expect(never.Effect).toBe('Deny');
    expect(actions(never)).toEqual(['cognito-idp:*']);
    expect(resources(never)).toContain(':userpool/');
    expect(resources(never)).toContain('ExportsOutputRefUsers'); // the rp-users pool, by reference
    // DataZone: read any, delete only COA's (tagged) domain
    expect(actions(bySid.DeleteCoaDataZoneDomain)).toEqual(['datazone:DeleteDomain']);
    expect(bySid.DeleteCoaDataZoneDomain.Condition).toEqual({ StringEquals: { 'aws:ResourceTag/Project': 'provenant-coa' } });
    expect(actions(bySid.ReadDataZoneDomain)).toEqual(['datazone:GetDomain']);
    // GuardDuty's leftovers in COA's VPC: only resources GuardDuty tagged
    expect(bySid.DeleteGuardDutyLeftovers.Condition).toEqual({ StringEquals: { 'aws:ResourceTag/GuardDutyManaged': 'true' } });
    expect(actions(bySid.DeleteGuardDutyLeftovers).sort()).toEqual(['ec2:DeleteSecurityGroup', 'ec2:DeleteVpcEndpoints']);
  });

  test('COA\'s bootstrap deploy role is kept off rp-* and toolkit stacks', () => {
    const name = `cdk-${cfg.bootstrap.qualifier}-deploy-role-000000000000-us-east-1`;
    t.hasResourceProperties('AWS::IAM::ManagedPolicy', {
      ManagedPolicyName: cfg.bootstrap.deployGuardPolicy,
      Roles: [name],
      PolicyDocument: { Statement: [Match.objectLike({ Effect: 'Deny', Sid: 'DenyProvenantStacks',
        Action: Match.arrayWith(['cloudformation:CreateChangeSet', 'cloudformation:ExecuteChangeSet', 'cloudformation:DeleteStack']) })] },
    });
    const guard = Object.values(t.findResources('AWS::IAM::ManagedPolicy'))
      .find((x: any) => x.Properties.ManagedPolicyName === cfg.bootstrap.deployGuardPolicy) as any;
    const res = JSON.stringify(guard.Properties.PolicyDocument.Statement[0].Resource);
    for (const stack of [':stack/rp-*/*', ':stack/CDKToolkit/*', `:stack/${cfg.bootstrap.toolkitStackName}/*`]) {
      expect(res).toContain(stack);
    }
    expect(res).not.toContain(':stack/coa-');
  });

  test('rp-coa-guard: the boundary denies Provenant and requires itself on every new role', () => {
    const policy = Object.values(g.findResources('AWS::IAM::ManagedPolicy'))[0] as any;
    expect(policy.Properties.ManagedPolicyName).toBe(cfg.bootstrap.boundaryPolicy);
    const st = policy.Properties.PolicyDocument.Statement as (Stmt & { Effect: string })[];
    const bySid = Object.fromEntries(st.map((x) => [x.Sid!, x]));
    expect(bySid.AllowWithinTheBoundary.Effect).toBe('Allow');
    for (const x of st.filter((y) => y.Sid !== 'AllowWithinTheBoundary')) expect(x.Effect).toBe('Deny');
    expect(JSON.stringify(bySid.DenyRolesOrUsersWithoutThisBoundary.Condition)).toContain(`policy/${cfg.bootstrap.boundaryPolicy}`);
    expect(actions(bySid.DenyRolesOrUsersWithoutThisBoundary)).toContain('iam:CreateRole');
    expect(resources(bySid.DenyProvenantStacks)).toContain(':stack/rp-*/*');
    expect(resources(bySid.DenyProvenantStacks)).toContain(':stack/CDKToolkit/*');
    expect(actions(bySid.DenyProvenantUserPool)).toEqual(['cognito-idp:*']);
    expect(bySid.DenyProvenantTagged.Condition).toEqual({ StringEquals: { 'aws:ResourceTag/project': 'regionproof' } });
    expect(resources(bySid.DenyAssumingProvenantOrBootstrapRoles)).toContain(':role/cdk-*');
    expect(resources(bySid.DenyDefaultBootstrapAssets)).toContain('cdk-hnb659fds-');
    expect(resources(bySid.DenyGuardPolicyEdits)).toContain(`policy/${cfg.bootstrap.deployGuardPolicy}`);
    // within the 6,144-character managed-policy limit (whitespace excluded)
    expect(JSON.stringify(policy.Properties.PolicyDocument).replace(/\s/g, '').length).toBeLessThan(6144);
    // the guard deploys before the control stack (and before scripts/coa/bootstrap.sh, which needs it)
    expect(on.coaControl!.dependencies).toContain(on.coaGuard!);
  });

  test('needs-attention alarm on Provenant/CoaEnv, with no action of its own', () => {
    t.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'rp-coa-env-needs-attention', Namespace: 'Provenant/CoaEnv', MetricName: 'NeedsAttention',
      Dimensions: [{ Name: 'Environment', Value: 'coa-dev' }], Threshold: 1, TreatMissingData: 'notBreaching',
    });
    const alarm = Object.values(t.findResources('AWS::CloudWatch::Alarm'))[0] as any;
    expect(alarm.Properties.AlarmActions).toBeUndefined();
    expect(t.findResources('AWS::SNS::Subscription')).toEqual({});
  });

  test('only rp-api may invoke rp-coa-control in this app', () => {
    const invokers: string[] = [];
    for (const stack of [on.base, on.knowledge, on.ingest, on.agent, on.web, on.coaGuard!, on.coaControl!]) {
      const tpl = Template.fromStack(stack);
      const policies = { ...tpl.findResources('AWS::IAM::Policy'), ...tpl.findResources('AWS::IAM::ManagedPolicy') };
      for (const [id, pol] of Object.entries(policies)) {
        for (const st of (pol as any).Properties.PolicyDocument.Statement as (Stmt & { Effect?: string })[]) {
          const acts = actions(st);
          if (st.Effect !== 'Deny' && (acts.includes('lambda:InvokeFunction') || acts.includes('lambda:*') || acts.includes('*'))
            && resources(st).includes('rp-coa-control')) invokers.push(`${stack.stackName}/${id}`);
        }
      }
    }
    expect(invokers.length).toBe(1);
    expect(invokers[0]).toMatch(/^rp-agent\/FnApiServiceRole/);
  });

  test('no account id, e-mail or profile in the template beyond the placeholder', () => {
    const body = JSON.stringify(t.toJSON());
    expect(body).not.toMatch(/@(?!aws)[a-z0-9-]+\.(com|net|org)/i);
    expect(body.match(/\b\d{12}\b/g)?.every((id) => id === '000000000000') ?? true).toBe(true);
  });
});

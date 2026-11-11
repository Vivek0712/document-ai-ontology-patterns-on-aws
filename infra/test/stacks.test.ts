import * as fs from 'fs';
import * as os from 'os';
import * as path from 'path';
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { ROUTES } from '../lib/api-routes';
import { ReferencePipelineStacks, buildApp } from '../lib/app';
import { loadBuildAssets } from '../lib/build-assets';
import { FUNCTIONS, specHandler, specName } from '../lib/functions';
import { REPO_ROOT, loadTableSpecs } from '../lib/names';

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

function synth(context: Record<string, unknown> = {}): { stacks: ReferencePipelineStacks; t: Record<string, Template> } {
  const app = new App({
    context: { 'rp:account': '000000000000', 'rp:region': 'us-east-1', 'rp:devOrigins': ['http://localhost:5173'],
      ...context },
  });
  const stacks = buildApp(app, { assets: loadBuildAssets(stubBuild()), uiDistDir: path.join(os.tmpdir(), 'no-ui') });
  const t = Object.fromEntries(Object.entries(stacks).filter(([, s]) => s).map(([k, s]) => [k, Template.fromStack(s)]));
  return { stacks, t };
}

const { t } = synth();

describe('environment', () => {
  test('availability zones follow the region when not given (no account or AZ in cdk.json)', () => {
    expect(synth({ 'rp:region': 'eu-west-1' }).stacks.base.availabilityZones).toEqual(['eu-west-1a', 'eu-west-1b']);
    expect(synth().stacks.base.availabilityZones).toEqual(['us-east-1a', 'us-east-1b']);
    expect(synth({ 'rp:availabilityZones': 'eu-west-1b,eu-west-1c' }).stacks.base.availabilityZones).toEqual(['eu-west-1b', 'eu-west-1c']);
    const cdkJson = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'cdk.json'), 'utf8'));
    expect(cdkJson.context['rp:account']).toBeUndefined();
    expect(cdkJson.context['rp:availabilityZones']).toBeUndefined();
  });
});

describe('rp-base', () => {
  test('tables follow contracts/generated/tables.json', () => {
    const specs = loadTableSpecs(REPO_ROOT);
    for (const spec of Object.values(specs)) {
      t.base.hasResourceProperties('AWS::DynamoDB::Table', {
        TableName: spec.name,
        BillingMode: 'PAY_PER_REQUEST',
        KeySchema: spec.sk
          ? [{ AttributeName: spec.pk, KeyType: 'HASH' }, { AttributeName: spec.sk, KeyType: 'RANGE' }]
          : [{ AttributeName: spec.pk, KeyType: 'HASH' }],
        PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
        ...(spec.stream ? { StreamSpecification: { StreamViewType: 'NEW_IMAGE' } } : {}),
        ...(spec.ttl ? { TimeToLiveSpecification: { AttributeName: spec.ttl, Enabled: true } } : {}),
        ...(spec.gsis.length
          ? { GlobalSecondaryIndexes: Match.arrayWith(spec.gsis.map(([name]) => Match.objectLike({ IndexName: name }))) }
          : {}),
      });
    }
  });

  test('data bucket is versioned, KMS-encrypted, EventBridge-enabled', () => {
    t.base.hasResourceProperties('AWS::S3::Bucket', {
      VersioningConfiguration: { Status: 'Enabled' },
      BucketEncryption: { ServerSideEncryptionConfiguration: [Match.objectLike({
        ServerSideEncryptionByDefault: Match.objectLike({ SSEAlgorithm: 'aws:kms' }) })] },
    });
    // CDK applies bucket notifications through its notifications custom resource
    t.base.hasResourceProperties('Custom::S3BucketNotifications', {
      NotificationConfiguration: Match.objectLike({ EventBridgeConfiguration: {} }),
    });
  });

  test('VPC has no NAT, gateway endpoints for S3 and DynamoDB, one states interface endpoint', () => {
    t.base.resourceCountIs('AWS::EC2::NatGateway', 0);
    t.base.resourceCountIs('AWS::EC2::VPCEndpoint', 3);
    t.base.hasResourceProperties('AWS::EC2::VPCEndpoint', { VpcEndpointType: 'Interface',
      ServiceName: 'com.amazonaws.us-east-1.states' });
  });
});

describe('functions', () => {
  test('every spec is deployed with the contract name and handler', () => {
    const all = [t.knowledge, t.ingest, t.agent];
    for (const spec of FUNCTIONS.filter((f) => f.stack !== 'coaControl')) { // opt-in stack: coa-control.test.ts
      const found = all.some((tpl) => Object.keys(tpl.findResources('AWS::Lambda::Function', {
        Properties: { FunctionName: specName(spec), Handler: specHandler(spec), Runtime: 'python3.12' },
      })).length === 1);
      expect({ fn: specName(spec), found }).toEqual({ fn: specName(spec), found: true });
    }
  });

  test('VPC functions run in private isolated subnets', () => {
    for (const name of ['rp-docling-call', 'rp-graph-write']) {
      t.ingest.hasResourceProperties('AWS::Lambda::Function', {
        FunctionName: name, VpcConfig: Match.objectLike({ SecurityGroupIds: Match.anyValue() }),
      });
    }
  });
});

describe('rp-ingest', () => {
  test('one state machine named rp-ingest, Standard', () => {
    t.ingest.hasResourceProperties('AWS::StepFunctions::StateMachine', {
      StateMachineName: 'rp-ingest', StateMachineType: 'STANDARD',
    });
  });

  test('Bronze rule fans out to both pipeline versions', () => {
    const rules = t.ingest.findResources('AWS::Events::Rule', { Properties: { Name: 'rp-bronze-created' } });
    const rule = Object.values(rules)[0] as any;
    expect(rule.Properties.EventPattern.detail.object.key).toEqual([{ prefix: 'bronze/' }]);
    const templates = rule.Properties.Targets.map((x: any) => x.InputTransformer.InputTemplate);
    expect(templates.some((s: string) => s.includes('"pipelineVersion":"1.0"'))).toBe(true);
    expect(templates.some((s: string) => s.includes('"pipelineVersion":"2.3.1"'))).toBe(true);
  });

  test('docling: upstream image, 4 vCPU / 16 GB, desired 0, Cloud Map, exactly one task (0..1)', () => {
    t.ingest.hasResourceProperties('AWS::ECS::TaskDefinition', {
      Cpu: '4096', Memory: '16384',
      ContainerDefinitions: [Match.objectLike({
        Image: Match.stringLikeRegexp('^quay.io/docling-project/docling-serve-cpu'),
        PortMappings: [Match.objectLike({ ContainerPort: 5001 })],
        Environment: Match.arrayWith([
          { Name: 'DOCLING_SERVE_ENG_KIND', Value: 'local' },
          { Name: 'DOCLING_SERVE_ENG_LOC_NUM_WORKERS', Value: '2' },
        ]),
      })],
    });
    t.ingest.hasResourceProperties('AWS::ECS::Service', {
      ServiceName: 'rp-docling', DesiredCount: 0,
      DeploymentConfiguration: Match.objectLike({ MaximumPercent: 100, MinimumHealthyPercent: 0 }),
      NetworkConfiguration: { AwsvpcConfiguration: Match.objectLike({ AssignPublicIp: 'ENABLED' }) },
    });
    t.ingest.hasResourceProperties('AWS::ServiceDiscovery::PrivateDnsNamespace', { Name: 'rp.local' });
    t.ingest.hasResourceProperties('AWS::ApplicationAutoScaling::ScalableTarget', { MinCapacity: 0, MaxCapacity: 1 });
    t.ingest.hasResourceProperties('AWS::CloudWatch::Alarm', {
      AlarmName: 'rp-docling-queue-busy',
      Metrics: Match.arrayWith([Match.objectLike({ Expression: 'visible + inflight' })]),
    });
    t.ingest.hasResourceProperties('AWS::SQS::Queue', { QueueName: 'rp-docling',
      RedrivePolicy: Match.objectLike({ maxReceiveCount: 10 }) });
  });

  test('DocStatus stream feeds rp-status-stream for STAGE and LANE records only', () => {
    t.ingest.hasResourceProperties('AWS::Lambda::EventSourceMapping', {
      FilterCriteria: { Filters: [{ Pattern: Match.stringLikeRegexp('STAGE') }] },
    });
  });
});

describe('rp-agent', () => {
  test('every route has a Cognito-authorized method with the access-token scope', () => {
    const methods = t.agent.findResources('AWS::ApiGateway::Method');
    const authed = Object.values(methods).filter((m: any) => m.Properties.HttpMethod !== 'OPTIONS');
    expect(authed.length).toBe(ROUTES.length);
    for (const m of authed as any[]) {
      expect(m.Properties.AuthorizationType).toBe('COGNITO_USER_POOLS');
      expect(m.Properties.AuthorizationScopes).toEqual(['aws.cognito.signin.user.admin']);
    }
  });

  test('stage v1, user pool rp-users with both groups, SRP client without secret', () => {
    t.agent.hasResourceProperties('AWS::ApiGateway::Stage', { StageName: 'v1' });
    t.agent.hasResourceProperties('AWS::Cognito::UserPool', { UserPoolName: 'rp-users' });
    t.agent.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'lending-analyst' });
    t.agent.hasResourceProperties('AWS::Cognito::UserPoolGroup', { GroupName: 'markets-analyst' });
    t.agent.hasResourceProperties('AWS::Cognito::UserPoolClient', {
      ClientName: 'rp-web', GenerateSecret: false, ExplicitAuthFlows: Match.arrayWith(['ALLOW_USER_SRP_AUTH']),
    });
  });

  test('without a runtime ARN /ask goes to rp-api-ask', () => {
    t.agent.hasOutput('AskIntegration', { Value: 'lambda:rp-api-ask' });
  });

  test('with a runtime ARN /ask is a streaming HTTP proxy to AgentCore', () => {
    const arn = 'arn:aws:bedrock-agentcore:us-east-1:000000000000:runtime/rp_agent-abc';
    const { t: t2 } = synth({ 'rp:agentRuntimeArn': arn });
    t2.agent.hasResourceProperties('AWS::ApiGateway::Method', {
      HttpMethod: 'POST',
      Integration: Match.objectLike({
        Type: 'HTTP_PROXY',
        ResponseTransferMode: 'STREAM',
        Uri: Match.stringLikeRegexp('bedrock-agentcore.us-east-1.amazonaws.com/runtimes/arn%3Aaws%3Abedrock-agentcore'),
      }),
    });
  });

  test('AgentCore roles are trusted by bedrock-agentcore', () => {
    for (const name of ['rp-agentcore-gateway', 'rp-agentcore-runtime']) {
      t.agent.hasResourceProperties('AWS::IAM::Role', {
        RoleName: name,
        AssumeRolePolicyDocument: Match.objectLike({ Statement: [Match.objectLike({
          Principal: { Service: 'bedrock-agentcore.amazonaws.com' } })] }),
      });
    }
  });
});

describe('rp-knowledge and rp-web', () => {
  test('AOSS vector collection and Neptune Serverless with IAM auth', () => {
    t.knowledge.hasResourceProperties('AWS::OpenSearchServerless::Collection', { Name: 'rp-units',
      Type: 'VECTORSEARCH', CollectionGroupName: 'rp-units-group' });
    // NextGen (scale to zero) is chosen only through a collection group; without one it is Classic.
    t.knowledge.hasResourceProperties('AWS::OpenSearchServerless::CollectionGroup', {
      Name: 'rp-units-group', Generation: 'NEXTGEN', StandbyReplicas: 'ENABLED',
      CapacityLimits: Match.objectLike({ MinIndexingCapacityInOcu: 0, MinSearchCapacityInOcu: 0 }),
    });
    t.knowledge.hasResourceProperties('AWS::Neptune::DBCluster', {
      DBClusterIdentifier: 'rp-graph', IamAuthEnabled: true,
      ServerlessScalingConfiguration: { MinCapacity: 1, MaxCapacity: 2.5 },
    });
    t.knowledge.hasResourceProperties('AWS::Neptune::DBInstance', { DBInstanceClass: 'db.serverless' });
  });

  test('CloudFront serves the UI and proxies /api/* uncached', () => {
    t.web.hasResourceProperties('AWS::CloudFront::Distribution', {
      DistributionConfig: Match.objectLike({
        DefaultRootObject: 'index.html',
        CacheBehaviors: [Match.objectLike({ PathPattern: '/api/*',
          AllowedMethods: Match.arrayWith(['POST', 'DELETE']) })],
      }),
    });
    t.web.hasResourceProperties('AWS::CloudFront::OriginAccessControl', Match.anyValue());
  });
});

describe('review fixes', () => {
  test('rp-agent owns the API Gateway CloudWatch Logs role (retained) for access logging', () => {
    const accounts = t.agent.findResources('AWS::ApiGateway::Account');
    expect(Object.keys(accounts).length).toBe(1);
    const roles = t.agent.findResources('AWS::IAM::Role');
    const cw = Object.values(roles).find((r: any) => JSON.stringify(r.Properties.AssumeRolePolicyDocument)
      .includes('apigateway.amazonaws.com')) as any;
    expect(cw.DeletionPolicy).toBe('Retain');
  });

  test('rp-api reads only the served tiers (never deploy/*) and may delete but not create indexes', () => {
    const policies = t.agent.findResources('AWS::IAM::Policy');
    const api = Object.entries(policies).find(([id]) => id.startsWith('FnApiServiceRoleDefaultPolicy'));
    const doc = JSON.stringify(api?.[1]);
    expect(doc).toContain('bronze/*');
    expect(doc).toContain('runs/*');
    expect(doc).not.toContain('"/*"]'); // no whole-bucket object grant
    expect(doc).not.toContain('deploy/');
    const aossPolicies = JSON.stringify(t.agent.findResources('AWS::OpenSearchServerless::AccessPolicy'));
    expect(aossPolicies).toContain('delete');
  });
});

describe('integration fixes', () => {
  test('docling demand scaling: ExtractOpen raises the floor, a 5-minute reaper lowers it', () => {
    t.ingest.hasResourceProperties('AWS::SSM::Parameter', { Name: '/rp/docling/floor' });
    t.ingest.hasResourceProperties('AWS::Events::Rule', {
      Name: 'rp-docling-reaper', ScheduleExpression: 'rate(5 minutes)',
      Targets: [Match.objectLike({ Input: JSON.stringify({ op: 'docling_reap' }) })],
    });
    const fns = t.ingest.findResources('AWS::Lambda::Function', { Properties: { FunctionName: 'rp-stage-status' } });
    const env = (Object.values(fns)[0] as any).Properties.Environment.Variables;
    expect(env.RP_DOCLING_FLOOR_PARAM).toBe('/rp/docling/floor');
    expect(env.RP_DOCLING_QUEUE_URL).toBeDefined();
    const policies = JSON.stringify(t.ingest.findResources('AWS::IAM::Policy'));
    expect(policies).toContain('application-autoscaling:RegisterScalableTarget');
    expect(policies).toContain('sqs:GetQueueAttributes');
  });

  test('docling shim reports partial batch failures', () => {
    t.ingest.hasResourceProperties('AWS::Lambda::EventSourceMapping', {
      BatchSize: 1, FunctionResponseTypes: ['ReportBatchItemFailures'],
    });
  });

  test('Ask-path functions publish a live alias and SSM points callers at it', () => {
    for (const name of ['rp-search-units', 'rp-graph-hop', 'rp-get-region', 'rp-get-region-http']) {
      const aliases = t.agent.findResources('AWS::Lambda::Alias', { Properties: { Name: 'live' } });
      expect(Object.keys(aliases).length).toBe(4);
      const params = t.agent.findResources('AWS::SSM::Parameter', { Properties: { Name: `/rp/lambda/${name}/arn` } });
      expect(JSON.stringify(Object.values(params)[0])).toContain('LiveAlias');
    }
  });

  test('pii_gate reads Bronze (glyph boxes for the blackout)', () => {
    const policies = t.ingest.findResources('AWS::IAM::Policy');
    const pii = Object.entries(policies).find(([id]) => id.startsWith('FnPiiGate'));
    expect(JSON.stringify(pii?.[1])).toContain('bronze/*');
  });

  test('operator scripts can sign in with ADMIN_USER_PASSWORD_AUTH; browser uses SRP', () => {
    t.agent.hasResourceProperties('AWS::Cognito::UserPoolClient', {
      ExplicitAuthFlows: Match.arrayWith(['ALLOW_ADMIN_USER_PASSWORD_AUTH', 'ALLOW_USER_SRP_AUTH']),
    });
  });

  test('the AgentCore runtime role can read its code bundle', () => {
    const policies = t.agent.findResources('AWS::IAM::Policy');
    const rt = Object.entries(policies).find(([id]) => id.startsWith('RuntimeRole'));
    expect(JSON.stringify(rt?.[1])).toContain('deploy/agentcore/*');
  });
});

describe('rp-web routing', () => {
  test('API errors are not rewritten to index.html; SPA deep links are rewritten on the UI behavior only', () => {
    const dists = t.web.findResources('AWS::CloudFront::Distribution');
    const cfg = (Object.values(dists)[0] as any).Properties.DistributionConfig;
    expect(cfg.CustomErrorResponses).toBeUndefined();
    expect(cfg.DefaultCacheBehavior.FunctionAssociations).toHaveLength(1);
    const api = cfg.CacheBehaviors.find((b: any) => b.PathPattern === '/api/*');
    expect(api.FunctionAssociations).toHaveLength(1);
  });

  test('SPA rewrite keeps files and rewrites routes', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const { SPA_REWRITE } = require('../lib/web-stack');
    // eslint-disable-next-line no-new-func
    const fn = new Function(`${SPA_REWRITE}; return handler;`)();
    const run = (uri: string): string => fn({ request: { uri } }).uri;
    expect(run('/packages/pkg-lending-01/documents/w2-x')).toBe('/index.html');
    expect(run('/assets/index-abc.js')).toBe('/assets/index-abc.js');
    expect(run('/config.json')).toBe('/config.json');
    expect(run('/favicon.ico')).toBe('/favicon.ico');
    expect(run('/')).toBe('/index.html');
    // Client routes whose ids carry the pipeline version (dots) must still serve the SPA.
    const unit = encodeURIComponent('w2-2025-maya-fictus-827d37:2.3.1:eu-03');
    expect(run(`/u/${unit}`)).toBe('/index.html');
    expect(run(`/region/${unit}`)).toBe('/index.html');
    expect(run(`/region/${encodeURIComponent('w2-2025-maya-fictus-827d37:1.0:c-02')}`)).toBe('/index.html');
    expect(run('/pipelines/2.3.1')).toBe('/index.html');
    expect(run('/pipelines/1.0')).toBe('/index.html');
    expect(run('/d/w2-2025-maya-fictus-827d37/units')).toBe('/index.html');
  });
});

describe('v2 inspection routes', () => {
  test('rp-xray is its own Lambda (ingest layer) behind GET /documents/{docId}/lanes/{pv}/xray', () => {
    const fns = t.agent.findResources('AWS::Lambda::Function', { Properties: { FunctionName: 'rp-xray' } });
    const [[xrayId, xray]] = Object.entries(fns) as [string, any][];
    expect(xray.Properties.Handler).toBe('lambdas.xray.handler.handler');
    expect(xray.Properties.Environment.Variables.RP_CORS_ORIGINS).toContain('http://localhost:5173');
    const lanes = Object.entries(t.agent.findResources('AWS::ApiGateway::Resource'))
      .filter(([, r]: [string, any]) => r.Properties.PathPart === 'xray');
    expect(lanes).toHaveLength(1);
    const [[resId]] = lanes;
    const methods = Object.values(t.agent.findResources('AWS::ApiGateway::Method')) as any[];
    const get = methods.find((m) => m.Properties.HttpMethod === 'GET' && m.Properties.ResourceId?.Ref === resId);
    expect(JSON.stringify(get.Properties.Integration.Uri)).toContain(xrayId);
    // no static `1.0` segment beside `{pv}`: it would capture /lanes/1.0/units and the other lane routes
    const parts = (Object.values(t.agent.findResources('AWS::ApiGateway::Resource')) as any[]).map((r) => r.Properties.PathPart);
    expect(parts).not.toContain('1.0');
  });

  test('rp-xray writes only its inspect cache; rp-api may invoke only rp-graph-hop:live and knows it', () => {
    const policies = t.agent.findResources('AWS::IAM::Policy');
    const xray = JSON.stringify(Object.entries(policies).find(([id]) => id.startsWith('FnXrayServiceRoleDefaultPolicy'))?.[1]);
    expect(xray).toContain('gold/*/1.0/inspect/*');
    expect(xray).not.toContain('silver/*"]},{"Action":"s3:PutObject');
    // the role's default policy, and its overflow managed policy once the statements pass the inline size limit
    const apiPolicies = { ...policies, ...t.agent.findResources('AWS::IAM::ManagedPolicy') };
    const api = JSON.stringify(Object.entries(apiPolicies).filter(([id]) => id.startsWith('FnApiServiceRole')).map(([, v]) => v));
    expect(api).toContain('function:rp-graph-hop:live');
    expect(api).not.toContain('function:rp-graph-hop"');  // never $LATEST
    t.agent.hasResourceProperties('AWS::Lambda::Function', {
      FunctionName: 'rp-api',
      Environment: { Variables: Match.objectLike({ RP_GRAPH_HOP_FUNCTION: 'rp-graph-hop:live' }) },
    });
  });
});

describe('v2 routes, access log and the publication flag', () => {
  test('rp-access-log: pk runId, sk {at}#{callId}, GSI bySince, TTL; the interceptor writes it, rp-api reads it', () => {
    t.base.hasResourceProperties('AWS::DynamoDB::Table', {
      TableName: 'rp-access-log',
      KeySchema: [{ AttributeName: 'runId', KeyType: 'HASH' }, { AttributeName: 'sk', KeyType: 'RANGE' }],
      TimeToLiveSpecification: { AttributeName: 'ttl', Enabled: true },
      GlobalSecondaryIndexes: [Match.objectLike({ IndexName: 'bySince' })],
    });
    const fn = (name: string) => Object.values(t.agent.findResources('AWS::Lambda::Function', { Properties: { FunctionName: name } }))[0] as any;
    expect(fn('rp-interceptor').Properties.Environment.Variables.RP_TABLE_ACCESS_LOG).toBeDefined();
    const policies = { ...t.agent.findResources('AWS::IAM::Policy'), ...t.agent.findResources('AWS::IAM::ManagedPolicy') };
    const of = (prefix: string) => JSON.stringify(Object.entries(policies).filter(([id]) => id.startsWith(prefix)).map(([, v]) => v));
    expect(of('FnInterceptorServiceRole')).toContain('dynamodb:PutItem');
    expect(of('FnInterceptorServiceRole')).not.toContain('dynamodb:Query');  // write only
    // the ask gate logs its refusals (SC-7): rp-api-ask and the Runtime entrypoint may put rows, never read them
    expect(of('FnApiAskServiceRole')).toContain('dynamodb:PutItem');
    expect(fn('rp-api-ask').Properties.Environment.Variables.RP_TABLE_ACCESS_LOG).toBeDefined();
    const runtime = of('RuntimeRole');
    expect(runtime).toContain('dynamodb:PutItem');
    const accessLogStatements = (Object.entries(policies).filter(([id]) => id.startsWith('FnApiAskServiceRole') || id.startsWith('RuntimeRole'))
      .flatMap(([, v]) => (v as any).Properties.PolicyDocument.Statement) as any[])
      .filter((st) => JSON.stringify(st.Resource).includes('AccessLog'));
    expect(accessLogStatements.length).toBeGreaterThanOrEqual(2);
    for (const st of accessLogStatements) expect(st.Action).toBe('dynamodb:PutItem');
  });

  test('rp-api serves the new routes and withholds rates and dollars unless rp:publicMetrics=on', () => {
    const parts = (Object.values(t.agent.findResources('AWS::ApiGateway::Resource')) as any[]).map((r) => r.Properties.PathPart);
    for (const p of ['access', 'decisions', 'governance', 'definitions', 'decision', 'eval', 'cost', 'ontology', 'terms', 'neighbourhood']) {
      expect(parts).toContain(p);
    }
    const api = (tpl: Template) => Object.values(tpl.findResources('AWS::Lambda::Function', { Properties: { FunctionName: 'rp-api' } }))[0] as any;
    expect(api(t.agent).Properties.Environment.Variables.RP_PUBLIC_METRICS).toBe('off');
    expect(api(synth({ 'rp:publicMetrics': 'on' }).t.agent).Properties.Environment.Variables.RP_PUBLIC_METRICS).toBe('on');
  });
});

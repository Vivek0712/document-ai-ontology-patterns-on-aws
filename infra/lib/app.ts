/** Wires the five stacks (deploy order rp-base, rp-knowledge, rp-ingest, rp-agent, rp-web). */
import { App, Environment, Tags } from 'aws-cdk-lib';
import { AgentStack } from './agent-stack';
import { BaseStack } from './base-stack';
import { BuildAssets, loadBuildAssets } from './build-assets';
import { IngestStack } from './ingest-stack';
import { KnowledgeStack } from './knowledge-stack';
import { STACKS } from './names';
import { WebStack } from './web-stack';

export interface ReferencePipelineStacks {
  base: BaseStack;
  knowledge: KnowledgeStack;
  ingest: IngestStack;
  agent: AgentStack;
  web: WebStack;
}

function ctx<T>(app: App, key: string, fallback: T): T {
  const value = app.node.tryGetContext(key);
  return value === undefined || value === '' ? fallback : (value as T);
}

function list(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String).filter(Boolean);
  if (typeof value === 'string') return value.split(',').map((s) => s.trim()).filter(Boolean);
  return [];
}

export function buildApp(app: App, opts: { assets?: BuildAssets; repoRoot?: string; uiDistDir?: string } = {}):
ReferencePipelineStacks {
  const region = ctx(app, 'rp:region', 'us-east-1');
  const env: Environment = {
    account: ctx(app, 'rp:account', process.env.CDK_DEFAULT_ACCOUNT),
    region,
  };
  const assets = opts.assets ?? loadBuildAssets(ctx<string | undefined>(app, 'rp:buildDir', undefined));
  const devOrigins = list(ctx<unknown>(app, 'rp:devOrigins', []));
  const runtimeArn = ctx<string | undefined>(app, 'rp:agentRuntimeArn', undefined);

  const base = new BaseStack(app, STACKS.base, { env, assets, repoRoot: opts.repoRoot,
    availabilityZones: list(ctx<unknown>(app, 'rp:availabilityZones', [`${region}a`, `${region}b`])),
    description: 'Regionproof base: KMS, data bucket, DynamoDB, VPC, layers, config' });
  const knowledge = new KnowledgeStack(app, STACKS.knowledge, {
    env, base,
    neptuneEngineVersion: ctx(app, 'rp:neptuneEngineVersion', '1.4.8.1'),
    aossAdminArns: list(ctx<unknown>(app, 'rp:aossAdminArns', [])),
    description: 'Regionproof knowledge: OpenSearch Serverless rp-units, Neptune Serverless rp-graph',
  });
  const ingest = new IngestStack(app, STACKS.ingest, {
    env, base,
    aossEndpoint: knowledge.aossEndpoint,
    aossCollectionArn: knowledge.aossCollectionArn,
    neptuneEndpoint: knowledge.neptuneEndpoint,
    neptuneResourceId: knowledge.neptuneResourceId,
    doclingImage: ctx(app, 'rp:doclingImage', 'quay.io/docling-project/docling-serve-cpu:v1.38.2'),
    description: 'Regionproof ingest: rp-ingest state machine, stage Lambdas, docling-serve on Fargate',
  });
  const agent = new AgentStack(app, STACKS.agent, {
    env, base,
    aossEndpoint: knowledge.aossEndpoint,
    aossCollectionArn: knowledge.aossCollectionArn,
    neptuneEndpoint: knowledge.neptuneEndpoint,
    neptuneResourceId: knowledge.neptuneResourceId,
    stateMachineArn: ingest.stateMachine.stateMachineArn,
    corsOrigins: devOrigins,
    agentRuntimeArn: runtimeArn,
    askTimeoutMs: Number(ctx(app, 'rp:askTimeoutMs', 29000)),
    publicMetrics: ctx<string>(app, 'rp:publicMetrics', 'off') === 'on' ? 'on' : 'off',
    description: 'Regionproof agent plane: Cognito, REST API rp-api, MCP tool Lambdas, AgentCore roles',
  });
  const web = new WebStack(app, STACKS.web, {
    env,
    api: agent.api,
    dataBucket: base.bucket,
    userPoolId: agent.userPool.userPoolId,
    userPoolClientId: agent.userPoolClient.userPoolClientId,
    devOrigins,
    uiDistDir: opts.uiDistDir,
    description: 'Regionproof web: UI bucket + CloudFront (OAC) with /api/* to rp-api',
  });
  knowledge.addDependency(base);
  ingest.addDependency(knowledge);
  agent.addDependency(ingest);
  web.addDependency(agent);

  for (const stack of [base, knowledge, ingest, agent, web]) {
    Tags.of(stack).add('project', 'regionproof');
    Tags.of(stack).add('env', 'demo');
  }
  return { base, knowledge, ingest, agent, web };
}

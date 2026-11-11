/**
 * Builds the Lambda functions of one stack from FUNCTIONS, with the shared environment, layers, VPC placement, log group, an SSM parameter with the ARN, and the
 * least-privilege grants declared in each spec.
 */
import { Duration, RemovalPolicy, Stack } from 'aws-cdk-lib';
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { BaseResources } from './base-stack';
import { Access, AossMode, EnvKey, FunctionSpec, LIVE_ALIAS, StackKey, functionsFor, specHandler, specName } from './functions';
import { SSM, TABLE_ENV, TableLogical } from './names';

/** Values that only exist in later stacks are passed in as plain strings/tokens. */
export interface FunctionEnvValues {
  aossEndpoint?: string;
  aossCollectionArn?: string;
  neptuneEndpoint?: string;
  neptuneResourceId?: string;
  doclingUrl?: string;
  doclingQueueUrl?: string;
  stateMachineArn?: string;
  userPoolId?: string;
  userPoolClientId?: string;
  corsOrigins?: string[];
  /** rp-api-ask upstream budget: the API Gateway integration timeout minus a margin for init and reads. */
  askTimeoutSeconds?: number;
  /** RP_PUBLIC_METRICS: 'off' until the decision says otherwise; the API withholds rates and dollars */
  publicMetrics?: 'on' | 'off';
}

export interface BuiltFunctions {
  byName: Record<string, lambda.Function>;
  /** Roles that need OpenSearch Serverless data access, with the access level. */
  aossPrincipals: { role: iam.IRole; mode: AossMode }[];
  get(name: string): lambda.Function;
  /** What callers should invoke: the `live` alias when the spec publishes one, else the function. */
  invokable(name: string): lambda.IFunction;
}

export function buildFunctions(
  scope: Construct,
  stackKey: StackKey,
  base: BaseResources,
  values: FunctionEnvValues,
): BuiltFunctions {
  const stack = Stack.of(scope);
  const code = lambda.Code.fromAsset(base.assets.codeDir);
  const byName: Record<string, lambda.Function> = {};
  const aliases: Record<string, lambda.Alias> = {};
  const aossPrincipals: BuiltFunctions['aossPrincipals'] = [];

  for (const spec of functionsFor(stackKey)) {
    const name = specName(spec);
    const id = 'Fn' + name.replace(/^rp-/, '').replace(/(^|-)(\w)/g, (_m, _d, c: string) => c.toUpperCase());
    const logGroup = new logs.LogGroup(scope, `${id}Logs`, {
      logGroupName: `/aws/lambda/${name}`,
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    const layers = spec.layers
      .map((l) => base.layers[l])
      .filter((l): l is lambda.ILayerVersion => l !== undefined);
    const fn = new lambda.Function(scope, id, {
      functionName: name,
      description: spec.description,
      runtime: lambda.Runtime.PYTHON_3_12,
      architecture: lambda.Architecture.X86_64,
      handler: specHandler(spec),
      code,
      layers,
      timeout: Duration.seconds(spec.timeoutS),
      memorySize: spec.memoryMb,
      tracing: lambda.Tracing.ACTIVE,
      logGroup,
      environment: environmentFor(spec, base, values),
      ...(spec.vpc
        ? {
            vpc: base.vpc,
            vpcSubnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
            securityGroups: [base.lambdaSg],
          }
        : {}),
    });
    applyAccess(stack, fn, spec.access, base, values);
    if (spec.access.aoss) aossPrincipals.push({ role: fn.role as iam.IRole, mode: spec.access.aoss });
    let invokeArn = fn.functionArn;
    if (spec.liveAlias) {
      const alias = new lambda.Alias(scope, `${id}LiveAlias`, { aliasName: LIVE_ALIAS, version: fn.currentVersion });
      aliases[name] = alias;
      invokeArn = alias.functionArn;
    }
    new ssm.StringParameter(scope, `${id}ArnParam`, {
      parameterName: SSM.lambdaArn(name),
      stringValue: invokeArn,
    });
    byName[name] = fn;
  }

  return {
    byName,
    aossPrincipals,
    get(name: string): lambda.Function {
      const fn = byName[name];
      if (!fn) throw new Error(`function ${name} is not built in stack ${stackKey}`);
      return fn;
    },
    invokable(name: string): lambda.IFunction {
      return aliases[name] ?? this.get(name);
    },
  };
}

function environmentFor(spec: FunctionSpec, base: BaseResources, v: FunctionEnvValues): Record<string, string> {
  const env: Record<string, string> = {
    RP_ENV: 'demo',
    RP_CONFIG_SOURCE: 's3',
    RP_DATA_BUCKET: base.bucket.bucketName,
    RP_CODE_SHA: base.assets.codeSha.slice(0, 12),
    RP_LOG_LEVEL: 'INFO',
    RP_HEARTBEAT_SECONDS: '5',
  };
  for (const [logical, envName] of Object.entries(TABLE_ENV)) {
    env[envName] = base.tables[logical as TableLogical].tableName;
  }
  const need = (key: EnvKey, value: string | undefined): string => {
    if (!value) throw new Error(`${specName(spec)} needs env '${key}' but the stack did not provide it`);
    return value;
  };
  for (const key of spec.env ?? []) {
    switch (key) {
      case 'aoss':
        env.RP_AOSS_ENDPOINT = need(key, v.aossEndpoint);
        break;
      case 'neptune':
        env.RP_NEPTUNE_ENDPOINT = need(key, v.neptuneEndpoint);
        break;
      case 'docling':
        env.RP_DOCLING_URL = need(key, v.doclingUrl);
        break;
      case 'doclingQueue':
        env.RP_DOCLING_QUEUE_URL = need(key, v.doclingQueueUrl);
        break;
      case 'stateMachine':
        env.RP_STATE_MACHINE_ARN = need(key, v.stateMachineArn);
        break;
      case 'userPool':
        env.RP_USER_POOL_ID = need(key, v.userPoolId);
        env.RP_USER_POOL_CLIENT_ID = need(key, v.userPoolClientId);
        break;
      case 'graphAdmin':
        env.RP_GRAPH_ADMIN_FUNCTION = 'rp-graph-admin';
        break;
      case 'graphHop':
        // rp-api runs allow-listed showcase templates through rp-graph-hop's live alias
        env.RP_GRAPH_HOP_FUNCTION = 'rp-graph-hop:live';
        break;
      case 'askTimeout':
        env.RP_ASK_TIMEOUT_SECONDS = need(key, v.askTimeoutSeconds !== undefined ? String(v.askTimeoutSeconds) : undefined);
        break;
      case 'publicMetrics':
        env.RP_PUBLIC_METRICS = v.publicMetrics ?? 'off';
        break;
      case 'cors': {
        const origins = v.corsOrigins ?? [];
        // Read by evidence_core.env.cors_headers (api, upload_url, get_region_http).
        env.RP_CORS_ORIGINS = origins.join(',');
        break;
      }
    }
  }
  return env;
}

function applyAccess(stack: Stack, fn: lambda.Function, a: Access, base: BaseResources, v: FunctionEnvValues): void {
  const role = fn.role as iam.IRole;
  for (const p of a.s3Read ?? []) base.bucket.grantRead(role, p);
  for (const p of a.s3Write ?? []) base.bucket.grantPut(role, p);
  if (a.s3Delete?.length) {
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['s3:DeleteObject', 's3:DeleteObjectVersion'],
      resources: a.s3Delete.map((p) => base.bucket.arnForObjects(p)),
    }));
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['s3:ListBucket', 's3:ListBucketVersions'],
      resources: [base.bucket.bucketArn],
    }));
  }
  const writes = new Set(a.ddbWrite ?? []);
  for (const t of writes) base.tables[t].grantReadWriteData(role);
  for (const t of a.ddbRead ?? []) if (!writes.has(t)) base.tables[t].grantReadData(role);
  // put-only (an append log the writer never reads): PutItem plus the table key, nothing else
  for (const t of a.ddbPut ?? []) {
    base.tables[t].grant(role, 'dynamodb:PutItem');
    base.key.grantEncryptDecrypt(role);
  }
  if (a.bedrock) {
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${stack.partition}:bedrock:*::foundation-model/*`,
        `arn:${stack.partition}:bedrock:${stack.region}:${stack.account}:inference-profile/*`,
      ],
    }));
  }
  if (a.textract) {
    role.addToPrincipalPolicy(new iam.PolicyStatement({ actions: ['textract:AnalyzeDocument'], resources: ['*'] }));
  }
  if (a.comprehend) {
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['comprehend:DetectPiiEntities', 'comprehend:ContainsPiiEntities'],
      resources: ['*'],
    }));
  }
  if (a.aoss) {
    if (!v.aossCollectionArn) throw new Error(`${fn.node.id}: aoss access without a collection`);
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['aoss:APIAccessAll'],
      resources: [v.aossCollectionArn],
    }));
  }
  if (a.neptune) {
    if (!v.neptuneResourceId) throw new Error(`${fn.node.id}: neptune access without a cluster`);
    const actions = a.neptune === 'read'
      ? ['neptune-db:ReadDataViaQuery', 'neptune-db:GetQueryStatus']
      : ['neptune-db:ReadDataViaQuery', 'neptune-db:WriteDataViaQuery', 'neptune-db:DeleteDataViaQuery',
        'neptune-db:GetQueryStatus', 'neptune-db:CancelQuery'];
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions,
      resources: [`arn:${stack.partition}:neptune-db:${stack.region}:${stack.account}:${v.neptuneResourceId}/*`],
    }));
  }
  if (a.sfnCallback) {
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['states:SendTaskSuccess', 'states:SendTaskFailure', 'states:SendTaskHeartbeat'],
      resources: [`arn:${stack.partition}:states:${stack.region}:${stack.account}:stateMachine:rp-ingest`],
    }));
  }
  if (a.sfnStart) {
    if (!v.stateMachineArn) throw new Error(`${fn.node.id}: sfnStart without a state machine`);
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['states:StartExecution'],
      resources: [v.stateMachineArn],
    }));
  }
  for (const target of a.invoke ?? []) {
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['lambda:InvokeFunction'],
      resources: [`arn:${stack.partition}:lambda:${stack.region}:${stack.account}:function:${target}`],
    }));
  }
  for (const prefix of a.ssmRead ?? []) {
    role.addToPrincipalPolicy(new iam.PolicyStatement({
      actions: ['ssm:GetParameter', 'ssm:GetParameters'],
      resources: [`arn:${stack.partition}:ssm:${stack.region}:${stack.account}:parameter${prefix}`],
    }));
  }
}

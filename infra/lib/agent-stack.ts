/**
 * rp-agent: Cognito (`rp-users`, groups, app client `rp-web`), the REST API `rp-api` (stage v1,
 * Cognito authorizer with the access-token scope on every method), the API Lambdas, upload_url,
 * C's MCP tool Lambdas, interceptor and get_region_http, and the IAM roles the AgentCore Gateway and
 * Runtime assume (scripts/deploy_agentcore.sh creates those resources).
 *
 * POST /ask: with context `rp:agentRuntimeArn` the method is an HTTP proxy straight to the Runtime
 * invocations endpoint with response streaming (preferred). Without it, the method
 * targets `rp-api-ask`, which reads the Runtime ARN from SSM at request time.
 */
import { CfnOutput, Duration, RemovalPolicy, Stack, StackProps } from 'aws-cdk-lib';
import * as apigw from 'aws-cdk-lib/aws-apigateway';
import * as cognito from 'aws-cdk-lib/aws-cognito';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as ssm from 'aws-cdk-lib/aws-ssm';
import { Construct } from 'constructs';
import { addAossDataPolicy } from './aoss';
import { ROUTES, RouteTarget } from './api-routes';
import { BaseResources } from './base-stack';
import { ACCESS_TOKEN_SCOPE, SSM, USER_GROUPS } from './names';
import { buildFunctions } from './rp-functions';

export interface AgentStackProps extends StackProps {
  base: BaseResources;
  aossEndpoint: string;
  aossCollectionArn: string;
  neptuneEndpoint: string;
  neptuneResourceId: string;
  stateMachineArn: string;
  /** Browser origins allowed by CORS for direct API calls (dev server). The deployed UI calls the API
   *  through CloudFront `/api/*` on its own origin, so it needs no CORS. */
  corsOrigins: string[];
  agentRuntimeArn?: string;
  askTimeoutMs: number;
  /** RP_PUBLIC_METRICS for rp-api (context `rp:publicMetrics`, default off) */
  publicMetrics?: 'on' | 'off';
  /** context `rp:coaControl`: rp-api forwards /coa/environment to rp-coa-control (and may invoke it) */
  coaControl?: boolean;
}

export const TOOL_FUNCTIONS = ['rp-search-units', 'rp-graph-hop', 'rp-get-region', 'rp-doc-status'];

export class AgentStack extends Stack {
  readonly api: apigw.RestApi;
  readonly userPool: cognito.UserPool;
  readonly userPoolClient: cognito.UserPoolClient;

  constructor(scope: Construct, id: string, props: AgentStackProps) {
    super(scope, id, props);
    const { base } = props;

    // --- Cognito --------------------------------------------------------------------------------------
    this.userPool = new cognito.UserPool(this, 'Users', {
      userPoolName: 'rp-users',
      selfSignUpEnabled: false,
      signInAliases: { email: true },
      signInCaseSensitive: false,
      standardAttributes: { email: { required: true, mutable: false } },
      passwordPolicy: { minLength: 12, requireDigits: true, requireLowercase: true, requireUppercase: true,
        requireSymbols: false },
      accountRecovery: cognito.AccountRecovery.NONE,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    for (const group of USER_GROUPS) {
      new cognito.CfnUserPoolGroup(this, `Group${group.replace(/(^|-)(\w)/g, (_m, _d, c: string) => c.toUpperCase())}`, {
        userPoolId: this.userPool.userPoolId,
        groupName: group,
        description: `Regionproof ${group}`,
      });
    }
    this.userPoolClient = this.userPool.addClient('WebClient', {
      userPoolClientName: 'rp-web',
      generateSecret: false,
      // SRP for the browser; adminUserPassword only for operator scripts (scripts/lib/rp_ops/auth.py fallback,
      // needs cognito-idp:AdminInitiateAuth IAM permission, never reachable from the browser).
      authFlows: { userSrp: true, adminUserPassword: true },
      disableOAuth: true,
      preventUserExistenceErrors: true,
      accessTokenValidity: Duration.hours(1),
      idTokenValidity: Duration.hours(1),
      refreshTokenValidity: Duration.days(1),
      enableTokenRevocation: true,
    });

    // --- Lambdas --------------------------------------------------------------------------------------
    const fns = buildFunctions(this, 'agent', base, {
      aossEndpoint: props.aossEndpoint,
      aossCollectionArn: props.aossCollectionArn,
      neptuneEndpoint: props.neptuneEndpoint,
      neptuneResourceId: props.neptuneResourceId,
      stateMachineArn: props.stateMachineArn,
      userPoolId: this.userPool.userPoolId,
      userPoolClientId: this.userPoolClient.userPoolClientId,
      corsOrigins: props.corsOrigins,
      // rp-api-ask gives up 4 s before API Gateway's integration timeout (whose clock includes the Lambda
      // cold start and the SSM/DynamoDB reads), so the Ask screen gets an SSE `timeout` frame, not a 504.
      askTimeoutSeconds: Math.max(5, Math.min(props.askTimeoutMs, 29000) / 1000 - 4),
      publicMetrics: props.publicMetrics,
      coaControl: props.coaControl,
    });
    if (props.coaControl) {
      // rp-api -> rp-coa-control: by name, so rp-agent never depends on the opt-in stack (deployed after it)
      fns.get('rp-api').addToRolePolicy(new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [`arn:${this.partition}:lambda:${this.region}:${this.account}:function:rp-coa-control`],
      }));
    }
    addAossDataPolicy(this, 'rp-units-agent', fns.aossPrincipals);

    // --- REST API ---------------------------------------------------------------------------------------
    const accessLogs = new logs.LogGroup(this, 'ApiAccessLogs', {
      logGroupName: '/rp/apigw/rp-api',
      retention: logs.RetentionDays.ONE_MONTH,
      removalPolicy: RemovalPolicy.DESTROY,
    });
    this.api = new apigw.RestApi(this, 'Api', {
      restApiName: 'rp-api',
      description: 'Regionproof REST API',
      endpointTypes: [apigw.EndpointType.REGIONAL],
      deployOptions: {
        stageName: 'v1',
        tracingEnabled: true,
        metricsEnabled: true,
        throttlingRateLimit: 50,
        throttlingBurstLimit: 100,
        accessLogDestination: new apigw.LogGroupLogDestination(accessLogs),
        accessLogFormat: apigw.AccessLogFormat.jsonWithStandardFields(),
      },
      // Access logging needs the account-level API Gateway CloudWatch Logs role. Own it here instead of
      // relying on whichever stack set it last (today an unrelated one); RETAIN keeps the account setting
      // valid if rp-agent is deleted.
      cloudWatchRole: true,
      cloudWatchRoleRemovalPolicy: RemovalPolicy.RETAIN,
      defaultCorsPreflightOptions: props.corsOrigins.length
        ? {
            allowOrigins: props.corsOrigins,
            allowMethods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
            allowHeaders: ['Authorization', 'Content-Type', 'X-Amzn-Bedrock-AgentCore-Runtime-Session-Id'],
            maxAge: Duration.hours(1),
          }
        : undefined,
    });
    if (props.corsOrigins.length) {
      const headers = { 'Access-Control-Allow-Origin': `'${props.corsOrigins[0]}'`, Vary: "'Origin'" };
      this.api.addGatewayResponse('Default4xx', { type: apigw.ResponseType.DEFAULT_4XX, responseHeaders: headers });
      this.api.addGatewayResponse('Default5xx', { type: apigw.ResponseType.DEFAULT_5XX, responseHeaders: headers });
    }
    const authorizer = new apigw.CognitoUserPoolsAuthorizer(this, 'Authorizer', {
      authorizerName: 'rp-cognito',
      cognitoUserPools: [this.userPool],
      identitySource: 'method.request.header.Authorization',
    });
    const methodAuth: apigw.MethodOptions = {
      authorizer,
      authorizationType: apigw.AuthorizationType.COGNITO,
      authorizationScopes: [ACCESS_TOKEN_SCOPE],
    };

    const integrationTimeout = Duration.millis(Math.min(props.askTimeoutMs, 29000));
    const lambdaIntegration = (fn: lambda.IFunction, timeout?: Duration): apigw.LambdaIntegration =>
      new apigw.LambdaIntegration(fn, { proxy: true, allowTestInvoke: false, timeout });
    const integrations: Record<Exclude<RouteTarget, 'ask'>, apigw.Integration> = {
      'rp-api': lambdaIntegration(fns.get('rp-api')),
      'rp-upload-url': lambdaIntegration(fns.get('rp-upload-url')),
      'rp-get-region-http': lambdaIntegration(fns.invokable('rp-get-region-http')),
      'rp-xray': lambdaIntegration(fns.get('rp-xray')),
    };
    const askTimeout = Duration.millis(props.askTimeoutMs);
    const askIntegration: apigw.Integration = props.agentRuntimeArn
      ? new apigw.HttpIntegration(
          `https://bedrock-agentcore.${this.region}.amazonaws.com/runtimes/${encodeURIComponent(props.agentRuntimeArn)}`
            + '/invocations?qualifier=DEFAULT',
          { proxy: true, httpMethod: 'POST', options: { timeout: askTimeout } },
        )
      : lambdaIntegration(fns.get('rp-api-ask'), integrationTimeout);

    for (const route of ROUTES) {
      const resource = this.api.root.resourceForPath(route.path);
      const integration = route.target === 'ask' ? askIntegration : integrations[route.target];
      const method = resource.addMethod(route.method, integration, methodAuth);
      if (route.target === 'ask' && props.agentRuntimeArn) {
        // REST API response streaming (Runtime emits SSE); raised timeouts need a quota increase above 29 s.
        (method.node.defaultChild as apigw.CfnMethod).addPropertyOverride('Integration.ResponseTransferMode',
          'STREAM');
      }
    }

    // --- AgentCore roles (used by scripts/deploy_agentcore.sh) ----------------------------------------------
    const agentcorePrincipal = new iam.ServicePrincipal('bedrock-agentcore.amazonaws.com', {
      conditions: { StringEquals: { 'aws:SourceAccount': this.account } },
    });
    const gatewayRole = new iam.Role(this, 'GatewayRole', {
      roleName: 'rp-agentcore-gateway',
      assumedBy: agentcorePrincipal,
      description: 'AgentCore Gateway rp-gateway: invoke the MCP tool Lambdas and the interceptor',
    });
    for (const name of [...TOOL_FUNCTIONS, 'rp-interceptor']) fns.get(name).grantInvoke(gatewayRole);

    const runtimeRole = new iam.Role(this, 'RuntimeRole', {
      roleName: 'rp-agentcore-runtime',
      assumedBy: agentcorePrincipal,
      description: 'AgentCore Runtime rp_agent: Bedrock models, run records, config',
    });
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock:InvokeModel', 'bedrock:InvokeModelWithResponseStream'],
      resources: [
        `arn:${this.partition}:bedrock:*::foundation-model/*`,
        `arn:${this.partition}:bedrock:${this.region}:${this.account}:inference-profile/*`,
      ],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['logs:CreateLogGroup', 'logs:CreateLogStream', 'logs:PutLogEvents', 'logs:DescribeLogStreams',
        'logs:DescribeLogGroups'],
      resources: [`arn:${this.partition}:logs:${this.region}:${this.account}:log-group:/aws/bedrock-agentcore/*`],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['xray:PutTraceSegments', 'xray:PutTelemetryRecords', 'xray:GetSamplingRules',
        'xray:GetSamplingTargets'],
      resources: ['*'],
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['cloudwatch:PutMetricData'],
      resources: ['*'],
      conditions: { StringEquals: { 'cloudwatch:namespace': 'bedrock-agentcore' } },
    }));
    runtimeRole.addToPolicy(new iam.PolicyStatement({
      actions: ['bedrock-agentcore:GetWorkloadAccessToken', 'bedrock-agentcore:GetWorkloadAccessTokenForJWT',
        'bedrock-agentcore:GetWorkloadAccessTokenForUserId'],
      resources: [`arn:${this.partition}:bedrock-agentcore:${this.region}:${this.account}:workload-identity-directory/*`],
    }));
    base.bucket.grantRead(runtimeRole, 'config/*');
    // Direct code deploy: the Runtime pulls its zip from the data bucket (services/agent/deploy/deploy.py).
    base.bucket.grantRead(runtimeRole, 'deploy/agentcore/*');
    base.bucket.grantPut(runtimeRole, 'runs/*');
    base.tables.runs.grantReadWriteData(runtimeRole);
    for (const t of ['packages', 'documents', 'doc_acl'] as const) base.tables[t].grantReadData(runtimeRole);

    // --- outputs ------------------------------------------------------------------------------------------------
    const param = (pid: string, name: string, value: string): void => {
      new ssm.StringParameter(this, pid, { parameterName: name, stringValue: value });
    };
    param('ApiUrlParam', SSM.apiUrl, this.api.url);
    param('UserPoolIdParam', SSM.userPoolId, this.userPool.userPoolId);
    param('UserPoolClientIdParam', SSM.userPoolClientId, this.userPoolClient.userPoolClientId);
    param('GatewayRoleParam', SSM.gatewayRoleArn, gatewayRole.roleArn);
    param('RuntimeRoleParam', SSM.runtimeRoleArn, runtimeRole.roleArn);
    new CfnOutput(this, 'ApiUrl', { value: this.api.url });
    new CfnOutput(this, 'UserPoolId', { value: this.userPool.userPoolId });
    new CfnOutput(this, 'UserPoolClientId', { value: this.userPoolClient.userPoolClientId });
    new CfnOutput(this, 'AskIntegration', { value: props.agentRuntimeArn ? 'http-proxy-stream' : 'lambda:rp-api-ask' });
  }
}

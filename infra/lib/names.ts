/**
 * Names shared by every stack. The DynamoDB key schema is read from
 * contracts/generated/tables.json, which `make contracts` exports from evidence_core.ddb, so the
 * tables CDK creates and the tables the Python code (and its moto tests) use cannot drift.
 */
import * as fs from 'fs';
import * as path from 'path';

export const REPO_ROOT = path.resolve(__dirname, '..', '..');
export const PIPELINE_VERSIONS = ['1.0', '2.3.1'] as const;
export const USER_GROUPS = ['lending-analyst', 'markets-analyst'] as const;
export const ACCESS_TOKEN_SCOPE = 'aws.cognito.signin.user.admin';

export const STACKS = {
  base: 'rp-base',
  knowledge: 'rp-knowledge',
  ingest: 'rp-ingest',
  agent: 'rp-agent',
  web: 'rp-web',
  coaControl: 'rp-coa-control',
  coaGuard: 'rp-coa-guard',
} as const;

export const SSM = {
  dataBucket: '/rp/data-bucket',
  uiBucket: '/rp/ui-bucket',
  apiUrl: '/rp/api-url',
  webUrl: '/rp/web-url',
  userPoolId: '/rp/user-pool-id',
  userPoolClientId: '/rp/user-pool-client-id',
  aossEndpoint: '/rp/aoss-endpoint',
  neptuneEndpoint: '/rp/neptune-endpoint',
  stateMachineArn: '/rp/state-machine-arn',
  doclingQueueUrl: '/rp/docling-queue-url',
  lambdaSgId: '/rp/vpc/lambda-sg-id',
  privateSubnetIds: '/rp/vpc/private-subnet-ids',
  lambdaArn: (fn: string) => `/rp/lambda/${fn}/arn`,
  // Additions for scripts/deploy_agentcore.sh: roles the Gateway and Runtime assume.
  gatewayRoleArn: '/rp/agent/gateway-role-arn',
  runtimeRoleArn: '/rp/agent/runtime-role-arn',
  // Written by scripts/deploy_agentcore.sh (read at runtime by rp-api-ask).
  agentRuntimeArn: '/rp/agent/runtime-arn',
} as const;

export type TableLogical =
  | 'packages'
  | 'documents'
  | 'doc_status'
  | 'unit_status'
  | 'doc_acl'
  | 'runs'
  | 'pipeline_versions'
  | 'review_queue'
  | 'access_log';

export interface TableSpec {
  name: string;
  pk: string;
  sk: string | null;
  gsis: [string, string, string | null][];
  ttl: string | null;
  stream: boolean;
}

export const TABLE_ENV: Record<TableLogical, string> = {
  packages: 'RP_TABLE_PACKAGES',
  documents: 'RP_TABLE_DOCUMENTS',
  doc_status: 'RP_TABLE_DOC_STATUS',
  unit_status: 'RP_TABLE_UNIT_STATUS',
  doc_acl: 'RP_TABLE_DOC_ACL',
  runs: 'RP_TABLE_RUNS',
  pipeline_versions: 'RP_TABLE_PIPELINE_VERSIONS',
  review_queue: 'RP_TABLE_REVIEW_QUEUE',
  access_log: 'RP_TABLE_ACCESS_LOG',
};

export function loadTableSpecs(repoRoot: string = REPO_ROOT): Record<TableLogical, TableSpec> {
  const file = path.join(repoRoot, 'contracts', 'generated', 'tables.json');
  const specs = JSON.parse(fs.readFileSync(file, 'utf-8')) as Record<string, TableSpec>;
  for (const logical of Object.keys(TABLE_ENV)) {
    if (!specs[logical]) throw new Error(`contracts/generated/tables.json has no table '${logical}'`);
  }
  return specs as Record<TableLogical, TableSpec>;
}

/** `rp-` + directory with `_` -> `-`. */
export function functionName(dir: string): string {
  const leaf = dir.split('/').pop() as string;
  return `rp-${leaf.replace(/_/g, '-')}`;
}

/** `services/lambdas/<dir>/handler.py` -> `lambdas.<dir>.handler.handler`. */
export function handlerString(dir: string): string {
  return `lambdas.${dir.replace(/\//g, '.')}.handler.handler`;
}

/** contracts/generated/coa_control.json: config/coa.yaml resolved by `make contracts` (decision record 0006). */
export interface CoaControlConfig {
  coa: { repo: string; tag: string; commit: string; prefix: string; env: string; region: string;
    deployContext: Record<string, string>; smusAdminArnsParameter: string; configParameter: string };
  bootstrap: { qualifier: string; toolkitStackName: string; boundaryPolicy: string; deployGuardPolicy: string };
  executor: { project: string; image: string; computeType: string; timeoutMinutes: number; concurrency: number;
    binfmtImage: string; toolchain: { java: string; node: string; pnpm: string; maven: string; uv: string; python: string };
    checksums: { node: string; maven: string; uv: string; pnpm: string } };
  lifecycle: { upMinutes: [number, number]; downMinutes: [number, number]; eniRetryMinutes: number;
    eniMaxRetries: number; eniWindowHours: number; failedDownRetryMinutes: number; failedDownAutoRetries: number;
    settleMinutes: number };
  idle: { tickMinutes: number; defaultMinutes: number; minMinutes: number; maxMinutes: number; maxSessionHours: number };
  outputs: { consoleUrl: { stack: string }; restEndpoint: { stack: string }; probeStack: string };
  seed: { bucketPrefix: string; glueDatabase: string; glueTable: string; csv: string; ontology: string; metric: string };
  adminGroup: string;
  function: string;
  table: { name: string; pk: string };
  tableEnv: string;
}

export function loadCoaControlConfig(repoRoot: string = REPO_ROOT): CoaControlConfig {
  const file = path.join(repoRoot, 'contracts', 'generated', 'coa_control.json');
  return JSON.parse(fs.readFileSync(file, 'utf-8')) as CoaControlConfig;
}

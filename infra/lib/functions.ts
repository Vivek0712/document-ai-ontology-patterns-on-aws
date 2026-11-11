/**
 * Every Lambda function of the product: where it lives (stack), its layers, network placement,
 * sizing, extra environment and least-privilege access. Handler strings and names follow
 * one naming rule (`lambdas.<dir>.handler.handler`, `rp-<dir with - for _>`).
 */
import { LayerName } from './build-assets';
import { TableLogical } from './names';

export type StackKey = 'knowledge' | 'ingest' | 'agent' | 'coaControl';
export type AossMode = 'read' | 'delete' | 'write';

export type EnvKey =
  | 'aoss'
  | 'neptune'
  | 'docling'
  | 'doclingQueue'
  | 'stateMachine'
  | 'userPool'
  | 'graphAdmin'
  | 'graphHop'
  | 'askTimeout'
  | 'publicMetrics'
  | 'coaControl'
  | 'coaEnv'
  | 'cors';

export interface Access {
  /** S3 key patterns in the data bucket (`bronze/*`). */
  s3Read?: string[];
  s3Write?: string[];
  s3Delete?: string[];
  ddbRead?: TableLogical[];
  ddbWrite?: TableLogical[];
  /** PutItem only (an append-only log the function never reads back) */
  ddbPut?: TableLogical[];
  bedrock?: boolean;
  textract?: boolean;
  comprehend?: boolean;
  /**
   * OpenSearch Serverless data access on the rp-units collection: `read` (search), `delete` (read and
   * delete documents, no index management) or `write` (also creates and updates indexes).
   */
  aoss?: AossMode;
  /** Neptune IAM data actions (IAM database authentication is on). */
  neptune?: 'read' | 'write';
  /** Step Functions task-token callbacks (docling shim). */
  sfnCallback?: boolean;
  /** StartExecution on rp-ingest (reprocess). */
  sfnStart?: boolean;
  /** Functions this function may invoke: `name` or `name:alias` (then only that alias is allowed). */
  invoke?: string[];
  ssmRead?: string[];
}

export interface FunctionSpec {
  dir: string; // services/lambdas/<dir>
  stack: StackKey;
  layers: LayerName[];
  vpc?: boolean;
  timeoutS: number;
  memoryMb: number;
  env?: EnvKey[];
  access: Access;
  description: string;
  /**
   * Publish a `live` alias (provisioned concurrency on the Ask path, set by scripts/prewarm.sh).
   * Callers (AgentCore Gateway targets via SSM /rp/lambda/<name>/arn, API Gateway) then invoke the alias.
   */
  liveAlias?: boolean;
  /** Overrides for names that do not follow the directory rule. */
  name?: string;
  handler?: string;
}

const ING: LayerName[] = ['core', 'ingest'];
const AGT: LayerName[] = ['core', 'agent'];
const API: LayerName[] = ['core', 'api'];
const STATUS: TableLogical[] = ['doc_status'];

export const FUNCTIONS: FunctionSpec[] = [
  // ---------------- B: ingestion (rp-ingest) ----------------
  {
    dir: 'register', stack: 'ingest', layers: ING, timeoutS: 120, memoryMb: 1024,
    description: 'Validate Bronze object, hash, count pages, init lane and stage records',
    access: {
      s3Read: ['bronze/*', 'config/*'], s3Write: ['silver/*', 'quarantine/*'],
      ddbRead: ['packages', 'pipeline_versions'], ddbWrite: ['documents', 'doc_acl', ...STATUS],
    },
  },
  {
    dir: 'route', stack: 'ingest', layers: ING, timeoutS: 120, memoryMb: 1024,
    description: 'Per-page digital/scan routing from text-layer char counts',
    access: { s3Read: ['bronze/*', 'silver/*', 'config/*'], s3Write: ['silver/*'], ddbWrite: STATUS },
  },
  {
    dir: 'text_layer', stack: 'ingest', layers: ING, timeoutS: 300, memoryMb: 1024,
    description: '1.0 lane: pypdf text layer per page',
    access: { s3Read: ['bronze/*', 'config/*'], s3Write: ['silver/*'], ddbWrite: STATUS },
  },
  {
    dir: 'docling_call', stack: 'ingest', layers: ING, vpc: true, timeoutS: 900, memoryMb: 1024,
    env: ['docling'],
    description: 'SQS-triggered shim: docling-serve on Fargate, then SendTaskSuccess',
    access: {
      s3Read: ['bronze/*', 'silver/*', 'config/*'], s3Write: ['silver/*'], ddbWrite: STATUS, sfnCallback: true,
    },
  },
  {
    dir: 'textract_page', stack: 'ingest', layers: ING, timeoutS: 300, memoryMb: 1024,
    description: 'Textract AnalyzeDocument (LAYOUT, TABLES, FORMS) for one scanned page',
    access: { s3Read: ['bronze/*', 'config/*'], s3Write: ['silver/*'], ddbWrite: STATUS, textract: true },
  },
  {
    dir: 'stage_status', stack: 'ingest', layers: ['core'], timeoutS: 60, memoryMb: 512,
    description: 'Extract open/close, LaneFailed and execution-status backstop',
    access: { s3Read: ['silver/*', 'config/*'], ddbWrite: STATUS },
  },
  {
    dir: 'classify', stack: 'ingest', layers: ING, timeoutS: 120, memoryMb: 1024,
    description: 'SKOS indicator scoring, Nova 2 Lite tie-break',
    access: {
      s3Read: ['silver/*', 'config/*'], s3Write: ['silver/*'], ddbWrite: ['documents', ...STATUS], bedrock: true,
    },
  },
  {
    dir: 'naive_chunk', stack: 'ingest', layers: ING, timeoutS: 300, memoryMb: 1024,
    description: '1.0 lane: fixed-size chunks',
    access: { s3Read: ['silver/*', 'config/*'], s3Write: ['silver/*'], ddbWrite: ['unit_status', ...STATUS] },
  },
  {
    dir: 'eu_builder', stack: 'ingest', layers: ING, timeoutS: 600, memoryMb: 2048,
    description: '2.3.1 lane: evidence units (Phase A/B/C), fields with bbox, review queue',
    access: {
      s3Read: ['bronze/*', 'silver/*', 'config/*'], s3Write: ['silver/*'],
      ddbWrite: ['unit_status', 'review_queue', ...STATUS], bedrock: true,
    },
  },
  {
    dir: 'pii_gate', stack: 'ingest', layers: ING, timeoutS: 300, memoryMb: 1024,
    description: 'Comprehend DetectPiiEntities per unit; hold unmasked PII',
    access: {
      // Bronze: locates the glyph boxes of held spans on the PDF text layer for the Gold blackout.
      s3Read: ['bronze/*', 'silver/*', 'config/*'], s3Write: ['silver/*'], ddbWrite: ['unit_status', ...STATUS],
      comprehend: true,
    },
  },
  {
    dir: 'promote', stack: 'ingest', layers: ING, timeoutS: 600, memoryMb: 2048,
    description: 'Passed units to Gold; page PNGs with held regions filled',
    access: {
      s3Read: ['bronze/*', 'silver/*', 'config/*'], s3Write: ['gold/*'], ddbWrite: ['unit_status', ...STATUS],
    },
  },
  {
    dir: 'embed_index', stack: 'ingest', layers: ING, timeoutS: 600, memoryMb: 1024, env: ['aoss'],
    description: 'Titan V2 embeddings, bulk index into units-v<pv>',
    access: {
      s3Read: ['gold/*', 'config/*'], s3Write: ['gold/*'], ddbRead: ['packages'],
      ddbWrite: ['unit_status', ...STATUS], bedrock: true, aoss: 'write',
    },
  },
  {
    dir: 'graph_write', stack: 'ingest', layers: ING, vpc: true, timeoutS: 300, memoryMb: 1024,
    env: ['neptune'],
    description: 'ABox facts and PROV-O lineage into the package named graph',
    access: {
      s3Read: ['gold/*', 'config/*'], s3Write: ['gold/*'], ddbWrite: ['unit_status', ...STATUS], neptune: 'write',
    },
  },
  {
    dir: 'mark_ready', stack: 'ingest', layers: ING, timeoutS: 180, memoryMb: 512, env: ['aoss'],
    description: 'Wait for index count = unit count, mark lane SUCCEEDED',
    access: {
      s3Read: ['gold/*', 'config/*'], s3Write: ['gold/*'], ddbRead: ['unit_status'], ddbWrite: STATUS, aoss: 'read',
    },
  },
  {
    dir: 'status_stream', stack: 'ingest', layers: ['core'], timeoutS: 60, memoryMb: 512,
    description: 'DocStatus stream -> package lane rollup and counters',
    access: { ddbRead: ['doc_status', 'documents'], ddbWrite: ['packages'] },
  },

  // ---------------- C: knowledge admin (rp-knowledge, VPC) ----------------
  {
    dir: 'neptune_load', stack: 'knowledge', layers: AGT, vpc: true, timeoutS: 900, memoryMb: 1024,
    env: ['neptune'],
    description: 'Load TBox N-Quads for an ontology version (seed)',
    access: { s3Read: ['config/*'], neptune: 'write' },
  },
  {
    dir: 'graph_admin', stack: 'knowledge', layers: AGT, vpc: true, timeoutS: 300, memoryMb: 512,
    env: ['neptune'],
    description: 'Drop ABox graphs (reset, package delete), graph stats',
    access: { s3Read: ['config/*'], neptune: 'write' },
  },
  {
    dir: 'vocab_export', stack: 'knowledge', layers: AGT, vpc: true, timeoutS: 300, memoryMb: 512,
    env: ['neptune'],
    description: 'Export the vocab snapshot from Neptune to config/ontology/{ver}/vocab.json',
    access: { s3Read: ['config/*'], s3Write: ['config/ontology/*'], neptune: 'read' },
  },

  // ---------------- C: agent plane (rp-agent) ----------------
  {
    dir: 'tools/search_units', liveAlias: true, stack: 'agent', layers: AGT, timeoutS: 30, memoryMb: 1024, env: ['aoss'],
    description: 'MCP tool search_units@1: filtered hybrid search with client-side fusion',
    access: {
      s3Read: ['config/*'], ddbRead: ['packages', 'doc_acl', 'unit_status'], bedrock: true, aoss: 'read',
    },
  },
  {
    dir: 'tools/graph_hop', liveAlias: true, stack: 'agent', layers: AGT, vpc: true, timeoutS: 30, memoryMb: 1024,
    env: ['neptune'],
    description: 'MCP tool graph_hop@1: approved SPARQL templates',
    access: { s3Read: ['config/*'], ddbRead: ['packages', 'doc_acl'], neptune: 'read' },
  },
  {
    dir: 'tools/get_region', liveAlias: true, stack: 'agent', layers: AGT, timeoutS: 30, memoryMb: 512,
    description: 'MCP tool get_region@1: page + bbox for a unit/field',
    access: { s3Read: ['gold/*', 'config/*'], ddbRead: ['packages', 'doc_acl', 'unit_status', 'documents'] },
  },
  {
    dir: 'tools/doc_status', stack: 'agent', layers: AGT, timeoutS: 30, memoryMb: 512,
    description: 'MCP tool doc_status@1: lane readiness per document',
    access: { s3Read: ['config/*'], ddbRead: ['packages', 'documents', 'doc_status', 'doc_acl'] },
  },
  {
    dir: 'interceptor', stack: 'agent', layers: AGT, timeoutS: 15, memoryMb: 512, env: ['userPool'],
    description: 'AgentCore Gateway REQUEST interceptor: JWT re-validation, identity injection, access log',
    // one rp-access-log row per tools/call decision (SC-7, W-13)
    access: { ddbPut: ['access_log'] },
  },
  {
    dir: 'get_region_http', liveAlias: true, stack: 'agent', layers: AGT, timeoutS: 15, memoryMb: 512, env: ['cors'],
    description: 'GET /region/{unitId}: DocAcl check, presigned Gold page image',
    access: { s3Read: ['gold/*', 'config/*'], ddbRead: ['packages', 'doc_acl', 'unit_status', 'documents'] },
  },
  {
    dir: 'upload_url', stack: 'agent', layers: ING, timeoutS: 15, memoryMb: 512, env: ['cors'],
    description: 'POST /packages/{id}/upload-url: presigned PUT to bronze/{sub}/{pkg}/',
    access: {
      s3Write: ['bronze/*'], ddbRead: ['packages'], ddbWrite: ['packages', 'documents', 'doc_acl'],
    },
  },

  // ---------------- D: REST API (rp-agent) ----------------
  {
    dir: 'api', stack: 'agent', layers: API, timeoutS: 60, memoryMb: 1024,
    env: ['aoss', 'stateMachine', 'graphAdmin', 'graphHop', 'publicMetrics', 'coaControl', 'cors'],
    description: 'REST routes: packages, status, documents, stages, units, lineage, artifacts, pipelines, runs, '
      + 'v2 inspection, access log, governance, review decisions, eval, cost, ontology, graph, COA environment',
    access: {
      // Only the tiers the routes serve (artifacts, overlays, runs, pipelines); never deploy/* (Runtime code).
      s3Read: ['bronze/*', 'silver/*', 'gold/*', 'quarantine/*', 'config/*', 'runs/*', 'eval/*'],
      s3Delete: ['bronze/*', 'silver/*', 'gold/*', 'quarantine/*'],
      ddbRead: ['packages', 'documents', 'doc_status', 'unit_status', 'doc_acl', 'runs', 'pipeline_versions',
        'review_queue', 'access_log'],
      ddbWrite: ['packages', 'documents', 'doc_status', 'unit_status', 'doc_acl', 'review_queue'],
      // aoss 'delete' includes aoss:ReadDocument, which GET /inspect/filters needs for _count
      // rp-graph-hop:live only (the alias the Gateway targets): the rp-api -> rp-graph-hop trust edge (docs/architecture.md)
      aoss: 'delete', sfnStart: true, invoke: ['rp-graph-admin', 'rp-graph-hop:live'],
    },
  },
  {
    // v2: X-ray of a v1.0 page. pdfium + pypdf + pillow from the ingest layer. Inspection only:
    // it writes nothing but its own cache and the masked render under gold/{pkg}/{doc}/1.0/inspect/.
    dir: 'xray', stack: 'agent', layers: ING, timeoutS: 60, memoryMb: 2048, env: ['cors'],
    description: 'GET /documents/{docId}/lanes/1.0/xray: v1.0 chunk geometry and reading order (inspection only)',
    access: {
      s3Read: ['bronze/*', 'silver/*', 'gold/*', 'config/*', 'runs/*'],
      s3Write: ['gold/*/1.0/inspect/*'],
      ddbRead: ['packages', 'doc_acl', 'documents'],
    },
  },
  {
    dir: 'api/ask', stack: 'agent', layers: API, timeoutS: 60, memoryMb: 512, name: 'rp-api-ask',
    env: ['cors', 'askTimeout'],
    description: 'POST /ask: proxy to the AgentCore Runtime with the caller access token',
    // ddbPut access_log: one ask-gate row per refused ask (SC-7: the other user's evidence is the gate's refusal)
    access: { ddbRead: ['packages'], ddbPut: ['access_log'], ssmRead: ['/rp/agent/*'] },
  },

  // ---------------- COA environment lifecycle (rp-coa-control, opt-in: context rp:coaControl) ----------------
  {
    // The state row, CodeBuild, CloudWatch and CloudFormation grants are added in coa-control-stack.ts (they name
    // resources of that stack and of COA, not of rp-base).
    dir: 'coa_control', stack: 'coaControl', layers: ['core'], timeoutS: 60, memoryMb: 512,
    env: ['coaEnv'],
    description: 'COA environment: /coa/environment routes (via rp-api), rp-coa-lifecycle events, idle watcher',
    access: { s3Read: ['config/*'], ddbPut: ['access_log'] },
  },
];

/** Alias name scripts/prewarm.sh puts provisioned concurrency on (RP_PC_QUALIFIER default). */
export const LIVE_ALIAS = 'live';

export function specName(spec: FunctionSpec): string {
  if (spec.name) return spec.name;
  const leaf = spec.dir.split('/').pop() as string;
  return `rp-${leaf.replace(/_/g, '-')}`;
}

export function specHandler(spec: FunctionSpec): string {
  return spec.handler ?? `lambdas.${spec.dir.replace(/\//g, '.')}.handler.handler`;
}

export function functionsFor(stack: StackKey): FunctionSpec[] {
  return FUNCTIONS.filter((f) => f.stack === stack);
}

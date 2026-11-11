/**
 * Provenant REST + SSE contract for the UI. Mirrors packages/evidence-core/evidence_core/models/*.py
 * (Python wins on conflict). camelCase on the wire. Routes and access rules: docs/architecture.md.
 * The UI imports it as `@contracts/api`.
 */

// ---------- primitives ----------

export type PipelineVersion = "1.0" | "2.3.1" | (string & {});
export type IsoTime = string; // "2026-11-16T17:04:05.123Z"

/** Canonical bbox: PDF points, top-left origin, 1-based page. Scale by imageWidthPx / pageWidth; no flip. */
export interface BBox {
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export type StageId =
  | "register"
  | "route"
  | "extract"
  | "classify"
  | "units"
  | "pii_gate"
  | "promote"
  | "index"
  | "graph"
  | "ready";
export const STAGE_ORDER: StageId[] = [
  "register",
  "route",
  "extract",
  "classify",
  "units",
  "pii_gate",
  "promote",
  "index",
  "graph",
  "ready",
];
export type StageGroup = "upload" | "extract" | "units" | "gate" | "index";
/** Pipeline board columns -> stage groups. */
export const BOARD_COLUMNS: { title: string; group: StageGroup }[] = [
  { title: "extract", group: "extract" },
  { title: "evidence units", group: "units" },
  { title: "PII gate", group: "gate" },
  { title: "index and graph", group: "index" },
];
export type StageState = "PENDING" | "QUEUED" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED";
export type StageEventType = "QUEUED" | "STARTED" | "HEARTBEAT" | "SUCCEEDED" | "FAILED" | "SKIPPED";
export type LaneState = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "SKIPPED";
export type Engine =
  | "text-layer"
  | "docling"
  | "textract"
  | "pypdf"
  | "skos-indicators"
  | "nova"
  | "eu-builder"
  | "fixed-chunker"
  | "comprehend"
  | "pdfium"
  | "titan-v2+aoss"
  | "neptune"
  | "lambda";
export type UnitType = "chunk" | "table_panel" | "section_text" | "figure_panel" | "field_group" | "list_block";
export type PiiFlag = "none" | "masked" | "held";
export type CanonicalRole =
  | "title"
  | "section_header"
  | "text"
  | "list_item"
  | "table"
  | "table_cell"
  | "key_value"
  | "caption"
  | "footnote"
  | "picture"
  | "formula"
  | "page_header"
  | "page_footer"
  | "unit_label"
  | "marker_link";
export type ArtifactKind =
  | "source_pdf"
  | "manifest"
  | "route"
  | "text_layer"
  | "docling_json"
  | "textract_json"
  | "classification"
  | "units_jsonl"
  | "build_report"
  | "pii_decisions"
  | "gold_unit"
  | "gold_units_jsonl"
  | "page_image"
  | "index_receipt"
  | "abox_nquads"
  | "graph_receipt"
  | "ready_receipt"
  | "config"
  | "run";

// ---------- errors / identity ----------

export interface ApiError {
  error: {
    code: "bad_request" | "unauthorized" | "forbidden" | "not_found" | "conflict" | "not_ready" | "internal";
    message: string;
    requestId?: string;
  };
}

export interface Me {
  sub: string;
  username: string;
  email?: string;
  groups: string[];
  principals: string[]; // ["user:<sub>", "group:<g>"]
}

/** Written by the web stack to /config.json next to index.html. */
/** /config.json, written next to index.html by the rp-web stack (BucketDeployment). */
export interface RuntimeConfig {
  region: string;
  /** "/api" (same origin through CloudFront, which strips the prefix) or a full stage URL ending in /v1. */
  apiUrl: string;
  userPoolId: string;
  userPoolClientId: string;
  /** API Gateway stage URL (dev origins only; CORS allows rp:devOrigins). */
  directApiUrl?: string;
  webUrl?: string;
  pipelineVersions?: string[];
  demoUsers?: boolean;
}

// ---------- artifacts & status records ----------

export interface ArtifactRef {
  key: string;
  kind: ArtifactKind;
  contentType: string;
  sizeBytes?: number;
  sha256?: string;
  versionId?: string;
  page?: number;
  count?: number;
  note?: string;
}

export interface StageError {
  type: string;
  message: string;
  cause?: string;
  retryable: boolean;
  at: IsoTime;
}

export interface StageProgress {
  done: number;
  total?: number;
  unit: string;
}

export interface StageVersions {
  pipelineVersion: string;
  configSha: string;
  configVersionId?: string;
  ontologyVersion?: string;
  engineVersion?: string;
  modelId?: string;
  promptSha?: string;
  codeSha?: string;
}

export interface StageConfigSnapshot {
  paths: string[];
  values: Record<string, unknown>;
  sha: string;
}

export interface StageRecord {
  docId: string;
  packageId: string;
  pipelineVersion: string;
  stageId: StageId;
  order: number;
  group: StageGroup;
  title: string;
  state: StageState;
  attempt: number;
  engine?: Engine;
  services: string[];
  functionName?: string;
  sfnState?: string;
  executionArn?: string;
  queuedAt?: IsoTime;
  startedAt?: IsoTime;
  lastHeartbeatAt?: IsoTime;
  heartbeatCount: number;
  endedAt?: IsoTime;
  durationMs?: number;
  progress?: StageProgress;
  message?: string;
  headline?: string;
  inputs: ArtifactRef[];
  artifacts: ArtifactRef[];
  input?: Record<string, unknown>;
  output?: Record<string, unknown>; // StageOutputs[stageId]
  metrics: Record<string, number>;
  config?: StageConfigSnapshot;
  versions?: StageVersions;
  skipReason?: string;
  error?: StageError;
  updatedAt: IsoTime;
}

export interface StageEvent {
  docId: string;
  packageId: string;
  pipelineVersion: string;
  stageId: StageId;
  event: StageEventType;
  at: IsoTime;
  attempt: number;
  message?: string;
  progress?: StageProgress;
  durationMs?: number;
  error?: StageError;
  /** The execution that wrote the event. */
  executionArn?: string;
}

export interface LaneRecord {
  docId: string;
  packageId: string;
  pipelineVersion: string;
  state: LaneState;
  currentStage?: StageId;
  executionArn?: string;
  startedAt?: IsoTime;
  endedAt?: IsoTime;
  durationMs?: number;
  configSha?: string;
  configVersionId?: string;
  promptSha?: string;
  ontologyVersion?: string;
  units?: number;
  held?: number;
  indexed?: number;
  triples?: number;
  skipReason?: string;
  error?: StageError;
  updatedAt: IsoTime;
}

/** Typed StageRecord.output per stage (models/stage_outputs.py). */
export interface PageInfo {
  page: number;
  width: number;
  height: number;
  textChars: number;
  route?: "digital" | "scan";
}
export interface StageOutputs {
  register: {
    pageCount: number;
    pages: PageInfo[];
    sha256: string;
    sizeBytes: number;
    contentType: string;
    sourceVersionId?: string;
    validation: "ok" | "quarantined" | "skipped";
    quarantineReason?: string;
  };
  route: { pageCount: number; digitalPages: number[]; scanPages: number[]; thresholdChars: number; pages: PageInfo[] };
  extract: {
    engines: ("text-layer" | "docling" | "textract")[];
    pagesByEngine: Record<string, number[]>;
    blocks: number;
    emptyPages: number[];
  };
  classify: {
    docType: string;
    label: string;
    scores: Record<string, number>;
    margin: number;
    tieBreakUsed: boolean;
    tieBreakModel?: string;
  };
  units: {
    units: number;
    unitTypes: Record<string, number>;
    fields: number;
    reviewItems: number;
    phaseCounts: Record<string, number>;
    meanChars: number;
  };
  pii_gate: { units: number; passed: number; masked: number; held: number; heldUnitIds: string[] };
  promote: { goldUnits: number; pagesRendered: number; heldRegions: number };
  index: { index: string; indexed: number; deletedPrevious: number; embedModel: string; dimensions: number };
  graph: { graph: string; triples: number; facts: number; deletedPrevious: number };
  ready: { index: string; expected: number; indexed: number; waitedMs: number };
}

// ---------- stage registry (GET /pipelines/{pv}.stages, contracts/generated/stages.json) ----------

export interface ResolvedStage {
  id: StageId;
  order: number;
  group: StageGroup;
  title: string;
  enabled: boolean;
  skipReason?: string;
  engines: Engine[];
  services: string[];
  functions: string[];
  sfnStates: string[];
  produces: ArtifactKind[];
  consumes: ArtifactKind[];
  configPaths: string[];
  description: string;
}

// ---------- packages ----------

export interface LaneDocCounts {
  total: number;
  pending: number;
  running: number;
  succeeded: number;
  failed: number;
  skipped: number;
}

export interface LaneRollup {
  state: "EMPTY" | "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED" | "PARTIAL";
  ready: boolean;
  indexed: number;
  expected: number;
  docs: LaneDocCounts;
}

export interface Counters {
  units: number;
  held: number;
  indexed: number;
  triples: number;
}

export interface Package {
  packageId: string;
  name: string;
  description?: string;
  ownerSub: string;
  createdAt: IsoTime;
  updatedAt?: IsoTime;
  allowedPrincipals: string[];
  pipelineVersions: PipelineVersion[];
  docCount: number;
  lanes: Record<string, LaneRollup>;
  counters: Record<string, Counters>;
}

export interface PackageCreate {
  name: string;
  description?: string;
  shareWithGroups?: string[];
  pipelineVersions?: PipelineVersion[];
}

export interface PackageList {
  items: Package[];
}

export type UploadState = "AWAITING_UPLOAD" | "IN_BRONZE" | "QUARANTINED";

export interface DocumentItem {
  packageId: string;
  docId: string;
  fileName: string;
  bronzeKey: string;
  contentType: string;
  sizeBytes?: number;
  sha256?: string;
  sourceVersionId?: string;
  pageCount?: number;
  docType?: string;
  uploadedBy: string;
  uploadState: UploadState;
  uploadUrlIssuedAt: IsoTime;
  inBronzeAt?: IsoTime;
  quarantineReason?: string;
  quarantineKey?: string;
}

export interface PackageDetail {
  package: Package;
  documents: DocumentItem[];
  canWrite: boolean;
}

export interface UploadUrlRequest {
  fileName: string; // ^[A-Za-z0-9._ -]+\.pdf$
  contentType: "application/pdf";
  sizeBytes: number; // <= 25 MB
}

export interface UploadUrlResponse {
  docId: string;
  key: string;
  url: string;
  method: "PUT";
  headers: Record<string, string>;
  expiresIn: number;
}

export interface DeletePackageResponse {
  packageId: string;
  deleted: Record<string, number>;
}

// ---------- status (GET /packages/{id}/status, polled every 1 s) ----------

export interface StageCell {
  stageId: StageId;
  group: StageGroup;
  order: number;
  title: string;
  state: StageState;
  engine?: Engine;
  headline?: string;
  attempt: number;
  queuedAt?: IsoTime;
  startedAt?: IsoTime;
  endedAt?: IsoTime;
  durationMs?: number;
  lastHeartbeatAt?: IsoTime;
  heartbeatCount: number;
  progress?: StageProgress;
  metrics: Record<string, number>;
  skipReason?: string;
  error?: StageError;
}

export interface LaneView {
  state: LaneState;
  currentStage?: StageId;
  executionArn?: string;
  startedAt?: IsoTime;
  endedAt?: IsoTime;
  durationMs?: number;
  configSha?: string;
  stages: StageCell[];
}

export interface DocStatusView {
  docId: string;
  fileName: string;
  sizeBytes?: number;
  pageCount?: number;
  docType?: string;
  uploadState: UploadState;
  bronzeKey: string;
  inBronzeAt?: IsoTime;
  lanes: Record<string, LaneView>;
}

export interface PackageStatus {
  packageId: string;
  updatedAt: IsoTime;
  serverTime: IsoTime;
  lanes: Record<string, LaneRollup>;
  counters: Record<string, Counters>;
  objectsInBronze: number;
  askReady: boolean;
  docs: DocStatusView[];
}

// ---------- documents & stage inspection ----------

export interface LaneDetail {
  lane: LaneRecord;
  stages: StageRecord[];
}

export interface DocumentDetail {
  document: DocumentItem;
  lanes: Record<string, LaneDetail>;
  canWrite: boolean;
}

export interface StageDetail {
  record: StageRecord;
  definition: ResolvedStage;
  /** The current run's events only (same executionArn as the record). */
  events: StageEvent[];
  previousStage?: StageId;
  nextStage?: StageId;
  /** This stage in earlier executions (reprocess, re-upload), newest first. */
  history: StageRecord[];
}

/** One box drawn on a page in the stage inspector. Never carries document text. */
export interface OverlayBox {
  kind: "block" | "member" | "unit" | "field" | "held";
  bbox: BBox;
  role?: string;
  label?: string;
  ref?: string;
  unitId?: string;
  phase?: string;
  allocScore?: number;
  piiFlag?: string;
}
/** GET /documents/{docId}/lanes/{pv}/stages/{stageId}/overlay?page= (computed from whole artifacts server-side). */
export interface StageOverlay {
  docId: string;
  pipelineVersion: string;
  stageId: StageId;
  page: number;
  pageCount: number;
  pageWidth?: number;
  pageHeight?: number;
  frame: "pdf-points-top-left";
  pageImageUrl?: string;
  sources: string[];
  boxes: OverlayBox[];
  note?: string;
}
export interface StageEventPage {
  items: StageEvent[];
  nextCursor?: string;
}

export interface ReprocessRequest {
  pipelineVersion: PipelineVersion;
}

export interface ReprocessResponse {
  docId: string;
  pipelineVersion: string;
  executionArn: string;
}

// ---------- units ----------

export interface BlockRef {
  engine: Engine;
  artifactKey: string;
  pointer: string;
  engineLabel?: string;
}

export interface Member {
  memberId: string;
  role: CanonicalRole;
  text?: string;
  bbox?: BBox;
  blockRef?: BlockRef;
  phase?: "A" | "B" | "C" | "marker";
  allocScore?: number;
}

export interface FieldValue {
  fieldId: string;
  label: string;
  value: string;
  valueNumber?: string;
  currency?: string;
  concept?: string;
  period?: string;
  bbox?: BBox;
  source?: string;
  confidence?: number;
  blockRefs: BlockRef[];
}

export interface UnitProvenance {
  engine: Engine;
  engineVersion?: string;
  ontologyVersion?: string;
  bboxFrame: "pdf-points-top-left";
  sourceKey: string;
  sourceVersionId?: string;
  sourceSha: string;
  extractArtifacts: string[];
  configSha: string;
  build: Record<string, unknown>;
}

export interface Unit {
  unitId: string;
  docId: string;
  packageId: string;
  pipelineVersion: string;
  unitType: UnitType;
  label: string;
  chunkLabel?: string;
  fileName: string;
  docType?: string;
  breadcrumb: string[];
  text: string;
  pages: number[];
  pageWidth?: number;
  pageHeight?: number;
  bboxes: BBox[];
  members: Member[];
  fields: FieldValue[];
  fieldConcepts: string[];
  period?: string;
  piiFlag: PiiFlag;
  allowedPrincipals: string[];
  confidence?: number;
  chars: number;
  tokens?: number;
  provenance: UnitProvenance;
}

export interface UnitStatusItem {
  unitId: string;
  docId: string;
  packageId: string;
  pipelineVersion: string;
  docLane: string;
  packageLane: string;
  unitType: UnitType;
  label: string;
  piiFlag: PiiFlag;
  held: boolean;
  silverKey: string;
  goldKey?: string;
  aossId?: string;
  indexedAt?: IsoTime;
  graphTriples?: number;
  reviewItems: number;
  updatedAt?: IsoTime;
}

export interface PiiEntityHit {
  type: string;
  score: number;
  begin: number;
  end: number;
  digits: number;
  masked: boolean;
}

export interface PiiDecision {
  unitId: string;
  decision: PiiFlag;
  rule: string;
  entities: PiiEntityHit[];
  heldBboxes: BBox[];
}

export interface UnitListItem {
  unitId: string;
  label: string;
  unitType: UnitType;
  pages: number[];
  piiFlag: PiiFlag;
  held: boolean;
  indexed: boolean;
  aossId?: string;
  fieldCount: number;
  chars: number;
  silverKey: string;
  goldKey?: string;
  reviewItems: number;
}

export interface UnitList {
  items: UnitListItem[];
}

export interface UnitView {
  unit: Unit;
  status?: UnitStatusItem;
  pii?: PiiDecision;
  held: boolean;
}

// ---------- lineage ----------

export interface StageRef {
  stageId: StageId;
  state: StageState;
  startedAt?: IsoTime;
  endedAt?: IsoTime;
  durationMs?: number;
  attempt: number;
  executionArn?: string;
}

export type LineageKind =
  | "claim"
  | "field"
  | "unit"
  | "blocks"
  | "region"
  | "page_image"
  | "source"
  | "index"
  | "graph";

export interface LineageStep {
  kind: LineageKind;
  title: string;
  summary: string;
  data: Record<string, unknown>; // keys per kind: see the stage that writes it
  bbox?: BBox;
  artifacts: ArtifactRef[];
  stage?: StageRef;
}

export interface Lineage {
  unitId: string;
  fieldId?: string;
  packageId: string;
  pipelineVersion: string;
  steps: LineageStep[];
  versions: Record<string, string | null>;
}

export interface ArtifactView {
  key: string;
  tier: string;
  contentType: string;
  sizeBytes: number;
  etag?: string;
  versionId?: string;
  lastModified?: IsoTime;
  preview?: string;
  previewTruncated: boolean;
  previewMasked: boolean;
  url?: string;
  expiresIn?: number;
}

// ---------- region (GET /region/{unitId}?field=) ----------

export interface RegionResult {
  unitId: string;
  fieldId?: string;
  docId: string;
  fileName: string;
  label: string;
  page: number;
  bbox: BBox;
  fieldBbox?: BBox;
  unitBboxes: BBox[];
  pageWidth: number;
  pageHeight: number;
  pageImageKey: string;
  heldRegions: BBox[];
  value?: string;
  fieldLabel?: string;
  concept?: string;
  unitType: UnitType;
  breadcrumb: string;
  engine?: string;
  piiFlag: PiiFlag;
  pipelineVersion: string;
  ontologyVersion?: string;
  url?: string;
  expiresIn?: number;
}

// ---------- pipelines ----------

export interface PipelineVersionItem {
  pipelineVersion: string;
  title: string;
  mode: "naive" | "evidence_units";
  configKey: string;
  configVersionId?: string;
  configSha: string;
  blockShas: Record<string, string>;
  promptSha: string;
  modelIds: Record<string, string>;
  ontologyVersion?: string;
  status: "active" | "retired";
  registeredAt: IsoTime;
}

export interface PipelineList {
  items: PipelineVersionItem[];
}

export interface PipelineDetail {
  item: PipelineVersionItem;
  yaml: string;
  resolved: Record<string, unknown>;
  stages: ResolvedStage[];
  promptText: string;
}

export interface DiffLine {
  op: " " | "-" | "+";
  aNo?: number;
  bNo?: number;
  text: string;
  block?: "preprocessing" | "retrieval" | "generation" | "agent";
}

export interface BlockCompare {
  a: string;
  b: string;
  equal: boolean;
}

export interface ConfigDiff {
  a: string;
  b: string;
  aConfigSha: string;
  bConfigSha: string;
  changedBlocks: string[];
  identicalBlocks: string[];
  blocks: Record<string, BlockCompare>;
  promptSha: BlockCompare;
  modelId: BlockCompare;
  lines: DiffLine[];
  summary: string;
}

// ---------- ask (POST /ask, text/event-stream; each frame "data: <AskEvent JSON>\n\n") ----------

export interface AskRequest {
  question: string;
  packageId: string;
  pipelineVersion: PipelineVersion;
  sessionId?: string;
  toolsDisabled?: string[];
  /** swaps the answer model for this run only (lane config unchanged): frontier = MODEL_FRONTIER, compact = MODEL_COMPACT */
  generationProfile?: GenerationProfile;
}

export type GenerationProfile = "default" | "frontier" | "compact";

export interface UnitScores {
  denseRaw?: number;
  bm25Raw?: number;
  denseNorm: number;
  bm25Norm: number;
  fused: number;
}

export interface FieldBrief {
  fieldId: string;
  label: string;
  value: string;
  valueNumber?: string;
  concept?: string;
  period?: string;
  page?: number;
  bbox?: BBox;
}

export interface RetrievedUnit {
  rank: number;
  unitId: string;
  docId: string;
  fileName: string;
  unitType: UnitType;
  label: string;
  chunkLabel?: string;
  docType?: string;
  breadcrumb: string;
  text: string;
  pages: number[];
  pageWidth?: number;
  pageHeight?: number;
  bboxes: BBox[];
  fields: FieldBrief[];
  period?: string;
  piiFlag: PiiFlag;
  resolvable: boolean;
  scores: UnitScores;
}

export interface Claim {
  text: string;
  value?: string;
  unitId: string;
  fieldId?: string;
  docId?: string;
  fileName?: string;
  label?: string;
  page?: number;
  bbox?: BBox;
  unitBbox?: BBox;
  resolvable: boolean;
  chunkLabel?: string;
  reason?: string;
}

export interface AnswerPayload {
  answer: string;
  total?: string;
  currency?: string;
  arithmetic?: string;
  claims: Claim[];
  caveats: string[];
  citationRetries: number;
  uncitedNumbers: string[];
}

export interface Check {
  qid: string;
  expected: string;
  actual?: string;
  match: boolean;
}

export interface RunFooter {
  runId: string;
  packageId: string;
  pipelineVersion: string;
  configSha: string;
  blockShas: Record<string, string>;
  promptSha: string;
  modelId: string;
  corpusHash: string;
  memoryRecordsInjected: number;
  latencyMs: number;
  usage: Record<string, number>;
  startedAt: IsoTime;
  runtimeSessionId?: string;
}

export type ToolName = "search_units" | "graph_hop" | "get_region" | "doc_status";

export interface RunStartEvent {
  type: "run_start";
  runId: string;
  packageId: string;
  pipelineVersion: string;
  question: string;
  modelId: string;
  configSha: string;
  promptSha: string;
  tools: string[];
  t: number;
}
export interface ToolStartEvent {
  type: "tool_start";
  callId: string;
  tool: ToolName | string;
  input: Record<string, unknown>;
  t: number;
}
export interface ToolEndEvent {
  type: "tool_end";
  callId: string;
  tool: ToolName | string;
  ms: number;
  status: "ok" | "error" | "denied";
  summary: string;
  units: string[];
  scores: { unitId: string; denseNorm: number; bm25Norm: number; fused: number }[];
  retrieved: RetrievedUnit[];
  rows: Record<string, unknown>[];
  total?: string;
  template?: string;
  error?: string;
  t: number;
}
export interface TextEvent {
  type: "text";
  delta: string;
}
export interface AnswerEvent extends AnswerPayload {
  type: "answer";
}
export interface CheckEvent extends Check {
  type: "check";
}
export interface RunEvent extends RunFooter {
  type: "run";
  /** Echoed so a replayed stream is self-contained. */
  question?: string;
}
export interface ErrorEvent {
  type: "error";
  code: "forbidden" | "not_ready" | "agent_error" | "timeout" | (string & {});
  message: string;
  runId?: string;
}
export type AskEvent =
  | RunStartEvent
  | ToolStartEvent
  | ToolEndEvent
  | TextEvent
  | AnswerEvent
  | CheckEvent
  | RunEvent
  | ErrorEvent;

/** Parse SSE text (streamed or buffered) into events. Keeps the incomplete tail for the next chunk. */
export function parseSse(buffer: string): { events: AskEvent[]; rest: string } {
  const events: AskEvent[] = [];
  const frames = buffer.split("\n\n");
  const rest = frames.pop() ?? "";
  for (const frame of frames) {
    const data = frame
      .split("\n")
      .filter((l) => l.startsWith("data:"))
      .map((l) => l.slice(5).trimStart())
      .join("\n");
    if (data) events.push(JSON.parse(data) as AskEvent);
  }
  return { events, rest };
}

// ---------- runs & review ----------

export interface ToolCallRecord {
  callId: string;
  tool: string;
  input: Record<string, unknown>;
  injected: Record<string, unknown>;
  startedAt: IsoTime;
  ms: number;
  status: "ok" | "error" | "denied";
  output: Record<string, unknown>;
  error?: string;
}

export interface RunRecord {
  runId: string;
  createdAt: IsoTime;
  packageId: string;
  pipelineVersion: string;
  question: string;
  callerSub: string;
  groups: string[];
  modelId: string;
  promptSha: string;
  configSha: string;
  blockShas: Record<string, string>;
  corpusHash: string;
  memoryRecordsInjected: number;
  toolsEnabled: string[];
  toolCalls: ToolCallRecord[];
  retrieved: RetrievedUnit[];
  retrievedText: string[];
  answer?: AnswerPayload;
  check?: Check;
  usage: Record<string, number>;
  latencyMs: number;
  status: "ok" | "error";
  error?: string;
  runtimeSessionId?: string;
  s3Key: string;
  /** "default" on records logged before the field existed */
  generationProfile?: GenerationProfile;
  /** models.yaml key of modelId */
  modelKey?: string;
  /** sha256 of the tool specs sent to Converse; absent on records logged before 2026-11-11 (unverified, never identical) */
  toolSpecSha?: string;
}

export interface RunSummary {
  runId: string;
  createdAt: IsoTime;
  packageId: string;
  pipelineVersion: string;
  question: string;
  callerSub: string;
  modelId: string;
  promptSha: string;
  configSha: string;
  corpusHash: string;
  total?: string;
  checkMatch?: boolean;
  qid?: string;
  claims: number;
  resolvableClaims: number;
  toolSequence: string[];
  latencyMs: number;
  status: "ok" | "error";
  memoryRecordsInjected: number;
  s3Key: string;
  generationProfile?: GenerationProfile;
}

export interface RunPage {
  items: RunSummary[];
  nextCursor?: string;
}

export interface ReviewItem {
  packageId: string;
  reviewKey: string;
  docId: string;
  pipelineVersion: string;
  unitId: string;
  fieldId: string;
  label: string;
  value: string;
  confidence: number;
  threshold: number;
  engine: Engine;
  bbox?: BBox;
  reason: string;
  /** GET /review: the extract artifact's model version; a `LOCAL-` version is the dev stand-in (no confidences) */
  engineModelVersion?: string;
  state: "OPEN" | "ACCEPTED" | "CORRECTED" | "REJECTED";
  createdAt: IsoTime;
  /** the latest decision (W-14); a CORRECT keeps `value` and records the reviewer's value here */
  decision?: ReviewDecision;
  history?: ReviewDecision[];
}

export interface ReviewList {
  items: ReviewItem[];
}

// ---------- v2 inspection routes (evidence_core/models/inspect.py) ----------

export type Provenance =
  | "measured" | "local-run" | "generator" | "derived" | "design-target" | "estimate" | "what-if" | "counterfactual"
  | "snapshot" | "replay";

/** One stored object or query a payload was read from (the receipts drawer lists these). */
export interface SourceRef {
  /** local: a dev-machine stand-in (pyoxigraph, the in-memory index), never an AWS service */
  kind: "s3" | "ddb" | "aoss" | "neptune" | "route" | "coa" | "local";
  key: string;
  versionId?: string | null;
  sha256?: string | null;
  at?: string | null;
}

/** How one layout block joined its unit (GET /documents/{docId}/lanes/{pv}/build). No block text. */
export interface BuildAllocation {
  blockPointer: string;
  memberId?: string | null;
  role: string;
  page?: number | null;
  bbox?: BBox | null;
  /** how it joined `unitId` */
  phase: "A" | "marker" | "B" | "C";
  /** phase B cosine against `candidateUnitId` (also when below tau) */
  score?: number | null;
  tau?: number | null;
  /** joined a seeded unit (pass A, marker link, pass B); false = residual section text (pass C) */
  allocated: boolean;
  unitId: string;
  candidateUnitId?: string | null;
  crossPage?: boolean | null;
}

/** A footnote attached to the seed that prints its marker (eu_builder hard rule). */
export interface MarkerLink {
  unitId: string;
  marker: string;
  footnoteMember?: string | null;
  footnotePage?: number | null;
  footnoteBbox?: BBox | null;
  /** the raised glyph in the seed, when that was the evidence */
  markerBox?: BBox | null;
  /** the field whose printed value (or row label) carries the marker */
  fieldId?: string | null;
  crossPage: boolean;
  /** "raised glyph" | "marker glyph in text" */
  evidence: string;
}

export interface BuildUnitSummary {
  unitId: string;
  unitType: string;
  members: number;
  fields: number;
  pages: number[];
  mergedTables: number;
}

export interface BuildInvariants {
  mode: "off" | "log" | "enforce";
  violations: Record<string, unknown>[];
  note?: string | null;
}

export interface BuildReplay {
  docId: string;
  packageId: string;
  pipelineVersion: string;
  builtAt?: string | null;
  /** Phase B embeddings ("local-hash-embedder" marks a build without Titan) */
  embedModel: string;
  tau: number;
  phaseB?: string | null;
  phaseCounts: Record<string, number>;
  mergedTables: number;
  derivedRoles: Record<string, number>;
  allocations: BuildAllocation[];
  markerLinks: MarkerLink[];
  units: BuildUnitSummary[];
  invariants?: BuildInvariants | null;
  sources: SourceRef[];
}

/** `_count` queries with the logged filter, and the sizes the tool's sub-queries could return. */
export interface FilterCounts {
  /** units of the run's package and lane the caller may read (`FilterInspection.scope`); the lane index is shared */
  indexTotal: number;
  afterFilter: number;
  bm25Matches: number;
  /** min(subQueryK, afterFilter): derived, not counted */
  denseTopN: number;
  /** min(subQueryK, bm25Matches): derived, not counted */
  bm25TopN: number;
  /** from the logged tool output */
  fusedK: number;
  /** the fields above that are computed from other counts, not counted ("denseTopN", "bm25TopN") */
  derived?: string[];
}

export interface FilterCounterfactual {
  label: "counterfactual · counts only";
  topKUnfiltered: number;
  survivingAfterAcl: number;
  otherPackagesInTopK: number;
}

/** GET /inspect/filters?runId=&callId= */
export interface FilterInspection {
  runId: string;
  callId: string;
  packageId: string;
  pipelineVersion: string;
  index: string;
  queryText: string;
  filter: Record<string, unknown>;
  k: number;
  subQueryK: number;
  weights: Record<string, number>;
  normalization: string;
  counts: FilterCounts;
  counterfactual?: FilterCounterfactual | null;
  /** "OpenSearch Serverless _count", or the local stand-in's label */
  countedBy: string;
  /** when the counts were taken (request time, not the run's time); null in the mock-world export */
  countedAt?: string | null;
  /** the first bar's query: the run's package and lane, the caller's principals */
  scope?: Record<string, unknown> | null;
  sources: SourceRef[];
}

export interface GraphNode {
  iri: string;
  kind: "class" | "individual" | "literal" | "unit" | "field" | "region";
  label: string;
  types: string[];
  graph: string;
}

export interface GraphEdge {
  s: string;
  p: string;
  o: string;
  label: string;
  graph: string;
}

/** GET /runs/{runId}/calls/{callId}/path (source "logged-rows"); GET /graph/neighbourhood ("neptune") */
export interface GraphNeighbourhood {
  focus: string;
  depth: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: boolean;
  template: string;
  sparql?: string | null;
  latencyMs?: number | null;
  source: "logged-rows" | "neptune";
  runId?: string | null;
  callId?: string | null;
}

export interface ShowcaseComposer {
  showcaseId: string;
  title: string;
  params: string[];
  reads: string[];
}

/** GET /showcases */
export interface ShowcaseCatalog {
  items: ShowcaseComposer[];
}

/** GET /showcases/{showcaseId}/payload */
export interface ShowcasePayload<T = Record<string, unknown>> {
  showcaseId: string;
  version: number;
  /** local-run: composed over the local pipeline snapshot (mock world), not on AWS */
  mode: "live" | "replay" | "local-run";
  packageId: string;
  runIds: string[];
  snapshotId?: string | null;
  recordedAt?: string | null;
  payloadSha: string;
  data: T;
  sources: SourceRef[];
  /** JSONPath -> provenance of the values under it */
  provenance: Record<string, Provenance>;
}

/** SC-3 payload data (`showcaseId` "sc-3"): the coupon, its raised marker, the footnote and the graph's step event. */
export interface CouponTermsData {
  security: string;
  docId?: string | null;
  fileName?: string | null;
  unitId?: string | null;
  couponField?: FieldValue | null;
  markerLink?: MarkerLink | null;
  /** the classify StageRecord's output, state, engine and duration */
  classify?: Record<string, unknown> | null;
  /** graph_hop coupon_terms_v1 as rp-graph-hop returned it (template, sparql, graphs, rows, latencyMs) */
  graph?: Record<string, unknown> | null;
  /** the template's row: newRate, effectiveFromYear, triggerRating, footnote region */
  stepEvent?: Record<string, string> | null;
  stepEventParse: "ok" | "failed" | "no-link";
  /** the template's bound triple patterns drawn as a graph (`api/graph_paths.py`, source "logged-rows") */
  path?: GraphNeighbourhood | null;
}

// ---------- route table (method, path template, request, response) ----------

export interface Routes {
  "GET /me": { req: void; res: Me };
  "GET /packages": { req: void; res: PackageList };
  "POST /packages": { req: PackageCreate; res: Package };
  "GET /packages/{packageId}": { req: void; res: PackageDetail };
  "DELETE /packages/{packageId}": { req: void; res: DeletePackageResponse };
  "POST /packages/{packageId}/upload-url": { req: UploadUrlRequest; res: UploadUrlResponse };
  "GET /packages/{packageId}/status": { req: void; res: PackageStatus };
  "GET /documents/{docId}": { req: void; res: DocumentDetail };
  "GET /documents/{docId}/lanes/{pv}/stages/{stageId}": { req: void; res: StageDetail };
  "GET /documents/{docId}/lanes/{pv}/events": { req: void; res: StageEventPage };
  "GET /documents/{docId}/lanes/{pv}/units": { req: void; res: UnitList };
  "POST /documents/{docId}/reprocess": { req: ReprocessRequest; res: ReprocessResponse };
  "GET /units/{unitId}": { req: void; res: UnitView };
  "GET /units/{unitId}/lineage": { req: void; res: Lineage };
  "GET /region/{unitId}": { req: void; res: RegionResult };
  "GET /artifacts": { req: void; res: ArtifactView };
  "GET /pipelines": { req: void; res: PipelineList };
  "GET /pipelines/{pv}": { req: void; res: PipelineDetail };
  "GET /pipelines/diff": { req: void; res: ConfigDiff };
  "POST /ask": { req: AskRequest; res: AskEvent[] };
  "GET /runs": { req: void; res: RunPage };
  "GET /runs/{runId}": { req: void; res: RunRecord };
  "GET /review": { req: void; res: ReviewList };
  // v2
  "GET /documents/{docId}/lanes/1.0/xray": { req: void; res: unknown /* usecases/provenant/ui/src/api/xray.ts XrayPage */ };
  "GET /documents/{docId}/lanes/{pv}/build": { req: void; res: BuildReplay };
  "GET /inspect/filters": { req: void; res: FilterInspection };
  "GET /runs/{runId}/calls/{callId}/path": { req: void; res: GraphNeighbourhood };
  "GET /showcases": { req: void; res: ShowcaseCatalog };
  "GET /showcases/{showcaseId}/payload": { req: void; res: ShowcasePayload };
}

// ---------------------------------------------------------------------------------------------------------------
// governance (models/governance.py), measurement (models/measure.py) and
// knowledge (models/knowledge.py). Python wins; tests/integration/test_contract_mirror.py checks the fields.

// GET /access/decisions?runId=|since=  (SC-7, W-13)
export interface AccessInjected {
  pipelineVersion: string;
  packageId: string;
  runId?: string;
}

export interface AccessDecision {
  at: IsoTime;
  runId: string;
  callId: string;
  tool: string;
  /** Gateway target ("search-units") */
  target: string;
  callerSub6: string;
  groups: string[];
  injected: AccessInjected;
  /** ask-gate: the ask gate (rp-api-ask or the Runtime entrypoint) refused the ask before any run started */
  injectionPoint: "interceptor" | "agent_hook" | "local-gateway" | "coa-control" | "ask-gate";
  /** run-record rows: the tool's decision from the call's logged result ("unknown": another error) */
  decision: "allow" | "deny" | "unknown";
  reason?: string;
  /** identity keys the model supplied and the gateway removed (names only) */
  strippedModelKeys: string[];
  /** argument names the model supplied (no values) */
  modelArgs: string[];
  /** access-log: written by the interceptor; run-record: rebuilt from the RunRecord's injected values */
  source: "access-log" | "run-record";
  /** ask-gate rows only (runId `gate#{packageId}`, callId = request id, tool `ask`) */
  gate?: AskGateRefusal;
  day?: string;
  ttl?: number;
}

/** What the ask gate refused: an ask that never started a run (no model call, no tool call). */
export interface AskGateRefusal {
  /** api: rp-api-ask (POST /ask); runtime: the AgentCore Runtime entrypoint (streaming path) */
  path: "api" | "runtime";
  /** API Gateway request id (api) or the Runtime session id (runtime) */
  requestId: string;
  /** the error code the caller got: forbidden | bad_request */
  code: string;
  /** sha256 of the normalised question (the text is never logged) */
  questionSha: string;
  questionId?: string;
}

export interface AccessDecisionPage {
  items: AccessDecision[];
  runId?: string;
  since?: string;
  nextCursor?: string;
  note?: string;
}

// GET /governance/definitions/{name}  (SC-8 governed definition card; COA slot off or snapshot)
export interface OntologyCheckResult {
  name: string;
  ok: boolean;
  /** what the check found (a label check: the concept IRI it resolved to) */
  detail?: string;
}

export interface OntologyCheckRecord {
  command: string;
  passed: number;
  total: number;
  checks: OntologyCheckResult[];
  inputsSha256: string;
  ranAt?: IsoTime;
}

export interface GitRecord {
  commit: string;
  author: string;
  committedAt: IsoTime;
}

export interface ReviewRecord {
  reviewer: string;
  url?: string;
  approvedAt?: IsoTime;
}

export interface DefinitionTerm {
  iri: string;
  name: string;
  label?: string;
  /** rdfs:comment, verbatim */
  description?: string;
  subClassOf: string[];
  altLabels: string[];
  operandConcepts: string[];
  computedBy?: string;
  graph?: string;
}

export interface TemplateRef {
  name: string;
  path: string;
  sha256: string;
}

export interface OntologySourceRef {
  ontologyId: string;
  version: string;
  nquads: string;
  source: string;
  sourceSha256: string;
}

/** One step of COA's Tier-1 resolver trace, as recorded ("t1.metric_match" → "metric_match"). */
export interface CoaTraceStep {
  step: string;
  status: string;
  /** cedar | sql-firewall | sql-engine */
  tool?: string;
  /** athena, on the execute step */
  engine?: string;
  durationMs?: number;
}

/** COA's metric as GET /namespaces/{ns}/metrics/{name} returned it. definedBy and effectiveFrom are set by COA's server. */
export interface CoaMetricCard {
  name: string;
  label?: string;
  description?: string;
  dialect: string;
  sql: string;
  unit?: string;
  returnType?: string;
  defaultTimeGrain?: string;
  synonyms: string[];
  ontologyConcepts: string[];
  sourceTable?: string;
  /** the creating principal; "<EMAIL>" when the snapshot redacted it */
  definedBy: string;
  definedByRedacted: boolean;
  effectiveFrom?: string;
}

export interface CoaOntologyAnchor {
  concept: string;
  ontologyId: string;
  /** user_uploaded: COA ingests uploaded Turtle without steward review */
  ontologyType: string;
  stewardReviewed: boolean;
  label: string;
  subClassOf: string[];
  operandConcepts: string[];
  classCount?: number;
  axiomCount?: number;
  /** the uploaded fin.ttl's sha256 equals the Git source's */
  matchesGitSource?: boolean;
}

export interface CoaCrossCheck {
  metric: string;
  tier: number;
  confidence: number;
  rationale?: string;
  /** resultRows[0][metric], verbatim */
  value: string;
  dimensions: Record<string, string>;
  trace: CoaTraceStep[];
  runs: number;
  identicalRuns: number;
  mcpValue?: string;
  mcpRuns: number;
}

export interface CoaHash {
  name: string;
  sha256: string;
  detail?: string;
}

export interface CoaLatency {
  call: string;
  n: number;
  medianMs: number;
  minMs: number;
  maxMs: number;
}

export interface CoaSnapshot {
  /** the day the recorded calls ran (YYYY-MM-DD) */
  capturedAt: string;
  coaVersion?: string;
  namespace?: string;
  region?: string;
  metric: CoaMetricCard;
  ontology: CoaOntologyAnchor;
  crossCheck: CoaCrossCheck;
  hashes: CoaHash[];
  latency: { measured: boolean; basis: string; calls: CoaLatency[] };
  access?: { rule: string; refused: { token: string; status: number; message?: string }[] };
  /** snapshot file -> sha256 */
  files: Record<string, string>;
}

export interface CoaSlot {
  /** off: the Git record only; snapshot: a recorded COA session, replayed (never live) */
  mode: "off" | "live" | "snapshot";
  state: "not-captured" | "captured";
  snapshotId?: string;
  snapshotAt?: IsoTime;
  note: string;
  snapshot?: CoaSnapshot;
}

export interface GovernedDefinitionView {
  source: "git" | "coa-live" | "coa-snapshot";
  definition: DefinitionTerm;
  ontology: OntologySourceRef;
  template?: TemplateRef;
  /** absent until the Turtle source has a commit */
  git?: GitRecord;
  /** absent until a reviewed pull request merged it */
  review?: ReviewRecord;
  check: OntologyCheckRecord;
  coa: CoaSlot;
  recordedAt: IsoTime;
  recordedBy: string;
}

// POST /review/{packageId}/items/{reviewKey}/decision  (W-14)
export interface ReviewDecisionRequest {
  action: "ACCEPT" | "CORRECT" | "REJECT";
  value?: string;
  note?: string;
  /** `decision.at` as the reviewer saw it; absent = no decision. Anything newer answers 409. */
  expectedDecisionAt?: string | null;
}

export interface ReviewDecision {
  action: "ACCEPT" | "CORRECT" | "REJECT";
  by: string;
  at: IsoTime;
  value?: string;
  note?: string;
}

// GET /eval/runs, GET /eval/runs/{evalRunId}  (SC-12, W-16). RP_PUBLIC_METRICS=off withholds `lanes`.
export interface EvalOutcome {
  qid: string;
  type: string;
  pipelineVersion: string;
  repeat: number;
  runId?: string;
  recallAt1: boolean;
  regionHitAt1?: boolean;
  answerCorrect?: boolean;
  citationValid?: boolean;
  top1UnitId?: string;
  top1Chars?: number;
  error?: string;
  /** the golden answer (display form): recorded by the harness, else joined by qid from config/golden.jsonl */
  expected?: string;
  /** where the golden answer is printed */
  gold?: EvalGold;
}

export interface EvalGold {
  docId: string;
  page?: number;
  bbox?: BBox;
}

export interface EvalRate {
  hits: number;
  n: number;
  rate?: number;
}

export interface EvalTypeMetrics {
  n: number;
  recallAt1: EvalRate;
  regionHitAt1: EvalRate;
  answerCorrect: EvalRate;
  citationValid: EvalRate;
  errors: number;
}

export interface EvalLane {
  pipelineVersion: string;
  configSha?: string;
  promptSha?: string;
  modelId?: string;
  meanUnitChars?: number;
  byType: Record<string, EvalTypeMetrics>;
}

export interface EvalRunSummary {
  evalRunId: string;
  createdAt: IsoTime;
  mode: string;
  modeNote: string;
  packageIds: string[];
  pipelineVersions: string[];
  questions: number;
  outcomes: number;
  goldenSha: string;
  corpusHash?: string;
}

export interface EvalRunList {
  items: EvalRunSummary[];
  published: boolean;
}

export interface EvalRunView {
  evalRunId: string;
  createdAt: IsoTime;
  mode: string;
  modeNote: string;
  caption: string;
  goldenPath: string;
  goldenSha: string;
  packageIds: string[];
  pipelineVersions: string[];
  corpusHash?: string;
  repeats: number;
  goldenTypes: Record<string, number>;
  outcomes: EvalOutcome[];
  /** only when published (RP_PUBLIC_METRICS=on) */
  lanes?: Record<string, EvalLane>;
  published: boolean;
  withheld: string[];
  meta: Record<string, unknown>;
  /** where the outcomes' `expected` came from ("eval result", "config/golden.jsonl (joined by qid)", or both) */
  expectedSource?: string;
}

// GET /cost?packageId=&groupBy=&lane=  (SC-13, W-17). Amounts are `estimate · measured usage × list price`;
// with RP_PUBLIC_METRICS=off every dollar field is absent.
export interface CostUsage {
  unit: "page" | "unit_100_chars" | "token_in" | "token_out" | "vcpu_s" | "gb_s" | "request" | "lambda_gb_s" | "ms";
  quantity: number;
  /** true: logged by the stage or the model call; false: derived (see `basis`) */
  measured: boolean;
  basis?: string;
}

export interface CostLine {
  packageId: string;
  docId?: string;
  docType?: string;
  pipelineVersion: string;
  stageId: string;
  service: string;
  usage: CostUsage;
  unitPriceUsd?: number;
  priceRef?: string;
  estimateUsd?: number;
  runId?: string;
  at?: IsoTime;
}

export interface StageDuration {
  docId: string;
  pipelineVersion: string;
  stageId: string;
  engine?: string;
  durationMs?: number;
  startedAt?: IsoTime;
}

export interface CostGroup {
  key: string;
  estimateUsd?: number;
  byService: Record<string, number>;
  lines: number;
  /** lines with no list price: `estimateUsd` covers the priced lines only */
  unpricedLines?: number;
}

export interface CostPerDocument {
  docId: string;
  docType?: string;
  lane: string;
  pages?: number;
  estimateUsd?: number;
}

export interface TieringRow {
  callType: string;
  modelKey: string;
  modelId: string;
  calls: number;
  tokensIn: number;
  tokensOut: number;
  estimateUsd?: number;
  source: string;
}

export interface CostPrices {
  source: string;
  asOf: string;
  refs: Record<string, string>;
}

export interface CostView {
  label: string;
  packageId: string;
  lane?: string;
  groupBy: string;
  published: boolean;
  withheld: string[];
  prices: CostPrices;
  groups: CostGroup[];
  perDocument: CostPerDocument[];
  tiering: TieringRow[];
  stages: StageDuration[];
  lines: CostLine[];
  notes: string[];
  /** more runs than the route reads: the newest are kept */
  truncated?: boolean;
}

// GET /ontology/classes?module=, GET /ontology/terms/{iri}  (W-09); GET /graph/neighbourhood -> GraphNeighbourhood
export interface TermLabel {
  value: string;
  scheme: "prefLabel" | "altLabel" | "notation" | "label";
  /** the triple in Turtle short form */
  triple: string;
}

export interface OntologyTerm {
  iri: string;
  curie: string;
  kind: "class" | "concept" | "object_property" | "datatype_property" | "annotation_property" | "individual";
  module: string;
  graph: string;
  label?: string;
  labels: TermLabel[];
  definition?: string;
  subClassOf: string[];
  broader: string[];
  domain: string[];
  range: string[];
  properties: Record<string, string[]>;
  /** packageId -> fields with this concept the caller can read (terms route only) */
  usedBy?: Record<string, number>;
}

export interface OntologyModule {
  module: string;
  graph: string;
  classes: number;
  concepts: number;
  properties: number;
}

export interface OntologyTermList {
  ontologyVersion: string;
  module?: string;
  modules: OntologyModule[];
  items: OntologyTerm[];
  source: string;
  sha256?: string;
}

// ---------- COA environment lifecycle (models/coa_env.py; Settings → COA environment, decision 0006) ----------

export type CoaEnvState = "off" | "starting" | "running" | "stopping" | "failed" | "partially_down";
export type CoaOperation = "up" | "down";
export type CoaPhaseId = "queued" | "provisioning" | "install" | "pre_build" | "build" | "post_build" | "done";
export type CoaPhaseStatus = "pending" | "running" | "succeeded" | "failed" | "skipped";
export type CoaActivitySourceId = "api_gateway" | "agentcore" | "provenant" | "keep_alive" | "became_running";

/** One step of the executor (CodeBuild rp-coa-lifecycle phases folded into seven). */
export interface CoaPhase {
  id: CoaPhaseId;
  label: string;
  status?: CoaPhaseStatus;
  startedAt?: IsoTime;
  endedAt?: IsoTime;
}

export interface CoaBuild {
  id: string; // CodeBuild build id, or "pending:<opId>" until StartBuild answered
  operation: CoaOperation;
  status?: string; // CodeBuild buildStatus
  startedAt: IsoTime;
  endedAt?: IsoTime;
  reason: string;
  requestedBy: string; // caller sub (6) | "idle-watcher" | "cli"
  attempt?: number;
  phases?: CoaPhase[];
  logUrl?: string;
  /** leftovers: the destroy finished but COA stacks remain (the state is failed) */
  result?: "ok" | "eni_pending" | "leftovers" | "failed";
}

export interface CoaSettings {
  autoTeardown: boolean;
  idleMinutes: number;
  minMinutes: number;
  maxMinutes: number;
  keepAliveMinutes: number;
  /** with auto tear-down on, the hard cap on a session (unless kept alive) */
  maxSessionHours?: number;
}

export interface CoaActivity {
  source: CoaActivitySourceId;
  at?: IsoTime;
  /** what was counted, e.g. "3 successful requests in its last active 5-minute period" */
  detail?: string;
}

export interface CoaError {
  message: string;
  at: IsoTime;
  phase?: string;
  logUrl?: string;
}

/** Read from COA's stack outputs: both change on every redeploy. */
export interface CoaEndpoints {
  consoleUrl?: string;
  restEndpoint?: string;
  readAt?: IsoTime;
}

export interface CoaEniRetry {
  retries: number;
  maxRetries: number;
  nextRetryAt?: IsoTime;
  windowEndsAt?: IsoTime;
}

export interface CoaHistoryEntry {
  at: IsoTime;
  fromState: CoaEnvState;
  toState: CoaEnvState;
  reason: string;
  by: string;
}

export interface CoaIdleView {
  lastActivityAt?: IsoTime;
  lastActivitySource?: CoaActivitySourceId;
  sources?: CoaActivity[];
  keepAliveUntil?: IsoTime;
  /** when the automatic tear-down starts if nothing happens (auto tear-down on) */
  teardownAt?: IsoTime;
  cooldownUntil?: IsoTime;
  checkedAt?: IsoTime;
  /** the hard cap (idle.maxSessionHours after it became running), pushed out by Keep alive */
  maxSessionUntil?: IsoTime;
  /** CloudWatch could not be read; past the idle threshold the watcher decides on the activity it last saw */
  activityError?: string;
  activityErrorSince?: IsoTime;
}

export interface CoaCostComponent {
  id: string;
  label: string;
  usdPerHour: number;
  source: string;
}

/** List-price estimate (config/prices.yaml coaEnvironment), never a billed amount; admins only. */
export interface CoaCostEstimate {
  provenance?: "estimate";
  usdPerHour: number;
  sessionHours?: number;
  sessionUsd?: number;
  components: CoaCostComponent[];
  asOf: string;
  source?: string;
  note?: string;
  /** what may still bill after the session clock stopped (partially down, failed tear-down) */
  residual?: string;
}

export interface CoaViewer {
  canOperate: boolean;
  adminGroup?: string;
}

export interface CoaLifecycleInfo {
  upMinutes: [number, number];
  downMinutes: [number, number];
  eniWindowHours: number;
  coaTag: string;
  stackPrefix: string;
  project: string;
}

/** GET /coa/environment (and the action routes, with `outcome`). */
export interface CoaEnvironmentView {
  deployed: boolean;
  /** "simulated": the mock world's state machine (no AWS) */
  mode?: "live" | "simulated";
  /** the clock the deadlines were computed against (simulated time in mock mode) */
  serverTime?: IsoTime;
  state?: CoaEnvState;
  /** in failed: which operation failed (after a failed tear-down, spin-up is refused) */
  failedOperation?: CoaOperation;
  version?: number;
  stateSince?: IsoTime;
  runningSince?: IsoTime;
  sessionStartedAt?: IsoTime;
  stoppedAt?: IsoTime;
  reason?: string;
  build?: CoaBuild;
  settings?: CoaSettings;
  idle?: CoaIdleView;
  cost?: CoaCostEstimate;
  endpoints?: CoaEndpoints;
  error?: CoaError;
  eni?: CoaEniRetry;
  history?: CoaHistoryEntry[];
  viewer: CoaViewer;
  lifecycle?: CoaLifecycleInfo;
  cli?: string[];
  detected?: boolean;
  outcome?: "started" | "noop" | "updated" | "stopped_and_started";
  note?: string;
  /** shown until an admin acts, e.g. COA adopted with auto tear-down off */
  notice?: string;
}

export interface CoaUpRequest {
  expectedVersion?: number;
  idempotencyKey?: string;
}

export interface CoaDownRequest extends CoaUpRequest {
  /** admin only: stop a running spin-up and tear down */
  force?: boolean;
}

export interface CoaSettingsRequest {
  autoTeardown: boolean;
  idleMinutes: number;
  expectedVersion?: number;
}

export interface CoaKeepAliveRequest {
  expectedVersion?: number;
}

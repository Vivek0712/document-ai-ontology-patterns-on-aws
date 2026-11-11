import { parseSse } from "@contracts/api";
import type {
  AccessDecisionPage,
  ApiError,
  ArtifactView,
  AskRequest,
  BuildReplay,
  CoaDownRequest,
  CoaEnvironmentView,
  CoaKeepAliveRequest,
  CoaSettingsRequest,
  CoaUpRequest,
  ConfigDiff,
  CostView,
  DeletePackageResponse,
  DocumentDetail,
  EvalRunList,
  EvalRunView,
  FilterInspection,
  GraphNeighbourhood,
  Lineage,
  Me,
  OntologyTerm,
  OntologyTermList,
  Package,
  PackageCreate,
  PackageDetail,
  PackageList,
  PackageStatus,
  PipelineDetail,
  PipelineList,
  RegionResult,
  ReprocessResponse,
  ReviewDecisionRequest,
  ReviewItem,
  ReviewList,
  RunPage,
  RunRecord,
  ShowcaseCatalog,
  ShowcasePayload,
  StageDetail,
  StageEventPage,
  StageId,
  StageOverlay,
  UnitList,
  UnitView,
  UploadUrlRequest,
  UploadUrlResponse,
} from "@contracts/api";
import type { GitGovernedDefinition } from "./governance";
import type { RunScore } from "./runs";
import type { XrayPage } from "./xray";
import { type AskHandlers, type RpApi, RpApiError, type UploadHandle, type UploadProgress } from "./types";

const enc = encodeURIComponent;
const XRAY_LANE = "1.0";
// stored eval runs are read for run scores at most once a minute
const EVAL_SCORES_TTL_MS = 60_000;

/** Returns a fresh Cognito access token (refreshing when needed). */
export type TokenProvider = () => Promise<string>;

function qs(params: Record<string, string | number | undefined>): string {
  const parts = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== "")
    .map(([k, v]) => `${enc(k)}=${enc(String(v))}`);
  return parts.length ? `?${parts.join("&")}` : "";
}

export async function toApiError(res: Response): Promise<RpApiError> {
  let body: Partial<ApiError> | undefined;
  try {
    body = (await res.json()) as Partial<ApiError>;
  } catch {
    body = undefined;
  }
  const code = body?.error?.code ?? statusToCode(res.status);
  const message = body?.error?.message ?? `${res.status} ${res.statusText}`;
  return new RpApiError(res.status, code, message, body?.error?.requestId);
}

function statusToCode(status: number): RpApiError["code"] {
  switch (status) {
    case 400:
      return "bad_request";
    case 401:
      return "unauthorized";
    case 403:
      return "forbidden";
    case 404:
      return "not_found";
    case 409:
      return "conflict";
    case 503:
      return "not_ready";
    default:
      return "internal";
  }
}

export class HttpApi implements RpApi {
  readonly mode = "http" as const;

  constructor(
    private readonly baseUrl: string,
    private readonly token: TokenProvider,
  ) {}

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { Authorization: `Bearer ${await this.token()}` };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let res: Response;
    try {
      res = await fetch(`${this.baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new RpApiError(0, "network", e instanceof Error ? e.message : "network error");
    }
    if (!res.ok) throw await toApiError(res);
    return (await res.json()) as T;
  }

  me = () => this.request<Me>("GET", "/me");
  listPackages = () => this.request<PackageList>("GET", "/packages");
  createPackage = (body: PackageCreate) => this.request<Package>("POST", "/packages", body);
  getPackage = (id: string) => this.request<PackageDetail>("GET", `/packages/${enc(id)}`);
  deletePackage = (id: string) => this.request<DeletePackageResponse>("DELETE", `/packages/${enc(id)}`);
  requestUploadUrl = (id: string, file: File) =>
    this.request<UploadUrlResponse>("POST", `/packages/${enc(id)}/upload-url`, {
      fileName: file.name,
      contentType: "application/pdf",
      sizeBytes: file.size,
    } satisfies UploadUrlRequest);

  async upload(packageId: string, file: File, onProgress: (p: UploadProgress) => void): Promise<UploadHandle> {
    const signed = await this.requestUploadUrl(packageId, file);
    const xhr = new XMLHttpRequest();
    const done = new Promise<void>((resolve, reject) => {
      xhr.open(signed.method, signed.url);
      for (const [k, v] of Object.entries(signed.headers)) xhr.setRequestHeader(k, v);
      xhr.upload.onprogress = (e) => onProgress({ loaded: e.loaded, total: e.lengthComputable ? e.total : file.size });
      xhr.onload = () =>
        xhr.status >= 200 && xhr.status < 300
          ? resolve()
          : reject(new RpApiError(xhr.status, "internal", `S3 PUT failed: ${xhr.status} ${xhr.responseText.slice(0, 200)}`));
      xhr.onerror = () => reject(new RpApiError(0, "network", "S3 PUT failed (network or CORS)"));
      xhr.onabort = () => reject(new RpApiError(0, "network", "upload cancelled"));
      xhr.send(file);
    });
    return { docId: signed.docId, key: signed.key, done, abort: () => xhr.abort() };
  }

  packageStatus = (id: string) => this.request<PackageStatus>("GET", `/packages/${enc(id)}/status`);
  getDocument = (docId: string) => this.request<DocumentDetail>("GET", `/documents/${enc(docId)}`);
  getStage = (docId: string, pv: string, stageId: StageId) =>
    this.request<StageDetail>("GET", `/documents/${enc(docId)}/lanes/${enc(pv)}/stages/${enc(stageId)}`);
  getOverlay = (docId: string, pv: string, stageId: StageId, page: number) =>
    this.request<StageOverlay>(
      "GET",
      `/documents/${enc(docId)}/lanes/${enc(pv)}/stages/${enc(stageId)}/overlay${qs({ page })}`,
    );
  getEvents = (docId: string, pv: string, cursor?: string, limit?: number) =>
    this.request<StageEventPage>("GET", `/documents/${enc(docId)}/lanes/${enc(pv)}/events${qs({ cursor, limit })}`);
  listUnits = (docId: string, pv: string) =>
    this.request<UnitList>("GET", `/documents/${enc(docId)}/lanes/${enc(pv)}/units`);
  reprocess = (docId: string, pv: string) =>
    this.request<ReprocessResponse>("POST", `/documents/${enc(docId)}/reprocess`, { pipelineVersion: pv });
  getUnit = (unitId: string) => this.request<UnitView>("GET", `/units/${enc(unitId)}`);
  getLineage = (unitId: string, fieldId?: string) =>
    this.request<Lineage>("GET", `/units/${enc(unitId)}/lineage${qs({ field: fieldId })}`);
  getRegion = (unitId: string, fieldId?: string) =>
    this.request<RegionResult>("GET", `/region/${enc(unitId)}${qs({ field: fieldId })}`);
  getArtifact = (key: string) => this.request<ArtifactView>("GET", `/artifacts${qs({ key })}`);
  listPipelines = () => this.request<PipelineList>("GET", "/pipelines");
  getPipeline = (pv: string) => this.request<PipelineDetail>("GET", `/pipelines/${enc(pv)}`);
  diffPipelines = (a: string, b: string) => this.request<ConfigDiff>("GET", `/pipelines/diff${qs({ a, b })}`);
  listRuns = (packageId: string, cursor?: string, limit?: number) =>
    this.request<RunPage>("GET", `/runs${qs({ packageId, cursor, limit })}`);
  getRun = (runId: string) => this.request<RunRecord>("GET", `/runs/${enc(runId)}`);
  listReview = (packageId: string) => this.request<ReviewList>("GET", `/review${qs({ packageId })}`);
  getGovernedDefinition = (name: string) => this.request<GitGovernedDefinition>("GET", `/governance/definitions/${enc(name)}`);

  /**
   * The eval harness's score of each logged run, from the stored eval runs (`GET /eval/runs`, then each run's
   * outcomes): an outcome that names its `runId` is that run's score (`eval/run.py outcome_from_events`), the newest
   * eval run winning. Only eval runs that cover the package count. Each outcome carries the golden answer
   * (`expected`) and where it is printed (`gold`): recorded by the harness, or joined by qid from the deployed golden
   * set by the API (`EvalRunView.expectedSource`); an outcome with neither keeps `expected` empty.
   */
  getRunScores = async (packageId: string): Promise<Record<string, RunScore>> => {
    const now = Date.now();
    if (!this.evalScores || now - this.evalScores.at > EVAL_SCORES_TTL_MS) {
      this.evalScores = { at: now, scores: this.loadEvalScores() };
      this.evalScores.scores.catch(() => (this.evalScores = undefined));
    }
    return (await this.evalScores.scores)[packageId] ?? {};
  };

  private evalScores?: { at: number; scores: Promise<Record<string, Record<string, RunScore>>> };

  private async loadEvalScores(): Promise<Record<string, Record<string, RunScore>>> {
    const list = await this.listEvalRuns();
    const oldestFirst = [...list.items].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    const views = await Promise.all(oldestFirst.map((e) => this.getEvalRun(e.evalRunId)));
    const out: Record<string, Record<string, RunScore>> = {};
    for (const v of views) {
      for (const o of v.outcomes) {
        if (!o.runId || o.answerCorrect === undefined || o.answerCorrect === null) continue;
        const gold = o.gold ? { docId: o.gold.docId, page: o.gold.page ?? null, bbox: o.gold.bbox ?? null } : null;
        const score: RunScore = { qid: o.qid, expected: o.expected ?? "", answerCorrect: o.answerCorrect, citationValid: Boolean(o.citationValid), by: `eval/run.py outcome_from_events · ${v.evalRunId}`, gold };
        for (const pid of v.packageIds) (out[pid] ??= {})[o.runId] = score;
      }
    }
    return out;
  }

  /** An errored ask writes a run record with status `error` here; `laneRunStats` counts those. */
  getAskErrors = async (_packageId: string): Promise<Record<string, number>> => ({});

  /** GET /documents/{docId}/lanes/{pv}/xray: rp-xray reads the 1.0 lane only */
  getXray = (docId: string, page: number, size?: number, runId?: string) =>
    this.request<XrayPage>("GET", `/documents/${enc(docId)}/lanes/${XRAY_LANE}/xray${qs({ page, size, runId })}`);

  getBuild = (docId: string, pv: string) => this.request<BuildReplay>("GET", `/documents/${enc(docId)}/lanes/${enc(pv)}/build`);
  inspectFilters = (runId: string, callId: string) => this.request<FilterInspection>("GET", `/inspect/filters${qs({ runId, callId })}`);
  getCallPath = (runId: string, callId: string) => this.request<GraphNeighbourhood>("GET", `/runs/${enc(runId)}/calls/${enc(callId)}/path`);
  listShowcases = () => this.request<ShowcaseCatalog>("GET", "/showcases");
  getShowcasePayload = <T,>(showcaseId: string, params: Record<string, string>) =>
    this.request<ShowcasePayload<T>>("GET", `/showcases/${enc(showcaseId)}/payload${qs(params)}`);

  // v2 routes (rp-api; wired in CDK, not deployed yet)
  getAccessDecisions = (runId: string) => this.request<AccessDecisionPage>("GET", `/access/decisions${qs({ runId })}`);
  decideReview = (packageId: string, reviewKey: string, body: ReviewDecisionRequest) =>
    this.request<ReviewItem>("POST", `/review/${enc(packageId)}/items/${enc(reviewKey)}/decision`, body);
  listEvalRuns = () => this.request<EvalRunList>("GET", "/eval/runs");
  getEvalRun = (evalRunId: string) => this.request<EvalRunView>("GET", `/eval/runs/${enc(evalRunId)}`);
  getCost = (packageId: string, groupBy?: string) => this.request<CostView>("GET", `/cost${qs({ packageId, groupBy })}`);
  listOntologyClasses = (module?: string) => this.request<OntologyTermList>("GET", `/ontology/classes${qs({ module })}`);
  getOntologyTerm = (iri: string) => this.request<OntologyTerm>("GET", `/ontology/terms/${enc(iri)}`);
  getGraphNeighbourhood = (iri: string, packageId: string, depth?: 1 | 2, pv?: string) =>
    this.request<GraphNeighbourhood>("GET", `/graph/neighbourhood${qs({ iri, packageId, depth, pv })}`);

  // COA environment lifecycle (rp-api -> rp-coa-control)
  getCoaEnvironment = () => this.request<CoaEnvironmentView>("GET", "/coa/environment");
  coaUp = (body: CoaUpRequest) => this.request<CoaEnvironmentView>("POST", "/coa/environment/up", body);
  coaDown = (body: CoaDownRequest) => this.request<CoaEnvironmentView>("POST", "/coa/environment/down", body);
  coaSettings = (body: CoaSettingsRequest) => this.request<CoaEnvironmentView>("PUT", "/coa/environment/settings", body);
  coaKeepAlive = (body: CoaKeepAliveRequest) => this.request<CoaEnvironmentView>("POST", "/coa/environment/keep-alive", body);

  /** POST /ask and read the text/event-stream incrementally (AgentCore Runtime response streaming). */
  async ask(body: AskRequest, { onEvent, signal }: AskHandlers): Promise<void> {
    const res = await fetch(`${this.baseUrl}/ask`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${await this.token()}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify(body),
      signal,
    });
    if (!res.ok) throw await toApiError(res);
    if (!res.body) {
      const { events } = parseSse((await res.text()) + "\n\n");
      events.forEach(onEvent);
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true }).replace(/\r\n/g, "\n");
      const { events, rest } = parseSse(buffer);
      buffer = rest;
      events.forEach(onEvent);
    }
    if (buffer.trim()) parseSse(buffer + "\n\n").events.forEach(onEvent);
  }
}

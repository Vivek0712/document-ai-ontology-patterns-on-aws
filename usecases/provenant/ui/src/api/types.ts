/** The UI's single API surface. HttpApi talks to API Gateway; MockApi serves fixtures (dev only). */
import type {
  AccessDecisionPage,
  ArtifactView,
  AskEvent,
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
  UploadUrlResponse,
} from "@contracts/api";
import type { GitGovernedDefinition } from "./governance";
import type { RunScore } from "./runs";
import type { XrayPage } from "./xray";

export type ApiErrorCode =
  | "bad_request"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "conflict"
  | "not_ready"
  | "internal"
  | "network";

export class RpApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: ApiErrorCode,
    message: string,
    public readonly requestId?: string,
  ) {
    super(message);
    this.name = "RpApiError";
  }
}

export interface UploadProgress {
  loaded: number;
  total: number;
}

export interface UploadHandle {
  docId: string;
  key: string;
  done: Promise<void>;
  abort: () => void;
}

export interface AskHandlers {
  onEvent: (event: AskEvent) => void;
  signal?: AbortSignal;
}

export interface RpApi {
  readonly mode: "http" | "mock" | "replay";
  me(): Promise<Me>;
  listPackages(): Promise<PackageList>;
  createPackage(body: PackageCreate): Promise<Package>;
  getPackage(packageId: string): Promise<PackageDetail>;
  deletePackage(packageId: string): Promise<DeletePackageResponse>;
  requestUploadUrl(packageId: string, file: File): Promise<UploadUrlResponse>;
  /** POST upload-url then presigned PUT; resolves when the object is in Bronze (PUT 200). */
  upload(packageId: string, file: File, onProgress: (p: UploadProgress) => void): Promise<UploadHandle>;
  packageStatus(packageId: string): Promise<PackageStatus>;
  getDocument(docId: string): Promise<DocumentDetail>;
  getStage(docId: string, pv: string, stageId: StageId): Promise<StageDetail>;
  /** Boxes the stage saw/produced on one page (server-side; no document text). */
  getOverlay(docId: string, pv: string, stageId: StageId, page: number): Promise<StageOverlay>;
  getEvents(docId: string, pv: string, cursor?: string, limit?: number): Promise<StageEventPage>;
  listUnits(docId: string, pv: string): Promise<UnitList>;
  reprocess(docId: string, pv: string): Promise<ReprocessResponse>;
  getUnit(unitId: string): Promise<UnitView>;
  getLineage(unitId: string, fieldId?: string): Promise<Lineage>;
  getRegion(unitId: string, fieldId?: string): Promise<RegionResult>;
  getArtifact(key: string): Promise<ArtifactView>;
  listPipelines(): Promise<PipelineList>;
  getPipeline(pv: string): Promise<PipelineDetail>;
  diffPipelines(a: string, b: string): Promise<ConfigDiff>;
  ask(body: AskRequest, handlers: AskHandlers): Promise<void>;
  listRuns(packageId: string, cursor?: string, limit?: number): Promise<RunPage>;
  getRun(runId: string): Promise<RunRecord>;
  listReview(packageId: string): Promise<ReviewList>;
  /**
   * v2 inspection (rp-xray): the 1.0 lane's view of one page, with the masked page image. `size` ≠ control is a
   * labelled what-if; `runId` (a logged 1.0 run) adds `answerNumbers`.
   */
  /** `size` omitted = the control size; the payload's `whatIfSizes` lists every size the API serves */
  getXray(docId: string, page: number, size?: number, runId?: string): Promise<XrayPage>;
  /** `GET /governance/definitions/{name}`: the Git-governed definition the ontology build recorded (SC-8 fallback). */
  getGovernedDefinition(name: string): Promise<GitGovernedDefinition>;
  /**
   * The eval harness's score of each logged run of a package (`eval/run.py outcome_from_events`), by run id.
   * Without the Evaluate route the HTTP client returns an empty map and runs are scored from the
   * agent's own golden check where it wrote one (see `scoreOf`).
   */
  getRunScores(packageId: string): Promise<Record<string, RunScore>>;
  /**
   * Asks that ended in an agent error and wrote no run record, by `${qid}|${pipelineVersion}`. The local run index
   * counts them (`runs/local/index.json` summary `errors`); on AWS an errored ask writes a run record with status
   * `error`, which `laneRunStats` counts, so the HTTP client returns an empty map.
   */
  getAskErrors(packageId: string): Promise<Record<string, number>>;
  // ---- v2 inspection routes (rp-api)
  /** How every layout block joined its unit: phase A / marker / B (cosine vs tau) / C. 2.3.1 lanes only; no text. */
  getBuild(docId: string, pv: string): Promise<BuildReplay>;
  /** `_count` with the exact filter a logged `search_units` call sent (counts only). */
  inspectFilters(runId: string, callId: string): Promise<FilterInspection>;
  /** The graph a logged `graph_hop` call's rows bound (source "logged-rows"; no graph query). */
  getCallPath(runId: string, callId: string): Promise<GraphNeighbourhood>;
  /** Server-side showcase composers (`sc-3`). */
  listShowcases(): Promise<ShowcaseCatalog>;
  getShowcasePayload<T>(showcaseId: string, params: Record<string, string>): Promise<ShowcasePayload<T>>;
  // ---- v2 routes (rp-api)
  /** What the gateway injected into each tool call of a run, and the decision (SC-7, W-13). */
  getAccessDecisions(runId: string): Promise<AccessDecisionPage>;
  /** ACCEPT / CORRECT / REJECT a low-confidence field (W-14); CORRECT records an override, never overwrites. */
  decideReview(packageId: string, reviewKey: string, body: ReviewDecisionRequest): Promise<ReviewItem>;
  /** Golden-set results (SC-12, W-16); unpublished builds return outcomes only (`published: false`). */
  listEvalRuns(): Promise<EvalRunList>;
  getEvalRun(evalRunId: string): Promise<EvalRunView>;
  /** Measured usage × list price (SC-13, W-17); unpublished builds return usage with no dollar amounts. */
  getCost(packageId: string, groupBy?: "docType" | "stage" | "lane" | "service"): Promise<CostView>;
  /** The built TBox (W-09). */
  listOntologyClasses(module?: string): Promise<OntologyTermList>;
  getOntologyTerm(iri: string): Promise<OntologyTerm>;
  /** An IRI's neighbourhood in the TBox and the package's ABox (W-11; `neighbourhood_v1` via rp-graph-hop). */
  getGraphNeighbourhood(iri: string, packageId: string, depth?: 1 | 2, pv?: string): Promise<GraphNeighbourhood>;
  // ---- COA environment lifecycle (Settings → COA environment; rp-api forwards to rp-coa-control)
  /** State, build progress, idle deadline. Reading it is never activity. `deployed: false` without the opt-in stack. */
  getCoaEnvironment(): Promise<CoaEnvironmentView>;
  coaUp(body: CoaUpRequest): Promise<CoaEnvironmentView>;
  coaDown(body: CoaDownRequest): Promise<CoaEnvironmentView>;
  coaSettings(body: CoaSettingsRequest): Promise<CoaEnvironmentView>;
  coaKeepAlive(body: CoaKeepAliveRequest): Promise<CoaEnvironmentView>;
  /** mock mode only: what the next simulated build does (`?coa=` sets the starting state) */
  coaSimulate?(next: CoaSimulatedOutcome): void;
}

export type CoaSimulatedOutcome = "ok" | "fail" | "eni_pending";

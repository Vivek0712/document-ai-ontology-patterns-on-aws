/** Shape of mock-public/mock/world.json: real API responses over the local run (`make mock-world`, scripts/mock_world.py). */
import type {
  AccessDecisionPage,
  ArtifactView,
  AskEvent,
  BuildReplay,
  ConfigDiff,
  CostView,
  EvalRunList,
  EvalRunView,
  FilterInspection,
  GraphNeighbourhood,
  DocumentItem,
  LaneRecord,
  Lineage,
  Me,
  OntologyTerm,
  OntologyTermList,
  Package,
  PipelineDetail,
  PipelineList,
  RegionResult,
  ReviewItem,
  RunSummary,
  ShowcaseCatalog,
  ShowcasePayload,
  StageEvent,
  StageOverlay,
  StageRecord,
  UnitList,
  UnitView,
} from "@contracts/api";
import type { GitGovernedDefinition } from "../api/governance";
import type { RunScore } from "../api/runs";

export interface WorldLane {
  lane: LaneRecord;
  stages: StageRecord[];
  events: StageEvent[];
}

export interface WorldPackage {
  package: Package;
  documents: DocumentItem[];
  canWrite: boolean;
  /** this package's own recorded time origin (each package's local build ran at a different time) */
  baseTime?: string;
}

/** Logged runs of one golden question, replayed by the mock ask (representative run per lane). */
export interface AskReplay {
  qid: string;
  question: string;
  packageId?: string;
  lanes: Record<string, AskEvent[]>;
}

/** N-of-N summary of the logged local runs (runs/local/index.json). */
export interface LocalRunSummary {
  qid: string;
  pipelineVersion: string;
  /** the cohort (runs/local index): the gate's N of N is ("default", "a"); SC-10/SC-13/SC-7 runs are their own rows */
  generationProfile?: "default" | "frontier" | "compact";
  caller?: "a" | "b";
  modelIds?: string[];
  costUsd?: number;
  /** runs that wrote a RunRecord; agent errors (no record) are counted in `errors` */
  n: number;
  errors?: number;
  correct: number;
  citationsValid: number;
  totals: Record<string, number>;
  representativeRunId: string | null;
  expected: string;
  question: string;
  /** the golden question's package (each package has its own local build) */
  packageId?: string;
  /** derived by the export: what the lane holds where the golden answer is printed */
  goldRegion?: { state: "held" | "no-units"; docId: string; unitId?: string } | null;
}

/** The harness's check that the lanes differ only in preprocessing, over every logged RunRecord. */
export interface LaneParity {
  ok: boolean;
  lanes: string[];
  checked: string[];
  differing: string[];
  mixedWithinLane: string[];
  /** false when a checked field is unrecorded on some runs (`unverified` names it; e.g. toolSpecSha before 11-11) */
  complete?: boolean;
  unverified?: string[];
  toolSpecUnrecorded?: number;
  notes?: string[];
  runs: number;
  source: string;
}

export interface World {
  generatedBy: string;
  /** where the world came from: the local run, its engines, and the runs' provenance */
  source?: { mode: string; build: string; runs: string; api: string; engines: Record<string, string | null>; runsProvenance?: string };
  baseTime: string;
  users: Record<"a" | "b", Me>;
  packages: WorldPackage[];
  lanes: Record<string, Record<string, WorldLane>>;
  /** GET .../stages/{stageId}/overlay responses, key `${docId}|${pv}|${stageId}|${page}` */
  overlays?: Record<string, StageOverlay>;
  units: Record<string, UnitView>;
  unitLists: Record<string, Record<string, UnitList>>;
  /** key: unitId or `${unitId}|${fieldId}` */
  lineage: Record<string, Lineage>;
  regions: Record<string, RegionResult>;
  /** large previews live in `previewFile` (fetched on demand, like the real API's per-object response) */
  artifacts: Record<string, ArtifactView & { previewFile?: string }>;
  review: ReviewItem[];
  pipelines: PipelineList;
  pipelineDetails: Record<string, PipelineDetail>;
  diff: ConfigDiff;
  asks: Record<string, AskEvent[]>;
  headlineQuestion: string;
  /** key: `${packageId}|${normalised question}` (Python `normalise_question`); two packages ask the same question */
  askReplays?: Record<string, AskReplay>;
  /** run records are served from `/mock/runs/{runId}.json`; the world carries their summaries */
  runSummaries: RunSummary[];
  /** the eval harness's score per logged run (runs/local/index.json) */
  runScores?: Record<string, RunScore>;
  localRuns?: { provenance?: string; summary: LocalRunSummary[]; spentUsd?: number; laneParity?: LaneParity | null };
  /** fin:QualifyingIncome as governed in Git (SC-8 fallback card) */
  governance?: GitGovernedDefinition;
  // ---- v2 inspection routes, exported from in-process calls of the real handlers
  /** GET /documents/{docId}/lanes/{pv}/build, key docId → pv */
  builds?: Record<string, Record<string, BuildReplay>>;
  /** GET /inspect/filters?runId=&callId=, key `${runId}|${callId}` (representative logged runs) */
  filterInspections?: Record<string, FilterInspection>;
  /** GET /runs/{runId}/calls/{callId}/path, key `${runId}|${callId}` */
  graphPaths?: Record<string, GraphNeighbourhood>;
  showcaseCatalog?: ShowcaseCatalog;
  /**
   * GET /showcases/{id}/payload. Keys: `sc-3|${packageId}|${security}`, `sc-7|${packageId}|${question}` and
   * `sc-10|${packageId}|${question}|${lane}` (question = Python `normalise_question`).
   */
  showcasePayloads?: Record<string, ShowcasePayload>;
  // ---- v2 routes, exported the same way
  /** GET /access/decisions?runId=, key runId (every logged run) */
  accessDecisions?: Record<string, AccessDecisionPage>;
  /** GET /region/{unitId}?field= as User B for the SC-7 probe, key `${unitId}|${fieldId}`: the handler's status */
  regionChecks?: Record<string, { as: "b"; status: number; body: unknown }>;
  /** POST /ask as User B (the package check), key packageId → `${question}|${pv}` */
  askChecks?: Record<string, Record<string, { as: "b"; status: number; body: unknown }>>;
  /** GET /cost, key packageId → `off` (docType); `on` (every groupBy) only in an export made with
   *  `make mock-world PUBLIC_METRICS=on`: an unpublished export never holds dollar amounts */
  cost?: Record<string, { on?: Record<string, CostView>; off: Record<string, CostView> }>;
  /** GET /graph/neighbourhood?iri=&depth=, key packageId → `${curie}|${depth}` (2.3.1) */
  graphNeighbourhoods?: Record<string, Record<string, GraphNeighbourhood>>;
  /** GET /eval/runs and /eval/runs/{id} with RP_PUBLIC_METRICS off (and on, only in a published export), and
   *  User B's status per result */
  evalRuns?: { list: Partial<Record<"off" | "on", EvalRunList>> & { off: EvalRunList }; runs: Record<string, Partial<Record<"off" | "on", EvalRunView>>>; asB: Record<string, number> };
  /** Per-package shards, fetched the first time a route touches the package: packageId → path under /mock/ */
  shards?: Record<string, string>;
  /** The ontology explorer's export (`knowledge.json`), fetched on first use */
  knowledge?: string;
}

/** `knowledge.json`: GET /ontology/classes (every module) and GET /ontology/terms/{iri} for fin: and doc: terms. */
export interface KnowledgeExport {
  classes: OntologyTermList;
  terms: Record<string, OntologyTerm>;
}

/** The keys a package shard holds (`packages/{packageId}.json`, scripts/mock_world.py `shard`). */
export const SHARD_KEYS = [
  "overlays", "units", "unitLists", "lineage", "regions", "artifacts", "builds", "filterInspections", "graphPaths",
  "showcasePayloads", "askReplays", "accessDecisions", "regionChecks",
] as const;
const SHARD_LISTS = ["review"] as const;
const SHARD_BY_PACKAGE = ["askChecks", "cost", "graphNeighbourhoods"] as const;

/** Merge one package shard into the world (the same keys, now holding that package's entries too). */
export function mergeShard(world: World, shard: Record<string, unknown>): void {
  const w = world as unknown as Record<string, unknown>;
  for (const key of SHARD_KEYS) {
    const part = shard[key] as Record<string, unknown> | undefined;
    if (part) w[key] = { ...((w[key] as Record<string, unknown> | undefined) ?? {}), ...part };
  }
  for (const key of SHARD_LISTS) {
    const part = shard[key] as unknown[] | undefined;
    if (part) w[key] = [...((w[key] as unknown[] | undefined) ?? []), ...part];
  }
  const pkg = shard.packageId as string;
  for (const key of SHARD_BY_PACKAGE) {
    if (shard[key] === undefined) continue;
    w[key] = { ...((w[key] as Record<string, unknown> | undefined) ?? {}), [pkg]: shard[key] };
  }
}

/** Every shard key present (empty) so code that reads the world never meets `undefined` for them. */
function withShardDefaults(world: World): World {
  const w = world as unknown as Record<string, unknown>;
  for (const key of SHARD_KEYS) w[key] ??= {};
  for (const key of SHARD_LISTS) w[key] ??= [];
  return world;
}

export const MOCK_WORLD_MISSING = "Mock world not found: run `make mock-world` from the repository root, then reload";

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`/mock/${path}`);
  // A missing file comes back as the dev server's SPA fallback (index.html with status 200), not as a 404.
  const html = (res.headers?.get?.("content-type") ?? "").includes("text/html");
  if (!res.ok || html) throw new Error(`${MOCK_WORLD_MISSING} (/mock/${path}: ${html ? "not found" : res.status})`);
  return (await res.json()) as T;
}

/** Served from mock-public/ (vite publicDir in mock/replay mode only); never bundled into the production build. */
export async function loadWorld(): Promise<World> {
  return withShardDefaults(await fetchJson<World>("world.json"));
}

export async function loadShard(path: string): Promise<Record<string, unknown>> {
  return fetchJson<Record<string, unknown>>(path);
}

export async function loadKnowledge(path: string): Promise<KnowledgeExport> {
  return fetchJson<KnowledgeExport>(path);
}

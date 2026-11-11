/**
 * Logged runs as evidence: the before/after answers are never scripted. For a golden question and a
 * lane, count the logged runs on the lane's current config and pick the representative run the way the run index
 * does (`services/local_agent/harness.py`): the most common total (normalised), then the first run that has it.
 *
 * One scoring definition: the eval harness's (`eval/run.py outcome_from_events`: `answerCorrect`, and
 * `citation.valid` = every claim resolvable with a box and no uncited number). Runs the harness has not scored
 * fall back to the agent's own golden check, labelled as such; a run with neither is "not scored".
 * Everything is derived from `GET /runs` summaries and the run scores; nothing here has a default value.
 */
import type { BBox, PackageStatus, RunSummary } from "@contracts/api";
import type { RpApi } from "./types";

/** The eval harness's outcome for one logged run (runs/local/index.json on a dev machine, Evaluate on AWS). */
export interface RunScore {
  qid: string;
  expected: string;
  answerCorrect: boolean;
  citationValid: boolean;
  by: string;
  /** where the golden answer is printed (golden.jsonl) */
  gold?: { docId: string; page?: number | null; bbox?: BBox | null } | null;
}

/**
 * What a lane holds where a golden answer is printed, from the unit views (derived): `held` when a unit the PII
 * gate held covers the gold box, `no-units` when the lane built no unit for the document (e.g. no text layer on
 * a scan). Undefined when neither explains a miss.
 */
export type GoldRegion = { state: "held"; unitId: string } | { state: "no-units"; docId: string };

export async function goldRegionOf(api: RpApi, gold: NonNullable<RunScore["gold"]>, pv: string): Promise<GoldRegion | undefined> {
  const list = await api.listUnits(gold.docId, pv);
  if (!list.items.length) return { state: "no-units", docId: gold.docId };
  const g = gold.bbox;
  for (const it of list.items.filter((i) => i.held)) {
    const v = await api.getUnit(it.unitId);
    const covers = !g || v.unit.bboxes.some((b) => b.page === g.page && b.x0 < g.x1 && b.x1 > g.x0 && b.y0 < g.y1 && b.y1 > g.y0);
    if (covers) return { state: "held", unitId: it.unitId };
  }
  return undefined;
}

/**
 * Whether an eval run measured the deployed stack: `eval/run.py` modes `ask` (POST /ask, AgentCore) and `search`
 * (the production rp-search-units Lambda) run on AWS; `local` and `logged` re-score dev-machine runs.
 */
export function evalRunOnAws(mode: string): boolean {
  return mode === "ask" || mode === "search" || mode === "aws";
}

/** Model id of the mock ask's keyword stand-in (mock mode only): no model ran; screens tag it "mock". */
export const MOCK_MODEL_ID = "mock-keyword-retriever";

export type ScoredBy = "eval" | "agent-check";

export interface LaneRunStats {
  qid: string;
  pipelineVersion: string;
  /** the lane config the runs were counted on (runs on another config are left out) */
  configSha: string;
  n: number;
  /** runs on this config that ended in an error (status not ok); they are not in `n` */
  errors: number;
  /** runs with a score; `correct` and `cited` count only these */
  scored: number;
  correct: number;
  /** runs where every claim opens a page region (the eval's citation check) */
  cited: number;
  /** how the scored runs were scored (both when mixed) */
  scoredBy: ScoredBy[];
  /** total as first written → how many runs gave it (grouped by normalised amount; "—" for no total) */
  totals: Record<string, number>;
  representative?: RunSummary;
  runs: RunSummary[];
  firstAt?: string;
  lastAt?: string;
}

/** "$169,650.00" and "169650" → "169650.00" (Python `normalise_amount`); text answers have no total. */
export function normaliseAmount(s: string | undefined | null): string | undefined {
  if (!s) return undefined;
  const cleaned = s.replace(/[^\d.-]/g, "");
  const n = Number(cleaned);
  return cleaned && Number.isFinite(n) ? n.toFixed(2) : undefined;
}

/** A run's score: the eval harness's when it has one, else the agent's own golden check (q-lend-01 only). */
export function scoreOf(r: RunSummary, scores: Record<string, RunScore>): { qid: string; correct: boolean; cited: boolean; by: ScoredBy } | undefined {
  const s = scores[r.runId];
  if (s) return { qid: s.qid, correct: s.answerCorrect, cited: s.citationValid, by: "eval" };
  if (r.checkMatch !== undefined && r.qid) return { qid: r.qid, correct: r.checkMatch, cited: r.claims > 0 && r.resolvableClaims === r.claims, by: "agent-check" };
  return undefined;
}

/** A run's golden question id: from its score, else its check, else matched on the question text. */
export function qidOf(r: RunSummary, scores: Record<string, RunScore>, byQuestion: Map<string, string>): string | undefined {
  return scores[r.runId]?.qid ?? r.qid ?? byQuestion.get(normaliseQuestion(r.question));
}

export function normaliseQuestion(q: string): string {
  return q.trim().toLowerCase().replace(/\?+$/, "").trim().replace(/\s+/g, " ");
}

/** Question text → golden id, from every run that carries one. */
export function questionIndex(all: RunSummary[], scores: Record<string, RunScore>): Map<string, string> {
  const out = new Map<string, string>();
  for (const r of all) {
    const q = scores[r.runId]?.qid ?? r.qid;
    if (q) out.set(normaliseQuestion(r.question), q);
  }
  return out;
}

/**
 * Which runs a lane's N of N counts: one generation profile (`default`, the lane's own model; SC-10's bigger model
 * and SC-13's compact tier are counted apart) and, when given, one caller (the package owner's gate runs; another
 * user's runs, such as SC-7's User B, are scoped by their own identity and never mixed in).
 */
export interface Cohort {
  profile?: "default" | "frontier" | "compact";
  callerSub?: string;
}

export function inCohort(r: RunSummary, cohort: Cohort = {}): boolean {
  return (r.generationProfile ?? "default") === (cohort.profile ?? "default") && (!cohort.callerSub || r.callerSub === cohort.callerSub);
}

export function laneRunStats(all: RunSummary[], qid: string, pv: string, configSha: string, scores: Record<string, RunScore> = {}, cohort: Cohort = {}): LaneRunStats {
  const byQuestion = questionIndex(all, scores);
  const asked = all.filter((r) => inCohort(r, cohort) && r.pipelineVersion === pv && r.configSha === configSha && qidOf(r, scores, byQuestion) === qid);
  const runs = asked.filter((r) => r.status === "ok").sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  const counts = new Map<string, { shown: string; n: number }>();
  for (const r of runs) {
    const k = normaliseAmount(r.total) ?? "—";
    const c = counts.get(k) ?? { shown: r.total ?? "—", n: 0 };
    counts.set(k, { ...c, n: c.n + 1 });
  }
  const totals = Object.fromEntries([...counts.values()].map((c) => [c.shown, c.n]));
  // the first most common total wins ties, as Python's Counter.most_common does
  const modal = [...counts.entries()].reduce<[string, { n: number }] | undefined>((best, e) => (!best || e[1].n > best[1].n ? e : best), undefined)?.[0];
  const scored = runs.map((r) => scoreOf(r, scores)).filter((s): s is NonNullable<typeof s> => Boolean(s));
  return {
    qid,
    pipelineVersion: pv,
    configSha,
    n: runs.length,
    errors: asked.length - runs.length,
    scored: scored.length,
    correct: scored.filter((s) => s.correct).length,
    cited: scored.filter((s) => s.cited).length,
    scoredBy: [...new Set(scored.map((s) => s.by))],
    totals,
    representative: runs.find((r) => (normaliseAmount(r.total) ?? "—") === modal),
    runs,
    firstAt: runs[0]?.createdAt,
    lastAt: runs.at(-1)?.createdAt,
  };
}

/** Every golden question with logged runs in the package, in question order. */
export function goldenQids(all: RunSummary[], scores: Record<string, RunScore>): string[] {
  const ids = new Set<string>();
  for (const r of all) {
    const q = scores[r.runId]?.qid ?? r.qid;
    if (q) ids.add(q);
  }
  return [...ids].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
}

/** Logged-run stats for one question on each lane, counted on each lane's registered config. */
export async function loggedRuns(api: RpApi, packageId: string, qid: string, lanes: string[], profile: Cohort["profile"] = "default"): Promise<Record<string, LaneRunStats>> {
  const [runs, pipelines, scores, pkg] = await Promise.all([allRuns(api, packageId), api.listPipelines(), api.getRunScores(packageId), api.getPackage(packageId)]);
  const out: Record<string, LaneRunStats> = {};
  for (const pv of lanes) {
    const item = pipelines.items.find((p) => p.pipelineVersion === pv);
    if (item) out[pv] = laneRunStats(runs, qid, pv, item.configSha, scores, { profile, callerSub: pkg.package.ownerSub });
  }
  return out;
}

/** Every run summary of a package (follows the cursor). */
export async function allRuns(api: RpApi, packageId: string): Promise<RunSummary[]> {
  const out: RunSummary[] = [];
  let cursor: string | undefined;
  do {
    const page = await api.listRuns(packageId, cursor, 200);
    out.push(...page.items);
    cursor = page.nextCursor;
  } while (cursor);
  return out;
}

/**
 * Showcases replay finished artifacts: wait until every lane of the package is ready (the mock world replays its
 * recorded ingest when a session opens). Rejects after `timeoutMs` with the lanes still running.
 */
export async function waitForLanes(api: RpApi, packageId: string, { timeoutMs = 60_000, pollMs = 1000 } = {}): Promise<PackageStatus> {
  const until = Date.now() + timeoutMs;
  for (;;) {
    const st = await api.packageStatus(packageId);
    const pending = Object.entries(st.lanes).filter(([, l]) => !l.ready);
    if (!pending.length) return st;
    if (Date.now() > until) throw new Error(`Lanes still running in ${packageId}: ${pending.map(([pv]) => pv).join(", ")}.`);
    await new Promise((r) => setTimeout(r, pollMs));
  }
}

/** "10 Nov 2026" */
export function dayOf(iso: string | undefined): string | undefined {
  return iso ? new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : undefined;
}

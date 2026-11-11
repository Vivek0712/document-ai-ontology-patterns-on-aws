/**
 * Logged runs compared across lanes (SC-11) or generation profiles (SC-10): which package holds a golden question,
 * the gate cohort's N of N per lane, each lane's representative run record, the eval harness's score of every run
 * and the lane config diff. Everything is read from `GET /runs`, the run scores and `GET /pipelines/diff`; a lane
 * with no logged run is reported as such, never filled in.
 */
import type { ConfigDiff, RetrievedUnit, RunRecord, RunSummary } from "@contracts/api";
import { type LaneRunStats, type RunScore, allRuns, loggedRuns, qidOf, questionIndex, scoreOf } from "../../api/runs";
import type { RpApi } from "../../api/types";
import type { Provenance } from "../../design";
import { LANES, type Lane } from "./lending";

/** One run as a dot: its eval outcome (or "error" / "not scored"). */
export interface RunDot {
  runId: string;
  status: "correct" | "wrong" | "error" | "unscored";
  cited: boolean;
  total?: string;
  error?: string;
}

export interface LaneEvidence {
  stats: LaneRunStats;
  /** the representative run (modal total, first run with it); absent when the lane has no ok run */
  run?: RunRecord;
  dots: RunDot[];
}

export interface QuestionRuns {
  qid: string;
  question: string;
  expected?: string;
  packageId: string;
  packageName: string;
  lanes: Record<Lane, LaneEvidence>;
  diff: ConfigDiff;
  provenance: Provenance;
}

/** Every readable package with its logged runs and their scores, fetched in parallel (one round trip per package). */
export async function packageRuns(api: RpApi): Promise<{ packageId: string; name: string; runs: RunSummary[]; scores: Record<string, RunScore> }[]> {
  const { items } = await api.listPackages();
  return Promise.all(items.map(async (p) => {
    const [runs, scores] = await Promise.all([allRuns(api, p.packageId), api.getRunScores(p.packageId)]);
    return { packageId: p.packageId, name: p.name, runs, scores };
  }));
}

/** The package (the preferred one first) whose logged runs answer `qid`, from `packageRuns`. */
export function homeOf(all: Awaited<ReturnType<typeof packageRuns>>, qid: string, preferred?: string): { packageId: string; name: string } | undefined {
  const ordered = [...all].sort((a, b) => Number(b.packageId === preferred) - Number(a.packageId === preferred));
  const hit = ordered.find((p) => {
    const byQ = questionIndex(p.runs, p.scores);
    return p.runs.some((r) => qidOf(r, p.scores, byQ) === qid);
  });
  return hit && { packageId: hit.packageId, name: hit.name };
}

/** The package whose logged runs answer `qid` (scored by the eval harness, or by the agent's golden check). */
export async function packageOfQuestion(api: RpApi, qid: string, preferred?: string): Promise<{ packageId: string; name: string } | undefined> {
  return homeOf(await packageRuns(api), qid, preferred);
}

/** A run's dot: errors are their own state (never counted as wrong), unscored runs say so. */
export function dotOf(r: RunSummary, scores: Record<string, RunScore>, error?: string): RunDot {
  if (r.status !== "ok") return { runId: r.runId, status: "error", cited: false, error };
  const s = scoreOf(r, scores);
  return { runId: r.runId, status: !s ? "unscored" : s.correct ? "correct" : "wrong", cited: Boolean(s?.cited), total: r.total };
}

/** Logged runs of `qid` on lane `pv` that `match` (a cohort), oldest first; errored runs are matched on their question. */
export function cohortRuns(all: RunSummary[], scores: Record<string, RunScore>, qid: string, pv: string, match: (r: RunSummary) => boolean): RunSummary[] {
  const byQ = questionIndex(all, scores);
  return all.filter((r) => r.pipelineVersion === pv && match(r) && qidOf(r, scores, byQ) === qid).sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

/** Both lanes of one golden question: gate-cohort stats, representative records, a dot per run, the config diff. */
export async function questionRuns(api: RpApi, qid: string, preferred?: string): Promise<QuestionRuns> {
  const pkg = await packageOfQuestion(api, qid, preferred);
  if (!pkg) throw new Error(`No logged run of ${qid} in any package you can read. Capture it first.`);
  const [stats, diff, all, scores, detail] = await Promise.all([
    loggedRuns(api, pkg.packageId, qid, [...LANES]),
    api.diffPipelines(LANES[0], LANES[1]),
    allRuns(api, pkg.packageId),
    api.getRunScores(pkg.packageId),
    api.getPackage(pkg.packageId),
  ]);
  const owner = detail.package.ownerSub;
  const lanes = {} as Record<Lane, LaneEvidence>;
  for (const pv of LANES) {
    const s = stats[pv];
    if (!s) throw new Error(`Lane ${pv} is not registered.`);
    const runs = cohortRuns(all, scores, qid, pv, (r) => (r.generationProfile ?? "default") === "default" && r.callerSub === owner && r.configSha === s.configSha);
    lanes[pv] = { stats: s, run: s.representative ? await api.getRun(s.representative.runId) : undefined, dots: runs.map((r) => dotOf(r, scores)) };
  }
  const anyScore = Object.values(scores).find((x) => x.qid === qid);
  return {
    qid,
    question: lanes["2.3.1"].run?.question ?? lanes["1.0"].run?.question ?? "",
    expected: anyScore?.expected,
    packageId: pkg.packageId,
    packageName: pkg.name,
    lanes,
    diff,
    provenance: api.mode === "http" ? "measured" : "local-run",
  };
}

/** The units a run's first successful `search_units` call returned, in rank order (what the model was handed first). */
export function firstRetrieved(run: RunRecord | undefined): { callId?: string; k?: number; units: RetrievedUnit[] } {
  const call = run?.toolCalls.find((c) => c.tool === "search_units" && c.status === "ok" && Array.isArray(c.output.units) && (c.output.units as unknown[]).length > 0);
  if (!call) return { units: [] };
  // the tool echoes its query (index, K, fusion, filter) as `query`
  const echo = call.output.query as { k?: number } | undefined;
  return { callId: call.callId, k: echo?.k, units: call.output.units as RetrievedUnit[] };
}

/** "9 of 10" style counts over dots. */
export function tally(dots: RunDot[]) {
  return {
    n: dots.filter((d) => d.status !== "error").length,
    correct: dots.filter((d) => d.status === "correct").length,
    wrong: dots.filter((d) => d.status === "wrong").length,
    cited: dots.filter((d) => d.cited).length,
    errors: dots.filter((d) => d.status === "error").length,
    unscored: dots.filter((d) => d.status === "unscored").length,
  };
}

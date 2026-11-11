/**
 * SC-10 payload: the naive lane's logged runs of one question with the lane's own model (`generationProfile`
 * default) and with the bigger model (`frontier`), from `GET /showcases/sc-10/payload` (the server pairs the two
 * cohorts, diffs the representative records' configs and compares the retrieved text). Each run is scored by the
 * eval harness (`getRunScores`): results come from eval scores only. Every other question with frontier
 * runs is loaded the same way, so the lead question is never the only evidence shown.
 */
import type { ShowcasePayload, SourceRef } from "@contracts/api";
import { type RunScore, dayOf, normaliseAmount, qidOf, questionIndex } from "../../api/runs";
import type { RpApi } from "../../api/types";
import type { Provenance } from "../../design";
import type { DataSource, Loaded, StoryContext } from "../engine/types";
import { type RunDot, homeOf, packageRuns, tally } from "./compare";
import { gateQuestion } from "./lending";

/** One run as the composer serialises it. */
export interface Sc10RunRow {
  runId: string;
  createdAt: string;
  modelId: string;
  status: "ok" | "error";
  error: string | null;
  total: string | null;
  answer: string | null;
  claims: number;
  resolvableClaims: number;
  chips: { text: string; label: string; resolvable: boolean; reason: string | null }[];
  retrievedSha: string;
  retrievedUnits: string[];
  usage: { inputTokens?: number; outputTokens?: number };
  latencyMs: number;
}

export interface Sc10ProfileData {
  modelIds: string[];
  modelKeys: string[];
  n: number;
  errors: number;
  representativeRunId: string | null;
  retrievedShas: string[];
  runs: Sc10RunRow[];
}

export interface Sc10Data {
  question: string;
  lane: string;
  packageId: string;
  profiles: Record<string, Sc10ProfileData>;
  /** `unverified`: fields one record never logged (toolSpecSha before 2026-11-11): neither identical nor differing */
  configDiff: { identical: string[]; differing: { key: string; a: string; b: string }[]; unverified?: { key: string; a: string | null; b: string | null }[]; a: string; b: string } | null;
  sameRetrievedText: boolean;
}

export type ProfileKey = "default" | "frontier";
export const PROFILES: ProfileKey[] = ["default", "frontier"];

export interface Sc10Profile {
  key: ProfileKey;
  modelId?: string;
  dots: RunDot[];
  /** total as written → runs that gave it (ok runs only) */
  totals: [string, number][];
  representative?: Sc10RunRow;
  retrievedShas: string[];
}

export interface Sc10Question {
  qid?: string;
  question: string;
  packageId: string;
  packageName: string;
  lane: string;
  expected?: string;
  profiles: Record<ProfileKey, Sc10Profile>;
  configDiff: Sc10Data["configDiff"];
  sameRetrievedText: boolean;
  payloadSha: string;
  sources: SourceRef[];
}

export interface Sc10Payload {
  lead: Sc10Question;
  others: Sc10Question[];
  provenance: Provenance;
}

function dotOfRow(r: Sc10RunRow, scores: Record<string, RunScore>): RunDot {
  if (r.status !== "ok") return { runId: r.runId, status: "error", cited: false, error: r.error ?? undefined };
  const s = scores[r.runId];
  // the eval harness's score; a run it has not scored stays "not scored" (no fallback to a default)
  if (!s) return { runId: r.runId, status: "unscored", cited: r.claims > 0 && r.resolvableClaims === r.claims, total: r.total ?? undefined };
  return { runId: r.runId, status: s.answerCorrect ? "correct" : "wrong", cited: s.citationValid, total: r.total ?? undefined };
}

function profileOf(key: ProfileKey, d: Sc10ProfileData | undefined, scores: Record<string, RunScore>): Sc10Profile {
  const runs = d?.runs ?? [];
  const counts = new Map<string, { shown: string; n: number }>();
  for (const r of runs.filter((x) => x.status === "ok")) {
    const k = normaliseAmount(r.total) ?? "—";
    const c = counts.get(k) ?? { shown: r.total ?? "no total", n: 0 };
    counts.set(k, { ...c, n: c.n + 1 });
  }
  return {
    key,
    modelId: d?.modelIds[0],
    dots: runs.map((r) => dotOfRow(r, scores)),
    totals: [...counts.values()].map((c) => [c.shown, c.n] as [string, number]),
    representative: runs.find((r) => r.runId === d?.representativeRunId) ?? runs[0],
    retrievedShas: d?.retrievedShas ?? [],
  };
}

async function questionOf(api: RpApi, packageId: string, packageName: string, question: string, qid: string | undefined): Promise<Sc10Question> {
  const [res, scores] = await Promise.all([api.getShowcasePayload<Sc10Data>("sc-10", { packageId, question, lane: "1.0" }), api.getRunScores(packageId)]);
  const d = (res as ShowcasePayload<Sc10Data>).data;
  return {
    qid,
    question: d.question,
    packageId,
    packageName,
    lane: d.lane,
    expected: Object.values(scores).find((s) => s.qid === qid)?.expected,
    profiles: { default: profileOf("default", d.profiles.default, scores), frontier: profileOf("frontier", d.profiles.frontier, scores) },
    configDiff: d.configDiff,
    sameRetrievedText: d.sameRetrievedText,
    payloadSha: res.payloadSha,
    sources: res.sources,
  };
}

export async function loadSc10({ api, packageId }: StoryContext): Promise<Loaded<Sc10Payload>> {
  const lead = gateQuestion("sc-10").questionId;
  // every package's runs and scores in one parallel round, read once for the home package and the asked list
  const all = await packageRuns(api);
  const home = homeOf(all, lead, packageId);
  if (!home) throw new Error(`No logged run of ${lead} in any package you can read.`);
  // every (package, question) the bigger model was asked on, the lead first
  const asked: { packageId: string; name: string; question: string; qid?: string }[] = [];
  for (const { packageId: pid, name, runs, scores } of all) {
    const byQ = questionIndex(runs, scores);
    for (const r of runs.filter((x) => x.generationProfile === "frontier")) {
      if (!asked.some((a) => a.packageId === pid && a.question === r.question)) asked.push({ packageId: pid, name, question: r.question, qid: qidOf(r, scores, byQ) });
    }
  }
  const leadAsk = asked.find((a) => a.packageId === home.packageId && a.qid === lead);
  if (!leadAsk) throw new Error(`No frontier-profile run of ${lead} in ${home.packageId}. Run make local-ask GEN=frontier first.`);
  const [first, ...rest] = await Promise.all([leadAsk, ...asked.filter((a) => a !== leadAsk)].map((a) => questionOf(api, a.packageId, a.name, a.question, a.qid)));
  const runIds = PROFILES.flatMap((k) => first.profiles[k].dots.map((d) => d.runId));
  const local = api.mode !== "http";
  const source: DataSource = {
    mode: local ? "local-run" : "replay",
    label: `Replay of ${runIds.length} logged ${local ? "local " : ""}runs · lane ${first.lane}`,
    detail: local
      ? "Logged runs: the v1 agent on Amazon Bedrock from a dev machine, two generation profiles, tools in-process over the local pipeline snapshot (not AWS)"
      : "Logged runs on AWS (Bedrock AgentCore), two generation profiles",
    recordedAt: dayOf(first.profiles.frontier.representative?.createdAt),
    runId: first.profiles.frontier.representative?.runId,
  };
  return { source, payload: { lead: first, others: rest, provenance: local ? "local-run" : "measured" } };
}

// ------------------------------------------------------------------------------------------- captions

/** Step 3: chosen from the frontier runs' eval scores (spec "honest variants"). */
export function resultsCaption(p: Pick<Sc10Payload, "lead">): string {
  const f = tally(p.lead.profiles.frontier.dots);
  if (!f.n) return "The bigger model finished no run on this question.";
  const cite = f.cited === 0 ? " It still cannot cite a region." : "";
  if (f.correct === 0) return `The bigger model got none of ${f.n} right.${cite}`;
  return `The bigger model got ${f.correct} of ${f.n} right.${cite}`;
}

/** Step 1, from the config diff and the retrieved text: what the two record cohorts provably share. */
export function oneChangeCaption(p: Pick<Sc10Payload, "lead">): string {
  const d = p.lead.configDiff;
  if (!d) return "The bigger model has no completed run to compare.";
  const onlyModel = d.differing.length === 1 && d.differing[0].key === "modelId";
  if (!onlyModel) return `The run records differ in ${d.differing.length} fields; see the diff.`;
  if (d.unverified?.some((u) => u.key === "toolSpecSha")) return "Same prompt and config; tool schema not recorded for the default runs.";
  if (d.unverified?.length) return `Only the model differs; ${d.unverified.map((u) => u.key).join(", ")} not recorded on one side.`;
  if (!p.lead.sameRetrievedText) return "Same prompt and config. Only the model changes; retrieval differed.";
  return "Same chunks, same prompt. Only the model changes.";
}

/** Step 2: equal retrieved text is a fact of the payload, not an assumption. */
export function evidenceCaption(p: Pick<Sc10Payload, "lead">): string {
  return p.lead.sameRetrievedText ? "Both models read the same retrieved text." : "The two models' runs retrieved different text from the same index.";
}

/** Step 4: by construction chunks carry no box; the count says it for these runs. */
export function citationsCaption(p: Pick<Sc10Payload, "lead">): string {
  const cited = PROFILES.reduce((s, k) => s + tally(p.lead.profiles[k].dots).cited, 0);
  return cited === 0 ? "Neither model can cite a region the chunk does not have." : `${cited} runs cite a region; see the receipts.`;
}

/** A run's correctness from the harness, for the receipts. */
export function scoreLine(d: RunDot): string {
  return `${d.runId}: ${d.status}${d.total ? ` · ${d.total}` : ""}${d.cited ? " · region" : " · no region"}`;
}


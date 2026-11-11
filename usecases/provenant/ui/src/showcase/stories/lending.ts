/**
 * Resolution shared by the lending stories (SC-0, SC-9): the logged runs of the headline golden question and the
 * cited figure the story follows. Subjects are found by concept, borrower and doc type;
 * the question id comes from the catalog, the runs from `GET /runs`, never from this file.
 */
import type { RunRecord } from "@contracts/api";
import { type LaneRunStats, dayOf, loggedRuns, waitForLanes } from "../../api/runs";
import { type Trace, type TraceContrast, composeContrast, composeTrace } from "../../api/trace";
import type { RpApi } from "../../api/types";
import type { Provenance } from "../../design";
import { showcaseMeta } from "../catalog";
import type { DataSource } from "../engine/types";

export const LANES = ["1.0", "2.3.1"] as const;
export type Lane = (typeof LANES)[number];

/** The figure the lending stories follow: Maya's W-2 Box 1, as the evidence-units answer cites it. */
export const FOLLOW_SUBJECT = { docType: "doc:W2", borrower: "Maya Fictus", concept: "fin:W2WagesBox1" } as const;

export interface HeadlineRuns {
  qid: string;
  stats: Record<Lane, LaneRunStats>;
  runs: Record<Lane, RunRecord>;
  /** where the logged runs ran: "measured" on AWS, "local-run" from a dev machine (Bedrock either way) */
  provenance: Provenance;
}

/** The catalog's gate question for a story (the gate run: q × N per lane). */
export function gateQuestion(storyId: string): { questionId: string; repeats: number; milestone: string } {
  const g = showcaseMeta(storyId)?.gateRun;
  if (!g) throw new Error(`${storyId} has no gate question in the catalog`);
  return g;
}

/** The representative logged run per lane (modal total, first run with it) and the N-of-N counts. */
export async function headlineRuns(api: RpApi, packageId: string, qid: string): Promise<HeadlineRuns> {
  const stats = (await loggedRuns(api, packageId, qid, [...LANES])) as Record<Lane, LaneRunStats>;
  for (const pv of LANES) {
    if (!stats[pv]?.representative) throw new Error(`No logged run of ${qid} on lane ${pv} with its current config. Capture the gate run first.`);
  }
  const [naive, units] = await Promise.all(LANES.map((pv) => api.getRun(stats[pv].representative!.runId)));
  return { qid, stats, runs: { "1.0": naive, "2.3.1": units }, provenance: api.mode === "http" ? "measured" : "local-run" };
}

/** The cited claim of the evidence-units run for the subject figure, traced to its region and bytes. */
export async function followTrace(api: RpApi, run: RunRecord): Promise<Trace> {
  const cited = (run.answer?.claims ?? []).filter((c) => c.resolvable && c.fieldId);
  for (const c of cited) {
    const t = await composeTrace(api, { unitId: c.unitId, fieldId: c.fieldId, runId: run.runId });
    if (t.field?.concept === FOLLOW_SUBJECT.concept && t.unit.label.includes(FOLLOW_SUBJECT.borrower)) return t;
  }
  throw new Error(`Run ${run.runId} cites no ${FOLLOW_SUBJECT.concept} field for ${FOLLOW_SUBJECT.borrower}.`);
}

export async function naiveContrast(api: RpApi, naive: RunRecord, trace: Trace): Promise<TraceContrast | undefined> {
  return composeContrast(api, naive.runId, trace.claim.value, trace);
}

/** The replay banner for stories built on logged runs. */
export function runsSource(api: RpApi, runIds: string[], recordedAt?: string): DataSource {
  const live = api.mode === "http";
  return {
    mode: live ? "replay" : "local-run",
    label: `Replay of ${live ? "run" : "local run"}${runIds.length > 1 ? "s" : ""} ${runIds.join(" · ")}`,
    detail: live
      ? "Logged runs on AWS (Bedrock AgentCore), replayed from their run records"
      : "Logged runs: the v1 agent on Amazon Bedrock from a dev machine, tools in-process over the local pipeline snapshot (not AWS)",
    recordedAt: dayOf(recordedAt),
    runId: runIds[0],
  };
}

export { waitForLanes };

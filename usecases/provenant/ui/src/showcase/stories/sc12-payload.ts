/**
 * SC-12 payload: the latest eval run (`GET /eval/runs`, `GET /eval/runs/{id}`). Each golden question becomes one dot
 * per lane: a Recall@1 hit when every logged run of it ranked the gold unit first, a miss when none did, mixed
 * otherwise. A question whose answer no single unit prints (a total of several fields: `meta.multiUnit`, read by the
 * eval harness from the units) has no top-1 outcome: it sits in its own bin, "needs 2+ units". Region hit (the top-1
 * unit's region covers the gold region) is read per lane too; it is measurable only where units carry regions.
 * Golden questions with no logged run yet stay hollow ("not run"). Rates per type exist only when the
 * build publishes metrics (`RP_PUBLIC_METRICS=on`); with the flag off the API withholds them and the
 * last step says so.
 */
import type { EvalOutcome, EvalRunView } from "@contracts/api";
import { dayOf, evalRunOnAws } from "../../api/runs";
import type { Provenance } from "../../design";
import type { Loaded, StoryContext } from "../engine/types";
import { LANES, type Lane } from "./lending";

export type Bin = "hit" | "miss" | "mixed" | "multi" | "not-run";
export const BINS: Bin[] = ["hit", "miss", "mixed", "multi", "not-run"];
/** Which outcome a row of dots shows: top-1 (Recall@1) or region hit (RegionHit@1). */
export type Metric = "top1" | "region";

export interface Outcome {
  hits: number;
  /** runs where the outcome was measurable (region: the top-1 unit carried a region) */
  n: number;
  bin: Bin;
}

export interface QuestionDot {
  /** absent for a golden question with no logged run (only its type is known from the golden set counts) */
  qid?: string;
  /** per lane: Recall@1 hits / repeats, and region hits / runs whose top-1 carried a region */
  lanes: Record<Lane, Outcome & { region: Outcome }>;
}

export interface TypeRow {
  type: string;
  /** golden questions of this type (golden set) */
  n: number;
  dots: QuestionDot[];
}

export interface Sc12Payload {
  run: Omit<EvalRunView, "outcomes">;
  rows: TypeRow[];
  /** qids whose answer no single unit prints (the eval harness's `meta.multiUnit`) */
  multiUnit: string[];
  questions: number;
  scored: number;
  provenance: Provenance;
}

export function binOf(hits: number, n: number): Bin {
  if (!n) return "not-run";
  return hits === n ? "hit" : hits === 0 ? "miss" : "mixed";
}

/** The outcome a dot shows for one metric. */
export const outcomeOf = (d: QuestionDot, pv: Lane, metric: Metric): Outcome => (metric === "region" ? d.lanes[pv].region : d.lanes[pv]);

/** One dot per golden question and type; logged questions first (natural qid order), then the ones not run yet. */
export function typeRows(goldenTypes: Record<string, number>, outcomes: EvalOutcome[], multiUnit: string[] = []): TypeRow[] {
  return Object.entries(goldenTypes).map(([type, n]) => {
    const qids = [...new Set(outcomes.filter((o) => o.type === type).map((o) => o.qid))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
    const dots: QuestionDot[] = qids.map((qid) => ({
      qid,
      lanes: Object.fromEntries(
        LANES.map((pv) => {
          const mine = outcomes.filter((o) => o.qid === qid && o.pipelineVersion === pv);
          const hits = mine.filter((o) => o.recallAt1).length;
          const measured = mine.filter((o) => o.regionHitAt1 !== undefined && o.regionHitAt1 !== null);
          const rHits = measured.filter((o) => o.regionHitAt1).length;
          const bin: Bin = mine.length && multiUnit.includes(qid) ? "multi" : binOf(hits, mine.length);
          return [pv, { hits, n: mine.length, bin, region: { hits: rHits, n: measured.length, bin: binOf(rHits, measured.length) } }];
        }),
      ) as QuestionDot["lanes"],
    }));
    const notRun = { hits: 0, n: 0, bin: "not-run" as const };
    const none = { ...notRun, region: notRun };
    for (let i = dots.length; i < n; i++) dots.push({ lanes: { "1.0": none, "2.3.1": none } });
    return { type, n, dots };
  });
}

export async function loadSc12({ api }: StoryContext): Promise<Loaded<Sc12Payload>> {
  const list = await api.listEvalRuns();
  const latest = [...list.items].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  if (!latest) throw new Error("No eval run is stored yet. Run make eval-logged (or eval/run.py on AWS).");
  const { outcomes, ...run } = await api.getEvalRun(latest.evalRunId);
  const multiUnit = Array.isArray(run.meta.multiUnit) ? (run.meta.multiUnit as string[]) : [];
  const rows = typeRows(run.goldenTypes, outcomes, multiUnit);
  const local = !evalRunOnAws(run.mode);
  return {
    source: {
      mode: local ? "local-run" : "replay",
      label: `Eval run ${run.evalRunId} · ${run.mode}`,
      detail: run.modeNote,
      recordedAt: dayOf(run.createdAt),
    },
    payload: {
      run,
      rows,
      multiUnit,
      questions: rows.reduce((s, r) => s + r.n, 0),
      scored: rows.reduce((s, r) => s + r.dots.filter((d) => d.qid).length, 0),
      provenance: local ? "local-run" : "measured",
    },
  };
}

/** What the dots measure, said once under the header (and the reason top-1 cannot score a total). */
export const metricNote = (p: Sc12Payload) =>
  `Top-1: the first unit search returned prints the gold answer on the gold page. Region hit: that unit's region covers the gold region (naive chunks carry none).${p.multiUnit.length ? ` ${p.multiUnit.length} questions need 2+ units (a total of several fields), so top-1 does not apply to them.` : ""}`;

export const questionsCaption = (p: Pick<Sc12Payload, "questions">) => `${p.questions} questions with known pages and regions.`;

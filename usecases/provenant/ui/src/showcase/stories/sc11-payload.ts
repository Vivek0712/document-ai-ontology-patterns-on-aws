/**
 * SC-11 payload: one before/after example (the catalog's `examples`, chosen by `?ex=`), plus the outcome of every
 * example for the switcher, so a stable naive failure (a wrong total) sits beside the "right but uncited" cases.
 *
 * Per example: the gate cohort's logged runs on both lanes (N of N, a dot per run, the eval harness's score), the
 * representative run of each lane, the units each lane's first `search_units` call returned, the lane config diff,
 * and, when the naive lane read a W-2, the X-ray of that page with the numbers the logged answer used (the constructed-reading-order line).
 */
import type { RetrievedUnit, RunRecord } from "@contracts/api";
import { dayOf } from "../../api/runs";
import type { RpApi } from "../../api/types";
import type { XrayPage } from "../../api/xray";
import { showcaseMeta, type ShowcaseExample } from "../catalog";
import type { Loaded, StoryContext } from "../engine/types";
import { type QuestionRuns, type RunDot, firstRetrieved, questionRuns, tally } from "./compare";
import { FOLLOW_SUBJECT, LANES, type Lane, runsSource } from "./lending";

export interface Sc11Outcome {
  example: ShowcaseExample;
  packageName: string;
  /** per lane: correct / n / cited / errors over the gate cohort */
  lanes: Record<Lane, ReturnType<typeof tally>>;
}

export interface Sc11Retrieved {
  callId?: string;
  k?: number;
  units: RetrievedUnit[];
}

export interface Sc11Payload {
  examples: Sc11Outcome[];
  example: ShowcaseExample;
  runs: QuestionRuns;
  retrieved: Record<Lane, Sc11Retrieved>;
  /** the X-ray of the W-2 page the naive answer's numbers were found on (W-2 examples only) */
  xray?: XrayPage;
}

export const exampleOf = (id: string | undefined): ShowcaseExample => {
  const all = showcaseMeta("sc-11")?.examples ?? [];
  const hit = all.find((e) => e.id === id) ?? all[0];
  if (!hit) throw new Error("SC-11 has no examples in the catalog.");
  return hit;
};

/** The representative run of a lane, required by the before and after steps. */
export function runOf(p: Sc11Payload, pv: Lane): RunRecord | undefined {
  return p.runs.lanes[pv].run;
}

/** The dot of a lane's representative run (its eval score). */
export function repDot(p: Sc11Payload, pv: Lane): RunDot | undefined {
  const r = runOf(p, pv);
  return r ? p.runs.lanes[pv].dots.find((d) => d.runId === r.runId) : undefined;
}

/**
 * The W-2 page among the naive run's retrieved units whose X-ray finds the logged answer's numbers: a page where a
 * number lands on a box's position-only value first, then any page with a match. Undefined when the run read no W-2.
 */
async function answerXray(api: RpApi, run: RunRecord | undefined, units: RetrievedUnit[]): Promise<XrayPage | undefined> {
  if (!run) return undefined;
  // chunks carry no doc type of their own: read it from the package's register
  const types = new Map((await api.getPackage(run.packageId)).documents.map((d) => [d.docId, d.docType]));
  const docs = [...new Set(units.filter((u) => (u.docType ?? types.get(u.docId)) === FOLLOW_SUBJECT.docType).map((u) => u.docId))];
  const pages = (await Promise.all(docs.map((d) => api.getXray(d, 1, undefined, run.runId).catch(() => undefined)))).filter((x): x is XrayPage => Boolean(x && !x.warning));
  const used = (x: XrayPage) => (x.answerNumbers ?? []).filter((n) => n.matchedLines.length > 0);
  return pages.find((x) => used(x).some((n) => n.isPositionalValueOf)) ?? pages.find((x) => used(x).length > 0) ?? pages[0];
}

export async function loadSc11({ api, packageId, params }: StoryContext): Promise<Loaded<Sc11Payload>> {
  const example = exampleOf(params?.ex);
  const all = showcaseMeta("sc-11")?.examples ?? [];
  const evidence = await Promise.all(all.map((e) => questionRuns(api, e.questionId, packageId).catch(() => undefined)));
  const runs = evidence[all.indexOf(example)];
  if (!runs) throw new Error(`No logged runs of ${example.questionId} on both lanes. Capture them first.`);
  const retrieved = Object.fromEntries(LANES.map((pv) => [pv, firstRetrieved(runs.lanes[pv].run)])) as Record<Lane, Sc11Retrieved>;
  const xray = await answerXray(api, runs.lanes["1.0"].run, retrieved["1.0"].units);
  const examples = all.flatMap((e, i) => {
    const q = evidence[i];
    return q ? [{ example: e, packageName: q.packageName, lanes: Object.fromEntries(LANES.map((pv) => [pv, tally(q.lanes[pv].dots)])) as Sc11Outcome["lanes"] }] : [];
  });
  const ids = LANES.flatMap((pv) => (runs.lanes[pv].run ? [runs.lanes[pv].run!.runId] : []));
  return {
    source: { ...runsSource(api, ids, runs.lanes["2.3.1"].run?.createdAt), recordedAt: dayOf(runs.lanes["2.3.1"].run?.createdAt) },
    subjectPackageId: runs.packageId,
    payload: { examples, example, runs, retrieved, xray },
  };
}

// ------------------------------------------------------------------------------------------- captions

/** Step 2: what the naive lane's runs did, from their scores. */
export function beforeCaption(p: Sc11Payload): string {
  const t = tally(p.runs.lanes["1.0"].dots);
  if (!t.n) return "Naive pipeline: no completed run.";
  if (t.wrong === t.n) return `Naive pipeline: wrong in all ${t.n} logged runs.`;
  if (t.correct === t.n && t.cited === 0) return "Naive pipeline: the right figure, with no region to cite.";
  return `Naive pipeline: ${t.correct} of ${t.n} logged runs correct.`;
}

/** Step 4: the evidence-units lane, from its scores. */
export function afterCaption(p: Sc11Payload): string {
  const t = tally(p.runs.lanes["2.3.1"].dots);
  if (t.n && t.correct === t.n && t.cited === t.n) return "Evidence units: the right figure, and every claim opens its region.";
  if (t.n && t.correct === t.n) return `Evidence units: right in all ${t.n}; ${t.cited} cite a region.`;
  return `Evidence units: ${t.correct} of ${t.n} logged runs correct.`;
}

/**
 * Where the logged answer's numbers are printed on the page (derived): each matched line with the box whose value it
 * is (the line's own `fieldId`). Never a mechanism claim: SC-1 draws the position-only hypothesis.
 */
export function xrayFindings(x: XrayPage): { value: string; lines: { ordinal: number; box?: string }[] }[] {
  const boxOf = (fieldId?: string) => x.pairs.find((pr) => pr.fieldId === fieldId)?.label.text;
  return (x.answerNumbers ?? []).map((n) => ({
    value: n.value,
    lines: n.matchedLines.map((o) => ({ ordinal: o, box: boxOf(x.lines.find((l) => l.ordinal === o)?.fieldId) })),
  }));
}

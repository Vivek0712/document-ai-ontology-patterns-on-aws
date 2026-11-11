/**
 * SC-0 payload: one lending package, its documents and stage records, the headline golden question's logged runs
 * per lane (N of N), the X-ray of the naive lane's page, the followed figure's trace and the governed definition.
 * Subjects are found by concept, borrower and doc type (lending.ts); nothing here is typed in.
 */
import type { ConfigDiff, DocumentItem, LaneRecord, PackageStatus, StageRecord } from "@contracts/api";
import type { GitGovernedDefinition } from "../../../api/governance";
import type { Trace } from "../../../api/trace";
import type { Provenance } from "../../../design";
import type { Loaded, StoryContext } from "../../engine/types";
import { type HeadlineRuns, type Lane, LANES, followTrace, gateQuestion, headlineRuns, runsSource, waitForLanes } from "../lending";
import { type Sc1Payload, loadSc1 } from "../sc1-payload";

export interface Sc0Payload {
  packageId: string;
  packageName: string;
  question: string;
  documents: DocumentItem[];
  /** per document and lane: the lane record and its stage records (measured durations) */
  lanes: { docId: string; fileName: string; lanes: Partial<Record<Lane, { lane: LaneRecord; stages: StageRecord[] }>> }[];
  counters: PackageStatus["counters"];
  runs: HeadlineRuns;
  xray: Sc1Payload;
  trace: Trace;
  definition?: GitGovernedDefinition;
  definitionError?: string;
  diff: ConfigDiff;
  provenance: Provenance;
}

export async function loadSc0({ api, packageId }: StoryContext): Promise<Loaded<Sc0Payload>> {
  const status = await waitForLanes(api, packageId);
  const q = gateQuestion("sc-0");
  const [pkg, runs, diff] = await Promise.all([api.getPackage(packageId), headlineRuns(api, packageId, q.questionId), api.diffPipelines(LANES[0], LANES[1])]);
  const naive = runs.runs["1.0"];
  const [xray, trace, details, definition] = await Promise.all([
    loadSc1({ api, packageId, runId: naive.runId }).then((l) => l.payload),
    followTrace(api, runs.runs["2.3.1"]),
    Promise.all(pkg.documents.map((d) => api.getDocument(d.docId))),
    api.getGovernedDefinition("qualifying_income").then(
      (d) => ({ d, e: undefined }),
      (e: unknown) => ({ d: undefined, e: e instanceof Error ? e.message : String(e) }),
    ),
  ]);
  return {
    source: runsSource(api, [naive.runId, runs.runs["2.3.1"].runId], naive.createdAt),
    payload: {
      packageId,
      packageName: pkg.package.name,
      question: naive.question,
      documents: pkg.documents,
      lanes: details.map((d) => ({
        docId: d.document.docId,
        fileName: d.document.fileName,
        lanes: Object.fromEntries(LANES.flatMap((pv) => (d.lanes[pv] ? [[pv, { lane: d.lanes[pv].lane, stages: d.lanes[pv].stages }]] : []))),
      })),
      counters: status.counters,
      runs,
      xray,
      trace,
      definition: definition.d,
      definitionError: definition.e,
      diff,
      provenance: runs.provenance,
    },
  };
}

/** The document the followed figure comes from (its sha256 is the one shown at upload). */
export const followedDoc = (p: Sc0Payload) => p.documents.find((d) => d.docId === p.trace.unit.unitId.split(":")[0]);
/** The representative logged run of a lane. */
export const rec = (p: Sc0Payload, pv: Lane) => p.runs.runs[pv];
export const sha12 = (s?: string | null) => (s ? s.slice(0, 12) : "—");
/** The evidence-units answer's total for this package: what COA's recorded recomputation is compared with. */
export const ourTotal = (p: Sc0Payload) => {
  const r = rec(p, "2.3.1");
  return r.answer?.total ? { packageId: p.packageId, total: r.answer.total, runId: r.runId } : undefined;
};

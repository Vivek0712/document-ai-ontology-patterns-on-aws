/**
 * SC-14 payload: one real execution of one document through the three planes, composed in the browser from
 * records the API already serves (until `GET /planes/replay` exists):
 * - ingestion: the document's Bronze object (`DocumentItem`), and per lane the LaneRecord and the StageRecords
 *   (state name, engine, measured ms, counters, artifacts) the pipeline wrote as it ran;
 * - knowledge: the write receipts the lanes recorded (units indexed per index, triples in the package ABox);
 * - agent: the gate question's representative evidence-units run (tool calls) and its access-log rows.
 * The Step Functions execution history is not part of a local run; every lit element maps to a record above, and
 * an element with no record says "not in this execution".
 */
import type { AccessDecision, Claim, DocumentItem, LaneRecord, RunRecord, StageRecord } from "@contracts/api";
import { dayOf } from "../../api/runs";
import type { Provenance } from "../../design";
import type { Loaded, StoryContext } from "../engine/types";
import { FOLLOW_SUBJECT, LANES, type Lane, gateQuestion, headlineRuns, waitForLanes } from "./lending";
import { documentsOfType } from "./subjects";

export interface Sc14Lane {
  lane: LaneRecord;
  stages: StageRecord[];
  /** the index this lane writes (from the lane's logged search echo) */
  index?: string;
}

export interface Sc14Payload {
  packageId: string;
  doc: DocumentItem;
  lanes: Record<Lane, Sc14Lane>;
  run: RunRecord;
  access: AccessDecision[];
  accessSource?: string;
  claim?: Claim;
  /** the ABox graph the run's hop read */
  abox?: string;
  provenance: Provenance;
}

export async function loadSc14({ api, packageId }: StoryContext): Promise<Loaded<Sc14Payload>> {
  const pkg = (await documentsOfType(api, FOLLOW_SUBJECT.docType, packageId))[0]?.packageId ?? packageId;
  await waitForLanes(api, pkg);
  const h = await headlineRuns(api, pkg, gateQuestion("sc-14").questionId);
  const run = h.runs["2.3.1"];
  // the document the run cites for the subject borrower (resolved, never typed)
  const claim = run.answer?.claims.find((c) => c.resolvable && (c.label ?? "").includes(FOLLOW_SUBJECT.borrower));
  const docId = claim?.docId ?? claim?.unitId.split(":")[0];
  if (!docId) throw new Error(`Run ${run.runId} cites no region for ${FOLLOW_SUBJECT.borrower}.`);
  const [detail, access] = await Promise.all([api.getDocument(docId), api.getAccessDecisions(run.runId).catch(() => undefined)]);
  const lanes = Object.fromEntries(
    LANES.map((pv) => {
      const l = detail.lanes[pv];
      if (!l) throw new Error(`${docId} has no ${pv} lane.`);
      const echo = h.runs[pv].toolCalls.find((c) => c.tool === "search_units")?.output.query as { index?: string } | undefined;
      // the index this lane writes, as its index stage recorded it (else the lane's logged search echo)
      const written = l.stages.find((s) => s.stageId === "index")?.output as { index?: string } | undefined;
      return [pv, { lane: l.lane, stages: [...l.stages].sort((a, b) => a.order - b.order), index: written?.index ?? echo?.index }];
    }),
  ) as Record<Lane, Sc14Lane>;
  const hop = run.toolCalls.find((c) => c.tool === "graph_hop");
  const graphs = (hop?.output.graphs as string[] | undefined) ?? [];
  return {
    source: {
      mode: h.provenance === "measured" ? "replay" : "local-run",
      label: `One execution of ${detail.document.fileName} · run ${run.runId}`,
      detail: h.provenance === "measured" ? "Stage records, write receipts and the run's access log on AWS" : "Stage records and receipts from the local pipeline run (moto, not AWS); the logged run on Amazon Bedrock",
      recordedAt: dayOf(detail.lanes["2.3.1"]?.lane.startedAt),
      runId: run.runId,
    },
    payload: {
      packageId: pkg,
      doc: detail.document,
      lanes,
      run,
      access: access?.items ?? [],
      accessSource: access?.items[0]?.source,
      claim,
      abox: graphs.find((g) => g.includes(":abox:")),
      provenance: h.provenance,
    },
  };
}

/** ms from the first lane's start: where each stage sits on the shared clock (for the order they light in). */
export function stageClock(p: Sc14Payload): { t0: number; t1: number } {
  const times = LANES.flatMap((pv) => p.lanes[pv].stages.flatMap((s) => (s.startedAt ? [Date.parse(s.startedAt), Date.parse(s.startedAt) + (s.durationMs ?? 0)] : [])));
  return { t0: Math.min(...times), t1: Math.max(...times) };
}

export const ingestionCaption = () => "Route, extract, build units, gate, promote: each stage is logged.";

export function gateCaption(p: Sc14Payload): string {
  const g = p.lanes["2.3.1"].stages.find((s) => s.stageId === "pii_gate");
  return g ? "Units cross the gate before anything reaches Gold." : "No gate record in this execution.";
}

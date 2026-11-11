/**
 * SC-13 payload: `GET /cost?packageId=&groupBy=docType` for the workspace's lending package. Usage is measured
 * (stage records, run usage) or derived with its basis (Docling seconds × task size, Comprehend units); amounts are
 * `estimate` = usage × the list price in `config/prices.yaml`, and exist only in a published build. The
 * showcase reads usage when amounts are withheld, never a default.
 */
import type { CostLine, CostView, StageDuration } from "@contracts/api";
import { allRuns, dayOf } from "../../api/runs";
import { LANE_NAME } from "../../design";
import { modelFamily } from "../../lib/format";
import type { Provenance } from "../../design";
import type { Loaded, StoryContext } from "../engine/types";
import { packageOfQuestion } from "./compare";
import { LANES, type Lane, gateQuestion } from "./lending";

export interface DocUsage {
  docId: string;
  docType?: string;
  fileName: string;
  pages?: number;
  lanes: Record<Lane, { lines: CostLine[]; extract?: StageDuration; estimateUsd?: number }>;
}

export interface AskTokens {
  runId: string;
  lane: string;
  tokensIn: number;
  modelId: string;
}

export interface Sc13Payload {
  cost: Omit<CostView, "lines" | "stages">;
  docs: DocUsage[];
  /** input tokens of the gate cohort's asks (package owner, default profile, completed) with the lane's own answer
   *  model, per lane: another user's refused asks and the other profiles' runs are in the ledger, not here */
  asks: AskTokens[];
  answerModel?: string;
  provenance: Provenance;
}

/** Usage kinds a document's ingest consumes, in display order, with their unit. */
export const USAGE: { service: string; unit: string; label: string; kind: "int" | "dec2" }[] = [
  { service: "fargate.vcpu", unit: "vCPU-s", label: "Docling on Fargate", kind: "dec2" },
  { service: "textract.analyze_document.layout", unit: "pages", label: "Amazon Textract", kind: "int" },
  { service: "comprehend.detect_pii_entities", unit: "units", label: "Amazon Comprehend", kind: "int" },
];

export async function loadSc13({ api, packageId }: StoryContext): Promise<Loaded<Sc13Payload>> {
  const home = await packageOfQuestion(api, gateQuestion("sc-13").questionId, packageId);
  const pkgId = home?.packageId ?? packageId;
  const [view, pkg, runs] = await Promise.all([api.getCost(pkgId, "docType"), api.getPackage(pkgId), allRuns(api, pkgId)]);
  // the gate cohort, as compare.ts and the run index count it: the owner's completed default-profile runs
  const owner = pkg.package.ownerSub;
  const gate = new Set(runs.filter((r) => r.callerSub === owner && (r.generationProfile ?? "default") === "default" && r.status === "ok").map((r) => r.runId));
  const { lines, stages, ...cost } = view;
  const docs: DocUsage[] = pkg.documents.map((d) => ({
    docId: d.docId,
    docType: d.docType,
    fileName: d.fileName,
    pages: view.perDocument.find((x) => x.docId === d.docId)?.pages ?? d.pageCount,
    lanes: Object.fromEntries(
      LANES.map((pv) => [
        pv,
        {
          lines: lines.filter((l) => l.docId === d.docId && l.pipelineVersion === pv),
          extract: stages.find((s) => s.docId === d.docId && s.pipelineVersion === pv && s.stageId === "extract"),
          estimateUsd: view.perDocument.find((x) => x.docId === d.docId && x.lane === pv)?.estimateUsd,
        },
      ]),
    ) as DocUsage["lanes"],
  }));
  // the lane's own answer model: the tiering row for the default call type
  const answerModel = view.tiering.find((t) => t.callType === "answer")?.modelId;
  const asks = lines
    .filter((l) => l.stageId === "ask" && l.usage.unit === "token_in" && l.runId && gate.has(l.runId) && l.service === answerModel)
    .map((l) => ({ runId: l.runId!, lane: l.pipelineVersion, tokensIn: l.usage.quantity, modelId: l.service }));
  const local = api.mode !== "http";
  return {
    source: { mode: local ? "local-run" : "replay", label: `Cost of ${pkgId} · usage × list price`, detail: `${view.prices.source}${view.published ? "" : " · amounts not published in this build"}`, recordedAt: dayOf(view.prices.asOf) },
    subjectPackageId: pkgId,
    payload: { cost, docs, asks, answerModel, provenance: local ? "local-run" : "measured" },
  };
}

export function median(xs: number[]): number | undefined {
  if (!xs.length) return undefined;
  const s = [...xs].sort((a, b) => a - b);
  return s.length % 2 ? s[(s.length - 1) / 2] : (s[s.length / 2 - 1] + s[s.length / 2]) / 2;
}

export const perDocCaption = (p: Sc13Payload) => (p.cost.published ? "Estimated cost per document, by stage." : "Usage per document, by stage. Amounts are not published.");
export const actualsCaption = () => "Billed actuals: not in this build.";

/** Step 3, from the tiering rows: which model each profile's answers ran on. */
export function tieringCaption(p: Pick<Sc13Payload, "cost">): string {
  const answer = p.cost.tiering.find((t) => t.callType === "answer");
  const compact = p.cost.tiering.find((t) => t.modelKey === "MODEL_COMPACT" && t.callType.startsWith("answer") && t.calls > 0);
  if (!answer) return "The tier per call is configuration.";
  return compact
    ? `The tier is configuration: answers on ${modelFamily(answer.modelId)}, the compact profile on ${modelFamily(compact.modelId)}.`
    : `The tier is configuration: every answer here ran on ${modelFamily(answer.modelId)}.`;
}

/** Step 4, from the medians: the same when within 20 %, else which lane reads more. */
export function tokensCaption(p: Pick<Sc13Payload, "asks">): string {
  const m = LANES.map((pv) => median(p.asks.filter((a) => a.lane === pv).map((a) => a.tokensIn)));
  if (m.some((x) => x === undefined)) return "Input tokens per answer, per lane.";
  const [a, b] = m as number[];
  if (Math.abs(a - b) / Math.max(a, b) <= 0.2) return "Input tokens per answer: about the same in both lanes.";
  return `Input tokens per answer: the ${LANE_NAME[a > b ? LANES[0] : LANES[1]]} lane reads more.`;
}

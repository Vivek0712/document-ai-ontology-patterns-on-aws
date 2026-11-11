/**
 * SC-5 payload: one logged evidence-units run of the gate question, read as the five moves of Pattern 5 (resolve,
 * retrieve, hop, assemble, cite). Every stop is a tool call the run actually made; times are the run
 * record's own (`startedAt` - `createdAt`, `ms`); the hop graph is what the call's rows bound
 * (`GET /runs/{runId}/calls/{callId}/path`); each cited figure's region comes from `GET /region/{unitId}?field=`.
 */
import type { AnswerPayload, Check, Claim, GraphNeighbourhood, RetrievedUnit, RunRecord, ToolCallRecord } from "@contracts/api";
import { waitForLanes } from "../../api/runs";
import type { RpApi } from "../../api/types";
import type { Provenance } from "../../design";
import { curie } from "../../lib/iris";
import { type Rect, toRect } from "../../viz/geom";
import type { Loaded, StoryContext } from "../engine/types";
import { FOLLOW_SUBJECT, gateQuestion, headlineRuns, runsSource } from "./lending";
import { documentsOfType } from "./subjects";

/** The template that resolves words to a concept (SC-5 step 2). */
export const RESOLVE_TEMPLATE = "concept_by_label_v1";

export interface Sc5Call {
  callId: string;
  tool: string;
  /** ms from the run's start (`startedAt` - `createdAt`) */
  at: number;
  ms: number;
  status: ToolCallRecord["status"];
  input: Record<string, unknown>;
  injected: Record<string, unknown>;
  /** the tool's result as logged (the inspector's JSON) */
  output: Record<string, unknown>;
  summary?: string;
}

/** An ontology term the run used, and where it appears (a tool input or the hop's bound graph). */
export interface Sc5Term {
  term: string;
  where: string;
}

export interface Sc5Region {
  claim: Claim;
  fileName: string;
  page: number;
  image?: { src: string; dpi: number };
  pageWidth: number;
  pageHeight: number;
  unit: Rect;
  field: Rect;
}

export interface Sc5Payload {
  qid: string;
  run: Pick<RunRecord, "runId" | "s3Key" | "createdAt" | "question" | "packageId" | "pipelineVersion" | "modelId" | "configSha" | "promptSha" | "latencyMs" | "usage" | "toolsEnabled">;
  calls: Sc5Call[];
  resolve: { call?: Sc5Call; terms: Sc5Term[] };
  retrieve?: { call: Sc5Call; units: RetrievedUnit[] };
  hop?: { call: Sc5Call; template: string; rows: Record<string, string>[]; total?: string; path: GraphNeighbourhood };
  answer: AnswerPayload;
  check?: Check;
  regions: Sc5Region[];
  provenance: Provenance;
}

const str = (v: unknown): string | undefined => (typeof v === "string" && v ? v : undefined);

export function callsOf(r: RunRecord): Sc5Call[] {
  const t0 = Date.parse(r.createdAt);
  return r.toolCalls.map((c) => ({
    callId: c.callId,
    tool: c.tool,
    at: Math.max(0, Date.parse(c.startedAt) - t0),
    ms: c.ms,
    status: c.status,
    input: c.input,
    injected: c.injected,
    output: c.output,
    summary: str(c.output.summary),
  }));
}

/** Ontology terms the run's own tool inputs and hop graph name (nothing else is inferred). */
export function termsOf(calls: Sc5Call[], path?: GraphNeighbourhood): Sc5Term[] {
  const terms: Sc5Term[] = [];
  for (const c of calls) {
    const docType = str(c.input.docType);
    if (docType) terms.push({ term: docType, where: `${c.tool} · docType` });
    const unitTypes = c.input.unitTypes;
    if (Array.isArray(unitTypes)) for (const u of unitTypes) terms.push({ term: String(u), where: `${c.tool} · unitTypes` });
    const template = str(c.input.template);
    if (template) terms.push({ term: template, where: `${c.tool} · template` });
  }
  for (const n of path?.nodes ?? []) if (n.kind === "class") terms.push({ term: curie(n.iri), where: "read by the hop template" });
  return terms.filter((t, i) => terms.findIndex((x) => x.term === t.term) === i);
}

/** A run that resolved a concept, made a hop and cites every figure; the steps take whichever it did. */
export async function loadSc5({ api, packageId }: StoryContext): Promise<Loaded<Sc5Payload>> {
  const pkg = (await documentsOfType(api, FOLLOW_SUBJECT.docType, packageId))[0]?.packageId ?? packageId;
  await waitForLanes(api, pkg);
  const qid = gateQuestion("sc-5").questionId;
  const h = await headlineRuns(api, pkg, qid);
  const run = h.runs["2.3.1"];
  const calls = callsOf(run);
  const search = calls.find((c) => c.tool === "search_units");
  const searchRecord = run.toolCalls.find((c) => c.callId === search?.callId);
  const hopCall = calls.find((c) => c.tool === "graph_hop" && str(c.input.template) !== RESOLVE_TEMPLATE);
  const hopRecord = run.toolCalls.find((c) => c.callId === hopCall?.callId);
  const path = hopCall ? await api.getCallPath(run.runId, hopCall.callId) : undefined;
  const answer = run.answer;
  if (!answer) throw new Error(`Run ${run.runId} has no answer.`);
  const regions = await Promise.all(answer.claims.filter((c) => c.resolvable).map((c) => regionOf(api, c)));
  return {
    source: runsSource(api, [run.runId], run.createdAt),
    payload: {
      qid,
      run: {
        runId: run.runId,
        s3Key: run.s3Key,
        createdAt: run.createdAt,
        question: run.question,
        packageId: run.packageId,
        pipelineVersion: run.pipelineVersion,
        modelId: run.modelId,
        configSha: run.configSha,
        promptSha: run.promptSha,
        latencyMs: run.latencyMs,
        usage: run.usage,
        toolsEnabled: run.toolsEnabled,
      },
      calls,
      resolve: { call: calls.find((c) => str(c.input.template) === RESOLVE_TEMPLATE), terms: termsOf(calls, path) },
      retrieve: search && searchRecord ? { call: search, units: (searchRecord.output.units as RetrievedUnit[] | undefined) ?? [] } : undefined,
      hop:
        hopCall && hopRecord && path
          ? { call: hopCall, template: str(hopCall.input.template) ?? "", rows: (hopRecord.output.rows as Record<string, string>[] | undefined) ?? [], total: str(hopRecord.output.total), path }
          : undefined,
      answer,
      check: run.check,
      regions,
      provenance: h.provenance,
    },
  };
}

async function regionOf(api: RpApi, c: Claim): Promise<Sc5Region> {
  const r = await api.getRegion(c.unitId, c.fieldId);
  return {
    claim: c,
    fileName: r.fileName,
    page: r.page,
    image: r.url ? { src: r.url, dpi: 150 } : undefined,
    pageWidth: r.pageWidth,
    pageHeight: r.pageHeight,
    unit: toRect(r.unitBboxes.find((b) => b.page === r.page) ?? r.bbox),
    field: toRect(r.fieldBbox ?? r.bbox),
  };
}

// ------------------------------------------------------------------------------------------- timeline maths

/** The run split into model time (derived: time not in a tool call) and the tool-call window. */
export function runSegments(p: Pick<Sc5Payload, "calls" | "run">): { kind: "model" | "tools"; from: number; to: number }[] {
  const total = p.run.latencyMs;
  const spans = p.calls.map((c) => [c.at, c.at + c.ms] as const).sort((a, b) => a[0] - b[0]);
  const merged: [number, number][] = [];
  for (const [a, b] of spans) {
    const last = merged.at(-1);
    if (last && a <= last[1]) last[1] = Math.max(last[1], b);
    else merged.push([a, b]);
  }
  const out: { kind: "model" | "tools"; from: number; to: number }[] = [];
  let t = 0;
  for (const [a, b] of merged) {
    if (a > t) out.push({ kind: "model", from: t, to: a });
    out.push({ kind: "tools", from: a, to: b });
    t = b;
  }
  if (total > t) out.push({ kind: "model", from: t, to: total });
  return out;
}

/** What the hop step's caption says, from the run. */
export function hopCaption(p: Pick<Sc5Payload, "hop">): string {
  if (!p.hop) return "No hop in this run.";
  if (p.hop.template.startsWith("qi_sum")) return "One graph hop sums the governed operands.";
  if (p.hop.template.startsWith("coupon_terms")) return "One graph hop reaches the footnote.";
  return `One graph hop: ${p.hop.template}.`;
}

export function resolveCaption(p: Pick<Sc5Payload, "resolve">): string {
  return p.resolve.call ? "Resolve the words against the ontology." : "No concept lookup in this run; the tool inputs name the ontology terms.";
}

export function citeCaption(p: Pick<Sc5Payload, "answer" | "regions">): string {
  const figures = p.answer.claims.length;
  if (figures && p.regions.length === figures) return "Every figure carries its document, page and region.";
  return `${p.regions.length} of ${figures} figures open a region.`;
}

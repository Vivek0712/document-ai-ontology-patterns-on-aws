/**
 * SC-4 payload: the metadata one evidence unit carries (set once, at ingestion, by named stages), the
 * `search_units` call of the gate question's logged evidence-units run (its query echo: the filter, K, the fusion
 * settings; its results: dense, BM25 and fused scores), and `GET /inspect/filters` (`_count` with that exact
 * filter). The unit is the figure the lending stories follow (FOLLOW_SUBJECT).
 */
import type { FilterInspection, RetrievedUnit, StageRecord, Unit } from "@contracts/api";
import { waitForLanes } from "../../api/runs";
import type { Provenance } from "../../design";
import { type Rect, toRect, union } from "../../viz/geom";
import type { Loaded, StoryContext } from "../engine/types";
import { FOLLOW_SUBJECT, gateQuestion, headlineRuns, runsSource } from "./lending";
import { documentsOfType } from "./subjects";

/** One metadata field on the unit record, the stage that wrote it, and whether the logged filter uses it. */
export interface MetaRow {
  key: string;
  value: string;
  stage: string;
  stageMs?: number;
  inFilter: boolean;
}

export interface Sc4Payload {
  runId: string;
  s3Key: string;
  callId: string;
  unit: Pick<Unit, "unitId" | "label" | "fileName"> & { page: number; rect: Rect; pageWidth: number; pageHeight: number; image?: { src: string; dpi: number } };
  groups: { title: "Classify" | "Scope" | "Trace"; rows: MetaRow[] }[];
  echo: { index: string; k: number; subQueryK: number; weights: Record<string, number>; normalization: string; embedModel?: string; filter: Record<string, unknown> };
  results: RetrievedUnit[];
  inspection: FilterInspection;
  provenance: Provenance;
}

/**
 * Which pipeline stage writes each unit field (register copies the package ACL; classify sets
 * docType; the builder sets concepts, period, breadcrumb, boxes; the PII gate sets piiFlag).
 */
const WRITER: Record<string, string> = {
  docType: "classify",
  fieldConcepts: "units",
  period: "units",
  piiFlag: "pii_gate",
  allowedPrincipals: "register",
  packageId: "register",
  breadcrumb: "units",
  page: "units",
  bbox: "units",
  pipelineVersion: "route",
  configSha: "units",
};

/** The field names a bool filter's clauses test (`term`, `terms`, in `filter` and `must_not`). */
export function filterKeys(filter: Record<string, unknown>): Set<string> {
  const keys = new Set<string>();
  const walk = (v: unknown) => {
    if (Array.isArray(v)) v.forEach(walk);
    else if (v && typeof v === "object")
      for (const [k, x] of Object.entries(v)) {
        if ((k === "term" || k === "terms") && x && typeof x === "object") Object.keys(x).forEach((f) => keys.add(f));
        else walk(x);
      }
  };
  walk(filter);
  return keys;
}

/**
 * The two request bodies `search_units` sends, rebuilt from the call's echo exactly as
 * `evidence_core/retrieval.py` builds them (`dense_query`, `bm25_query`). The query vector is not logged, so
 * it is named, not shown.
 */
export function subQueries(e: Sc4Payload["echo"], text: string): { dense: unknown; bm25: unknown } {
  const source = { excludes: ["embedding"] };
  return {
    dense: { size: e.subQueryK, _source: source, query: { knn: { embedding: { vector: `‹${e.embedModel ?? "query"} embedding of the query›`, k: e.subQueryK, filter: e.filter } } } },
    bm25: { size: e.subQueryK, _source: source, query: { bool: { must: [{ match: { searchText: { query: text } } }], filter: [e.filter] } } },
  };
}

export async function loadSc4({ api, packageId }: StoryContext): Promise<Loaded<Sc4Payload>> {
  const pkg = (await documentsOfType(api, FOLLOW_SUBJECT.docType, packageId))[0]?.packageId ?? packageId;
  await waitForLanes(api, pkg);
  const h = await headlineRuns(api, pkg, gateQuestion("sc-4").questionId);
  const run = h.runs["2.3.1"];
  const call = run.toolCalls.find((c) => c.tool === "search_units" && Array.isArray(c.output.units) && (c.output.units as unknown[]).length);
  if (!call) throw new Error(`Run ${run.runId} made no search_units call that returned units.`);
  const results = call.output.units as RetrievedUnit[];
  const echo = call.output.query as Sc4Payload["echo"];
  const hit = results.find((u) => u.docType === FOLLOW_SUBJECT.docType && u.label.includes(FOLLOW_SUBJECT.borrower) && u.fields.some((f) => f.concept === FOLLOW_SUBJECT.concept));
  if (!hit) throw new Error(`The logged search returned no ${FOLLOW_SUBJECT.concept} unit for ${FOLLOW_SUBJECT.borrower}.`);
  const [view, doc, inspection, region] = await Promise.all([api.getUnit(hit.unitId), api.getDocument(hit.docId), api.inspectFilters(run.runId, call.callId), api.getRegion(hit.unitId)]);
  const u = view.unit;
  const stages = doc.lanes[u.pipelineVersion]?.stages ?? [];
  const keys = filterKeys(echo.filter);
  const row = (key: string, value: string): MetaRow => {
    const stage = WRITER[key];
    const rec = stages.find((s: StageRecord) => s.stageId === stage);
    return { key, value, stage, stageMs: rec?.durationMs, inFilter: keys.has(key) };
  };
  const page = u.pages[0];
  return {
    source: runsSource(api, [run.runId], run.createdAt),
    payload: {
      runId: run.runId,
      s3Key: run.s3Key,
      callId: call.callId,
      unit: { unitId: u.unitId, label: u.label, fileName: u.fileName, page, rect: union(u.bboxes.filter((b) => b.page === page).map(toRect))!, pageWidth: region.pageWidth, pageHeight: region.pageHeight, image: region.url ? { src: region.url, dpi: 150 } : undefined },
      groups: [
        { title: "Classify", rows: [row("docType", u.docType ?? "none"), row("fieldConcepts", u.fieldConcepts.join(", "))] },
        { title: "Scope", rows: [row("period", u.period ?? "none"), row("piiFlag", u.piiFlag), row("allowedPrincipals", u.allowedPrincipals.join(", ")), row("packageId", u.packageId)] },
        {
          title: "Trace",
          rows: [
            row("breadcrumb", u.breadcrumb.join(" › ")),
            row("page", String(page)),
            row("bbox", toRect(u.bboxes[0]).map((n) => n.toFixed(1)).join(", ")),
            row("pipelineVersion", u.pipelineVersion),
            row("configSha", u.provenance.configSha),
          ],
        },
      ],
      echo,
      results,
      inspection,
      provenance: h.provenance,
    },
  };
}

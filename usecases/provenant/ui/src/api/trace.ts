/**
 * The follow-the-number trace (W-08, SC-9): one cited figure → its field, unit, engine
 * blocks, page region, Bronze object and the pipeline and run that produced it.
 *
 * `/trace` is a planned server-side composer; until it is deployed the browser composes the same
 * shape from v1 routes it already has (lineage, region, package, document, pipelines, run). Every value below is
 * a field of one of those responses; `sources` lists each route and artifact for the receipts drawer.
 */
import type { BBox, Claim, Lineage, LineageStep, RunRecord } from "@contracts/api";
import type { RpApi } from "./types";

export interface TraceSource {
  label: string;
  /** the route that returned it */
  route: string;
  /** the S3 key (or table) behind it */
  key?: string;
  versionId?: string;
  sha256?: string;
}

export interface TraceBlock {
  pointer: string;
  engineLabel?: string;
  role?: string | null;
  text?: string;
  bbox?: BBox;
  artifactKey?: string;
}

export interface TraceOperand {
  unitId: string;
  fieldId: string;
  value: string;
  label: string;
}

export interface Trace {
  claim: { text: string; value: string; resolvable: boolean; reason?: string; lane: string; label?: string };
  /** the graph hop that summed this figure into the answer's total, when the run made one */
  operand?: { template: string; total: string; operands: TraceOperand[] };
  field?: { fieldId: string; label: string; value: string; concept?: string; period?: string; confidence?: number | null; source?: string; bbox?: BBox };
  unit: { unitId: string; label: string; unitType: string; breadcrumb: string[]; memberCount?: number; piiFlag: string; bbox?: BBox };
  blocks: { engine: string; items: TraceBlock[]; artifactKey?: string; engineVersion?: string };
  /** the v1 lineage steps, in order: field, unit, blocks, region, page_image, source, index, graph */
  steps: LineageStep[];
  region: { page: number; bbox: BBox; unitBbox: BBox; pageWidth: number; pageHeight: number; image: { url?: string; dpi: number; key: string }; heldRegions: BBox[] };
  source: {
    fileName: string;
    bronzeKey: string;
    versionId?: string;
    sha256: string;
    sizeBytes?: number;
    inBronzeAt?: string;
    /** the sha256 `register` wrote on the DocumentItem when the object landed in Bronze */
    registerSha256?: string;
    /** `source.sha256 === DocumentItem.sha256`, computed here, never assumed */
    matchesRegister: boolean;
  };
  index?: { index: string; aossId: string; indexedAt?: string };
  graph?: { namedGraph: string; unitIri: string; fieldIri?: string; factConcept?: string };
  pipeline: {
    pipelineVersion: string;
    configSha: string;
    preprocessingSha?: string;
    /** from the stage record that built the unit; "local" on a local run */
    codeSha?: string;
    engineVersion?: string;
    ontologyVersion: string | null;
    /** `preprocessing.ontology.governance` from the lane config; null until a lane config adds it */
    governance: { source: "git" | "coa"; ontologyId: string } | null;
  };
  run?: { runId: string; modelId: string; promptSha: string; corpusHash: string; startedAt: string };
  sources: TraceSource[];
}

/** What the naive lane's citation for the same figure can be followed to (SC-9 step 8). */
export interface TraceContrast {
  lane: "1.0";
  runId: string;
  claim: Claim;
  /** the chunk's own lineage kinds, as the API returns them (unit, blocks, source, index) */
  stops: { kind: LineageStep["kind"]; title: string; summary: string }[];
  /** the kinds a cited field has and the chunk lacks */
  missing: LineageStep["kind"][];
}

export interface TraceRequest {
  unitId: string;
  fieldId?: string;
  /** the run whose claim this is: adds the claim, the operand row and the run versions */
  runId?: string;
}

const stepOf = (l: Lineage, kind: LineageStep["kind"]) => l.steps.find((s) => s.kind === kind);
const str = (v: unknown): string | undefined => (typeof v === "string" ? v : undefined);
const num = (v: unknown): number | undefined => (typeof v === "number" ? v : undefined);

/** The answer claim that cites (unitId, fieldId), if the run made one. */
export function claimOf(run: RunRecord | undefined, unitId: string, fieldId?: string): Claim | undefined {
  return run?.answer?.claims.find((c) => c.unitId === unitId && (fieldId === undefined || c.fieldId === fieldId));
}

/** The graph_hop call that produced the answer's total (its operands carry unit and field ids). */
export function operandOf(run: RunRecord | undefined): Trace["operand"] {
  const call = run?.toolCalls.find((c) => c.tool === "graph_hop" && Array.isArray(c.output.operands) && (c.output.operands as unknown[]).length > 0);
  if (!call) return undefined;
  const out = call.output as { template?: string; total?: string; operands: TraceOperand[] };
  return { template: out.template ?? String(call.input.template ?? ""), total: out.total ?? "", operands: out.operands };
}

export async function composeTrace(api: RpApi, req: TraceRequest): Promise<Trace> {
  const [lineage, region, run] = await Promise.all([
    api.getLineage(req.unitId, req.fieldId),
    api.getRegion(req.unitId, req.fieldId),
    req.runId ? api.getRun(req.runId) : Promise.resolve(undefined),
  ]);
  const [pkg, doc, pipelines] = await Promise.all([api.getPackage(lineage.packageId), api.getDocument(region.docId), api.listPipelines()]);
  const pv = lineage.pipelineVersion;

  const fieldStep = stepOf(lineage, "field");
  const unitStep = stepOf(lineage, "unit");
  const blocksStep = stepOf(lineage, "blocks");
  const imageStep = stepOf(lineage, "page_image");
  const sourceStep = stepOf(lineage, "source");
  const indexStep = stepOf(lineage, "index");
  const graphStep = stepOf(lineage, "graph");
  if (!unitStep || !sourceStep) throw new Error(`lineage of ${req.unitId} has no unit or source step`);

  const registered = pkg.documents.find((d) => d.docId === region.docId);
  const src = sourceStep.data;
  const sha256 = str(src.sha256) ?? "";
  const stages = doc.lanes[pv]?.stages ?? [];
  const unitsStage = stages.find((s) => s.stageId === "units");
  const extractStage = stages.find((s) => s.stageId === "extract");
  const item = pipelines.items.find((p) => p.pipelineVersion === pv);
  const claim = claimOf(run, req.unitId, req.fieldId);
  const imageArtifact = imageStep?.artifacts.find((a) => a.kind === "page_image");
  const fd = fieldStep?.data ?? {};
  const ud = unitStep.data;

  return {
    claim: {
      text: claim?.text ?? str(fd.value) ?? region.value ?? "",
      value: claim?.value ?? str(fd.valueNumber) ?? "",
      resolvable: claim?.resolvable ?? true,
      reason: claim?.reason,
      lane: pv,
      label: claim?.label ?? region.label,
    },
    operand: operandOf(run),
    field: fieldStep
      ? {
          fieldId: str(fd.fieldId) ?? req.fieldId ?? "",
          label: str(fd.label) ?? "",
          value: str(fd.value) ?? "",
          concept: str(fd.concept),
          period: str(fd.period),
          confidence: num(fd.confidence) ?? null,
          source: str(fd.source),
          bbox: fieldStep.bbox,
        }
      : undefined,
    unit: {
      unitId: lineage.unitId,
      label: str(ud.label) ?? region.label,
      unitType: str(ud.unitType) ?? region.unitType,
      breadcrumb: Array.isArray(ud.breadcrumb) ? (ud.breadcrumb as string[]) : [],
      memberCount: num(ud.memberCount),
      piiFlag: str(ud.piiFlag) ?? region.piiFlag,
      bbox: unitStep.bbox,
    },
    blocks: {
      engine: str(blocksStep?.data.engine) ?? region.engine ?? "",
      items: Array.isArray(blocksStep?.data.items) ? (blocksStep!.data.items as TraceBlock[]) : [],
      artifactKey: blocksStep?.artifacts[0]?.key,
      engineVersion: extractStage?.versions?.engineVersion,
    },
    steps: lineage.steps,
    region: {
      page: region.page,
      bbox: region.fieldBbox ?? region.bbox,
      unitBbox: region.unitBboxes[0] ?? region.bbox,
      pageWidth: region.pageWidth,
      pageHeight: region.pageHeight,
      image: { url: region.url, dpi: num(imageStep?.data.dpi) ?? 150, key: region.pageImageKey },
      heldRegions: region.heldRegions,
    },
    source: {
      fileName: str(src.fileName) ?? region.fileName,
      bronzeKey: str(src.bronzeKey) ?? "",
      versionId: str(src.versionId),
      sha256,
      sizeBytes: num(src.sizeBytes),
      inBronzeAt: str(src.inBronzeAt),
      registerSha256: registered?.sha256,
      matchesRegister: Boolean(sha256) && sha256 === registered?.sha256,
    },
    index: indexStep ? { index: str(indexStep.data.index) ?? "", aossId: str(indexStep.data.aossId) ?? "", indexedAt: str(indexStep.data.indexedAt) } : undefined,
    graph: graphStep ? { namedGraph: str(graphStep.data.namedGraph) ?? "", unitIri: str(graphStep.data.unitIri) ?? "", fieldIri: str(graphStep.data.fieldIri), factConcept: str(graphStep.data.factConcept) } : undefined,
    pipeline: {
      pipelineVersion: pv,
      configSha: lineage.versions.configSha ?? item?.configSha ?? "",
      preprocessingSha: item?.blockShas.preprocessing,
      codeSha: unitsStage?.versions?.codeSha,
      engineVersion: lineage.versions.engineVersion ?? undefined,
      ontologyVersion: lineage.versions.ontologyVersion ?? null,
      governance: null,
    },
    run: run ? { runId: run.runId, modelId: run.modelId, promptSha: run.promptSha, corpusHash: run.corpusHash, startedAt: run.createdAt } : undefined,
    sources: [
      { label: "Lineage", route: `GET /units/{unitId}/lineage${req.fieldId ? "?field=" : ""}`, key: unitStep.artifacts[0]?.key, versionId: unitStep.artifacts[0]?.versionId, sha256: unitStep.artifacts[0]?.sha256 },
      { label: "Region", route: "GET /region/{unitId}?field=", key: region.pageImageKey, versionId: imageArtifact?.versionId, sha256: imageArtifact?.sha256 },
      { label: "Engine output", route: "lineage · blocks", key: blocksStep?.artifacts[0]?.key, versionId: blocksStep?.artifacts[0]?.versionId, sha256: blocksStep?.artifacts[0]?.sha256 },
      { label: "Bronze object", route: "lineage · source", key: str(src.bronzeKey), versionId: str(src.versionId), sha256 },
      { label: "DocumentItem (register)", route: "GET /packages/{packageId}", key: `rp-documents · ${region.docId}`, sha256: registered?.sha256 },
      { label: "Stage records", route: "GET /documents/{docId}", key: `rp-stages · ${region.docId}#${pv}` },
      { label: "Pipeline", route: "GET /pipelines", key: item?.configKey, versionId: item?.configVersionId, sha256: item?.configSha },
      ...(run ? [{ label: "Run record", route: "GET /runs/{runId}", key: run.s3Key }] : []),
    ],
  };
}

/** The naive lane's claim for the same figure, and how far its lineage goes (SC-9 step 8, W-08 contrast). */
export async function composeContrast(api: RpApi, runId: string, value: string, cited: Trace): Promise<TraceContrast | undefined> {
  const run = await api.getRun(runId);
  const claim = run.answer?.claims.find((c) => c.value === value || c.text.replace(/[$,]/g, "") === value.replace(/,/g, ""));
  if (!claim) return undefined;
  const lineage = await api.getLineage(claim.unitId).catch(() => undefined);
  const stops = (lineage?.steps ?? []).map((s) => ({ kind: s.kind, title: s.title, summary: s.summary }));
  const have = new Set(stops.map((s) => s.kind));
  return { lane: "1.0", runId, claim, stops, missing: cited.steps.map((s) => s.kind).filter((k) => !have.has(k)) };
}

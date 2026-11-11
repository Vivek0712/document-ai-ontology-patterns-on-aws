import type { Engine, StageCell, StageGroup, StageId, StageState } from "@contracts/api";
import { STAGE_ORDER } from "@contracts/api";

export const GROUPS: { group: StageGroup; title: string; stages: StageId[] }[] = [
  { group: "upload", title: "upload", stages: ["register"] },
  { group: "extract", title: "extract", stages: ["route", "extract"] },
  { group: "units", title: "evidence units", stages: ["classify", "units"] },
  { group: "gate", title: "PII gate", stages: ["pii_gate", "promote"] },
  { group: "index", title: "index and graph", stages: ["index", "graph", "ready"] },
];

export const STAGE_SHORT: Record<StageId, string> = {
  register: "register",
  route: "route",
  extract: "extract",
  classify: "classify",
  units: "units",
  pii_gate: "PII gate",
  promote: "promote",
  index: "index",
  graph: "graph",
  ready: "ready",
};

/** The AWS service that does the work for an engine (cell tag on the board). */
export const ENGINE_SERVICE: Record<Engine, string> = {
  "text-layer": "Lambda",
  docling: "Fargate · SQS",
  textract: "Textract",
  pypdf: "Lambda",
  "skos-indicators": "Lambda",
  nova: "Bedrock",
  "eu-builder": "Lambda",
  "fixed-chunker": "Lambda",
  comprehend: "Comprehend",
  pdfium: "Lambda · S3",
  "titan-v2+aoss": "Bedrock · OpenSearch",
  neptune: "Neptune",
  lambda: "Lambda",
};

export const TERMINAL: StageState[] = ["SUCCEEDED", "FAILED", "SKIPPED"];

export function stageIndex(id: StageId): number {
  return STAGE_ORDER.indexOf(id);
}

export function cellFor(cells: StageCell[] | undefined, id: StageId): StageCell | undefined {
  return cells?.find((c) => c.stageId === id);
}

/** Stages whose output can be drawn on the page (layout blocks, units, held regions). */
export const PAGE_STAGES: StageId[] = ["extract", "units", "pii_gate", "promote"];

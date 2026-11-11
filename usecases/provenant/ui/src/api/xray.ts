/**
 * X-ray payload (`XrayPage`, contracts/api.ts), produced by services/lambdas/xray
 * (GET /documents/{docId}/lanes/1.0/xray?page=&size=&runId=) and, for mock mode, by `make xray-fixtures`
 * over the real local pipeline outputs. Inspection only: never stored in units, the index or citations.
 *
 * Extensions beyond the spec contract are marked "ext": the v2.3.1 evidence units on the page (`units`), held
 * regions, per-chunk coverage and a few convenience fields. Coordinates: PDF points, top-left origin.
 */

export interface XrayRect {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface XrayChunk {
  /** control: "c-01" (the stored v1.0 unit's ordinal) · what-if: "w-01" (never a stored-looking id) */
  chunkId: string;
  /** control: "chunk 827d37:c-01" (doc hash + ordinal; never file or page) · what-if: "what-if #1" */
  label: string;
  ordinal: number;
  tokenStart: number;
  tokenEnd: number;
  /** line-merged */
  rects: XrayRect[];
  /** ext: set for control chunks only (a what-if chunk is not a stored unit) */
  unitId: string | null;
  /** ext */
  tokens: number;
  /** ext: pages the chunk spans */
  pages: number[];
  /** ext: aligned characters / characters on this page */
  coverage: number | null;
}

export interface XrayToken {
  i: number;
  /** "[held]" when dropped */
  text: string;
  rect: XrayRect | null;
  chunkOrdinals: number[];
  line: number;
}

export type XrayLineKind = "label" | "value" | "other";

export interface XrayLine {
  /** stream order */
  ordinal: number;
  kind: XrayLineKind;
  text: string;
  rects: XrayRect[];
  tokenStart: number;
  tokenEnd: number;
  chunkOrdinals: number[];
  /** the 2.3.1 field whose label or value this line is */
  fieldId?: string;
  /** ext: union of rects */
  bbox: XrayRect | null;
}

export interface XrayPair {
  fieldId: string;
  /** null when the 2.3.1 field carries no concept */
  concept: string | null;
  label: XrayLine;
  /** value line inside the 2.3.1 field bbox */
  geometricValue: XrayLine | null;
  /** i-th amount line for the i-th numbered label line, in stream order: a hypothesis */
  positionalValue: XrayLine | null;
  positionalProvenance: "derived";
  tokenDistance: number | null;
  sameChunk: boolean;
  /** ext */
  unitId: string;
  labelIndex: number;
  fieldBbox: XrayRect | null;
  /** ext: the position-only reading lands on the printed value's own line */
  positionalSameLine: boolean;
  /** ext: …or on a line that prints the same figure (W-2 Boxes 3 and 5): "same figure, different box" */
  positionalSameValue: boolean;
}

export interface XrayAnswerNumber {
  runId: string;
  /** canonical "98,400.00" */
  value: string;
  /** XrayLine.ordinal; empty = not on this page */
  matchedLines: number[];
  /** fieldId, when the figure equals that pair's positional value */
  isPositionalValueOf?: string;
}

export interface XrayMember {
  memberId: string;
  role: string | null;
  page: number;
  bbox: XrayRect;
  text: string | null;
  phase: string | null;
}

export interface XrayField {
  fieldId: string;
  label: string;
  value: string;
  concept: string | null;
  bbox: XrayRect;
}

/** ext: a v2.3.1 Gold evidence unit on this page */
export interface XrayUnit {
  unitId: string;
  label: string;
  unitType: string;
  bboxes: XrayRect[];
  members: XrayMember[];
  fields: XrayField[];
}

export interface XraySources {
  bronze: { key: string; versionId: string | null; sha256: string | null };
  units: { key: string; versionId: string | null };
  pdfium: string;
  splitter: string;
  codeSha: string;
  /** ext */
  evidence?: { key: string };
  textLayer?: string;
  packageId?: string;
  fileName?: string;
  heldUnitIds?: string[];
  /** live: computed by rp-xray on AWS · local-run: exported by `make xray-fixtures` */
  mode: "live" | "local-run";
  build?: string;
  exportedAt?: string;
  /** the logged naive run `answerNumbers` came from (fixture export: a local run on Bedrock, never AWS-deployed) */
  answerRun?: { runId: string; qid: string; provenance: "local-run" | "measured"; label?: string; correct?: number; n?: number; file?: string };
}

export interface XrayPage {
  schema: "rp.xray/1";
  docId: string;
  page: number;
  pageCount: number;
  pageWidth: number;
  pageHeight: number;
  frame: "pdf-points-top-left";
  lane: "1.0";
  sizeTokens: number;
  overlapPct: number;
  control: { sizeTokens: number; overlapPct: number; library: string };
  whatIf: boolean;
  /** ext: the real splitter reproduces the stored control chunks */
  splitterMatchesControl: boolean;
  /** ext: false for a scanned page (no characters for the 1.0 lane to read) */
  textLayer: boolean;
  /** aligned / total characters; null when the page has no text layer (nothing aligned is not 100 %) */
  coverage: number | null;
  /** ext: the sizes the API serves, control first (RP_XRAY_WHATIF_SIZES) */
  whatIfSizes: number[];
  /** coverage < 0.98 or a chunk not found: the UI disables the layer */
  warning?: string;
  chunkCount: number;
  chunks: XrayChunk[];
  tokens: XrayToken[];
  lines: XrayLine[];
  pairs: XrayPair[];
  answerNumbers?: XrayAnswerNumber[];
  heldTokensDropped: number;
  /** ext */
  heldRegions: XrayRect[];
  /** ext */
  units: XrayUnit[];
  image: { url: string; zoomUrl?: string; dpi: number };
  sources: XraySources;
  computedAt: string;
}

/** Below this alignment coverage the X-ray layer is disabled for the page. */
export const XRAY_COVERAGE_FLOOR = 0.98;

/** Every top-level key of `XrayPage`: the contract test compares this list with the real export. */
export const XRAY_PAGE_KEYS = [
  "schema", "docId", "page", "pageCount", "pageWidth", "pageHeight", "frame", "lane", "sizeTokens", "overlapPct",
  "control", "whatIf", "splitterMatchesControl", "textLayer", "coverage", "whatIfSizes", "warning", "chunkCount",
  "chunks", "tokens", "lines", "pairs", "answerNumbers", "heldTokensDropped", "heldRegions", "units", "image",
  "sources", "computedAt",
] as const satisfies readonly (keyof XrayPage)[];

/** Why the X-ray layer is off for a page, or null when it can be drawn (warning, low coverage, no text layer). */
export function xrayUnusable(x: XrayPage): string | null {
  if (!x.textLayer || x.coverage === null) return "no text layer";
  if (x.warning) return x.warning;
  if (x.coverage < XRAY_COVERAGE_FLOOR) return `character alignment ${x.coverage.toFixed(3)} is below ${XRAY_COVERAGE_FLOOR}`;
  return null;
}

/** Mock/replay export index written by scripts/xray_fixtures.py (dev only). */
export interface XrayIndex {
  schema: "rp.xray/1";
  generatedBy: string;
  generatedAt: string;
  provenance: "local-run";
  packageId: string;
  corpusHash?: string;
  build: string;
  engines: Record<string, string | undefined>;
  control: { sizeTokens: number; overlapPct: number };
  whatIfSizes: number[];
  documents: {
    docId: string;
    fileName: string;
    docType?: string;
    pageCount: number;
    sha256: string;
    chunks: number;
    evidenceUnits: number;
    heldUnitIds: string[];
    files: Record<string, Record<string, string>>;
  }[];
}

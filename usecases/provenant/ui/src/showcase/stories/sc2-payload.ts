/**
 * SC-2 payload: how the evidence-units builder put one table unit together, from its build report
 * (`GET /documents/{docId}/lanes/2.3.1/build`: every block's phase, phase B cosine against tau, marker links,
 * unit summaries, the invariants record) and the unit itself (`GET /units/{unitId}`), drawn on the page render
 * from the X-ray export. The subject is found by document type and period, then by shape: the first table unit
 * whose build used all three passes (a seed, a marker link and a phase B paragraph). Nothing is typed.
 */
import type { BuildAllocation, BuildReplay, MarkerLink, Unit } from "@contracts/api";
import { waitForLanes } from "../../api/runs";
import type { RpApi } from "../../api/types";
import type { Provenance } from "../../design";
import { type Rect, toRect, union } from "../../viz/geom";
import type { Loaded, StoryContext } from "../engine/types";
import { D1_SUBJECT, documentsOfType } from "./subjects";

export const LANE = "2.3.1";

export interface Sc2Page {
  page: number;
  image?: { src: string; src2x?: string; dpi: number };
  width: number;
  height: number;
}

export interface Sc2Half {
  unitId: string;
  page: number;
  rect: Rect;
  label: string;
}

export interface Sc2Payload {
  docId: string;
  fileName: string;
  packageId: string;
  build: Pick<BuildReplay, "embedModel" | "tau" | "phaseB" | "phaseCounts" | "mergedTables" | "invariants" | "sources" | "builtAt">;
  /** the unit the story builds */
  unit: Pick<Unit, "unitId" | "unitType" | "label" | "breadcrumb" | "pages" | "fields" | "period"> & { rect: Rect };
  page: Sc2Page;
  /** every block on the subject's page, in build order */
  blocks: (BuildAllocation & { rect: Rect })[];
  seed?: BuildAllocation & { rect: Rect };
  link?: MarkerLink & { markerRect?: Rect; footnoteRect?: Rect; fieldLabel?: string };
  /** a table printed across a page break: consecutive pages, same section; one unit per half unless merged */
  split?: { pages: Sc2Page[]; halves: Sc2Half[]; mergedTables: number };
  provenance: Provenance;
}

const ROLE_WORD: Record<string, string> = { table: "table", unit_label: "unit label", footnote: "footnote", text: "paragraph", caption: "caption", section_header: "heading", picture: "figure" };
export const roleWord = (role: string) => ROLE_WORD[role] ?? role.replace(/_/g, " ");

/** "table, unit label and footnote" */
export function listWords(words: string[]): string {
  const w = [...new Set(words)];
  return w.length <= 1 ? (w[0] ?? "") : `${w.slice(0, -1).join(", ")} and ${w.at(-1)}`;
}

/** The first table unit whose build used a seed, a marker link and a phase B join. */
export function subjectUnit(b: BuildReplay): string | undefined {
  const tables = b.units.filter((u) => u.unitType === "table_panel");
  return tables.find((u) => {
    const own = b.allocations.filter((a) => a.unitId === u.unitId);
    return own.some((a) => a.phase === "A") && own.some((a) => a.phase === "marker") && own.some((a) => a.phase === "B");
  })?.unitId;
}

async function pageOf(api: RpApi, docId: string, page: number): Promise<Sc2Page> {
  const x = await api.getXray(docId, page);
  return { page, image: { src: x.image.url, src2x: x.image.zoomUrl, dpi: x.image.dpi }, width: x.pageWidth, height: x.pageHeight };
}

export async function loadSc2({ api, packageId }: StoryContext): Promise<Loaded<Sc2Payload>> {
  const found = await documentsOfType(api, D1_SUBJECT.docType, packageId);
  for (const { packageId: pkg, documents } of found) {
    await waitForLanes(api, pkg);
    for (const doc of documents) {
      const build = await api.getBuild(doc.docId, LANE).catch(() => undefined);
      const unitId = build && subjectUnit(build);
      if (!build || !unitId) continue;
      const view = await api.getUnit(unitId);
      if (view.unit.period !== D1_SUBJECT.period) continue;
      return { source: sourceOf(api), payload: await compose(api, build, view.unit, doc.fileName), subjectPackageId: pkg };
    }
  }
  throw new Error(`No ${D1_SUBJECT.docType} for ${D1_SUBJECT.period} has a table unit built with a seed, a marker link and a phase B join.`);
}

async function compose(api: RpApi, build: BuildReplay, unit: Unit, fileName: string): Promise<Sc2Payload> {
  const pageNo = unit.pages[0];
  const blocks = build.allocations.filter((a) => a.page === pageNo && a.bbox).map((a) => ({ ...a, rect: toRect(a.bbox!) }));
  const seed = blocks.find((a) => a.unitId === unit.unitId && a.phase === "A" && a.role === "table");
  const raw = build.markerLinks.find((m) => m.unitId === unit.unitId);
  const link = raw && {
    ...raw,
    markerRect: raw.markerBox ? toRect(raw.markerBox) : undefined,
    footnoteRect: raw.footnoteBbox ? toRect(raw.footnoteBbox) : undefined,
    fieldLabel: unit.fields.find((f) => f.fieldId === raw.fieldId)?.label,
  };
  return {
    docId: build.docId,
    fileName,
    packageId: build.packageId,
    build: { embedModel: build.embedModel, tau: build.tau, phaseB: build.phaseB, phaseCounts: build.phaseCounts, mergedTables: build.mergedTables, invariants: build.invariants, sources: build.sources, builtAt: build.builtAt },
    unit: { unitId: unit.unitId, unitType: unit.unitType, label: unit.label, breadcrumb: unit.breadcrumb, pages: unit.pages, fields: unit.fields, period: unit.period, rect: union(unit.bboxes.filter((b) => b.page === pageNo).map(toRect))! },
    page: await pageOf(api, build.docId, pageNo),
    blocks,
    seed,
    link,
    split: await splitTable(api, build),
    provenance: api.mode === "http" ? "measured" : "local-run",
  };
}

/** Two table units on consecutive pages under the same section heading: the halves of one printed table. */
async function splitTable(api: RpApi, build: BuildReplay): Promise<Sc2Payload["split"]> {
  const tables = build.units.filter((u) => u.unitType === "table_panel" && u.pages.length === 1);
  const views = await Promise.all(tables.map((u) => api.getUnit(u.unitId)));
  const section = (u: Unit) => u.breadcrumb.join(" › ");
  for (const a of views) {
    const b = views.find((v) => v.unit.pages[0] === a.unit.pages[0] + 1 && section(v.unit) === section(a.unit));
    if (!b) continue;
    const halves = [a.unit, b.unit].map((u) => ({ unitId: u.unitId, page: u.pages[0], rect: union(u.bboxes.map(toRect))!, label: u.label }));
    const merged = build.units.filter((u) => u.unitId === a.unit.unitId || u.unitId === b.unit.unitId).reduce((n, u) => n + u.mergedTables, 0);
    return { pages: await Promise.all(halves.map((h) => pageOf(api, build.docId, h.page))), halves, mergedTables: merged };
  }
  return undefined;
}

function sourceOf(api: RpApi) {
  const live = api.mode === "http";
  return {
    mode: live ? ("live" as const) : ("local-run" as const),
    label: live ? "Live · build report" : "Replay of a local pipeline run",
    detail: live ? "GET /documents/{docId}/lanes/2.3.1/build on AWS" : "The eu_builder build report from the local pipeline snapshot (Titan V2 phase B, recorded); not AWS",
  };
}

// ------------------------------------------------------------------------------------------- captions

/** Phase A members that attach to the seed (not the seed itself). */
export const phaseAMembers = (p: Sc2Payload) => p.blocks.filter((b) => b.unitId === p.unit.unitId && b.phase === "A" && b !== p.seed);
/** Paragraphs phase B scored on this page: the ones that joined and the ones that lost to section text. */
export const phaseBCandidates = (p: Sc2Payload) => p.blocks.filter((b) => b.phase === "B" || (b.score !== null && b.score !== undefined));

export function passACaption(p: Sc2Payload): string {
  const words = phaseAMembers(p).map((b) => roleWord(b.role));
  if (!p.seed) return "Pass A: no table seed on this page.";
  if (!words.length) return `Pass A: the ${roleWord(p.seed.role)} seeds a unit.`;
  return `Pass A: the ${roleWord(p.seed.role)} seeds a unit; its ${listWords(words)} ${words.length > 1 ? "attach" : "attaches"}.`;
}

export function markerCaption(p: Sc2Payload): string {
  if (!p.link) return "No raised marker links a footnote into this unit.";
  const after = p.link.fieldLabel ? ` after ${p.link.fieldLabel}` : "";
  return `A raised ${p.link.marker}${after} pulls footnote ${p.link.marker} into the unit.`;
}

export function passCCaption(p: Sc2Payload): string {
  const orphan = p.blocks.filter((b) => !b.unitId);
  return orphan.length ? `Pass C: ${orphan.length} blocks are left without a unit.` : "Pass C: the rest becomes section text. Nothing is dropped.";
}

export function unitCaption(p: Sc2Payload): string {
  const own = p.blocks.filter((b) => b.unitId === p.unit.unitId);
  const order = ["table", "unit_label", "caption", "footnote", "text"];
  const words = own.map((b) => b.role).sort((a, b) => order.indexOf(a) - order.indexOf(b)).map(roleWord);
  return `One unit: ${listWords(words)}.`;
}

export function splitCaption(p: Sc2Payload): string {
  if (!p.split) return "No table in this report is split across a page break.";
  return p.split.mergedTables > 0 ? "The halves of the split table become one unit across the page break." : "This build keeps each half of the split table as its own unit.";
}

export function checksCaption(p: Sc2Payload): string {
  const inv = p.build.invariants;
  if (!inv) return "This build report carries no invariants record.";
  if (inv.mode === "off") return "The anchoring and consistency checks are off in this lane.";
  return `The checks ran in ${inv.mode} mode: ${inv.violations.length} violations logged.`;
}

/**
 * A member's connector to its seed, routed through the gutter left of every block on the page (orthogonal: out of
 * the member's left edge, down or up the gutter, into the seed's left edge), so it never crosses a line of text.
 */
export function gutterRoute(from: Rect, to: Rect, blocks: Rect[], gapPt = 10): [number, number][] {
  const gx = Math.min(...blocks.map((b) => b[0]), from[0], to[0]) - gapPt;
  const fy = (from[1] + from[3]) / 2;
  const inset = Math.min(8, (to[3] - to[1]) / 4);
  const ty = fy < to[1] ? to[1] + inset : fy > to[3] ? to[3] - inset : fy;
  return [
    [from[0], fy],
    [gx, fy],
    [gx, ty],
    [to[0], ty],
  ];
}

/** The invariants note as rows: the build record's note ("I1: deferred (pipelineVersion 2.4.0, …)") -> ["I1", "deferred to 2.4.0"]. */
export function invariantRows(note: string | null | undefined): [string, string][] {
  return (note ?? "")
    .split(/;\s*/)
    .map((part) => part.trim())
    .filter(Boolean)
    .map((part) => {
      const [key, ...rest] = part.split(":");
      const value = rest.join(":").trim();
      const lane = /pipelineVersion\s+([\d.]+)/.exec(value)?.[1];
      return [key.trim(), lane && value.startsWith("deferred") ? `deferred to ${lane}` : value] as [string, string];
    });
}

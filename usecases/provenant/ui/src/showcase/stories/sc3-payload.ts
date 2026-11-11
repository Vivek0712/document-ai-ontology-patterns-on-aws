/**
 * SC-3 payload: the coupon of one trade confirmation, its raised marker, the footnote on the next page, and the
 * step event the graph holds. One server composer call (`GET /showcases/sc-3/payload`: the coupon field, the
 * marker link, the classify output and `graph_hop coupon_terms_v1` with its bound path), plus the 1.0 lane's
 * X-ray of both pages for the naive contrast and their page renders. Property values are the graph's:
 * the UI only finds where each one is printed in the footnote, to point at it.
 */
import type { CouponTermsData, DocumentItem, GraphNeighbourhood, MarkerLink, ShowcasePayload } from "@contracts/api";
import { waitForLanes } from "../../api/runs";
import type { RpApi } from "../../api/types";
import { type XrayPage, xrayUnusable } from "../../api/xray";
import type { Provenance } from "../../design";
import { type Rect, toRect } from "../../viz/geom";
import type { ChunkSlice } from "../../viz/PageCanvas";
import { sliceOf } from "../../viz/xray";
import type { DataSource, Loaded, StoryContext } from "../engine/types";
import { D2_SUBJECT, documentsOfType } from "./subjects";

/** The step event's properties, in the order the template returns them. */
export const STEP_PROPERTIES = ["newRate", "effectiveFromYear", "triggerRating"] as const;
export type StepProperty = (typeof STEP_PROPERTIES)[number];

export interface Sc3Page {
  page: number;
  image?: { src: string; src2x?: string; dpi: number };
  width: number;
  height: number;
  /** the 1.0 lane's control chunks on this page (X-ray), or why they cannot be drawn */
  chunks: ChunkSlice[];
  xrayWarning: string | null;
  xraySource: string;
}

export interface Sc3Payload {
  security: string;
  document: Pick<DocumentItem, "docId" | "fileName" | "packageId" | "pageCount" | "sha256">;
  data: CouponTermsData;
  coupon: { label: string; value: string; concept: string; page: number; rect: Rect };
  link: MarkerLink & { markerRect: Rect; footnoteRect: Rect };
  /** the unit's boxes per page (the boundary spans both) */
  unitRects: { page: number; rect: Rect }[];
  pages: Sc3Page[];
  /** where each property's value is printed in the footnote text, by token index */
  spans: Record<StepProperty, number | undefined>;
  footnoteTokens: string[];
  /** the naive chunk(s) holding the coupon and the footnote, from the X-ray */
  naive: { couponChunks: string[]; footnoteChunks: string[] };
  path?: GraphNeighbourhood;
  composer: Pick<ShowcasePayload, "payloadSha" | "sources" | "provenance" | "mode">;
  provenance: Provenance;
}

const numeric = (s: string) => Number(s.replace(/[^\d.-]/g, ""));

/**
 * The token of the footnote that prints each property's value: equal as a number (`6` ↔ `6.00%`), or equal as
 * text (`BBB-`). The leading marker digit is skipped. Display only: the values themselves are the graph's.
 */
export function spansOf(tokens: string[], marker: string, event: Record<string, string>): Record<StepProperty, number | undefined> {
  const start = tokens[0] === marker ? 1 : 0;
  const used = new Set<number>();
  const find = (value: string | undefined): number | undefined => {
    if (value === undefined) return undefined;
    for (let i = start; i < tokens.length; i++) {
      if (used.has(i)) continue;
      const t = tokens[i];
      const hit = /\d/.test(value) && /\d/.test(t) ? numeric(t) === numeric(value) : t === value;
      if (hit) {
        used.add(i);
        return i;
      }
    }
    return undefined;
  };
  return Object.fromEntries(STEP_PROPERTIES.map((k) => [k, find(event[k])])) as Record<StepProperty, number | undefined>;
}

const overlaps = (a: Rect, b: Rect) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

/** Control chunks of an X-ray page whose slices overlap `rect`. */
export function chunksAt(x: XrayPage | undefined, rect: Rect): string[] {
  return (x?.chunks ?? []).filter((c) => c.rects.some((r) => overlaps(toRect(r), rect))).map((c) => c.chunkId);
}

export async function loadSc3({ api, packageId }: StoryContext): Promise<Loaded<Sc3Payload>> {
  const found = await documentsOfType(api, D2_SUBJECT.docType, packageId);
  const pkg = found.find((f) => f.documents.length)?.packageId;
  if (!pkg) throw new Error("No package you can read holds a trade confirmation.");
  await waitForLanes(api, pkg);
  const composed = await api.getShowcasePayload<CouponTermsData>("sc-3", { packageId: pkg, security: D2_SUBJECT.security });
  const d = composed.data;
  const doc = found.find((f) => f.packageId === pkg)!.documents.find((x) => x.docId === d.docId);
  const field = d.couponField;
  if (!doc || !field?.bbox || !d.markerLink?.markerBox || !d.markerLink.footnoteBbox || !d.unitId) throw new Error(`The ${D2_SUBJECT.security} payload has no marker-linked coupon (stepEventParse: ${d.stepEventParse}).`);
  const unit = await api.getUnit(d.unitId);
  const pageNos = [...new Set([field.bbox.page, d.markerLink.footnotePage ?? field.bbox.page])].sort((a, b) => a - b);
  const xrays = await Promise.all(pageNos.map((n) => api.getXray(doc.docId, n).catch(() => undefined)));
  const pages: Sc3Page[] = pageNos.map((n, i) => {
    const x = xrays[i];
    const width = x?.pageWidth ?? unit.unit.pageWidth;
    const height = x?.pageHeight ?? unit.unit.pageHeight;
    if (!width || !height) throw new Error(`${doc.docId} p${n}: no page size in the X-ray or the unit.`);
    return {
      page: n,
      image: x ? { src: x.image.url, src2x: x.image.zoomUrl, dpi: x.image.dpi } : undefined,
      width,
      height,
      chunks: x && !xrayUnusable(x) ? x.chunks.map(sliceOf) : [],
      xrayWarning: x ? xrayUnusable(x) : "no X-ray for this page",
      xraySource: `GET /documents/${doc.docId}/lanes/1.0/xray?page=${n}`,
    };
  });
  const couponRect = toRect(field.bbox);
  const footnoteRect = toRect(d.markerLink.footnoteBbox);
  const xrayOf = (page: number) => xrays[pageNos.indexOf(page)];
  const event = (d.stepEvent ?? {}) as Record<string, string>;
  const tokens = (event.footnote ?? "").split(/\s+/).filter(Boolean);
  return {
    source: sc3Source(api, composed, doc),
    subjectPackageId: doc.packageId,
    payload: {
      security: d.security,
      document: { docId: doc.docId, fileName: doc.fileName, packageId: doc.packageId, pageCount: doc.pageCount, sha256: doc.sha256 },
      data: d,
      coupon: { label: field.label, value: field.value, concept: field.concept ?? "", page: field.bbox.page, rect: couponRect },
      link: { ...d.markerLink, markerRect: toRect(d.markerLink.markerBox), footnoteRect },
      unitRects: unit.unit.bboxes.map((b) => ({ page: b.page, rect: toRect(b) })),
      pages,
      spans: spansOf(tokens, d.markerLink.marker, event),
      footnoteTokens: tokens,
      naive: { couponChunks: chunksAt(xrayOf(field.bbox.page), couponRect), footnoteChunks: chunksAt(xrayOf(d.markerLink.footnotePage ?? 0), footnoteRect) },
      path: d.path ?? undefined,
      composer: { payloadSha: composed.payloadSha, sources: composed.sources, provenance: composed.provenance, mode: composed.mode },
      provenance: api.mode === "http" ? "measured" : "local-run",
    },
  };
}

function sc3Source(api: RpApi, p: ShowcasePayload<CouponTermsData>, doc: DocumentItem): DataSource {
  const live = api.mode === "http";
  return {
    mode: live ? "live" : "local-run",
    label: live ? `Live · ${doc.packageId}` : "Replay of a local pipeline run",
    detail: live
      ? `GET /showcases/sc-3/payload · payload sha ${p.payloadSha.slice(0, 12)}`
      : "The rp-api composer over the local pipeline snapshot: graph_hop on an in-process graph, not Neptune; not AWS",
    recordedAt: undefined,
  };
}

/** "…share a chunk" or "…are in different chunks", from the X-ray. */
export function naiveCaption(p: Pick<Sc3Payload, "naive" | "coupon">): string {
  const { couponChunks: a, footnoteChunks: b } = p.naive;
  if (!a.length || !b.length) return "The naive lane's chunks could not be drawn for these pages.";
  const shared = a.some((c) => b.includes(c));
  return shared ? "In the naive index the coupon and its footnote share a chunk, but carry no link." : "In the naive index the coupon and its footnote are in different chunks, with no link.";
}

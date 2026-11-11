/**
 * The PII gate's decisions per unit, per lane (SC-6, W-12).
 *
 * `/pii` is a planned server-side composer; until it is deployed the browser composes the same view from v1 routes:
 * the unit list and unit views (the gate's `PiiDecision` and the `UnitStatus` the index and graph stages wrote),
 * the document's stage records (the gate's `decisions.json`, the promoted Gold pages) and the Gold
 * `held_regions.json`. No span text is read or kept: hits carry type, score, digit count and offsets' length.
 *
 * Failures that change what a screen may claim are kept, not swallowed: `errors` names each missing input (the
 * lane config, a document's held regions), and screens show it. Unit views are fetched a few at a time (one
 * `/pii` call replaces all of this once that route is deployed).
 */
import type { BBox, PiiFlag, StageRecord, UnitType } from "@contracts/api";
import type { RpApi } from "./types";

/** `hold`: blocked in Silver. `pass`: no hit. `below_threshold`: hits, all masked under the rule (e.g. a last-4 SSN). */
export type PiiAction = "hold" | "pass" | "below_threshold";

export interface PiiHit {
  type: string;
  /** Comprehend's score for the entity */
  score: number;
  /** under the rule's digit threshold for its type */
  masked: boolean;
  /** characters in the span (never the span) */
  spanLength: number;
  digits: number;
}

export interface PiiUnitDecision {
  unitId: string;
  docId: string;
  fileName: string;
  docType?: string;
  label: string;
  unitType: UnitType;
  pipelineVersion: string;
  /** where the unit stays: a held unit never leaves Silver */
  tier: "silver" | "gold";
  piiFlag: PiiFlag;
  action: PiiAction;
  rule: string;
  hits: PiiHit[];
  /** written by the index stage (an AOSS `_id` on the UnitStatus row) */
  indexed: boolean;
  /** written by the graph stage (triples for this unit); null when the lane has no graph */
  inGraph: boolean | null;
  graphTriples?: number;
  aossId?: string;
  pages: number[];
  bboxes: BBox[];
  heldBboxes: BBox[];
  /** the gate's output object for this document and lane */
  decisions: { key: string; versionId?: string; sha256?: string };
}

export interface PiiGoldPage {
  docId: string;
  page: number;
  /** the masked Gold render (held regions filled black by promote) */
  url?: string;
  key: string;
  versionId?: string;
  heldRegions: BBox[];
  /** held_regions.json could not be read: the outlines are unknown (the render is still masked) */
  heldRegionsError?: string;
}

export interface PiiView {
  packageId: string;
  pipelineVersion: string;
  units: PiiUnitDecision[];
  counters: { units: number; held: number; masked: number; passed: number };
  goldPages: PiiGoldPage[];
  /** `preprocessing.piiGate` from the lane config */
  config?: { engine?: string; action?: string; rule?: string; minScore?: number };
  /** the lane config object the gate settings come from */
  configKey?: string;
  /** inputs that could not be read, e.g. "lane config: 404 …" */
  errors: string[];
}

/** Map with at most `limit` calls in flight. */
async function mapLimit<T, R>(items: T[], limit: number, fn: (x: T) => Promise<R>): Promise<R[]> {
  const out: R[] = new Array(items.length);
  let next = 0;
  await Promise.all(
    Array.from({ length: Math.min(limit, items.length) }, async () => {
      while (next < items.length) {
        const i = next++;
        out[i] = await fn(items[i]);
      }
    }),
  );
  return out;
}

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

export function actionOf(flag: PiiFlag, hits: number): PiiAction {
  if (flag === "held") return "hold";
  return flag === "masked" || hits > 0 ? "below_threshold" : "pass";
}

const stage = (stages: StageRecord[], id: string) => stages.find((s) => s.stageId === id);

export async function composePii(api: RpApi, packageId: string, pv: string): Promise<PiiView> {
  const errors: string[] = [];
  const [pkg, pipeline] = await Promise.all([
    api.getPackage(packageId),
    api.getPipeline(pv).catch((e: unknown) => {
      errors.push(`lane config ${pv}: ${message(e)}`);
      return undefined;
    }),
  ]);
  const perDoc = await mapLimit(pkg.documents, 4, async (doc) => {
      const [detail, list] = await Promise.all([api.getDocument(doc.docId), api.listUnits(doc.docId, pv)]);
      const stages = detail.lanes[pv]?.stages ?? [];
      const gate = stage(stages, "pii_gate")?.artifacts.find((a) => a.kind === "pii_decisions");
      const graphRuns = stage(stages, "graph")?.state === "SUCCEEDED";
      const views = await mapLimit(list.items, 6, (it) => api.getUnit(it.unitId));
      const units: PiiUnitDecision[] = views.map((v) => {
        const hits = (v.pii?.entities ?? []).map((h) => ({ type: h.type, score: h.score, masked: h.masked, spanLength: h.end - h.begin, digits: h.digits }));
        const flag = v.pii?.decision ?? v.unit.piiFlag;
        return {
          unitId: v.unit.unitId,
          docId: doc.docId,
          fileName: doc.fileName,
          docType: doc.docType,
          label: v.unit.label,
          unitType: v.unit.unitType,
          pipelineVersion: pv,
          tier: v.held ? "silver" : "gold",
          piiFlag: flag,
          action: actionOf(flag, hits.length),
          rule: v.pii?.rule ?? "",
          hits,
          indexed: Boolean(v.status?.aossId && v.status.indexedAt),
          inGraph: graphRuns ? (v.status?.graphTriples ?? 0) > 0 : null,
          graphTriples: v.status?.graphTriples,
          aossId: v.status?.aossId,
          pages: v.unit.pages,
          bboxes: v.unit.bboxes,
          heldBboxes: v.pii?.heldBboxes ?? [],
          decisions: { key: gate?.key ?? "", versionId: gate?.versionId, sha256: gate?.sha256 },
        };
      });
      // the Gold pages promote rendered, with the held regions it filled
      const images = stage(stages, "promote")?.artifacts.filter((a) => a.kind === "page_image") ?? [];
      let held: Record<string, BBox[]> = {};
      let heldRegionsError: string | undefined;
      if (images.length) {
        const dir = images[0].key.slice(0, images[0].key.lastIndexOf("/"));
        try {
          const art = await api.getArtifact(`${dir}/held_regions.json`);
          held = art.preview ? ((JSON.parse(art.preview) as { pages?: Record<string, BBox[]> }).pages ?? {}) : {};
        } catch (e) {
          heldRegionsError = message(e);
          errors.push(`held regions of ${doc.fileName}: ${heldRegionsError}`);
        }
      }
      const pages = await mapLimit(images, 4, async (img): Promise<PiiGoldPage> => {
        const view = await api.getArtifact(img.key).catch((e: unknown) => {
          errors.push(`Gold page ${img.page ?? 1} of ${doc.fileName}: ${message(e)}`);
          return undefined;
        });
        return { docId: doc.docId, page: img.page ?? 1, url: view?.url, key: img.key, versionId: img.versionId, heldRegions: held[String(img.page ?? 1)] ?? [], heldRegionsError };
      });
      return { units, pages };
    });
  const units = perDoc.flatMap((d) => d.units);
  const gateCfg = (pipeline?.resolved as { preprocessing?: { piiGate?: PiiView["config"] } } | undefined)?.preprocessing?.piiGate;
  return {
    packageId,
    pipelineVersion: pv,
    units,
    counters: {
      units: units.length,
      held: units.filter((u) => u.action === "hold").length,
      masked: units.filter((u) => u.action === "below_threshold").length,
      passed: units.filter((u) => u.action !== "hold").length,
    },
    goldPages: perDoc.flatMap((d) => d.pages),
    config: gateCfg,
    configKey: pipeline?.item.configKey,
    errors,
  };
}

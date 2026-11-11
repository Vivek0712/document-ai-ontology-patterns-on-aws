/**
 * The golden set: `GET /artifacts?key=config/golden.jsonl`, the file `eval/golden.jsonl` deployed to
 * the config prefix. Every question names its package, document, page and gold region; the expected answers are
 * the generator's (constructed data).
 */
import type { BBox } from "@contracts/api";
import type { RpApi } from "./types";

export const GOLDEN_KEY = "config/golden.jsonl";

export interface GoldenQuestion {
  qid: string;
  type: string;
  question: string;
  answer: string;
  packageId: string;
  docId: string;
  fileName: string;
  page: number;
  goldBbox?: BBox | null;
  headline?: boolean;
  aliases?: string[];
}

export interface GoldenSet {
  items: GoldenQuestion[];
  key: string;
  versionId?: string;
  etag?: string;
}

export async function goldenSet(api: RpApi): Promise<GoldenSet> {
  const a = await api.getArtifact(GOLDEN_KEY);
  let text = a.preview;
  if (!text || a.previewTruncated) {
    if (!a.url) throw new Error(`${GOLDEN_KEY} is larger than the artifact preview and has no download URL.`);
    text = await (await fetch(a.url)).text();
  }
  const items = text
    .split("\n")
    .filter((l) => l.trim())
    .map((l) => JSON.parse(l) as GoldenQuestion);
  return { items, key: a.key, versionId: a.versionId, etag: a.etag };
}

/** The Gold page render of the evidence-units lane, where a golden question's region is drawn. */
export const goldPageKey = (q: Pick<GoldenQuestion, "packageId" | "docId" | "page">) => `gold/${q.packageId}/${q.docId}/2.3.1/pages/p${String(q.page).padStart(3, "0")}.png`;

/**
 * Client-side StageOverlay builder, used only by the mock API (fixtures carry whole artifacts). The live API
 * computes the same shape server-side (GET .../stages/{stageId}/overlay), because a real artifact preview is
 * truncated at 64 KB and digit-masked, which breaks coordinates.
 */
import type { BBox, DocumentDetail, OverlayBox, PiiDecision, StageOverlay, StageRecord, Unit } from "@contracts/api";
import { doclingBlocks, textractBlocks } from "./geometry";

export type ReadText = (key: string) => Promise<string | undefined>;

function parseJsonl<T>(text: string | undefined): T[] {
  return (text ?? "").split("\n").filter(Boolean).flatMap((l) => {
    try {
      return [JSON.parse(l) as T];
    } catch {
      return [];
    }
  });
}

export async function overlayFromArtifacts(
  rec: StageRecord,
  doc: DocumentDetail,
  page: number,
  read: ReadText,
  pageImageUrl?: string,
): Promise<StageOverlay> {
  const lane = doc.lanes[rec.pipelineVersion]?.stages ?? [];
  const find = (sid: string, kind: string) => lane.find((s) => s.stageId === sid)?.artifacts.find((a) => a.kind === kind)?.key;
  const ov: StageOverlay = {
    docId: rec.docId, pipelineVersion: rec.pipelineVersion, stageId: rec.stageId, page,
    pageCount: doc.document.pageCount ?? 1, frame: "pdf-points-top-left", pageImageUrl, sources: [], boxes: [],
  };
  if (rec.state === "SKIPPED") return { ...ov, note: rec.skipReason ?? "skipped" };
  const boxes: OverlayBox[] = [];
  if (rec.stageId === "extract") {
    const ref = rec.artifacts.find((a) => (a.kind === "docling_json" || a.kind === "textract_json") && (a.page === undefined || a.page === page));
    if (!ref) return { ...ov, note: rec.pipelineVersion === "1.0" ? "the text layer carries no regions: 1.0 extracts characters only" : "no engine output yet" };
    const text = await read(ref.key);
    try {
      const parsed = JSON.parse(text ?? "{}");
      const blocks = "Blocks" in parsed ? textractBlocks(parsed, page) : doclingBlocks(parsed);
      for (const b of blocks) if (b.page === page) boxes.push({ kind: "block", bbox: b.bbox, role: b.role, label: b.label, ref: b.id });
    } catch {
      /* unparsable fixture: draw nothing */
    }
    return { ...ov, sources: [ref.key], boxes };
  }
  const unitsKey = ["promote", "index", "graph", "ready"].includes(rec.stageId) ? find("promote", "gold_units_jsonl") ?? find("units", "units_jsonl") : find("units", "units_jsonl");
  if (!unitsKey) return { ...ov, note: "no units yet" };
  const units = parseJsonl<Unit>(await read(unitsKey));
  let held: BBox[] = [];
  const decKey = find("pii_gate", "pii_decisions");
  const flags = new Map<string, string>();
  if (decKey && rec.stageId !== "units") {
    try {
      const dec = JSON.parse((await read(decKey)) ?? "{}") as { decisions?: PiiDecision[] };
      for (const d of dec.decisions ?? []) flags.set(d.unitId, d.decision);
      held = (dec.decisions ?? []).flatMap((x) => x.heldBboxes);
    } catch {
      held = [];
    }
  }
  const gold = ["promote", "index", "graph", "ready"].includes(rec.stageId);
  for (const u of units) {
    const flag = flags.get(u.unitId) ?? u.piiFlag;
    if (gold && flag === "held") continue;
    for (const b of u.bboxes) if (b.page === page) boxes.push({ kind: "unit", bbox: b, label: u.unitType, unitId: u.unitId, ref: u.unitId.split(":").pop(), piiFlag: flag });
    if (rec.stageId === "units") {
      for (const m of u.members) if (m.bbox && m.bbox.page === page) boxes.push({ kind: "member", bbox: m.bbox, role: m.role, label: m.blockRef?.engineLabel, ref: m.memberId, unitId: u.unitId, phase: m.phase ?? undefined, allocScore: m.allocScore ?? undefined });
      for (const f of u.fields) if (f.bbox && f.bbox.page === page) boxes.push({ kind: "field", bbox: f.bbox, label: f.label, ref: f.fieldId, unitId: u.unitId });
    }
  }
  for (const h of held) if (h.page === page) boxes.push({ kind: "held", bbox: h });
  return {
    ...ov,
    sources: [unitsKey, ...(decKey && rec.stageId !== "units" ? [decKey] : [])],
    boxes,
    note: rec.pipelineVersion === "1.0" && boxes.length === 0 ? "1.0 chunks carry no bbox: there is no page region to draw" : undefined,
  };
}

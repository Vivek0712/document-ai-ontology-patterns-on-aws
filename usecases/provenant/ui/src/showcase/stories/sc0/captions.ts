/**
 * SC-0 captions and panel lines that state something about the data: each is a function of the
 * payload, so it can never claim more than the logged runs, stage records or definition say. Steps are named by
 * their id in SC0_STEPS, never by position.
 */
import { coaOf, crossCheckState } from "../../../api/governance";
import type { Lane } from "../lending";
import { type Sc0Payload, ourTotal } from "./load";

/** Longer derived lines fall back to a plain one (captions sit on one line at the player's width). */
const CAPTION_MAX = 80;
const listOf = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs.at(-1)}`);

/** `question`: how many documents the package holds. */
export function questionCaption(p: Sc0Payload): string {
  return `Lending package · ${p.documents.length} constructed documents · the golden question.`;
}

/** The extraction engines a lane ran, from its stage records (e.g. text-layer; docling, textract). */
export function extractEngines(p: Sc0Payload, pv: Lane): string[] {
  const engines = p.lanes.flatMap((d) => d.lanes[pv]?.stages.filter((s) => s.stageId === "extract" && s.state === "SUCCEEDED").map((s) => s.engine ?? "") ?? []);
  return [...new Set(engines.filter(Boolean))].sort();
}

/** `watch`: what differs between the lanes on the same upload (the extract stage's engines). */
export function watchCaption(p: Sc0Payload): string {
  const naive = extractEngines(p, "1.0");
  const units = extractEngines(p, "2.3.1");
  const line = `Same upload. Naive uses ${listOf(naive)}; evidence units use ${listOf(units)}.`;
  return naive.length && units.length && line.length <= CAPTION_MAX ? line : "Both pipelines run on the same upload.";
}

/** `before`: chosen from the logged check and its stability, never written ahead of the run. */
export function beforeCaption(p: Sc0Payload): string {
  const s = p.runs.stats["1.0"];
  const check = p.runs.runs["1.0"].check;
  if (!check?.match) return "Naive pipeline: a total with no region.";
  if (s.scored > 0 && s.correct === s.scored) return `Naive pipeline: right in ${s.correct} of ${s.scored} logged runs, still no region.`;
  return "Naive pipeline: right this time, still no region.";
}

/** `why`: true whatever the logged answer used; the panel says what it used (matchLine). */
export const WHY_CAPTION = "Labels and values arrive apart; the chunk keeps the text and loses the box.";

/** `why` panel line (not the caption): what the logged answer's numbers matched on the page. */
export function matchLine(p: Sc0Payload): string {
  const h = p.xray.hypothesis;
  const pos = p.xray.box1.positionalValue?.text;
  if (h.state === "untested") return `No logged answer has tested the position-only pairing (Box 1 → ${pos}).`;
  if (h.state === "confirmed") return `The logged answer used ${pos}: the position-only value for Box 1.`;
  return `The logged answer used ${listOf(h.used)}; ${pos}, the position-only value for Box 1, is not among them.`;
}

/** `definition`: the governed concept's own label, or that the stack has none; COA only when its total matched. */
export function definitionCaption(p: Sc0Payload): string {
  const label = p.definition?.definition.label;
  if (!label) return "Governed definition unavailable on this stack.";
  const coa = coaOf(p.definition);
  if (coa && crossCheckState(coa, ourTotal(p)) === "match") return `${label} is defined once, governed in Git and recomputed by COA.`;
  return `${label} is defined once and governed in Git.`;
}

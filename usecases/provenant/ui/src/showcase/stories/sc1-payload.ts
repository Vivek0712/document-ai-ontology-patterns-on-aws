/**
 * SC-1 payload: resolved from real artifacts by doc type, borrower and concept, never by
 * an id typed into the story. Everything the frames draw is derived here, once, from the X-ray payloads.
 * A page whose X-ray is unusable (no text layer, a warning, alignment below the floor) is never drawn.
 */
import type { XrayField, XrayLine, XrayPage, XrayPair, XrayUnit } from "../../api/xray";
import { xrayUnusable } from "../../api/xray";
import type { PathStop } from "../../viz/PageCanvas";
import { type Rect, pad, toRect, union } from "../../viz/geom";
import type { DataSource, Loaded, StoryContext } from "../engine/types";

/** The subject of the showcase: what to find, not where it is. */
export const SC1_SUBJECT = { docType: "doc:W2", borrower: "Maya Fictus", concept: "fin:W2WagesBox1" } as const;

/**
 * The positional hypothesis: what a logged naive answer says about the positional hypothesis.
 * - `untested`: no logged 1.0 run yet; the pairing is drawn as a derived hypothesis only;
 * - `confirmed`: the answer used the positional value (× on the arc, shown as a fact on that run);
 * - `refuted`: the answer used other numbers (the frame shows what they matched instead).
 */
/** `provenance`: where the logged answer ran: "measured" (AWS) or "local-run" (Bedrock from a dev machine). */
export type Hypothesis =
  | { state: "untested" }
  | { state: "confirmed" | "refuted"; runId: string; used: string[]; provenance: "measured" | "local-run" };

/** A field of the unit, ordered by its printed box number, with the box it belongs to. */
export interface BoxField extends XrayField {
  box: string;
}

export interface Sc1Payload {
  doc: { docId: string; fileName: string; packageId: string; sha256?: string | null; docType: string };
  borrower: string;
  page: number;
  control: XrayPage;
  /** the largest what-if size at which the Box 1 label and its value fall into different chunks */
  whatIf: XrayPage;
  box1: XrayPair;
  label: Rect;
  value: Rect;
  positional: Rect;
  /** the field whose printed value the positional reading picks up (e.g. Box 12a) */
  positionalOwner?: { fieldId: string; label: string; box: string };
  hypothesis: Hypothesis;
  unit: XrayUnit;
  /** the unit's fields with a figure, by box number (1, 2, … 12a), never in stream order */
  fields: BoxField[];
  /** labels of the what-if chunks holding the Box 1 label and its value, e.g. ["what-if #1"] */
  whatIfChunks: { label: string[]; value: string[] };
  /** where what-if chunk #1 ends on the page: the right edge of its last visible token */
  cut?: { x: number; y0: number; y1: number };
  /** the reading path, split into the label run and the value run (stream order) */
  stops: { labels: PathStop[]; values: PathStop[]; all: { ordinal: number; kind: XrayLine["kind"] }[] };
  frames: { page: Rect; content: Rect; pairing: Rect; unit: Rect };
}

export const boxNumber = (label: string) => label.match(/^(\d{1,2}[a-d]?)\b/)?.[1] ?? label;
const lineRect = (ln: XrayLine | null | undefined): Rect | undefined => (ln?.bbox ? toRect(ln.bbox) : undefined);
const chunkLabels = (x: XrayPage, ln: XrayLine) => ln.chunkOrdinals.map((o) => x.chunks.find((c) => c.ordinal === o)?.label ?? `#${o}`);

/** Box numbers sort as printed: 1, 2, … 9, 10, 12a, 12b. */
export function byBoxNumber(a: string, b: string): number {
  const [na, sa] = [parseInt(a, 10), a.replace(/^\d+/, "")];
  const [nb, sb] = [parseInt(b, 10), b.replace(/^\d+/, "")];
  if (Number.isNaN(na) || Number.isNaN(nb)) return Number.isNaN(na) ? (Number.isNaN(nb) ? a.localeCompare(b) : 1) : -1;
  return na - nb || sa.localeCompare(sb);
}

/**
 * Grow `r` so no text line is cut in half: a line that overlaps the rect vertically and lies mostly inside it
 * horizontally is taken in whole (the camera then never frames half a row of the form).
 */
export function snapToLines(r: Rect, lines: Rect[]): Rect {
  let out = r;
  for (let pass = 0; pass < 3; pass++) {
    let next = out;
    for (const l of lines) {
      const overlapY = l[1] < next[3] && l[3] > next[1];
      const insideX = Math.max(0, Math.min(l[2], next[2]) - Math.max(l[0], next[0])) / Math.max(1e-6, l[2] - l[0]);
      if (overlapY && insideX > 0.5) next = union([next, l])!;
    }
    if (next.every((n, i) => n === out[i])) break;
    out = next;
  }
  return out;
}

export function lineOf(x: XrayPage, ordinal: number): XrayLine {
  const ln = x.lines.find((l) => l.ordinal === ordinal);
  if (!ln) throw new Error(`line ${ordinal} not in the X-ray of ${x.docId}`);
  return ln;
}

export function hypothesisOf(x: XrayPage, pair: XrayPair): Hypothesis {
  const nums = x.answerNumbers;
  if (!nums?.length) return { state: "untested" };
  const confirmed = nums.some((n) => n.isPositionalValueOf === pair.fieldId);
  const provenance = x.sources.mode === "live" && x.sources.answerRun?.provenance !== "local-run" ? "measured" : "local-run";
  return { state: confirmed ? "confirmed" : "refuted", runId: nums[0].runId, used: nums.map((n) => n.value), provenance };
}

export async function loadSc1({ api, packageId, runId }: StoryContext & { runId?: string }): Promise<Loaded<Sc1Payload>> {
  const pkg = await api.getPackage(packageId);
  const candidates = pkg.documents.filter((d) => d.docType === SC1_SUBJECT.docType);
  let found: { doc: (typeof candidates)[number]; control: XrayPage; unit: XrayUnit; box1: XrayPair } | undefined;
  const skipped: string[] = [];
  for (const doc of candidates) {
    // the first call asks for the control size the API defaults to; the payload then names every size it serves
    const control = await api.getXray(doc.docId, 1, undefined, runId);
    const unusable = xrayUnusable(control);
    if (unusable) {
      skipped.push(`${doc.fileName}: ${unusable}`);
      continue;
    }
    const unit = control.units.find((u) => u.label.includes(SC1_SUBJECT.borrower) && u.fields.some((f) => f.concept === SC1_SUBJECT.concept));
    const box1 = control.pairs.find((p) => p.concept === SC1_SUBJECT.concept);
    if (unit && box1?.positionalValue && box1.geometricValue) {
      found = { doc, control, unit, box1 };
      break;
    }
  }
  if (!found) {
    const why = skipped.length ? ` X-ray unusable: ${skipped.join("; ")}.` : "";
    throw new Error(`No ${SC1_SUBJECT.docType} for ${SC1_SUBJECT.borrower} with ${SC1_SUBJECT.concept} in ${packageId}.${why}`);
  }
  const { doc, control, unit, box1 } = found;
  const label = lineRect(box1.label)!;
  const value = lineRect(box1.geometricValue)!;
  const positional = lineRect(box1.positionalValue)!;

  // the what-if: the largest size below the control where the label and the value land in different chunks
  let whatIf: XrayPage | undefined;
  const sizes = control.whatIfSizes.filter((s) => s < control.control.sizeTokens).sort((a, b) => b - a);
  for (const size of sizes) {
    const x = await api.getXray(doc.docId, 1, size);
    if (xrayUnusable(x)) continue;
    const l = lineOf(x, box1.label.ordinal).chunkOrdinals;
    const v = lineOf(x, box1.geometricValue!.ordinal).chunkOrdinals;
    if (!l.some((c) => v.includes(c))) {
      whatIf = x;
      break;
    }
  }
  if (!whatIf) throw new Error("No usable what-if size separates the Box 1 label from its value.");

  const owner = control.pairs.find((p) => p.geometricValue?.ordinal === box1.positionalValue!.ordinal);
  const lineRects = control.lines.flatMap((l) => (l.bbox ? [toRect(l.bbox)] : []));
  const keyLines = control.lines.filter((l) => l.kind !== "other").map((l) => lineRect(l));
  const stop = (l: XrayLine): PathStop => ({ ordinal: l.ordinal, kind: l.kind, rect: toRect(l.bbox!) });
  const drawn = control.lines.filter((l) => l.bbox);

  // where what-if chunk #1 ends: the right edge of its last token that is drawn on this page
  const first = whatIf.chunks[0];
  const lastToken = first && [...whatIf.tokens].reverse().find((t) => t.i < first.tokenEnd && t.rect);
  const cut = lastToken?.rect ? { x: lastToken.rect.x1 + 1.5, y0: lastToken.rect.y0, y1: lastToken.rect.y1 } : undefined;

  const src = control.sources;
  const source: DataSource =
    src.mode === "live"
      ? { mode: "live", label: `Live · ${doc.packageId}`, detail: "rp-xray over the Bronze object; units from Silver and Gold" }
      : {
          mode: "local-run",
          label: "Replay of a local pipeline run",
          detail: `${src.build ?? "build/e2e"} · exported by make xray-fixtures · local Docling, not AWS`,
          recordedAt: src.exportedAt ? new Date(src.exportedAt).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : undefined,
        };

  return {
    source,
    payload: {
      doc: { docId: doc.docId, fileName: doc.fileName, packageId: doc.packageId, sha256: src.bronze.sha256 ?? doc.sha256, docType: doc.docType ?? SC1_SUBJECT.docType },
      borrower: SC1_SUBJECT.borrower,
      page: 1,
      control,
      whatIf,
      box1,
      label,
      value,
      positional,
      positionalOwner: owner ? { fieldId: owner.fieldId, label: owner.label.text, box: boxNumber(owner.label.text) } : undefined,
      hypothesis: hypothesisOf(control, box1),
      unit,
      fields: unit.fields
        .filter((f) => /\d/.test(f.value))
        .map((f) => ({ ...f, box: boxNumber(f.label) }))
        .sort((a, b) => byBoxNumber(a.box, b.box)),
      whatIfChunks: { label: chunkLabels(whatIf, lineOf(whatIf, box1.label.ordinal)), value: chunkLabels(whatIf, lineOf(whatIf, box1.geometricValue!.ordinal)) },
      cut,
      stops: {
        labels: drawn.filter((l) => l.kind === "label").map(stop),
        values: drawn.filter((l) => l.kind === "value").map(stop),
        all: control.lines.map((l) => ({ ordinal: l.ordinal, kind: l.kind })),
      },
      frames: {
        page: [0, 0, control.pageWidth, control.pageHeight],
        // the printed form plus the first chunk rect, with room above it for the chunk tag
        content: ((r: Rect): Rect => [r[0], r[1] - 24, r[2], r[3]])(pad(union([...keyLines, ...control.chunks.slice(0, 1).flatMap((c) => c.rects.slice(0, 1).map(toRect))])!, 18)),
        // Box 1's label, its printed value and the value a position-only read picks up, whole rows only
        pairing: pad(snapToLines(union([label, value, positional])!, lineRects), 14),
        unit: pad(union(unit.bboxes.map(toRect))!, 14),
      },
    },
  };
}

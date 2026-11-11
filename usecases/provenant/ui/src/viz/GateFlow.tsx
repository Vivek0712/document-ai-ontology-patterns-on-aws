/**
 * GateFlow (SC-6, W-12): Bronze → Silver → PII gate → Gold as four columns, with one row per unit.
 *
 * Silver keeps every unit it built, so each unit's token stays in the Silver column. What crosses the gate is a
 * copy: `travel` is the copy's column (1 Silver, 2 the gate, 3 Gold; fractional while it moves). In transit it is a
 * ghost (35 % opacity); a passed copy lands in Gold as a full token; a held copy goes back to Silver, and the
 * Silver token takes the orange outline and the pause glyph, with its hit note under it. The gate column is the
 * one orange frame here: the boundary motif, never decoration.
 */
import { Pause } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../design";

export interface GateDoc {
  id: string;
  label: string;
  /** short sha256 shown under the name */
  sha?: string;
  active?: boolean;
}

export interface GateToken {
  id: string;
  label: string;
  /** small qualifier (unit type, pages) */
  meta?: string;
  state: "idle" | "scanning" | "held" | "passed";
  /** the copy crossing the gate: 1 = Silver (no copy), 2 = in the gate, 3 = in Gold; fractional while moving */
  travel?: number;
  /** false: no Silver token for this row (a summary row that only exists in Gold, e.g. "13 units passed") */
  silver?: boolean;
  /** fade (entering tokens); de-emphasis is `dim`, so a resting token's text keeps its contrast */
  opacity?: number;
  /** de-emphasised: quiet border and text colours (text stays at least 4.5:1), never a low opacity */
  dim?: boolean;
  /** the token the step is about */
  emphasis?: boolean;
  /** a chip under the token: the hit (held, in Silver) or index/graph state (under the Gold copy) */
  note?: ReactNode;
}

const COLUMNS = [
  { id: "bronze", title: "Bronze", service: "Amazon S3" },
  { id: "silver", title: "Silver", service: "units" },
  { id: "gate", title: "PII gate", service: "Amazon Comprehend" },
  { id: "gold", title: "Gold", service: "masked" },
] as const;

type ColumnId = (typeof COLUMNS)[number]["id"];

/** Row height for one token (36 px) plus its note (two 16 px lines and a gap). */
const TOKEN = 36;
const GAP = 8;
const NOTE = 36;

/** Body height for `rows` rows: what the tests and the figure agree on. */
export function gateBodyHeight(rows: number, hasNotes: boolean, compact = false): number {
  return rows * rowHeight(hasNotes, compact) + GAP;
}

function rowHeight(hasNotes: boolean, compact: boolean): number {
  return TOKEN + (compact ? 4 : GAP) + (hasNotes ? NOTE : 0);
}

export function GateFlow({
  docs,
  tokens,
  label,
  counts,
  gate,
  className,
  compact,
}: {
  docs: GateDoc[];
  tokens: GateToken[];
  /** e.g. a lane badge, left of the columns' header row */
  label?: ReactNode;
  /** per column count line, e.g. { silver: "15 units · 2 held", gold: "13 units" } */
  counts?: Partial<Record<ColumnId, ReactNode>>;
  /** content pinned inside the gate column (e.g. the rule card on W-12) */
  gate?: ReactNode;
  className?: string;
  /** tighter rows (W-12 summary, the second lane in SC-6) */
  compact?: boolean;
}) {
  const hasNotes = tokens.some((t) => t.note);
  const rowH = rowHeight(hasNotes, Boolean(compact));
  const rows = Math.max(docs.length, tokens.length, gate ? 1 : 0);
  const body = gateBodyHeight(rows, hasNotes, compact);
  return (
    <figure className={cn("flex min-w-0 flex-col gap-1.5", className)} aria-label="Bronze, Silver, the PII gate and Gold" data-body-height={body}>
      {label && <figcaption className="flex items-center gap-2 text-xs text-text-2">{label}</figcaption>}
      <div className="relative grid grid-cols-4 gap-2">
        {COLUMNS.map((c) => (
          <div key={c.id} className={cn("flex min-w-0 flex-col rounded-md border bg-surface", c.id === "gate" ? "border-boundary" : "border-border")}>
            <div className="flex items-baseline gap-1.5 border-b border-border px-2.5 py-1.5">
              <span className="text-sm font-medium text-text">{c.title}</span>
              <span className="truncate text-xs text-text-2">{c.service}</span>
              {counts?.[c.id] != null && <span className="tnum ml-auto shrink-0 text-xs text-text-2">{counts[c.id]}</span>}
            </div>
            <div style={{ height: body }} className="relative">
              {c.id === "bronze" &&
                docs.map((d, i) => (
                  <div
                    key={d.id}
                    className={cn("absolute inset-x-2 flex h-9 flex-col justify-center rounded-xs border px-2", d.active ? "border-text-2 bg-surface-2" : "border-transparent")}
                    style={{ top: 4 + i * rowH }}
                    title={d.label}
                  >
                    <span className={cn("truncate text-xs", d.active ? "text-text" : "text-text-2")}>{d.label}</span>
                    {d.sha && <span className="truncate font-mono text-mono-sm text-text-3">sha256 {d.sha}</span>}
                  </div>
                ))}
              {c.id === "gate" && gate && <div className="absolute inset-x-2 top-1">{gate}</div>}
            </div>
          </div>
        ))}
        {/* tokens ride over the Silver, gate and Gold columns */}
        <div className="pointer-events-none absolute inset-x-0 bottom-0" style={{ height: body }}>
          {tokens.map((t, i) => (
            <Row key={t.id} t={t} top={4 + i * rowH} />
          ))}
        </div>
      </div>
    </figure>
  );
}

function Row({ t, top }: { t: GateToken; top: number }) {
  const travel = t.travel ?? 1;
  const moving = travel > 1.001 && travel < 2.999;
  const landed = travel >= 2.999;
  const held = t.state === "held";
  const opacity = t.opacity ?? 1;
  return (
    <>
      {t.silver !== false && (
        <Token t={t} column={1} top={top} opacity={opacity} held={held} note={held || !landed ? t.note : undefined} data={{ "data-token": t.id, "data-state": t.state, "data-column": "silver" }} />
      )}
      {(moving || landed) && (
        <Token
          t={t}
          column={travel}
          top={top}
          opacity={opacity * (landed ? 1 : 0.35)}
          held={false}
          note={landed && !held ? t.note : undefined}
          data={{ "data-copy": t.id, "data-state": landed ? t.state : "moving", "data-column": landed ? "gold" : "gate" }}
          ghost={!landed}
        />
      )}
    </>
  );
}

/** A token. A ghost (the copy crossing the gate) repeats its Silver token, so it is hidden from assistive tech. */
function Token({ t, column, top, opacity, held, note, data, ghost }: { t: GateToken; column: number; top: number; opacity: number; held: boolean; note?: ReactNode; data: Record<string, string>; ghost?: boolean }) {
  // column i spans [i/4, (i+1)/4) of the grid; the 8 px gaps are absorbed by the inner inset
  const left = `calc(${(column / 4) * 100}% + 8px)`;
  const hidden = ghost ? { "aria-hidden": true, inert: "" } : {};
  return (
    <div className="absolute flex flex-col gap-1" style={{ left, top, width: "calc(25% - 18px)", opacity }} {...data} {...hidden}>
      <div
        className={cn(
          "flex h-9 items-center gap-1.5 rounded-xs border px-2 text-xs",
          held ? "border-boundary bg-surface text-text" : t.emphasis ? "border-text-2 bg-surface-2 text-text" : t.dim ? "border-border bg-surface text-text-3" : "border-border-strong bg-surface text-text-2",
        )}
        title={t.label}
      >
        {held && <Pause className="size-3 shrink-0" aria-label="held" />}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="truncate">{t.label}</span>
          {t.meta && <span className="truncate text-text-3">{t.meta}</span>}
        </span>
      </div>
      {note && <div className="flex flex-wrap items-center gap-x-1 px-1 text-xs leading-4 text-text-2">{note}</div>}
    </div>
  );
}

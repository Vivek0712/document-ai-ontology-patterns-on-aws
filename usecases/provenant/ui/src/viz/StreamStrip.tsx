/**
 * The text layer as a 1-D strip: one cell per line in stream order (other text is a short tick, a box label an
 * outlined cell, an amount a filled cell). Cells up to `cursor` are lit, so "all labels, then all values" reads in
 * one glance while the reading path draws on the page. HTML, pinned to the canvas frame.
 */
import type { XrayLineKind } from "../api/xray";
import { cn } from "../design";

export interface StripLine {
  ordinal: number;
  kind: XrayLineKind;
}

/** `card` floats over a canvas corner; `inline` sits in a toolbar row (cells and caption on one line). */
export function StreamStrip({ lines, cursor, className, variant = "card" }: { lines: StripLine[]; cursor: number; className?: string; variant?: "card" | "inline" }) {
  const labels = lines.filter((l) => l.kind === "label");
  const values = lines.filter((l) => l.kind === "value");
  const range = (ls: StripLine[]) => (ls.length ? `#${ls[0].ordinal}–#${ls[ls.length - 1].ordinal}` : "none");
  return (
    <figure
      className={cn(variant === "card" ? "flex flex-col gap-1.5 rounded-sm border border-border bg-surface/90 px-2.5 py-2 shadow-sm backdrop-blur" : "flex items-center gap-3", className)}
      aria-label={`Stream order: ${lines.length} lines; box labels ${range(labels)}, then values ${range(values)}`}
    >
      <div className="flex items-end gap-px" aria-hidden>
        {lines.map((l) => {
          const lit = l.ordinal <= cursor;
          return (
            <span
              key={l.ordinal}
              title={`#${l.ordinal} · ${l.kind}`}
              className={cn(
                "shrink-0 rounded-[1px] transition-opacity duration-fast",
                variant === "inline" ? "w-1" : "w-1.5",
                l.kind === "other" ? "h-1.5 bg-text-3" : variant === "inline" ? "h-3" : "h-3.5",
                l.kind === "label" && "border border-text-2",
                l.kind === "value" && "bg-text",
                lit ? "opacity-100" : "opacity-20",
              )}
            />
          );
        })}
      </div>
      <figcaption className="flex items-center gap-3 text-xs text-text-2">
        {variant === "card" && <span>Stream order</span>}
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2.5 w-1.5 rounded-[1px] border border-text-2" aria-hidden /> labels {range(labels)}
        </span>
        <span aria-hidden>→</span>
        <span className="inline-flex items-center gap-1">
          <span className="inline-block h-2.5 w-1.5 rounded-[1px] bg-text" aria-hidden /> values {range(values)}
        </span>
      </figcaption>
    </figure>
  );
}

import { Check, Link2Off, Pause } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { cn } from "../cn";
import { Tooltip } from "./tooltip";

export type CitationKind = "cited" | "noregion" | "held" | "operand";

interface CitationChipProps {
  kind: CitationKind;
  label: ReactNode;
  /** trailing page tag, e.g. "p1" */
  page?: number;
  /** operand chips carry the value they contribute */
  value?: ReactNode;
  to?: string;
  title?: string;
  className?: string;
}

/**
 * Citation chip. cited: solid green + ✓ and opens the trace; no region: dashed pink border + a neutral
 * link-off glyph + "no region", never a link (× is kept for a wrong value: a correct value that cannot be cited
 * must not read as wrong); held: orange outline + pause glyph. Meaning never rests on colour alone.
 */
export function CitationChip({ kind, label, page, value, to, title, className }: CitationChipProps) {
  const base = "inline-flex h-[22px] max-w-full items-center gap-1.5 rounded-xs border px-2 text-xs font-medium text-text";
  if (kind === "noregion") {
    return (
      <Tooltip content={title ?? "This chunk has no page region."}>
        <span className={cn(base, "cursor-not-allowed border-dashed border-loss", className)} aria-label={`${typeof label === "string" ? label : "chunk"}: no region`}>
          <Link2Off className="size-3 text-text-2" aria-hidden />
          {value && (
            <>
              <span className="tnum">{value}</span>
              <span className="text-text-3">·</span>
            </>
          )}
          <span className="truncate">{label}</span>
          <span className="text-text-2">no region</span>
        </span>
      </Tooltip>
    );
  }
  if (kind === "held") {
    return (
      <span className={cn(base, "border-boundary", className)} title={title}>
        <Pause className="size-3" aria-hidden />
        <span className="truncate">{label}</span>
      </span>
    );
  }
  const body = (
    <>
      <Check className="size-3 text-cited" aria-hidden />
      <span className="truncate">{label}</span>
      {value && <span className="tnum">{value}</span>}
      {page !== undefined && <span className="font-mono text-mono-sm font-normal text-text-2">p{page}</span>}
    </>
  );
  const cls = cn(base, "border-cited", to && "hover:bg-cited-tint", className);
  return to ? (
    <Link to={to} className={cls} title={title}>
      {body}
    </Link>
  ) : (
    <span className={cls} title={title}>
      {body}
    </span>
  );
}

/** A generic, non-semantic chip (filters, file names). */
export function Chip({ children, icon, className }: { children: ReactNode; icon?: ReactNode; className?: string }) {
  return (
    <span className={cn("inline-flex h-[22px] items-center gap-1.5 rounded-xs border border-border bg-surface-2 px-2 text-xs text-text-2 [&_svg]:size-3", className)}>
      {icon}
      {children}
    </span>
  );
}

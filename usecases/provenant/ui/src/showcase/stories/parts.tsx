/**
 * Small visual parts shared by the comparison showcases (SC-7, SC-10, SC-11, SC-12, SC-14). Each is a pure function
 * of its props (the caller passes the step's progress), so frames stay deterministic.
 */
import { Check, Minus, X } from "lucide-react";
import type { ConfigDiff } from "@contracts/api";
import type { ReactNode } from "react";
import { Num, Tooltip, cn } from "../../design";
import { seg } from "../../viz/geom";
import type { RunDot } from "./compare";

/** One logged run as a 16 px dot: ✓ solid green (correct), × dashed pink (wrong), – dashed neutral (agent error). */
export function Dot({ d, on = 1, cited }: { d: RunDot; on?: number; cited?: boolean }) {
  const label =
    d.status === "correct" ? `correct${d.total ? ` · ${d.total}` : ""}` : d.status === "wrong" ? `wrong${d.total ? ` · ${d.total}` : ""}` : d.status === "error" ? `agent error${d.error ? ` · ${d.error}` : ""}` : "not scored";
  return (
    <Tooltip content={<span className="font-mono">{d.runId} · {label}{cited !== undefined ? (cited ? " · cites a region" : " · no region") : ""}</span>}>
      <span
        role="img"
        aria-label={`${d.runId}: ${label}`}
        tabIndex={-1}
        className={cn(
          "flex size-4 shrink-0 items-center justify-center rounded-full border [&_svg]:size-2.5",
          d.status === "correct" && "border-cited text-cited",
          d.status === "wrong" && "border-dashed border-loss text-loss",
          d.status === "error" && "border-dashed border-text-3 text-text-2",
          d.status === "unscored" && "border-border-strong",
        )}
        style={{ opacity: on, transform: `scale(${0.6 + 0.4 * on})` }}
        data-dot={d.status}
      >
        {d.status === "correct" ? <Check strokeWidth={3} /> : d.status === "wrong" ? <X strokeWidth={3} /> : d.status === "error" ? <Minus strokeWidth={3} /> : null}
      </span>
    </Tooltip>
  );
}

/** A row of run dots that land one after another over `t`. */
export function DotRow({ dots, t = 1, className, cited = false }: { dots: RunDot[]; t?: number; className?: string; cited?: boolean }) {
  return (
    <div className={cn("flex flex-wrap items-center gap-1", className)}>
      {dots.map((d, i) => (
        <Dot key={d.runId} d={d} on={seg(t, (i / Math.max(1, dots.length)) * 0.7, (i / Math.max(1, dots.length)) * 0.7 + 0.3)} cited={cited ? d.cited : undefined} />
      ))}
    </div>
  );
}

/** The config diff of the two lanes, one chip per block, the changed ones dashed (spec: on every comparison). */
export function DiffStrip({ diff, t = 1, className }: { diff: ConfigDiff; t?: number; className?: string }) {
  const rows: { key: string; equal: boolean }[] = [
    ...Object.entries(diff.blocks).map(([k, b]) => ({ key: k, equal: b.equal })),
    { key: "prompt", equal: diff.promptSha.equal },
    { key: "model", equal: diff.modelId.equal },
  ];
  return (
    <section className={cn("flex flex-wrap items-center gap-x-3 gap-y-2 rounded-md border border-border bg-surface px-4 py-2.5", className)} data-testid="diff-strip">
      <span className="text-caps uppercase text-text-2">Config diff</span>
      <span className="font-mono text-mono-sm text-text-2">
        {diff.a} → {diff.b}
      </span>
      <ul className="flex flex-wrap items-center gap-1.5">
        {rows.map((r, i) => (
          <li
            key={r.key}
            className={cn("inline-flex h-6 items-center gap-1.5 rounded-xs border px-2 text-xs", r.equal ? "border-border text-text-2" : "border-dashed border-text-2 text-text")}
            style={{ opacity: 0.25 + 0.75 * seg(t, i * 0.08, i * 0.08 + 0.3) }}
          >
            {r.equal ? <span aria-hidden>=</span> : <span aria-hidden>≠</span>}
            {r.key}
            <span className="sr-only">{r.equal ? "identical" : "changed"}</span>
          </li>
        ))}
      </ul>
      <span className="ml-auto text-xs text-text-2">{diff.summary}</span>
    </section>
  );
}

/** "9 of 10" with Num (provenance from the scope). */
export function OfN({ k, n, children }: { k: number; n: number; children?: ReactNode }) {
  return (
    <span className="inline-flex flex-wrap items-baseline gap-x-1">
      <Num value={k} kind="int" bare /> of <Num value={n} kind="int" bare />
      {children}
    </span>
  );
}

/** Text revealed character by character over `t` (a logged answer replaying), with a caret while it types. */
export function Typed({ text, t, className }: { text: string; t: number; className?: string }) {
  const n = Math.round(text.length * Math.max(0, Math.min(1, t)));
  return (
    <p className={cn("whitespace-pre-wrap text-sm text-text", className)}>
      {text.slice(0, n)}
      {n < text.length && <span className="ml-px inline-block h-3.5 w-px translate-y-0.5 bg-text-2" aria-hidden />}
      <span className="sr-only">{text.slice(n)}</span>
    </p>
  );
}

/**
 * A quiet card for a step whose evidence this build does not have yet: "Not in this build" by default, or the
 * caller's `note` ("Not published", "COA off"). `cut` is a plain slug for what is missing ("billing-actuals",
 * "unpublished-metrics"); it goes in `data-not-in-build` for tests, never in the visible copy.
 */
export function NotInBuild({ title, cut, note = "Not in this build", children, className }: { title: string; cut?: string; note?: string; children?: ReactNode; className?: string }) {
  return (
    <section className={cn("rounded-md border border-dashed border-border-strong bg-surface/60 px-4 py-3", className)} data-not-in-build={cut ?? true}>
      <p className="flex items-center gap-2 text-sm text-text">
        {title}
        <span className="text-xs text-text-2" title={cut}>
          {note}
        </span>
      </p>
      {children && <div className="mt-1 text-xs text-text-2">{children}</div>}
    </section>
  );
}

/**
 * Provenance: `<ProvenanceScope>` wraps a panel, column or card and renders ONE tag in its header;
 * `<Num>` inherits the scope and shows an inline tag only when its own provenance differs (a what-if inside a
 * local-run panel). A number with neither a scope nor a prop throws in dev and renders "—" in production.
 */
import { Calculator, Camera, FlaskConical, Gauge, GitBranch, GitCompare, History, Laptop, Lightbulb, Shapes, Sigma, Target } from "lucide-react";
import { createContext, type ReactNode, useContext } from "react";
import { cn } from "./cn";
import { Tooltip } from "./ui/tooltip";

export type Provenance =
  | "measured" // measured on AWS by a logged run
  | "local-run" // measured on the dev Mac by the local pipeline (not AWS)
  | "generator" // written by data/generate.py (constructed data)
  | "design-target"
  | "estimate"
  | "what-if" // a labelled re-split or counterfactual setting
  | "counterfactual"
  | "snapshot"
  | "replay"
  | "derived" // deterministic arithmetic/inspection over real artifacts
  | "hypothesis" // derived, and models a behaviour no logged run has confirmed yet
  | "illustrative";

export const PROVENANCE: Record<Provenance, { label: string; icon: ReactNode; dashed?: boolean; hint: string }> = {
  measured: { label: "measured", icon: <Gauge />, hint: "Measured on AWS by a logged run." },
  "local-run": { label: "local run", icon: <Laptop />, hint: "Measured by the local pipeline on the dev machine (not AWS)." },
  generator: { label: "generator", icon: <FlaskConical />, hint: "Constructed by data/generate.py (fictitious data)." },
  "design-target": { label: "design target", icon: <Target />, hint: "A value we chose." },
  estimate: { label: "estimate", icon: <Calculator />, hint: "Arithmetic from list prices or unmeasured assumptions." },
  "what-if": { label: "what-if", icon: <GitBranch />, dashed: true, hint: "A labelled what-if; the control lane is unchanged." },
  counterfactual: { label: "counterfactual", icon: <GitCompare />, dashed: true, hint: "A counterfactual query." },
  snapshot: { label: "snapshot", icon: <Camera />, hint: "From a dated snapshot." },
  replay: { label: "replay", icon: <History />, hint: "Replayed from a logged run." },
  derived: { label: "derived", icon: <Sigma />, hint: "Deterministic inspection or arithmetic over real artifacts." },
  hypothesis: { label: "derived · hypothesis", icon: <Lightbulb />, dashed: true, hint: "Derived from the artifacts; a logged run has not confirmed it." },
  illustrative: { label: "illustrative", icon: <Shapes />, dashed: true, hint: "Illustration only; never a measurement." },
};

const sentence = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * A provenance tag: quiet by design (12 px, text-3, sentence case, an icon, no box). It is a qualifier, never
 * louder than the number it qualifies; the border appears on hover and focus, where its tooltip gives the source.
 */
export function ProvenanceTag({ provenance, source, label, className }: { provenance: Provenance; source?: ReactNode; /** replaces the kind's label (a dated snapshot: "COA snapshot · captured …") */ label?: string; className?: string }) {
  const p = PROVENANCE[provenance];
  return (
    <Tooltip content={<span className="flex flex-col gap-0.5"><span>{p.hint}</span>{source && <span className="break-all font-mono text-text-2">{source}</span>}</span>}>
      <span
        className={cn(
          "inline-flex h-[18px] shrink-0 items-center gap-1 whitespace-nowrap rounded-xs border border-transparent px-1 text-xs font-normal normal-case leading-none tracking-normal text-text-3 transition-colors duration-fast [&_svg]:size-3",
          "hover:border-border hover:text-text-2 focus-visible:border-border focus-visible:text-text-2 focus-visible:outline-none",
          p.dashed && "hover:border-dashed focus-visible:border-dashed",
          className,
        )}
        tabIndex={0}
        data-provenance={provenance}
      >
        {p.icon}
        {label ?? sentence(p.label)}
      </span>
    </Tooltip>
  );
}

const Scope = createContext<{ provenance: Provenance; source?: ReactNode; label?: string } | null>(null);

/** Sets the provenance for every <Num> inside; render its tag once with <ScopeTag/> in the header. */
export function ProvenanceScope({ provenance, source, label, children }: { provenance: Provenance; source?: ReactNode; label?: string; children: ReactNode }) {
  return <Scope.Provider value={{ provenance, source, label }}>{children}</Scope.Provider>;
}

/** The scope's single tag (for a panel or column header). */
export function ScopeTag({ className }: { className?: string }) {
  const scope = useContext(Scope);
  return scope ? <ProvenanceTag provenance={scope.provenance} source={scope.source} label={scope.label} className={className} /> : null;
}

/**
 * A value that is only a derived hypothesis: muted, dashed underline, a trailing "?". It must never
 * look like an answer from across a room; the caller puts its qualifier on the same line.
 */
export function HypothesisValue({ value, className }: { value: string | null | undefined; className?: string }) {
  if (!value) return <span className="text-text-2">—</span>;
  return (
    <span
      className={cn("tnum font-mono text-mono-md text-text-2 underline decoration-text-2 decoration-dashed decoration-1 underline-offset-4", className)}
      data-hypothesis="untested"
    >
      {`${value}?`}
    </span>
  );
}

export type NumKind = "money" | "int" | "dec2" | "ratio" | "pct" | "ms" | "raw";

export function formatNum(value: number | string, kind: NumKind): string {
  if (kind === "raw") return String(value);
  const n = typeof value === "number" ? value : Number(String(value).replace(/,/g, ""));
  if (!Number.isFinite(n)) return String(value);
  switch (kind) {
    case "money":
      return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case "int":
      return Math.round(n).toLocaleString("en-US");
    case "dec2": // a usage quantity: one format per column (31.25, 2.62), never mixed precision
      return n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
    case "ratio":
      return n.toFixed(3);
    case "pct":
      return `${(n * 100).toFixed(1)} %`;
    case "ms":
      return n < 1000 ? `${Math.round(n)} ms` : `${(n / 1000).toFixed(1)} s`;
  }
}

interface NumProps {
  value: number | string | null | undefined;
  provenance?: Provenance;
  kind?: NumKind;
  /** run id, S3 key, payload path… shown on hover */
  source?: ReactNode;
  size?: "xs" | "sm" | "md" | "lg" | "figure";
  mark?: "cited" | "loss";
  unit?: ReactNode;
  className?: string;
  /** never show an inline tag (the surrounding frame already states it) */
  bare?: boolean;
}

const SIZE = { xs: "text-xs", sm: "text-sm", md: "text-md font-medium", lg: "text-hero", figure: "text-figure" } as const;

export function Num({ value, provenance: own, kind = "raw", source, size = "sm", mark, unit, className, bare }: NumProps) {
  const scope = useContext(Scope);
  const provenance = own ?? scope?.provenance;
  const showTag = !bare && Boolean(own) && own !== scope?.provenance;
  if (!provenance) {
    if (import.meta.env.DEV) throw new Error(`<Num value=${String(value)}> rendered without provenance`);
    return <span className="text-text-2">—</span>;
  }
  if (value === null || value === undefined || value === "") return <span className="text-text-2">—</span>;
  return (
    <span className={cn("inline-flex flex-wrap items-baseline gap-x-1.5 gap-y-1", className)}>
      {mark && (
        <span className={cn("self-center font-semibold", mark === "cited" ? "text-cited" : "text-loss")} aria-label={mark === "cited" ? "correct" : "wrong"}>
          {mark === "cited" ? "✓" : "×"}
        </span>
      )}
      <span className={cn("tnum text-text", SIZE[size])}>
        {formatNum(value, kind)}
        {unit && <span className="ml-0.5 text-text-2">{unit}</span>}
      </span>
      {showTag && <ProvenanceTag provenance={provenance} source={source} className="self-center" />}
    </span>
  );
}

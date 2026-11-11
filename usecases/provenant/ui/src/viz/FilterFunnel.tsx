/**
 * FilterFunnel (spec SC-4 step 5): index total → after the filter → each sub-query's top N → fused K. Bars are to
 * scale against the index total; every count is a `Num` (it inherits the caller's ProvenanceScope, or carries the
 * lane's own provenance, shown inline, when it differs: a computed count is `derived`). A stage may
 * have parallel lanes (the dense and BM25 sub-queries). `reveal` (0..1) grows the stages in order.
 */
import { Num, type Provenance, cn } from "../design";
import { seg } from "./geom";

export interface FunnelLane {
  label: string;
  count: number;
  note?: string;
  /** when this count is not what the scope says (e.g. `derived`: computed from other counts, not counted) */
  provenance?: Provenance;
}

export interface FunnelStage {
  title: string;
  lanes: FunnelLane[];
}

export function FilterFunnel({ stages, total, reveal = 1, className }: { stages: FunnelStage[]; total: number; reveal?: number; className?: string }) {
  const n = stages.length;
  return (
    <ol className={cn("flex flex-col gap-3", className)} aria-label="Filter funnel">
      {stages.map((s, i) => {
        const k = seg(reveal, i / n, (i + 0.8) / n);
        return (
          <li key={s.title} className="grid grid-cols-[132px_minmax(0,1fr)] items-start gap-3" style={{ opacity: 0.25 + 0.75 * k }}>
            <span className="pt-1 text-xs text-text-2">{s.title}</span>
            <div className={cn("grid gap-2", s.lanes.length > 1 && "sm:grid-cols-2")}>
              {s.lanes.map((l) => (
                <div key={l.label} className="flex flex-col gap-1">
                  <div className="flex items-baseline gap-2 text-xs">
                    <span className="text-text">{l.label}</span>
                    <span className="ml-auto">
                      <Num value={l.count} kind="int" size="xs" bare={!l.provenance} provenance={l.provenance} />
                    </span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-surface-3">
                    <div className="h-full rounded-full bg-text" style={{ width: `${total ? (l.count / total) * 100 * k : 0}%` }} />
                  </div>
                  {l.note && <span className="text-xs text-text-3">{l.note}</span>}
                </div>
              ))}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

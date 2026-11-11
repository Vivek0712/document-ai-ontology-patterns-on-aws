/**
 * Shared side-panel pieces for storyboards: a section with one provenance scope (its tag renders once, in the
 * header), and a lane card (lane badge + title + scope tag). Both are plain layout: no state, no timers.
 */
import type { ReactNode } from "react";
import { LaneBadge, type Provenance, ProvenanceScope, ScopeTag, cn } from "../../design";

/** A panel section with one provenance scope: its tag renders once, in the header. */
export function PanelSection({ title, provenance, source, meta, children, className }: { title: string; provenance?: Provenance; source?: ReactNode; meta?: ReactNode; children: ReactNode; className?: string }) {
  const body = (
    <section className={cn("border-b border-border px-4 py-3.5 last:border-0", className)}>
      <h2 className="mb-2.5 flex items-center gap-2 text-caps uppercase text-text-2">
        <span className="min-w-0 flex-1">{title}</span>
        {provenance && <ScopeTag />}
      </h2>
      {meta && <p className="-mt-1.5 mb-2.5 text-xs text-text-2">{meta}</p>}
      {children}
    </section>
  );
  return provenance ? (
    <ProvenanceScope provenance={provenance} source={source}>
      {body}
    </ProvenanceScope>
  ) : (
    body
  );
}

export function LaneCard({ lane, title, provenance, source, children }: { lane: string; title: string; provenance: Provenance; source?: ReactNode; children: ReactNode }) {
  return (
    <ProvenanceScope provenance={provenance} source={source}>
      <section className="rounded-md border border-border bg-surface">
        <div className="flex items-center gap-2 border-b border-border px-3 py-2">
          <LaneBadge pv={lane} />
          <span className="min-w-0 flex-1 truncate text-sm text-text-2">{title}</span>
          <ScopeTag />
        </div>
        <div className="p-3">{children}</div>
      </section>
    </ProvenanceScope>
  );
}

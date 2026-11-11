/**
 * The COA snapshot parts shared by SC-0 (step 8), SC-8 and W-10. Every value comes from the governance record's
 * `coa.snapshot`, which the ontology build reads from a recorded COA session (`runs/local/coa/<date>/`). Each part
 * carries the tag "COA snapshot · captured <date>": the session is replayed, never called live. Uploaded Turtle is
 * always labelled "uploaded, not steward-reviewed"; COA's server sets `definedBy` and `effectiveFrom`, and the
 * snapshot redacts `definedBy`.
 */
import type { CoaSnapshot } from "@contracts/api";
import { ArrowRight, Check } from "lucide-react";
import type { ReactNode } from "react";
import { type CrossCheckState, type OurTotal, coaTagLabel, crossCheckLabel, crossCheckState, curie, definedByText, traceChips } from "../../api/governance";
import { Badge, CodeBlock, DataList, Num, ProvenanceScope, ProvenanceTag, ScopeTag, Tooltip, cn } from "../../design";

const sha12 = (s?: string) => (s ? s.slice(0, 12) : "—");
/** One clause per line, for reading; the recorded SQL is otherwise shown verbatim. */
const sqlLines = (sql: string) => sql.replace(/ (FROM|WHERE|AND) /g, "\n$1 ");

/** Every COA value inside renders under the snapshot's own tag. */
export function CoaScope({ s, children }: { s: CoaSnapshot; children: ReactNode }) {
  return (
    <ProvenanceScope provenance="snapshot" label={coaTagLabel(s)} source={`runs/local/coa · COA ${s.coaVersion ?? ""} · namespace ${s.namespace ?? ""}`}>
      {children}
    </ProvenanceScope>
  );
}

export function CoaTag({ s, className }: { s: CoaSnapshot; className?: string }) {
  return <ProvenanceTag provenance="snapshot" label={coaTagLabel(s)} source={`runs/local/coa · COA ${s.coaVersion ?? ""}`} className={className} />;
}

function Section({ title, meta, children, className, testId }: { title: ReactNode; meta?: ReactNode; children: ReactNode; className?: string; testId?: string }) {
  return (
    <section className={cn("rounded-md border border-border bg-surface", className)} data-testid={testId}>
      <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border px-4 py-2 text-caps uppercase text-text-2">
        <span className="mr-auto">{title}</span>
        {meta}
        <ScopeTag />
      </h3>
      {children}
    </section>
  );
}

/** The concept label COA's copy of our ontology carries. */
export function ConceptAnchor({ s }: { s: CoaSnapshot }) {
  return (
    <span className="flex flex-wrap items-center gap-1.5">
      <span className="font-mono text-mono-sm">{curie(s.ontology.concept)}</span>
      <Badge tone="neutral" className="h-auto max-w-full shrink whitespace-normal py-0.5">
        {s.ontology.label}
      </Badge>
    </span>
  );
}

/** COA's metric definition card: label, description, SQL, ontology concept, and the fields COA's server set. */
export function CoaMetricCard({ s, compact = false, className }: { s: CoaSnapshot; compact?: boolean; className?: string }) {
  const m = s.metric;
  return (
    <CoaScope s={s}>
      <Section title={`COA metric · ${m.name}`} className={className} testId="coa-metric">
        <div className="flex flex-col gap-3 p-4">
          <p className="text-md text-text">{m.label ?? m.name}</p>
          {!compact && m.description && <blockquote className="border-l-2 border-border-strong pl-3 text-sm text-text">{m.description}</blockquote>}
          {!compact && <CodeBlock code={sqlLines(m.sql)} title={`${m.dialect} · ${m.sourceTable ?? ""}`} maxHeight={220} />}
          <DataList
            rows={[
              ["Concept", <ConceptAnchor s={s} />],
              ...(compact ? [] : ([["Unit", <span className="font-mono text-mono-sm">{[m.unit, m.returnType, m.defaultTimeGrain].filter(Boolean).join(" · ")}</span>], ["Synonyms", <span>{m.synonyms.join(" · ")}</span>]] as [ReactNode, ReactNode][])),
              ["Defined by", <span className="text-text-2">{definedByText(s)} · set by COA</span>],
              ["Effective from", <span><span className="font-mono text-mono-sm">{m.effectiveFrom ?? "—"}</span> <span className="text-text-2">· set by COA</span></span>],
            ]}
          />
        </div>
      </Section>
    </CoaScope>
  );
}

const MARK: Record<CrossCheckState, { text: string; className: string }> = {
  match: { text: "matches our total", className: "text-cited" },
  differs: { text: "differs from our total", className: "text-loss" },
  "not-compared": { text: "not compared with our total", className: "text-text-2" },
};

/** "COA governed metric · Tier 1 · confidence 1.0 · <value> ✓" and the resolver's trace chips. */
export function CrossCheckRow({ s, ours, reveal = 1, className }: { s: CoaSnapshot; ours?: OurTotal; reveal?: number; className?: string }) {
  const state = crossCheckState(s, ours);
  const c = s.crossCheck;
  const chips = traceChips(s);
  return (
    <CoaScope s={s}>
      <div className={cn("flex flex-col gap-2 rounded-md border border-border bg-surface px-4 py-3", className)} data-coa-crosscheck={state} data-testid="coa-crosscheck">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm text-text">{crossCheckLabel(s)}</span>
          <span className="text-text-2">·</span>
          <Num value={c.value} kind="money" size="md" />
          {state === "match" ? (
            <Check className="size-4 text-cited" aria-label={MARK.match.text} />
          ) : (
            <span className={cn("text-xs", MARK[state].className)}>{MARK[state].text}</span>
          )}
          <span className="ml-auto">
            <ScopeTag />
          </span>
        </div>
        <ol className="flex flex-wrap items-center gap-1" aria-label="COA resolver trace">
          {chips.map((x, i) => (
            <li key={x} className="flex items-center gap-1" style={{ opacity: reveal >= (i + 1) / chips.length ? 1 : 0.35 }}>
              {i > 0 && <ArrowRight className="size-3 text-text-3" aria-hidden />}
              <span className="rounded-xs border border-border px-1.5 py-0.5 font-mono text-mono-sm text-text">{x}</span>
            </li>
          ))}
        </ol>
        <p className="text-xs text-text-2">
          Independent recomputation of the same definition, for {Object.entries(c.dimensions).map(([k, v]) => `${k} ${v}`).join(", ") || "every package"}. <Num value={c.identicalRuns} kind="int" size="xs" bare /> of <Num value={c.runs} kind="int" size="xs" bare /> recorded runs identical
          {c.mcpValue !== undefined && c.mcpRuns > 0 && <>; the same value through COA's MCP server in <Num value={c.mcpRuns} kind="int" size="xs" bare /> of <Num value={c.mcpRuns} kind="int" size="xs" bare /></>}.
          {state === "differs" && ours && <> Our total: <Num value={ours.total} kind="money" size="xs" provenance="local-run" bare />.</>}
        </p>
      </div>
    </CoaScope>
  );
}

/** The three sha256 values that tie COA's copy to ours. */
export function CoaHashes({ s, className }: { s: CoaSnapshot; className?: string }) {
  return (
    <CoaScope s={s}>
      <Section title="Provenance hashes" className={className} testId="coa-hashes">
        <ul className="flex flex-col gap-1.5 px-4 py-3">
          {s.hashes.map((h) => (
            <li key={h.name} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 text-sm">
              <span className="text-text-2">{h.name}</span>
              <Tooltip content={h.sha256}>
                <span className="font-mono text-mono-sm text-text" tabIndex={0}>
                  sha256 {sha12(h.sha256)}
                </span>
              </Tooltip>
              {h.detail && <span className="col-span-2 text-xs text-text-2">{h.detail}</span>}
            </li>
          ))}
          {s.ontology.matchesGitSource !== undefined && (
            <li className="text-xs text-text-2">{s.ontology.matchesGitSource ? "The uploaded fin.ttl is byte-identical to the Git source." : "The uploaded fin.ttl differs from the Git source in this build."}</li>
          )}
        </ul>
      </Section>
    </CoaScope>
  );
}

/** Measured client latency of the recorded calls (not AWS-side timings), with where the clock ran. */
export function CoaLatency({ s, className }: { s: CoaSnapshot; className?: string }) {
  const where = /laptop/i.test(s.latency.basis) ? `laptop → ${s.region ?? "AWS"}` : s.latency.basis;
  return (
    <ProvenanceScope provenance="measured" label={`Measured · ${where}`} source={s.latency.basis}>
      <Section title="Latency (medians)" className={className} testId="coa-latency">
        <ul className="flex flex-col gap-1 px-4 py-3">
          {s.latency.calls.map((c) => (
            <li key={c.call} className="grid grid-cols-[minmax(0,1fr)_auto] items-baseline gap-x-3 text-sm">
              <span className="text-text-2">
                {c.call} <span className="text-xs">· n <Num value={c.n} kind="int" size="xs" bare /></span>
              </span>
              <Num value={c.medianMs} kind="ms" size="sm" />
            </li>
          ))}
          <li className="text-xs text-text-2">Too slow for a per-question call, so the cross-check runs when documents are prepared.</li>
        </ul>
      </Section>
    </ProvenanceScope>
  );
}

/** The token rule COA's MCP server enforced in the recording. */
export function CoaTokenRule({ s, className }: { s: CoaSnapshot; className?: string }) {
  if (!s.access) return null;
  return (
    <CoaScope s={s}>
      <Section title="Token rule" className={className} testId="coa-token-rule">
        <div className="flex flex-col gap-1.5 px-4 py-3 text-sm">
          <p className="text-text">{s.access.rule}</p>
          <ul className="flex flex-col gap-0.5 text-xs text-text-2">
            {s.access.refused.map((r) => (
              <li key={r.token}>
                {r.token}: <Num value={r.status} kind="raw" size="xs" bare /> {r.message}
              </li>
            ))}
          </ul>
        </div>
      </Section>
    </CoaScope>
  );
}

/**
 * SC-4 · Filters inside the query (Pattern 4, thin). The unit carries its context from ingestion
 * (what it is, who may read it, where it came from), and the logged `search_units` call puts the same filter on
 * those fields inside both sub-queries before anything is ranked; `_count` with that filter gives the funnel.
 *
 * Every frame is `render({ payload, step, progress })`: no timers, no typed values.
 */
import { easeCubicOut } from "d3-ease";
import { Filter } from "lucide-react";
import { Badge, CodeBlock, Num, ProvenanceScope, ProvenanceTag, ScopeTag, cn } from "../../design";
import { FilterFunnel } from "../../viz/FilterFunnel";
import { pad, seg } from "../../viz/geom";
import { Boundary, PageCanvas } from "../../viz/PageCanvas";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { PanelSection } from "./panel";
import { type MetaRow, type Sc4Payload, loadSc4, subQueries } from "./sc4-payload";
import { SC4_STEPS } from "./steps";

const STEP = Object.fromEntries(SC4_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC4_STEPS)[number]["id"], number>;

const callReceipt = (p: Sc4Payload) => ({ label: "Logged search_units call", value: `${p.runId} · ${p.callId}`, source: `${p.s3Key} · toolCalls`, provenance: p.provenance });

const steps: StoryStep<Sc4Payload>[] = [
  {
    ...SC4_STEPS[0],
    durationMs: 2600,
    caption: "Each unit carries what it is, who may read it and where it came from.",
    services: ["AWS Lambda · classify", "AWS Lambda · units", "Amazon Comprehend"],
    receipts: (p) => [
      { label: "Unit record", value: p.unit.unitId, source: `GET /units/${p.unit.unitId}`, path: "$.groups", provenance: p.provenance },
      ...p.groups.flatMap((g) => g.rows.map((r) => ({ label: `${g.title} · ${r.key} (set by ${r.stage})`, value: r.value, provenance: p.provenance }))),
    ],
  },
  {
    ...SC4_STEPS[1],
    durationMs: 2600,
    caption: "The same filter sits inside both sub-queries.",
    services: ["AWS Lambda · search_units", "OpenSearch Serverless"],
    receipts: (p) => [callReceipt(p), { label: "Query echo · filter", value: JSON.stringify(p.echo.filter), path: "$.echo.filter", provenance: p.provenance }, { label: "Sub-query bodies (as retrieval.py builds them)", value: "dense_query · bm25_query", provenance: "derived" }],
  },
  {
    ...SC4_STEPS[2],
    durationMs: 2400,
    caption: "Both sub-queries are filtered, normalised and combined into one score.",
    services: ["AWS Lambda · search_units"],
    receipts: (p) => [
      callReceipt(p),
      { label: "Fusion", value: `${p.echo.normalization} · ${Object.entries(p.echo.weights).map(([k, v]) => `${k} ${v}`).join(" · ")}`, path: "$.echo.weights", provenance: p.provenance },
      { label: "Scores per unit (dense, BM25, fused)", value: p.results.map((u) => `${u.rank}: ${u.scores.denseNorm} / ${u.scores.bm25Norm} / ${u.scores.fused}`).join(" · "), path: "$.results", provenance: p.provenance },
    ],
  },
  {
    ...SC4_STEPS[3],
    durationMs: 2400,
    caption: "Filters narrow the candidates before anything is ranked.",
    services: ["OpenSearch Serverless"],
    receipts: (p) => [
      { label: "Counted with", value: p.inspection.countedBy, source: `GET /inspect/filters?runId=${p.runId}&callId=${p.callId}`, provenance: p.provenance },
      { label: "Counts", value: JSON.stringify(p.inspection.counts), path: "$.inspection.counts", provenance: p.provenance },
      ...p.inspection.sources.map((s) => ({ label: `Source · ${s.kind}`, value: s.versionId ?? undefined, source: s.key, provenance: p.provenance })),
    ],
  },
];

// --------------------------------------------------------------------------------------------- frame

function Sc4Stage({ payload: p, step, progress: t }: Frame<Sc4Payload>) {
  return (
    <ProvenanceScope provenance={p.provenance} source={p.s3Key}>
      <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] lg:grid-cols-[380px_minmax(0,1fr)] lg:grid-rows-1">
        <aside className="min-h-0 overflow-y-auto border-b border-border bg-bg-subtle lg:border-b-0 lg:border-r">
          <MetadataCard p={p} step={step} t={t} />
        </aside>
        <div className="min-h-0 overflow-y-auto px-4 py-4 sm:px-5">
          {step === STEP.metadata && <UnitOnPage p={p} t={t} />}
          {step === STEP.query && <QueryBodies p={p} t={t} />}
          {step === STEP.fusion && <Fusion p={p} t={t} />}
          {step === STEP.funnel && <Funnel p={p} t={t} />}
        </div>
      </div>
    </ProvenanceScope>
  );
}

function MetadataCard({ p, step, t }: { p: Sc4Payload; step: number; t: number }) {
  const highlightFilter = step >= STEP.query;
  return (
    <>
      <PanelSection title="Unit metadata" provenance={p.provenance} meta={p.unit.label}>
        <span className="sr-only">Fields set at ingestion, with the stage that set each.</span>
      </PanelSection>
      {p.groups.map((g, gi) => (
        <section key={g.title} className="border-b border-border px-4 py-3 last:border-0" style={{ opacity: step === STEP.metadata ? 0.15 + 0.85 * easeCubicOut(seg(t, 0.1 + gi * 0.22, 0.4 + gi * 0.22)) : 1 }}>
          <h3 className="mb-1.5 text-caps uppercase text-text-2">{g.title}</h3>
          {byStage(g.rows).map((run) => (
            <div key={`${run.stage}-${run.rows[0].key}`} className="mb-1 last:mb-0">
              <p className="px-1.5 pb-0.5 pt-1 text-xs text-text-3">
                set by {run.stage}
                {run.stageMs !== undefined && (
                  <>
                    {" · "}
                    <Num value={run.stageMs} kind="ms" size="xs" bare />
                  </>
                )}
              </p>
              <dl className="grid grid-cols-[minmax(148px,max-content)_minmax(0,1fr)] gap-x-3">
                {run.rows.map((r) => (
                  <Row key={r.key} r={r} highlight={highlightFilter && r.inFilter} />
                ))}
              </dl>
            </div>
          ))}
        </section>
      ))}
    </>
  );
}

/** Consecutive rows written by the same stage, under one "set by <stage> · ms" line. */
function byStage(rows: MetaRow[]): { stage: string; stageMs?: number; rows: MetaRow[] }[] {
  const out: { stage: string; stageMs?: number; rows: MetaRow[] }[] = [];
  for (const r of rows) {
    const last = out.at(-1);
    if (last && last.stage === r.stage) last.rows.push(r);
    else out.push({ stage: r.stage, stageMs: r.stageMs, rows: [r] });
  }
  return out;
}

/** One field: its key (with a filter mark when the logged filter tests it) and its full value, wrapped. */
function Row({ r, highlight }: { r: MetaRow; highlight: boolean }) {
  const cell = cn("py-1", highlight && "bg-surface-2");
  return (
    <>
      <dt className={cn(cell, "flex items-start gap-1 rounded-l-xs pl-1.5 font-mono text-mono-sm text-text-2")}>
        {highlight ? <Filter className="mt-[3px] size-3 shrink-0 text-text" aria-label="in the filter" /> : <span className="w-3 shrink-0" aria-hidden />}
        <span>{r.key}</span>
      </dt>
      <dd className={cn(cell, "min-w-0 rounded-r-xs pr-1.5 font-mono text-mono-sm text-text [overflow-wrap:anywhere]")} title={r.value}>
        {/* wraps rather than truncating; a long list keeps three lines and the full value is the tooltip and in the receipts */}
        <span className="line-clamp-3">{r.value}</span>
      </dd>
    </>
  );
}

function UnitOnPage({ p, t }: { p: Sc4Payload; t: number }) {
  return (
    <figure className="flex h-full min-h-[320px] flex-col overflow-hidden rounded-md border border-border">
      <figcaption className="flex items-center gap-2 border-b border-border bg-bg-subtle px-3 py-2 text-xs text-text-2">
        <span className="truncate text-text">{p.unit.label}</span>
        <span className="ml-auto font-mono text-mono-sm">
          {p.unit.fileName} · p{p.unit.page}
        </span>
      </figcaption>
      <div className="min-h-0 flex-1">
        <PageCanvas ariaLabel={`${p.unit.fileName}, page ${p.unit.page}: the unit`} image={p.unit.image} pageWidth={p.unit.pageWidth} pageHeight={p.unit.pageHeight} focus={pad(p.unit.rect, 40)} animate={false} padding={16}>
          <Boundary rect={p.unit.rect} progress={seg(t, 0, 0.6)} width={1.5} />
        </PageCanvas>
      </div>
    </figure>
  );
}

/** Pretty JSON that keeps a value on one line when it fits in `width` characters (filter clauses stay readable). */
export function compactJson(v: unknown, indent = "", width = 64): string {
  const flat = JSON.stringify(v);
  if (flat === undefined || flat.length <= width - Math.min(indent.length, 16) || v === null || typeof v !== "object") return flat ?? "null";
  const inner = indent + "  ";
  if (Array.isArray(v)) return `[\n${v.map((x) => inner + compactJson(x, inner, width)).join(",\n")}\n${indent}]`;
  const rows = Object.entries(v as Record<string, unknown>).map(([k, x]) => `${inner}${JSON.stringify(k)}: ${compactJson(x, inner, width)}`);
  return `{\n${rows.join(",\n")}\n${indent}}`;
}

/** Shown inside each body where the logged filter sits (the filter itself is printed once, above). */
const FILTER_REF = "‹the filter above›";

function QueryBodies({ p, t }: { p: Sc4Payload; t: number }) {
  const q = subQueries({ ...p.echo, filter: FILTER_REF as unknown as Record<string, unknown> }, p.inspection.queryText);
  const filter = compactJson(p.echo.filter, "", 72);
  const dense = compactJson(q.dense, "", 56);
  const bm25 = compactJson(q.bm25, "", 56);
  const on = seg(t, 0.3, 0.6) > 0;
  const refLines = (body: string) => body.split("\n").flatMap((l, i) => (l.includes(FILTER_REF) ? [i + 1] : []));
  return (
    <div className="flex flex-col gap-3">
      <p className="flex flex-wrap items-center gap-2 text-sm text-text-2">
        <span className="font-mono text-mono-sm text-text">search_units</span>
        <span>“{p.inspection.queryText}”</span>
        <span className="text-text-3">·</span>
        <span className="font-mono text-mono-sm">{p.echo.index}</span>
        <span className="ml-auto flex items-center gap-1.5 text-xs">
          <ScopeTag /> filter echoed by the run; bodies rebuilt as retrieval.py builds them
        </span>
      </p>
      <CodeBlock title="filter · as the run echoed it" code={filter} highlight={on ? filter.split("\n").map((_, i) => i + 1) : []} maxHeight={240} />
      <div className="grid gap-3 xl:grid-cols-2">
        <CodeBlock title="dense · knn with an efficient filter" code={dense} highlight={on ? refLines(dense) : []} maxHeight={320} />
        <CodeBlock title="BM25 · match on searchText" code={bm25} highlight={on ? refLines(bm25) : []} maxHeight={320} />
      </div>
    </div>
  );
}

function Fusion({ p, t }: { p: Sc4Payload; t: number }) {
  return (
    <div className="flex max-w-3xl flex-col gap-3">
      <section className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface px-4 py-3 text-sm">
        <Badge tone="neutral">{p.echo.normalization}</Badge>
        {Object.entries(p.echo.weights).map(([k, v]) => (
          <span key={k} className="flex items-center gap-1 text-text-2">
            w<sub>{k}</sub> <Num value={v} kind="raw" bare />
          </span>
        ))}
        <span className="text-text-3">·</span>
        <span className="text-text-2">
          K <Num value={p.echo.k} kind="int" bare /> · each sub-query <Num value={p.echo.subQueryK} kind="int" bare />
        </span>
      </section>
      <section className="rounded-md border border-border bg-surface">
        <div className="grid grid-cols-[20px_minmax(0,1fr)_repeat(3,72px)] gap-3 border-b border-border bg-bg-subtle px-4 py-2 text-caps uppercase text-text-2">
          <span>#</span>
          <span>unit</span>
          <span className="text-right">dense</span>
          <span className="text-right">BM25</span>
          <span className="text-right">fused</span>
        </div>
        <ol>
          {p.results.map((u, i) => {
            const k = easeCubicOut(seg(t, 0.1 + i * 0.08, 0.5 + i * 0.08));
            return (
              <li key={u.unitId} className="grid grid-cols-[20px_minmax(0,1fr)_repeat(3,72px)] items-center gap-3 border-b border-border px-4 py-2 last:border-0" style={{ opacity: 0.2 + 0.8 * k }}>
                <span className="tnum font-mono text-mono-sm text-text-2">{u.rank}</span>
                <span className="truncate text-sm text-text">{u.label}</span>
                <Bar v={u.scores.denseNorm} k={k} />
                <Bar v={u.scores.bm25Norm} k={k} />
                <Bar v={u.scores.fused} k={k} strong />
              </li>
            );
          })}
        </ol>
      </section>
      <p className="text-xs text-text-2">Fused in the search_units tool. No RRF; v1 uses no OpenSearch search pipeline.</p>
    </div>
  );
}

function Bar({ v, k, strong }: { v: number; k: number; strong?: boolean }) {
  return (
    <span className="flex flex-col items-end gap-0.5">
      <Num value={v} kind="ratio" size="xs" bare />
      <span className="h-1 w-full overflow-hidden rounded-full bg-surface-3">
        <span className={cn("block h-full rounded-full", strong ? "bg-text" : "bg-text-3")} style={{ width: `${Math.max(0, Math.min(1, v)) * 100 * k}%` }} />
      </span>
    </span>
  );
}

function Funnel({ p, t }: { p: Sc4Payload; t: number }) {
  const c = p.inspection.counts;
  const derived = new Set(c.derived ?? ["denseTopN", "bm25TopN"]);
  const local = !/^OpenSearch/.test(p.inspection.countedBy);
  const when = p.inspection.countedAt ? `Counted ${p.inspection.countedAt.replace("T", " ").slice(0, 19)} UTC, not at the run's time.` : "Counted when the mock world was exported, not at the run's time.";
  return (
    <div className="flex max-w-3xl flex-col gap-3">
      <header className="flex items-center gap-2">
        <h3 className="text-caps uppercase text-text-2">_count with the logged filter</h3>
        <span className="ml-auto">{local ? <ProvenanceTag provenance="local-run" source={p.inspection.countedBy} /> : <ScopeTag />}</span>
      </header>
      <FilterFunnel
        total={c.indexTotal}
        reveal={seg(t, 0, 0.9)}
        stages={[
          { title: "This package and lane", lanes: [{ label: p.inspection.index, count: c.indexTotal, note: "units you may read in the run's package and lane (the index is shared)" }] },
          { title: "After the filter", lanes: [{ label: "filter matches", count: c.afterFilter, note: "readable by the caller, this package, this lane, not held" }] },
          {
            title: "Sub-queries",
            lanes: [
              { label: `dense top ${p.inspection.subQueryK}`, count: c.denseTopN, provenance: derived.has("denseTopN") ? "derived" : undefined, note: derived.has("denseTopN") ? `min(${p.inspection.subQueryK}, after the filter)` : undefined },
              { label: `BM25 top ${p.inspection.subQueryK}`, count: c.bm25TopN, provenance: derived.has("bm25TopN") ? "derived" : undefined, note: `${c.bm25Matches} match the words${derived.has("bm25TopN") ? ` · min(${p.inspection.subQueryK}, matches)` : ""}` },
            ],
          },
          { title: "Fused", lanes: [{ label: `returned (K ${p.inspection.k})`, count: c.fusedK, note: "from the logged tool output" }] },
        ]}
      />
      <p className="text-xs text-text-2">
        {local ? "Counted locally by an in-memory stand-in for OpenSearch _count. " : ""}
        {when}
      </p>
    </div>
  );
}

export const sc4Filters: Storyboard<Sc4Payload> = {
  id: "sc-4",
  code: "SC-4",
  title: showcaseMeta("sc-4")?.title ?? "",
  claim: showcaseMeta("sc-4")?.claim ?? "",
  load: loadSc4,
  steps,
  render: (frame) => <Sc4Stage {...frame} />,
  receiptsPayload: (p) => ({ unit: p.unit, groups: p.groups, echo: p.echo, results: p.results.map((u) => ({ rank: u.rank, unitId: u.unitId, scores: u.scores })), inspection: p.inspection }),
};

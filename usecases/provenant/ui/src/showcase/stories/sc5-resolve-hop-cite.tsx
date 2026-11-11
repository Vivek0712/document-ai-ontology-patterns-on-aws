/**
 * SC-5 · Resolve, retrieve, hop, assemble, cite (Pattern 5). One logged evidence-units run of the
 * gate question, stop by stop: every stop is a tool call the run made, placed at its measured time
 *; the hop is drawn from the rows the call bound; the answer's figures open their regions.
 *
 * Every frame is `render({ payload, step, progress })`: no timers, no typed values.
 */
import { easeCubicOut } from "d3-ease";
import { ArrowUpRight, Check, CircleDashed } from "lucide-react";
import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Badge, Chip, CitationChip, JsonView, LaneBadge, Num, ProvenanceScope, ScopeTag, cn } from "../../design";
import { claimLabel, traceHref } from "../../features/ask/parts";
import { GraphCanvas, edgeKey } from "../../viz/GraphCanvas";
import { lerp, pad, seg } from "../../viz/geom";
import { Boundary, BoxOutline, Mark, PageCanvas } from "../../viz/PageCanvas";
import { RunTimeline } from "../../viz/RunTimeline";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { PanelSection } from "./panel";
import { type Sc5Call, type Sc5Payload, citeCaption, hopCaption, loadSc5, resolveCaption, runSegments } from "./sc5-payload";
import { SC5_STEPS } from "./steps";

const STEP = Object.fromEntries(SC5_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC5_STEPS)[number]["id"], number>;

const runReceipt = (p: Sc5Payload) => ({ label: "Logged run record", value: p.run.runId, source: p.run.s3Key, provenance: p.provenance });
const callReceipt = (p: Sc5Payload, c: Sc5Call | undefined, label: string) =>
  c ? { label, value: `${c.tool} · ${c.callId} · at ${(c.at / 1000).toFixed(3)} s · ${c.ms} ms`, path: `$.calls[${p.calls.indexOf(c)}]`, source: `${p.run.s3Key} · toolCalls`, provenance: p.provenance } : { label, value: "not called in this run", provenance: p.provenance };

const steps: StoryStep<Sc5Payload>[] = [
  {
    ...SC5_STEPS[0],
    durationMs: 1400,
    caption: "One question, one agent.",
    services: ["Amazon Bedrock AgentCore", "Amazon Bedrock"],
    receipts: (p) => [runReceipt(p), { label: "Model · prompt sha", value: `${p.run.modelId} · ${p.run.promptSha}`, path: "$.run.modelId", provenance: p.provenance }, { label: "Lane config sha", value: p.run.configSha, path: "$.run.configSha", provenance: p.provenance }],
  },
  {
    ...SC5_STEPS[1],
    durationMs: 2000,
    caption: resolveCaption,
    services: ["Amazon Bedrock", "Amazon Neptune"],
    receipts: (p) => [callReceipt(p, p.resolve.call, "concept_by_label_v1"), ...p.resolve.terms.map((t) => ({ label: `Ontology term · ${t.where}`, value: t.term, path: "$.resolve.terms", provenance: p.provenance }))],
  },
  {
    ...SC5_STEPS[2],
    durationMs: 2600,
    caption: "Hybrid retrieval over whole units, filtered first.",
    services: ["AgentCore Gateway", "AWS Lambda · search_units", "OpenSearch Serverless"],
    receipts: (p) => [callReceipt(p, p.retrieve?.call, "search_units call"), { label: "Retrieved units (dense, BM25, fused: normalised)", value: p.retrieve?.units.map((u) => `${u.rank} ${u.unitId} ${u.scores.fused}`).join(" · "), path: "$.retrieve.units", provenance: p.provenance }],
  },
  {
    ...SC5_STEPS[3],
    durationMs: 2800,
    caption: hopCaption,
    services: ["AgentCore Gateway", "AWS Lambda · graph_hop", "Amazon Neptune"],
    receipts: (p) => [
      callReceipt(p, p.hop?.call, "graph_hop call"),
      { label: "Hop path (the rows' bound patterns)", value: p.hop ? `${p.hop.path.nodes.length} nodes · ${p.hop.path.edges.length} edges · ${p.hop.path.source}` : "no hop", source: p.hop ? `GET /runs/${p.run.runId}/calls/${p.hop.call.callId}/path` : undefined, path: "$.hop.path", provenance: p.provenance },
      { label: "Rows · total", value: p.hop ? `${p.hop.rows.length} rows · ${p.hop.total ?? "no total"}` : undefined, path: "$.hop.rows", provenance: p.provenance },
    ],
  },
  {
    ...SC5_STEPS[4],
    durationMs: 2000,
    caption: "Whole units are assembled, with breadcrumbs.",
    services: ["Amazon Bedrock"],
    receipts: (p) => [{ label: "Input tokens (usage.inputTokens)", value: p.run.usage.inputTokens, path: "$.run.usage", source: p.run.s3Key, provenance: p.provenance }, { label: "Units in context", value: p.retrieve?.units.map((u) => u.breadcrumb).join(" · "), path: "$.retrieve.units", provenance: p.provenance }],
  },
  {
    ...SC5_STEPS[5],
    durationMs: 2600,
    caption: citeCaption,
    services: ["AWS Lambda · rp-get-region", "Amazon S3"],
    receipts: (p) => [
      { label: "Answer", value: p.answer.answer, path: "$.answer.answer", provenance: p.provenance },
      ...p.regions.map((r, i) => ({ label: `Region · ${claimLabel(r.claim)}`, value: `${r.fileName} · p${r.page} · (${r.field.map((n) => n.toFixed(1)).join(", ")})`, path: `$.regions[${i}]`, source: `GET /region/${r.claim.unitId}?field=${r.claim.fieldId ?? ""}`, provenance: p.provenance })),
    ],
  },
];

// --------------------------------------------------------------------------------------------- frame

/** The run time the playhead sits at in each step (measured times; the motion between them is paced). */
function playheadAt(p: Sc5Payload, step: number, t: number): number {
  const segs = runSegments(p);
  const firstTool = p.calls.length ? Math.min(...p.calls.map((c) => c.at)) : p.run.latencyMs;
  const lastTool = p.calls.length ? Math.max(...p.calls.map((c) => c.at + c.ms)) : p.run.latencyMs;
  const ease = easeCubicOut(seg(t, 0, 0.8));
  switch (step) {
    case STEP.question:
      return 0;
    case STEP.resolve:
      return lerp(0, p.resolve.call?.at ?? firstTool, ease);
    case STEP.retrieve:
      return p.retrieve ? p.retrieve.call.at + p.retrieve.call.ms * ease : firstTool;
    case STEP.hop:
      return p.hop ? p.hop.call.at + p.hop.call.ms * ease : lastTool;
    case STEP.assemble:
      return lerp(lastTool, segs.at(-1)?.to ?? p.run.latencyMs, ease);
    default:
      return p.run.latencyMs;
  }
}

function focusCall(p: Sc5Payload, step: number): Sc5Call | undefined {
  if (step === STEP.resolve) return p.resolve.call;
  if (step === STEP.retrieve) return p.retrieve?.call;
  if (step === STEP.hop) return p.hop?.call;
  return undefined;
}

function Sc5Stage({ payload: p, step, progress: t }: Frame<Sc5Payload>) {
  const [picked, setPicked] = useState<string>();
  const focus = focusCall(p, step);
  const inspected = p.calls.find((c) => c.callId === picked) ?? focus;
  const reached = (c: Sc5Call) => (playheadAt(p, step, t) >= c.at ? 1 : 0);
  return (
    <ProvenanceScope provenance={p.provenance} source={p.run.s3Key}>
      <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)]">
        <section className="border-b border-border px-4 py-3 sm:px-5">
          <h2 className="mb-2 flex items-center gap-2 text-caps uppercase text-text-2">
            <span>Run timeline</span>
            <span className="font-mono normal-case tracking-normal text-text-3">{p.run.runId}</span>
            <span className="ml-auto text-xs normal-case tracking-normal text-text-3">run bar to scale · measured times as text</span>
            <ScopeTag />
          </h2>
          <RunTimeline
            segments={runSegments(p)}
            stops={p.calls.map((c) => ({ ...c, detail: typeof c.input.template === "string" ? c.input.template : undefined }))}
            total={p.run.latencyMs}
            playhead={playheadAt(p, step, t)}
            focus={focus?.callId}
            shown={(id) => reached(p.calls.find((c) => c.callId === id)!)}
            onSelect={(id) => setPicked((x) => (x === id ? undefined : id))}
            selected={picked}
          />
        </section>
        <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_340px] lg:grid-rows-1">
          <div className="flex min-h-0 min-w-0 flex-col">
            <Moves step={step} />
            <div className={cn("min-h-0 flex-1 px-4 py-4 sm:px-5", step === STEP.hop ? "flex flex-col" : "overflow-y-auto")}>
              <StepBody p={p} step={step} t={t} />
            </div>
          </div>
          <aside className="min-h-0 overflow-y-auto border-t border-border bg-bg-subtle lg:border-l lg:border-t-0">
            <Inspector p={p} call={inspected} />
          </aside>
        </div>
      </div>
    </ProvenanceScope>
  );
}

/** The five moves of Pattern 5, the current one in `--text`. */
function Moves({ step }: { step: number }) {
  return (
    <ol className="flex h-9 shrink-0 items-center gap-1.5 border-b border-border px-4 text-xs text-text-3 sm:px-5" aria-label="Pattern 5">
      {SC5_STEPS.slice(1).map((s, i) => (
        <li key={s.id} className="flex items-center gap-1.5">
          {i > 0 && <span aria-hidden>›</span>}
          <span className={cn(step === i + 1 ? "font-medium text-text" : step > i + 1 && "text-text-2")} aria-current={step === i + 1 ? "step" : undefined}>
            {s.title}
          </span>
        </li>
      ))}
    </ol>
  );
}

function StepBody({ p, step, t }: { p: Sc5Payload; step: number; t: number }) {
  const enter = (a = 0, b = 0.35) => {
    const k = easeCubicOut(seg(t, a, b));
    return { opacity: k, transform: `translateY(${(1 - k) * 4}px)` };
  };
  switch (step) {
    case STEP.question:
      return <QuestionCard p={p} style={enter()} />;
    case STEP.resolve:
      return <Resolve p={p} t={t} />;
    case STEP.retrieve:
      return <Retrieve p={p} t={t} />;
    case STEP.hop:
      return <Hop p={p} t={t} />;
    case STEP.assemble:
      return <Assemble p={p} t={t} />;
    default:
      return <Cite p={p} t={t} />;
  }
}

function QuestionCard({ p, style }: { p: Sc5Payload; style?: React.CSSProperties }) {
  return (
    <section className="max-w-2xl rounded-md border border-border bg-surface" style={style}>
      <div className="border-b border-border px-4 py-3">
        <p className="text-caps uppercase text-text-2">Question</p>
        <p className="mt-1 text-md text-text">{p.run.question}</p>
      </div>
      <dl className="grid grid-cols-[120px_minmax(0,1fr)] gap-x-3 gap-y-1.5 px-4 py-3 text-sm">
        <dt className="text-text-2">Package</dt>
        <dd className="font-mono text-mono-sm text-text">{p.run.packageId}</dd>
        <dt className="text-text-2">Lane</dt>
        <dd>
          <LaneBadge pv={p.run.pipelineVersion} />
        </dd>
        <dt className="text-text-2">Model</dt>
        <dd className="font-mono text-mono-sm text-text">{p.run.modelId}</dd>
        <dt className="text-text-2">Tools</dt>
        <dd className="flex flex-wrap gap-1">
          {p.run.toolsEnabled.map((x) => (
            <Chip key={x} className="font-mono">
              {x}
            </Chip>
          ))}
        </dd>
      </dl>
    </section>
  );
}

function Resolve({ p, t }: { p: Sc5Payload; t: number }) {
  return (
    <div className="flex max-w-2xl flex-col gap-3">
      <PanelSection title="Concept lookup" className="rounded-md border border-border bg-surface">
        {p.resolve.call ? (
          <p className="flex items-center gap-2 text-sm text-text">
            <Check className="size-3.5 text-agent" aria-hidden />
            <span className="font-mono text-mono-sm">graph_hop {String(p.resolve.call.input.template)}</span>
            <span className="text-text-2">{p.resolve.call.summary}</span>
          </p>
        ) : (
          <p className="flex items-center gap-2 text-sm text-text-2">
            <CircleDashed className="size-3.5" aria-hidden />
            <span className="font-mono text-mono-sm text-text">concept_by_label_v1</span> not called in this run
          </p>
        )}
      </PanelSection>
      <PanelSection title="Ontology terms in this run" meta="Read from the run's tool inputs and the graph the hop bound." className="rounded-md border border-border bg-surface">
        <ul className="flex flex-col divide-y divide-border">
          {p.resolve.terms.map((x, i) => {
            const k = seg(t, 0.15 + i * 0.08, 0.4 + i * 0.08);
            return (
              <li key={x.term} className="flex h-8 items-center gap-3" style={{ opacity: k }}>
                <span className="size-2 shrink-0 rotate-45 bg-concept" aria-hidden />
                <span className="min-w-0 flex-1 truncate font-mono text-mono-sm text-text">{x.term}</span>
                <span className="shrink-0 text-xs text-text-2">{x.where}</span>
              </li>
            );
          })}
        </ul>
      </PanelSection>
    </div>
  );
}

function Retrieve({ p, t }: { p: Sc5Payload; t: number }) {
  if (!p.retrieve) return <p className="text-sm text-text-2">search_units was not called in this run.</p>;
  const filter = (p.calls.find((c) => c.callId === p.retrieve!.call.callId)?.input ?? {}) as Record<string, unknown>;
  return (
    <section className="max-w-3xl rounded-md border border-border bg-surface">
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
        <span className="font-mono text-mono-sm text-text">search_units</span>
        <span className="truncate text-sm text-text-2">“{String(filter.query ?? "")}”</span>
        <Link to="/showcase/sc-4" className="ml-auto inline-flex items-center gap-1 text-xs text-text-2 hover:text-text">
          the filter inside both sub-queries <ArrowUpRight className="size-3" aria-hidden />
        </Link>
      </header>
      <ol className="divide-y divide-border">
        {p.retrieve.units.map((u, i) => {
          const k = easeCubicOut(seg(t, 0.1 + i * 0.06, 0.55 + i * 0.06));
          return (
            <li key={u.unitId} className="grid grid-cols-[20px_minmax(0,1fr)_200px] items-center gap-3 px-4 py-2" style={{ opacity: 0.2 + 0.8 * k }}>
              <span className="tnum text-right font-mono text-mono-sm text-text-2">{u.rank}</span>
              <div className="min-w-0">
                <p className="truncate text-sm text-text">{u.label}</p>
                <p className="flex items-center gap-1.5 text-xs text-text-2">
                  <Badge tone="concept" dot>
                    {u.unitType}
                  </Badge>
                  <span className="truncate">{u.breadcrumb}</span>
                </p>
              </div>
              <Scores s={u.scores} grow={k} />
            </li>
          );
        })}
      </ol>
    </section>
  );
}

function Scores({ s, grow }: { s: { denseNorm: number; bm25Norm: number; fused: number }; grow: number }) {
  const rows: [string, number][] = [["dense", s.denseNorm], ["BM25", s.bm25Norm], ["fused", s.fused]];
  return (
    <div className="grid grid-cols-[40px_minmax(0,1fr)_40px] items-center gap-x-2 gap-y-0.5 text-xs text-text-2" aria-label="Normalised scores">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <span>{k}</span>
          <span className="h-1 overflow-hidden rounded-full bg-surface-3">
            <span className={cn("block h-full rounded-full", k === "fused" ? "bg-text" : "bg-text-3")} style={{ width: `${Math.max(0, Math.min(1, v)) * 100 * grow}%` }} />
          </span>
          <Num value={v} kind="ratio" bare className="justify-end" />
        </div>
      ))}
    </div>
  );
}

function Hop({ p, t }: { p: Sc5Payload; t: number }) {
  const navigate = useNavigate();
  if (!p.hop) return <p className="text-sm text-text-2">No graph_hop call in this run.</p>;
  return (
    <section className="flex h-full min-h-[300px] flex-col rounded-md border border-border bg-surface">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <span className="font-mono text-mono-sm text-text">graph_hop {p.hop.template}</span>
        <span className="text-xs text-text-2">the graph its rows bound · no further query</span>
      </header>
      <GraphCanvas
        className="min-h-0 flex-1 px-2 py-2"
        graph={p.hop.path}
        hop={{ order: p.hop.path.edges.map(edgeKey), progress: seg(t, 0.1, 0.85) }}
        reveal={seg(t, 0, 0.12) * 0.6 + 0.4}
        onOpen={(n) => {
          const m = /^urn:rp:(?:field|unit):([^/]+)(?:\/(.+))?$/.exec(n.iri);
          if (m) navigate(traceHref({ unitId: m[1], fieldId: m[2] }, p.run.runId));
        }}
        ariaLabel={`graph_hop ${p.hop.template}: ${p.hop.path.nodes.length} nodes, ${p.hop.path.edges.length} edges, from the logged rows`}
      />
    </section>
  );
}

/**
 * Columns of the approved templates that hold a decimal amount (graph_hop's value columns): they print
 * as money, like the total the tool computes from them. Every other column prints as returned.
 */
const MONEY_COLUMNS = new Set(["wagesBox1", "amount"]);

/** The rows a graph_hop call returned, and the total it computed (the hop step's inspector). */
function HopRows({ p }: { p: Sc5Payload }) {
  if (!p.hop) return null;
  const cols = Object.keys(p.hop.rows[0] ?? {}).filter((k) => !/^(unit|field)$/.test(k));
  const numeric = (c: string) => MONEY_COLUMNS.has(c) || p.hop!.rows.every((r) => /^-?\d+(\.\d+)?$/.test(r[c] ?? ""));
  return (
    <PanelSection title="Rows returned">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-left">
            {cols.map((c) => (
              <th key={c} className={cn("pb-1 text-xs font-normal text-text-2", numeric(c) && "text-right")}>
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {p.hop.rows.map((r, i) => (
            <tr key={i} className="border-t border-border">
              {cols.map((c) => (
                <td key={c} className={cn("tnum h-7 text-sm text-text", numeric(c) && "text-right")}>
                  <Num value={r[c]} kind={MONEY_COLUMNS.has(c) ? "money" : "raw"} bare className={numeric(c) ? "justify-end" : undefined} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {p.hop.total && (
        <p className="mt-2 flex items-baseline justify-end gap-2 border-t border-border pt-2 text-xs text-text-2">
          total, computed by the tool <Num value={p.hop.total} kind="money" size="md" bare />
        </p>
      )}
    </PanelSection>
  );
}

function Assemble({ p, t }: { p: Sc5Payload; t: number }) {
  const units = p.retrieve?.units ?? [];
  return (
    <div className="flex max-w-3xl flex-col gap-2">
      <p className="flex items-center gap-2 text-caps uppercase text-text-2">
        Context the model read
        <span className="ml-auto text-xs normal-case tracking-normal text-text-2">
          input tokens <Num value={p.run.usage.inputTokens} kind="int" size="xs" bare />
        </span>
      </p>
      {units.map((u, i) => {
        const k = easeCubicOut(seg(t, 0.05 + i * 0.07, 0.45 + i * 0.07));
        return (
          <article key={u.unitId} className="flex items-center gap-3 rounded-sm border border-border bg-surface px-3 py-2" style={{ opacity: k, transform: `translateY(${(1 - k) * 12}px)` }}>
            <span className="size-2 shrink-0 rounded-full bg-concept" aria-hidden />
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm text-text">{u.breadcrumb}</p>
              <p className="truncate font-mono text-mono-sm text-text-2">
                {u.unitType} · p{u.pages.join(", ")} · {u.fields.length} fields
              </p>
            </div>
          </article>
        );
      })}
      {p.hop && (
        <article className="flex items-center gap-3 rounded-sm border border-border bg-surface px-3 py-2" style={{ opacity: easeCubicOut(seg(t, 0.4, 0.8)) }}>
          <span className="size-3 shrink-0 rotate-45 border-[1.5px] border-concept" aria-hidden />
          <p className="min-w-0 flex-1 truncate text-sm text-text">
            graph_hop rows <span className="font-mono text-mono-sm text-text-2">{p.hop.template}</span>
          </p>
          <span className="text-xs text-text-2">
            <Num value={p.hop.rows.length} kind="int" size="xs" bare /> rows
          </span>
        </article>
      )}
    </div>
  );
}

function Cite({ p, t }: { p: Sc5Payload; t: number }) {
  const [whole, setWhole] = useState(false);
  return (
    <div className="flex flex-col gap-3">
      <section className="max-w-3xl rounded-md border border-border bg-surface px-4 py-3">
        <p className="text-sm leading-6 text-text">{p.answer.answer}</p>
        <div className="mt-2.5 flex flex-wrap items-center gap-1.5" style={{ opacity: seg(t, 0.1, 0.35) }}>
          {p.answer.total && <Num value={p.answer.total} kind="money" size="md" mark={p.check ? (p.check.match ? "cited" : "loss") : undefined} />}
          {p.answer.claims.map((c, i) => (
            <CitationChip key={i} kind={c.resolvable ? "cited" : "noregion"} label={claimLabel(c)} page={c.page} to={c.resolvable ? traceHref(c, p.run.runId) : undefined} />
          ))}
        </div>
      </section>
      <div className="flex items-center gap-2 text-xs text-text-2">
        <span>Each cited figure, cropped to its field with 40 pt of the page around it</span>
        <button type="button" className="ml-auto rounded-xs px-1.5 py-0.5 text-text-2 underline-offset-2 hover:text-text hover:underline" aria-pressed={whole} onClick={() => setWhole((w) => !w)}>
          {whole ? "Crop to the field" : "Whole page"}
        </button>
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        {p.regions.map((r, i) => {
          const k = seg(t, 0.3 + i * 0.12, 0.85 + i * 0.05);
          const view = whole ? ([0, 0, r.pageWidth, r.pageHeight] as const) : pad(r.field, 40);
          return (
            <figure key={i} className="overflow-hidden rounded-md border border-border bg-surface" style={{ opacity: 0.3 + 0.7 * seg(t, 0.25 + i * 0.12, 0.45 + i * 0.12) }}>
              <figcaption className="flex items-center gap-2 border-b border-border px-3 py-2 text-xs text-text-2">
                <span className="truncate text-text">{claimLabel(r.claim)}</span>
                <span className="ml-auto truncate font-mono text-mono-sm">
                  {r.fileName} · p{r.page}
                </span>
              </figcaption>
              <div className="h-[150px]">
                <PageCanvas ariaLabel={`${r.fileName}, page ${r.page}: the cited field`} image={r.image} pageWidth={r.pageWidth} pageHeight={r.pageHeight} focus={[...view]} animate={false} padding={8}>
                  <Boundary rect={r.unit} progress={k} width={1} tint={0.25} />
                  <BoxOutline rect={pad(r.field, 2)} tone="cited" width={1.5} fill="var(--cited-tint)" opacity={seg(t, 0.6, 0.9)} />
                  <Mark x={r.field[2] + 2} y={r.field[1]} kind="cited" opacity={seg(t, 0.7, 0.95)} />
                </PageCanvas>
              </div>
              <p className="flex items-baseline gap-2 border-t border-border px-3 py-2 text-xs text-text-2" style={{ opacity: seg(t, 0.65, 0.95) }}>
                <span className="min-w-0 flex-1 truncate" title={r.claim.label ?? r.claim.fieldId}>
                  {r.claim.label ?? r.claim.fieldId}
                </span>
                <span className="shrink-0 whitespace-nowrap">
                  <Num value={r.claim.value ?? null} kind="money" bare mark="cited" />
                </span>
              </p>
            </figure>
          );
        })}
      </div>
    </div>
  );
}

/** The call the step is about (or the one clicked on the timeline): what the model sent, what the gateway added. */
function Inspector({ p, call }: { p: Sc5Payload; call?: Sc5Call }) {
  if (!call)
    return (
      <PanelSection title="Run" provenance={p.provenance} source={p.run.s3Key}>
        <dl className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
          <dt className="text-text-2">Latency</dt>
          <dd>
            <Num value={p.run.latencyMs} kind="ms" />
          </dd>
          <dt className="text-text-2">Tokens</dt>
          <dd className="text-text">
            <Num value={p.run.usage.inputTokens} kind="int" /> in · <Num value={p.run.usage.outputTokens} kind="int" /> out
          </dd>
          <dt className="text-text-2">configSha</dt>
          <dd className="truncate font-mono text-mono-sm text-text" title={p.run.configSha}>
            {p.run.configSha.slice(0, 12)}
          </dd>
          <dt className="text-text-2">promptSha</dt>
          <dd className="truncate font-mono text-mono-sm text-text" title={p.run.promptSha}>
            {p.run.promptSha.slice(0, 12)}
          </dd>
        </dl>
        <Link to={`/workbench/packages/${encodeURIComponent(p.run.packageId)}/ask?runs=${encodeURIComponent(p.run.runId)}`} className="mt-3 inline-flex items-center gap-1 text-xs text-text-2 hover:text-text">
          Open in Ask <ArrowUpRight className="size-3" aria-hidden />
        </Link>
      </PanelSection>
    );
  return (
    <>
      <PanelSection title="Tool call" provenance={p.provenance} source={p.run.s3Key}>
        <p className="flex items-center gap-2">
          <span className="font-mono text-mono-sm text-text">{call.tool}</span>
          <Badge tone={call.status === "ok" ? "agent" : "loss"} dot>
            {call.status}
          </Badge>
          <span className="ml-auto text-xs text-text-2">
            <Num value={call.ms} kind="ms" size="xs" bare />
          </span>
        </p>
        {call.summary && <p className="mt-1 text-xs text-text-2">{call.summary}</p>}
      </PanelSection>
      <PanelSection title="Input from the model">
        <JsonView value={call.input} collapseAfter={2} maxHeight={180} />
      </PanelSection>
      {call.callId === p.hop?.call.callId && <HopRows p={p} />}
      <PanelSection title="Result as logged">
        <JsonView value={call.output} collapseAfter={1} maxHeight={200} />
      </PanelSection>
      <PanelSection title="Injected by the gateway" meta="Identity and scope the model never sees or sets.">
        <JsonView value={call.injected} collapseAfter={2} maxHeight={200} />
      </PanelSection>
    </>
  );
}

export const sc5ResolveHopCite: Storyboard<Sc5Payload> = {
  id: "sc-5",
  code: "SC-5",
  title: showcaseMeta("sc-5")?.title ?? "",
  claim: showcaseMeta("sc-5")?.claim ?? "",
  load: loadSc5,
  steps,
  render: (frame) => <Sc5Stage {...frame} />,
  receiptsPayload: (p) => ({ run: p.run, calls: p.calls.map(({ output: _o, ...c }) => c), hop: p.hop && { ...p.hop, path: { nodes: p.hop.path.nodes.length, edges: p.hop.path.edges.length } }, regions: p.regions }),
};

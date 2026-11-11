/**
 * SC-0 board frames (no page canvas): the question, the upload, both lanes running, the two answers and the
 * governed definition. Each is a pure function of the payload and the step's progress `t`.
 */
import { easeCubicInOut } from "d3-ease";
import { Check, FileText, GitCommitHorizontal, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { StageRecord } from "@contracts/api";
import { coaOf, curie } from "../../../../api/governance";
import { CrossCheckRow } from "../../../../features/knowledge/coa";
import { Badge, Chip, LaneBadge, Num, ScopeTag, Skeleton, Tooltip, cn } from "../../../../design";
import { AnswerChips, AnswerFigure, StabilityLine, ToolTimeline, callsOfRun } from "../../../../features/ask/parts";
import { seg } from "../../../../viz/geom";
import { type Lane, LANES } from "../../lending";
import { type Sc0Payload, ourTotal, rec, sha12 } from "../load";

const ease = easeCubicInOut;

/** "bronze/<owner>/pkg/file.pdf" → "bronze/…/pkg/file.pdf" (the full key is in the tooltip and receipts). */
const shortKey = (k: string) => {
  const parts = k.split("/");
  return parts.length > 3 ? `${parts[0]}/…/${parts.slice(-2).join("/")}` : k;
};

export function QuestionCard({ p, t }: { p: Sc0Payload; t: number }) {
  return (
    <section className="mx-auto flex max-w-[720px] flex-col gap-4 rounded-md border border-border bg-surface p-6" style={{ opacity: 0.4 + 0.6 * ease(seg(t, 0, 0.4)) }}>
      <div className="flex items-center gap-2">
        <span className="text-caps uppercase text-text-2">Golden question</span>
        <span className="font-mono text-mono-sm text-text-2">{p.runs.qid}</span>
        <Chip className="ml-auto">{p.packageName}</Chip>
      </div>
      <p className="text-xl text-text">“{p.question}”</p>
      <ul className="grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {p.documents.map((d, i) => (
          <li key={d.docId} className="flex items-center gap-2 text-sm text-text-2" style={{ opacity: seg(t, 0.3 + i * 0.08, 0.5 + i * 0.08) }}>
            <FileText className="size-4 shrink-0 text-text-3" aria-hidden />
            <span className="truncate font-mono text-mono-sm">{d.fileName}</span>
            <Badge tone="neutral" className="ml-auto">{d.docType?.replace("doc:", "")}</Badge>
          </li>
        ))}
      </ul>
      <p className="text-xs text-text-2">Fictitious data. The generator built these documents for this repository; the expected answer comes from the golden set.</p>
    </section>
  );
}

export function UploadList({ p, t }: { p: Sc0Payload; t: number }) {
  const n = p.documents.length;
  const done = p.documents.filter((_, i) => t >= 0.2 + (0.6 * (i + 1)) / n).length;
  return (
    <section className="rounded-md border border-border bg-surface">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <h3 className="text-sm font-medium">Upload to Bronze</h3>
        <span className="text-xs text-text-2">presigned PUT · register writes the sha256</span>
        <span className="ml-auto flex items-center gap-1.5 text-xs text-text-2">
          <Num value={done} kind="int" bare /> objects in Bronze
          <ScopeTag />
        </span>
      </header>
      <ul className="divide-y divide-border">
        {p.documents.map((d, i) => {
          const a = 0.2 + (0.6 * i) / n;
          const put = seg(t, a, a + 0.6 / n);
          const ok = t >= a + 0.6 / n;
          return (
            <li key={d.docId} className="grid grid-cols-[minmax(240px,1.4fr)_80px_minmax(0,1.6fr)_140px] items-center gap-3 px-4 py-2 text-sm">
              <span className="truncate font-mono text-mono-sm text-text" title={d.fileName}>
                {d.fileName}
              </span>
              <Num value={d.sizeBytes} kind="int" unit="B" bare className="justify-end text-text-2" />
              <span className="flex min-w-0 items-center gap-2">
                <span className="h-1 w-[64px] shrink-0 overflow-hidden rounded-full bg-surface-3">
                  <span className="block h-full bg-text-2" style={{ width: `${put * 100}%` }} />
                </span>
                <span className="truncate font-mono text-mono-sm text-text-2" title={d.bronzeKey}>
                  PUT {shortKey(d.bronzeKey)}
                </span>
              </span>
              <span className={cn("flex items-center gap-1.5 font-mono text-mono-sm", ok ? "text-text" : "text-text-3")} data-sha={ok ? d.sha256 : undefined}>
                {ok ? <Check className="size-3.5 shrink-0" aria-label="registered" /> : <span className="size-3.5 shrink-0" />}
                {ok ? sha12(d.sha256) : "sha256 …"}
              </span>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

const fmtS = (ms: number) => (ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`);

/** One lane of one document: a tick per stage, its width linear in the measured ms (skipped stages are a 4 px stub). */
function LaneTicks({ pv, stages, lit, label }: { pv: Lane; stages: StageRecord[]; lit: number; label?: boolean }) {
  const ran = stages.filter((s) => s.state !== "SKIPPED");
  const total = ran.reduce((s, x) => s + (x.durationMs ?? 0), 0) || 1;
  const longest = ran.reduce<StageRecord | undefined>((m, s) => ((s.durationMs ?? 0) > (m?.durationMs ?? 0) ? s : m), undefined);
  let acc = 0;
  return (
    <div className="flex h-4 min-w-0 items-center gap-0.5" role="img" aria-label={`${pv}: ${stages.map((s) => `${s.stageId} ${s.state.toLowerCase()}${s.durationMs !== undefined ? ` ${s.durationMs} ms` : ""}`).join(", ")}`}>
      {stages.map((s) => {
        const skipped = s.state === "SKIPPED";
        const start = acc / total;
        if (!skipped) acc += s.durationMs ?? 0;
        const on = !skipped && (lit >= start + 1e-6 || lit === 1);
        const showLabel = label && s === longest && (s.durationMs ?? 0) / total > 0.3;
        return (
          <Tooltip
            key={s.stageId}
            content={
              <span className="flex items-baseline gap-1.5">
                <span className="font-mono">{s.stageId}</span>
                <span className="text-text-2">{s.state.toLowerCase()}</span>
                {s.durationMs !== undefined && <Num value={s.durationMs} kind="ms" size="xs" />}
              </span>
            }
          >
            <span
              className={cn(
                "flex h-full min-w-[4px] items-center overflow-hidden rounded-[2px] border",
                skipped ? "border-dashed border-border-strong" : on ? "border-text-2 bg-text-2" : "border-border bg-surface-3",
                s.stageId === "pii_gate" && !skipped && "outline outline-1 outline-offset-1 outline-boundary",
              )}
              style={skipped ? { flex: "0 0 4px" } : { flex: `${Math.max(1, s.durationMs ?? 0)} 1 4px` }}
            >
              {showLabel && (
                <span className={cn("truncate px-1 font-mono text-mono-sm leading-none", on ? "text-inverse" : "text-text-2")}>
                  {s.stageId} {fmtS(s.durationMs ?? 0)}
                </span>
              )}
            </span>
          </Tooltip>
        );
      })}
    </div>
  );
}

/** W-04 watch strip: per document, both lanes as stage ticks (playback paced; widths and times measured). */
export function WatchStrip({ p, t }: { p: Sc0Payload; t: number }) {
  const lit = seg(t, 0.05, 0.85);
  return (
    <section className="flex flex-col gap-3">
      <div className="rounded-md border border-border bg-surface">
        <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
          <h3 className="text-sm font-medium">Both pipelines, same upload</h3>
          <span className="text-xs text-text-2">playback paced · widths and times measured</span>
          <span className="ml-auto">
            <ScopeTag />
          </span>
        </header>
        <div className="divide-y divide-border">
          {p.lanes.map((d) => (
            <div key={d.docId} className="grid grid-cols-[minmax(200px,0.7fr)_minmax(0,2fr)] gap-3 px-4 py-2">
              <span className="truncate pt-0.5 font-mono text-mono-sm text-text" title={d.fileName}>
                {d.fileName}
              </span>
              <div className="flex flex-col gap-1">
                {LANES.map((pv) => {
                  const l = d.lanes[pv];
                  if (!l) return null;
                  return (
                    <div key={pv} className="grid grid-cols-[136px_minmax(0,1fr)_64px] items-center gap-2">
                      <LaneBadge pv={pv} className="justify-self-start" />
                      <LaneTicks pv={pv} stages={l.stages} lit={lit} label={pv === "2.3.1"} />
                      <span className="text-right text-xs text-text-2" style={{ opacity: lit >= 1 ? 1 : 0.35 }}>
                        <Num value={l.lane.durationMs} kind="ms" bare />
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2" style={{ opacity: seg(t, 0.7, 0.95) }}>
        {LANES.map((pv) => (
          <div key={pv} className="flex flex-wrap items-center gap-x-4 gap-y-1 rounded-md border border-border bg-surface px-4 py-2.5 text-sm">
            <LaneBadge pv={pv} />
            {(["units", "held", "indexed", "triples"] as const).map((k) => (
              <span key={k} className="flex items-baseline gap-1.5 text-text-2">
                <Num value={p.counters[pv]?.[k]} kind="int" bare />
                {k}
              </span>
            ))}
          </div>
        ))}
      </div>
      <p className="text-xs text-text-2">One tick per stage, width linear in its measured time within the row. The PII gate is outlined in orange; a dashed stub is a stage that lane skips.</p>
    </section>
  );
}

/** `before` and `after`: the two lanes side by side; the evidence-units column fills in at `after`. */
export function AskColumns({ p, after, t }: { p: Sc0Payload; after: boolean; t: number }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-text-2">
        “{p.question}” <span className="font-mono text-mono-sm">· {p.runs.qid}</span>
      </p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <LaneColumn p={p} pv="1.0" reveal={after ? 1 : ease(seg(t, 0, 0.6))} dim={after} />
        {after ? <LaneColumn p={p} pv="2.3.1" reveal={ease(seg(t, 0, 0.6))} trace={seg(t, 0.4, 0.9)} /> : <PendingLane />}
      </div>
      {after && (
        <div className="flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface px-4 py-2.5 text-sm" style={{ opacity: seg(t, 0.6, 0.9) }} data-testid="diff-strip">
          <span className="text-caps uppercase text-text-2">Config diff</span>
          <span className="text-text">{p.diff.summary}</span>
          <span className="ml-auto flex items-center gap-1.5 text-xs text-text-2">
            prompt {p.diff.promptSha.equal ? "identical" : "differs"} · model {p.diff.modelId.equal ? "identical" : "differs"}
          </span>
        </div>
      )}
    </div>
  );
}

/** The evidence-units card before it is asked: a solid, quiet placeholder (dashed would read as "no region"). */
function PendingLane() {
  return (
    <section className="flex min-h-[220px] flex-col rounded-md border border-border bg-surface/50" data-lane="2.3.1" aria-label="Evidence units: asked next">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2">
        <LaneBadge pv="2.3.1" />
        <span className="text-xs text-text-3">next: the same question on evidence units</span>
      </header>
      <div className="flex flex-col gap-3 p-4">
        <Skeleton className="h-7 w-[160px]" />
        <Skeleton className="h-[22px] w-[288px]" />
        <Skeleton className="h-3 w-[224px]" />
      </div>
    </section>
  );
}

function LaneColumn({ p, pv, reveal, dim, trace = 0 }: { p: Sc0Payload; pv: Lane; reveal: number; dim?: boolean; trace?: number }) {
  const r = rec(p, pv);
  const s = p.runs.stats[pv];
  return (
    <section className={cn("flex min-w-0 flex-col rounded-md border border-border bg-surface transition-opacity duration-base", dim && "opacity-70")} style={{ opacity: dim ? 0.7 : reveal }} data-lane={pv}>
      <header className="flex items-center gap-2 border-b border-border px-4 py-2">
        <LaneBadge pv={pv} />
        <span className="truncate font-mono text-mono-sm text-text-2">{r.runId}</span>
        <span className="ml-auto flex items-center gap-2">
          <Link to={`/workbench/packages/${encodeURIComponent(p.packageId)}/ask?runs=${p.runs.runs["1.0"].runId},${p.runs.runs["2.3.1"].runId}`} className="text-xs text-text-2 hover:text-text">
            Open in Ask
          </Link>
          <ScopeTag />
        </span>
      </header>
      <div className="flex flex-col gap-3 p-4">
        <AnswerFigure answer={r.answer} check={r.check} />
        {r.answer ? <AnswerChips answer={r.answer} runId={r.runId} /> : <span className="text-xs text-text-2">no answer</span>}
        <StabilityLine stats={s} />
        {trace > 0 && (
          <div style={{ opacity: trace }}>
            <ToolTimeline calls={callsOfRun(r)} latencyMs={r.latencyMs} reveal={Math.max(0.01, trace)} note={p.provenance === "local-run" ? <span>tool ms in-process (no Lambda or Gateway)</span> : undefined} />
          </div>
        )}
      </div>
    </section>
  );
}

export function DefinitionCard({ p, t }: { p: Sc0Payload; t: number }) {
  const d = p.definition;
  const coa = coaOf(d);
  if (!d) {
    return (
      <section className="mx-auto max-w-[720px] rounded-md border border-border bg-surface p-6 text-sm text-text-2">
        <p className="text-text">The governed definition is not available on this stack.</p>
        <p className="mt-1">{p.definitionError}</p>
      </section>
    );
  }
  const rows: [string, ReactNode][] = [
    ["Concept", <span className="font-mono text-mono-sm">{curie(d.definition.iri)} ⊑ {d.definition.subClassOf.map(curie).join(", ")}</span>],
    ["Operand", <span className="font-mono text-mono-sm">{d.definition.operandConcepts.map(curie).join(", ")}</span>],
    ["Computed by", <span className="font-mono text-mono-sm">{d.definition.computedBy} · sha256 {sha12(d.template?.sha256)}</span>],
    ["Source", <span className="font-mono text-mono-sm">{d.ontology.source} · sha256 {sha12(d.ontology.sourceSha256)}</span>],
    ["Commit", d.git ? <span className="font-mono text-mono-sm">{d.git.commit.slice(0, 12)} · {d.git.author}</span> : <span className="text-text-2">not committed yet</span>],
    ["Reviewer", d.review ? <span>{d.review.reviewer}</span> : <span className="text-text-2">no review record yet</span>],
    [
      d.check.command,
      <span className="flex items-center gap-1.5">
        {d.check.passed === d.check.total && <Check className="size-3.5 text-cited" aria-label="passed" />}
        <Num value={d.check.passed} kind="int" bare /> of <Num value={d.check.total} kind="int" bare /> checks passed
      </span>,
    ],
  ];
  return (
    <section className="mx-auto flex max-w-[760px] flex-col gap-4 rounded-md border border-border bg-surface p-6" style={{ opacity: 0.3 + 0.7 * ease(seg(t, 0, 0.4)) }}>
      <div className="flex items-center gap-2">
        <ShieldCheck className="size-4 text-text-2" aria-hidden />
        <span className="text-caps uppercase text-text-2">{coa ? "Governed in Git · cross-checked in COA" : "Governed in Git · COA off"}</span>
        <Badge tone="neutral" className="ml-auto">ontology {d.ontology.version}</Badge>
      </div>
      <div>
        <p className="text-md font-medium text-text">{d.definition.label}</p>
        <p className="mt-1 text-sm text-text">{d.definition.description}</p>
        {d.definition.altLabels.length > 0 && <p className="mt-1 text-xs text-text-2">also: {d.definition.altLabels.join(", ")}</p>}
      </div>
      <dl className="grid grid-cols-[136px_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm" style={{ opacity: seg(t, 0.25, 0.6) }}>
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-text-2">{k}</dt>
            <dd className="min-w-0 break-words">{v}</dd>
          </div>
        ))}
      </dl>
      <p className="flex items-center gap-1.5 text-xs text-text-2">
        <GitCommitHorizontal className="size-3.5" aria-hidden />
        The answer prompt carries this sentence verbatim, and {d.definition.computedBy} reads its operand concept from this class.
      </p>
      {coa && (
        <div style={{ opacity: seg(t, 0.55, 0.9) }}>
          <CrossCheckRow s={coa} ours={ourTotal(p)} reveal={seg(t, 0.6, 1)} />
        </div>
      )}
    </section>
  );
}

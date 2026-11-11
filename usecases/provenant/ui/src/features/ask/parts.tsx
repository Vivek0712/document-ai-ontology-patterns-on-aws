/**
 * Ask building blocks on the design system, shared by W-07 Ask, W-15 Runs and the SC-0 story:
 * the answer figure with its golden check, citation chips, the tool timeline (measured ms) and the N-of-N line.
 * Numbers render through <Num> and inherit the caller's ProvenanceScope.
 */
import { ArrowRight } from "lucide-react";
import type { ReactNode } from "react";
import type { AnswerPayload, Check, Claim, RetrievedUnit, RunRecord, ToolEndEvent, ToolStartEvent } from "@contracts/api";
import type { LaneRunStats } from "../../api/runs";
import { Badge, CitationChip, Num, Tooltip, cn } from "../../design";

/** `/workbench/trace/:unitId?field=&run=` (W-08). */
export function traceHref(c: { unitId: string; fieldId?: string }, runId?: string): string {
  const q = new URLSearchParams();
  if (c.fieldId) q.set("field", c.fieldId);
  if (runId) q.set("run", runId);
  const s = q.toString();
  return `/workbench/trace/${encodeURIComponent(c.unitId)}${s ? `?${s}` : ""}`;
}

/** Short chip label for a claim: "Maya Fictus · Box 1" from "W-2 · Maya Fictus · Wages (Boxes 1–6) · Box 1 · p.1". */
export function claimLabel(c: Claim): string {
  if (!c.resolvable) return c.chunkLabel ?? c.label ?? c.unitId;
  const parts = (c.label ?? "").split(" · ").filter((x) => !/^p\.\d+$/.test(x));
  return parts.length >= 4 ? `${parts[1]} · ${parts.at(-1)}` : (c.label ?? c.unitId);
}

/** The answer's total (or its text when it has none) with the golden check mark and the expected value. */
export function AnswerFigure({ answer, check, size = "figure", className }: { answer?: AnswerPayload; check?: Check; size?: "lg" | "figure"; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-1", className)}>
      {answer?.total ? (
        <Num value={answer.total} kind="money" size={size} mark={check ? (check.match ? "cited" : "loss") : undefined} />
      ) : (
        <span className={cn("text-text-2", size === "figure" ? "text-xl" : "text-md")}>{check ? (check.match ? "✓ " : "× ") : ""}no total</span>
      )}
      {check && (
        <span className="text-xs text-text-2">
          golden set <span className="font-mono">{check.qid}</span> · expected <Num value={check.expected} kind="money" bare />
        </span>
      )}
    </div>
  );
}

/**
 * The chips under an answer, decided from the data alone (never from the lane id): an arithmetic answer whose
 * claims all carry a value reads as its operands; anything else as one chip per claim.
 */
export function AnswerChips({ answer, runId, className }: { answer: AnswerPayload; runId?: string; className?: string }) {
  return answer.arithmetic && answer.claims.length > 0 && answer.claims.every((c) => c.value) ? (
    <OperandChips claims={answer.claims} runId={runId} total={answer.total} className={className} />
  ) : (
    <ClaimChips claims={answer.claims} runId={runId} className={className} />
  );
}

/**
 * Citation chips: cited claims open W-08; chunk claims show "no region" and are never links. Chunk claims with no
 * value that cite the same chunk collapse into one chip with a count.
 */
export function ClaimChips({ claims, runId, className }: { claims: Claim[]; runId?: string; className?: string }) {
  if (!claims.length) return <span className="text-xs text-text-2">no claims</span>;
  const shown: { c: Claim; n: number }[] = [];
  for (const c of claims) {
    const same = !c.resolvable && !c.value ? shown.find((x) => !x.c.resolvable && !x.c.value && x.c.unitId === c.unitId) : undefined;
    if (same) same.n += 1;
    else shown.push({ c, n: 1 });
  }
  return (
    <div className={cn("flex flex-wrap gap-1.5", className)}>
      {shown.map(({ c, n }, i) =>
        c.resolvable ? (
          <CitationChip key={i} kind="cited" label={claimLabel(c)} page={c.page} to={traceHref(c, runId)} title={`${c.fileName} · ${c.unitId}${c.fieldId ? ` · ${c.fieldId}` : ""}`} />
        ) : (
          <CitationChip key={i} kind="noregion" label={n > 1 ? `${claimLabel(c)} ×${n}` : claimLabel(c)} value={c.value ? <Num value={c.value} kind="money" bare /> : undefined} title={`${c.fileName ?? ""} · ${c.reason ?? "chunk has no page region"}`} />
        ),
      )}
    </div>
  );
}

/**
 * Operand chips for an arithmetic answer: "169,650.00 = 98,400.00 + 71,250.00", each operand its own chip. A cited
 * operand opens W-08; a chunk operand shows its value, its chunk and "no region", so two lanes' answers differ
 * only in ✓ region against × no region.
 */
export function OperandChips({ claims, runId, total, className }: { claims: Claim[]; runId?: string; total?: string; className?: string }) {
  const ops = claims.filter((c) => c.value);
  return (
    <div className={cn("flex flex-wrap items-center gap-1.5 text-sm text-text-2", className)}>
      {total && <Num value={total} kind="money" bare />}
      <span>=</span>
      {ops.map((c, i) => (
        <span key={i} className="flex items-center gap-1.5">
          {i > 0 && <span>+</span>}
          {c.resolvable ? (
            <CitationChip kind="operand" label={claimLabel(c).split(" · ")[0]} value={<Num value={c.value} kind="money" bare />} to={traceHref(c, runId)} title={`${c.fileName} · ${c.unitId}${c.fieldId ? ` · ${c.fieldId}` : ""}`} />
          ) : (
            <CitationChip kind="noregion" label={claimLabel(c)} value={<Num value={c.value} kind="money" bare />} title={`${c.fileName ?? ""} · ${c.reason ?? "chunk has no page region"}`} />
          )}
        </span>
      ))}
    </div>
  );
}

/** One tool call of a streamed run (tool_start, then tool_end when it returns). */
export interface TraceCall {
  start: ToolStartEvent;
  end?: ToolEndEvent;
}

export interface TimelineCall {
  callId: string;
  tool: string;
  /** ms from the run's start */
  at: number;
  ms?: number;
  status?: "ok" | "error" | "denied";
  summary?: string;
}

export function callsOfRun(r: RunRecord): TimelineCall[] {
  const t0 = Date.parse(r.createdAt);
  return r.toolCalls.map((c) => ({ callId: c.callId, tool: c.tool, at: Math.max(0, Date.parse(c.startedAt) - t0), ms: c.ms, status: c.status, summary: typeof c.output.summary === "string" ? c.output.summary : undefined }));
}

export function callsOfStream(calls: TraceCall[]): TimelineCall[] {
  return calls.map((c) => ({ callId: c.start.callId, tool: c.start.tool, at: c.start.t * 1000, ms: c.end?.ms, status: c.end?.status, summary: c.end?.summary }));
}

/**
 * The run's tool calls with their measured ms, in call order, over a bar of the whole run. Motion never implies
 * speed: times are text; the bar is to scale, so 12 ms tool calls in a 7 s run are slivers.
 */
export function ToolTimeline({ calls, latencyMs, tools, onSelect, selected, reveal = 1, note }: { calls: TimelineCall[]; latencyMs?: number; tools?: string[]; onSelect?: (callId: string) => void; selected?: string; reveal?: number; note?: ReactNode }) {
  const shown = calls.slice(0, Math.ceil(calls.length * reveal - 1e-9));
  const total = latencyMs ?? Math.max(1, ...calls.map((c) => c.at + (c.ms ?? 0)));
  const idle = (tools ?? []).filter((t) => !calls.some((c) => c.tool === t));
  const toolMs = calls.reduce((s, c) => s + (c.ms ?? 0), 0);
  return (
    <div className="flex flex-col gap-2">
      <ol className="flex flex-wrap items-center gap-1.5" aria-label="Tool calls">
        {shown.map((c, i) => {
          const body = (
            <>
              <span className="font-mono text-mono-sm text-text">{c.tool}</span>
              {c.ms !== undefined ? <Num value={c.ms} kind="ms" bare className="text-text-2" /> : <span className="text-text-2">running</span>}
            </>
          );
          return (
            <li key={c.callId} className="flex items-center gap-1.5">
              {i > 0 && <ArrowRight className="size-3 text-text-3" aria-hidden />}
              {onSelect ? (
                <button
                  type="button"
                  onClick={() => onSelect(c.callId)}
                  className={cn("inline-flex h-6 items-center gap-1.5 rounded-xs border px-2 text-xs hover:bg-surface-2", selected === c.callId ? "border-text-2 bg-surface-2" : "border-border", c.status && c.status !== "ok" && "border-dashed border-loss")}
                  title={c.summary}
                >
                  {body}
                </button>
              ) : (
                <span className="inline-flex h-6 items-center gap-1.5 rounded-xs border border-border px-2 text-xs" title={c.summary}>
                  {body}
                </span>
              )}
            </li>
          );
        })}
      </ol>
      <Tooltip content="To scale: the whole run, with each tool call at its start time.">
        <div className="relative h-1.5 w-full overflow-hidden rounded-full bg-surface-3" tabIndex={0} role="img" aria-label="Run timeline">
          {shown.map((c) => (
            <span key={c.callId} className="absolute inset-y-0 rounded-full bg-agent" style={{ left: `${(c.at / total) * 100}%`, width: `max(3px, ${((c.ms ?? 0) / total) * 100}%)` }} />
          ))}
        </div>
      </Tooltip>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-2">
        {latencyMs !== undefined && (
          <span>
            run <Num value={latencyMs} kind="ms" bare />
          </span>
        )}
        <span>
          tools <Num value={toolMs} kind="ms" bare />
        </span>
        {idle.length > 0 && <span>not called: {idle.join(", ")}</span>}
        {note}
      </div>
    </div>
  );
}

/** "10 of 10 logged runs correct · 0 of 10 cite a region" (N of N, never a single run). */
export function StabilityLine({ stats, className }: { stats: LaneRunStats; className?: string }) {
  if (stats.scored === 0)
    return (
      <p className={cn("text-xs text-text-3", className)}>
        <Num value={stats.n} kind="int" size="xs" bare /> logged runs · not scored
      </p>
    );
  return (
    <p className={cn("flex flex-wrap items-baseline gap-x-1 gap-y-0.5 text-xs text-text-2", className)}>
      <Num value={stats.correct} kind="int" size="xs" bare /> of <Num value={stats.scored} kind="int" size="xs" bare /> logged runs correct
      <span className="px-0.5 text-text-3">·</span>
      <Num value={stats.cited} kind="int" size="xs" bare /> of <Num value={stats.scored} kind="int" size="xs" bare /> cite a region
      {stats.scored < stats.n && (
        <span className="text-text-3">
          · <Num value={stats.n - stats.scored} kind="int" size="xs" bare /> not scored
        </span>
      )}
    </p>
  );
}

/** Retrieved units with dense, BM25 and fused scores (normalised, from the tool result). */
export function RetrievedList({ items, onOpen }: { items: RetrievedUnit[]; onOpen?: (r: RetrievedUnit) => void }) {
  if (!items.length) return <p className="text-sm text-text-2">Nothing retrieved.</p>;
  return (
    <ol className="flex flex-col divide-y divide-border">
      {items.map((r) => (
        <li key={`${r.rank}-${r.unitId}`} className="grid grid-cols-[20px_minmax(0,1fr)_132px] items-start gap-3 py-2">
          <span className="tnum pt-0.5 text-right font-mono text-mono-sm text-text-2">{r.rank}</span>
          <div className="min-w-0">
            <button type="button" className="block max-w-full truncate text-left text-sm text-text hover:underline" onClick={() => onOpen?.(r)} title={r.unitId}>
              {r.unitType === "chunk" ? `${r.fileName} · ${r.chunkLabel ?? r.label}` : r.label}
            </button>
            <div className="mt-0.5 flex flex-wrap items-center gap-1.5">
              <Badge tone="concept" dot>{r.unitType}</Badge>
              <span className="text-xs text-text-2">p{r.pages.join(", ")}</span>
              {r.piiFlag !== "none" && <Badge tone="boundary">{r.piiFlag}</Badge>}
              {!r.resolvable && <Badge tone="loss">no region</Badge>}
            </div>
          </div>
          <Scores s={r.scores} />
        </li>
      ))}
    </ol>
  );
}

function Scores({ s }: { s: RetrievedUnit["scores"] }) {
  const rows: [string, number][] = [["dense", s.denseNorm], ["BM25", s.bm25Norm], ["fused", s.fused]];
  return (
    <div className="grid grid-cols-[40px_minmax(0,1fr)_36px] items-center gap-x-1.5 gap-y-0.5 text-xs text-text-2" aria-label="Normalised scores">
      {rows.map(([k, v]) => (
        <div key={k} className="contents">
          <span>{k}</span>
          <span className="h-1 overflow-hidden rounded-full bg-surface-3">
            <span className={cn("block h-full rounded-full", k === "fused" ? "bg-text" : "bg-text-3")} style={{ width: `${Math.max(0, Math.min(1, v)) * 100}%` }} />
          </span>
          <Num value={v} kind="ratio" bare className="justify-end" />
        </div>
      ))}
    </div>
  );
}

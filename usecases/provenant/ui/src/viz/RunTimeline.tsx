/**
 * A logged run on its measured timeline (SC-5): the whole run to scale (model time, derived as the time not in a
 * tool call, and the tool-call windows), a playhead, and one row per tool call with its start time and its
 * measured ms. Tool calls of a few ms vanish on a run of seconds, so the rows scale their bars to the longest call
 * and print the times, and say so on the rows; nothing implies speed. Numbers inherit the caller's
 * ProvenanceScope, except model time, which is derived (the run's latency minus its tool windows).
 */
import { Num, cn } from "../design";

export interface TimelineSegment {
  kind: "model" | "tools";
  from: number;
  to: number;
}

export interface TimelineStop {
  callId: string;
  tool: string;
  at: number;
  ms: number;
  status: "ok" | "error" | "denied";
  /** e.g. the graph_hop template */
  detail?: string;
}

interface Props {
  segments: TimelineSegment[];
  stops: TimelineStop[];
  total: number;
  /** ms from the run's start */
  playhead: number;
  /** the stop the current step is about */
  focus?: string;
  /** 0..1 per stop: how far its row has appeared */
  shown?: (callId: string) => number;
  onSelect?: (callId: string) => void;
  selected?: string;
}

export function RunTimeline({ segments, stops, total, playhead, focus, shown = () => 1, onSelect, selected }: Props) {
  const pct = (ms: number) => `${(Math.max(0, Math.min(total, ms)) / total) * 100}%`;
  const longest = Math.max(1, ...stops.map((s) => s.ms));
  const overlaps = (s: TimelineStop) => stops.some((o) => o !== s && o.at < s.at + s.ms && s.at < o.at + o.ms);
  return (
    <div className="flex flex-col gap-2.5">
      <div className="relative h-7" role="img" aria-label={`Run timeline: ${segments.map((s) => `${s.kind === "model" ? "model (derived)" : "tool calls"} ${Math.round(s.to - s.from)} ms`).join(", ")}`}>
        {segments.map((s, i) => (
          <div
            key={i}
            className={cn("absolute inset-y-0 flex items-center justify-center overflow-hidden rounded-xs px-3 text-xs", s.kind === "model" ? "border border-border bg-surface-2 text-text-2" : "z-[1] bg-agent")}
            style={{ left: pct(s.from), width: s.kind === "tools" ? `max(3px, ${pct(s.to - s.from)})` : pct(s.to - s.from) }}
          >
            {s.kind === "model" && (s.to - s.from) / total > 0.18 && (
              <span className="truncate">
                model · <Num value={s.to - s.from} kind="ms" provenance="derived" />
              </span>
            )}
          </div>
        ))}
        <div className="absolute -bottom-1 -top-1 z-[2] w-px bg-text" style={{ left: pct(playhead) }} aria-hidden>
          <span className="absolute -left-[3px] -top-0.5 size-[7px] rounded-full bg-text" />
        </div>
      </div>
      <div className="flex justify-between font-mono text-mono-sm text-text-3" aria-hidden>
        <span>0 s</span>
        <span>
          <Num value={total} kind="ms" bare />
        </span>
      </div>
      <p className="-mb-1 flex justify-end text-xs text-text-3">
        <span>
          call bars relative to the longest call (<Num value={longest} kind="ms" bare />), not to the run
        </span>
      </p>
      <ol className="flex flex-col gap-1" aria-label="Tool calls, measured">
        {stops.map((s) => {
          const appear = shown(s.callId);
          const body = (
            <>
              <span className={cn("w-28 shrink-0 truncate font-mono text-mono-sm", appear > 0 ? "text-text" : "text-text-2")}>{s.tool}</span>
              <span className="tnum w-24 shrink-0 text-xs text-text-2">
                at <Num value={(s.at / 1000).toFixed(3)} kind="raw" bare /> s
              </span>
              <span className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-3">
                <span className={cn("absolute inset-y-0 left-0 rounded-full", s.status === "ok" ? "bg-agent" : "bg-loss")} style={{ width: `${(s.ms / longest) * 100 * appear}%` }} />
              </span>
              <span className="w-14 shrink-0 text-right text-xs text-text">
                <Num value={s.ms} kind="ms" bare />
              </span>
              <span className="hidden w-40 shrink-0 truncate font-mono text-mono-sm text-text-2 xl:inline">{s.detail ?? (overlaps(s) ? "in parallel" : "")}</span>
            </>
          );
          const cls = cn(
            "flex h-7 w-full items-center gap-3 rounded-xs border px-2 text-left transition-colors duration-fast",
            focus === s.callId || selected === s.callId ? "border-border-strong bg-surface-2" : "border-transparent",
          );
          return (
            <li key={s.callId}>
              {onSelect ? (
                <button type="button" className={cn(cls, "hover:bg-surface-2")} onClick={() => onSelect(s.callId)} aria-pressed={selected === s.callId}>
                  {body}
                </button>
              ) : (
                <div className={cls}>{body}</div>
              )}
            </li>
          );
        })}
      </ol>
    </div>
  );
}

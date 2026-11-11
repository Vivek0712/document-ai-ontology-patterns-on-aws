/**
 * Scrubber: one segment per step (width ∝ duration, 4 px apart), a playhead, click/drag to seek, a tooltip naming
 * each step. Keyboard: it is a focusable slider; ← → move 250 ms, Page Up/Down a step, Home/End the ends.
 */
import { type KeyboardEvent, type PointerEvent, useRef } from "react";
import { Tooltip, cn } from "../../design";

interface Props {
  durations: number[];
  titles: string[];
  time: number;
  total: number;
  step: number;
  onSeek(ms: number): void;
  onScrubStart?(): void;
}

export function Scrubber({ durations, titles, time, total, step, onSeek, onScrubStart }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const seekAt = (clientX: number) => {
    const r = ref.current?.getBoundingClientRect();
    if (!r) return;
    onSeek(((clientX - r.left) / r.width) * total);
  };
  const down = (e: PointerEvent) => {
    dragging.current = true;
    onScrubStart?.();
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    seekAt(e.clientX);
  };
  const starts = durations.map((_, i) => durations.slice(0, i).reduce((a, b) => a + b, 0));
  const onKeyDown = (e: KeyboardEvent) => {
    const to = {
      ArrowLeft: time - 250,
      ArrowRight: time + 250,
      PageUp: starts[Math.max(0, step - 1)],
      PageDown: starts[Math.min(durations.length - 1, step + 1)],
      Home: 0,
      End: total,
    }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    onScrubStart?.();
    onSeek(Math.min(total, Math.max(0, to)));
  };
  let acc = 0;
  return (
    <div
      ref={ref}
      role="slider"
      tabIndex={0}
      onKeyDown={onKeyDown}
      aria-label="Timeline"
      aria-valuemin={0}
      aria-valuemax={Math.round(total)}
      aria-valuenow={Math.round(time)}
      aria-valuetext={`step ${step + 1} of ${durations.length}: ${titles[step]}`}
      className="group relative mr-2 flex h-6 min-w-0 flex-1 cursor-pointer items-center rounded-xs focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-focus"
      onPointerDown={down}
      onPointerMove={(e) => dragging.current && seekAt(e.clientX)}
      onPointerUp={() => (dragging.current = false)}
    >
      <div className="flex h-1 w-full gap-1">
        {durations.map((d, i) => {
          const start = acc;
          acc += d;
          const fill = Math.max(0, Math.min(1, (time - start) / d));
          return (
            <Tooltip key={i} content={`${i + 1}/${durations.length} · ${titles[i]}`}>
              <div className={cn("relative h-full overflow-hidden rounded-full bg-surface-3 transition-[height] duration-fast group-hover:h-1.5")} style={{ flex: d }}>
                <div className={cn("absolute inset-y-0 left-0", i === step ? "bg-text" : "bg-text-2")} style={{ width: `${fill * 100}%` }} />
              </div>
            </Tooltip>
          );
        })}
      </div>
      <div
        aria-hidden
        className="pointer-events-none absolute top-1/2 size-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-bg bg-text shadow-sm"
        style={{ left: `${(time / total) * 100}%` }}
      />
    </div>
  );
}

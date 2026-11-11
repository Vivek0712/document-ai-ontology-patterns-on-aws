/**
 * The showcase Player: header (claim, live/replay banner), the stage (the storyboard's pure
 * render), the step caption, and the transport: previous / play-pause / next, a scrubber with step ticks,
 * speed, copy link and the receipts drawer. Keyboard: Space, ← →, , . (±250 ms), R. Keys go to the focused control
 * first: on the speed toggle or the timeline, arrows move that control, and Space on any button presses it.
 * `?step=n` opens paused at the end state of step n; the URL follows the current step.
 */
import { Link2, Pause, Play, ReceiptText, SkipBack, SkipForward } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useDocumentTitle } from "../../app/Page";
import { usePrefs } from "../../app/prefs";
import { isTyping } from "../../app/shortcuts";
import { Badge, Chip, IconButton, Kbd, SegmentedControl, Tooltip, cn, toast } from "../../design";
import { Receipts } from "./Receipts";
import { ReplayBanner } from "./ReplayBanner";
import { Scrubber } from "./Scrubber";
import { captionOf, type Loaded, type Storyboard } from "./types";
import { useStoryClock } from "./useStoryClock";

/** Controls that own their arrow keys (Radix roving focus, the timeline slider, tabs). */
const OWNS_ARROWS = '[role="radio"], [role="slider"], [role="tab"], [role="menuitem"], [role="option"]';

/** Should the Player's global key map leave this key to the focused element? */
export function keyBelongsToTarget(e: Pick<KeyboardEvent, "key" | "target">): boolean {
  const t = e.target as Element | null;
  if (!t?.closest) return false;
  if (e.key.startsWith("Arrow") || e.key === "Home" || e.key === "End") return Boolean(t.closest(OWNS_ARROWS));
  if (e.key === " " || e.key === "Enter") return Boolean(t.closest(`button, a, ${OWNS_ARROWS}`));
  return false;
}

export function Player<P>({ story, loaded }: { story: Storyboard<P>; loaded: Loaded<P> }) {
  const [params, setParams] = useSearchParams();
  const { reducedMotion } = usePrefs();
  const durations = useMemo(() => story.steps.map((s) => s.durationMs), [story]);
  const stepParam = Number(params.get("step"));
  const deepLinked = Number.isInteger(stepParam) && stepParam >= 1;
  const c = useStoryClock(durations, {
    initialStep: deepLinked ? stepParam - 1 : 0,
    autoplay: !deepLinked && !reducedMotion && params.get("autoplay") !== "0",
  });
  const [receipts, setReceipts] = useState(params.get("receipts") === "1");
  const step = story.steps[c.step];
  const caption = captionOf(step, loaded.payload);
  useDocumentTitle(`${story.code} ${story.title}`);

  // the URL follows the step (replace: no history spam)
  const lastStep = useRef(c.step);
  useEffect(() => {
    if (lastStep.current === c.step && params.get("step")) return;
    lastStep.current = c.step;
    const next = new URLSearchParams(params);
    next.set("step", String(c.step + 1));
    setParams(next, { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [c.step]);

  const copyLink = () => {
    const url = `${window.location.origin}/showcase/${story.id}?step=${c.step + 1}`;
    void navigator.clipboard?.writeText(url).then(() => toast.success("Link to this step copied"));
  };

  const keys = useRef({ c, setReceipts });
  keys.current = { c, setReceipts };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTyping(e) || e.metaKey || e.ctrlKey || e.altKey || e.defaultPrevented || keyBelongsToTarget(e)) return;
      const { c: k } = keys.current;
      const map: Record<string, () => void> = {
        " ": k.toggle,
        ArrowRight: k.next,
        ArrowLeft: k.prev,
        ",": () => k.nudge(-250),
        ".": () => k.nudge(250),
        r: () => keys.current.setReceipts((o) => !o),
      };
      const fn = map[e.key];
      if (fn) {
        e.preventDefault();
        fn();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const frame = { payload: loaded.payload, step: c.step, progress: reducedMotion ? 1 : c.progress, reduced: reducedMotion };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* header */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border px-4 py-2.5 sm:px-5">
        <Badge tone="strong" className="font-mono">{story.code}</Badge>
        <h1 className="text-md">{story.title}</h1>
        <p className="hidden min-w-0 flex-1 truncate text-sm text-text-2 lg:block" title={story.claim}>
          {story.claim}
        </p>
        <div className="ml-auto flex items-center gap-2">
          <ReplayBanner source={loaded.source} />
        </div>
      </div>

      {/* stage */}
      <div className="relative min-h-0 flex-1">{story.render(frame)}</div>

      {/* caption */}
      <div className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-border px-4 py-2.5 sm:px-5">
        <div className="flex min-w-0 flex-1 items-baseline gap-2.5">
          <span className="tnum shrink-0 font-mono text-mono-sm text-text-2" data-testid="step-counter">
            {c.step + 1}/{story.steps.length}
          </span>
          <span className="shrink-0 text-sm font-medium text-text">{step.title}</span>
          <p className="min-w-0 text-md text-text" aria-live="polite" data-testid="caption">
            {caption}
          </p>
        </div>
        {step.services && (
          <ServiceChips services={step.services} live={loaded.source.mode === "live"} />
        )}
      </div>

      {/* transport */}
      <div className="flex shrink-0 items-center gap-2 border-t border-border bg-bg-subtle px-3 py-2 sm:px-4">
        <IconButton label="Previous step" shortcut="←" icon={<SkipBack />} onClick={c.prev} disabled={c.step === 0} />
        <IconButton variant="secondary" label={c.playing ? "Pause" : "Play"} shortcut="Space" icon={c.playing ? <Pause /> : <Play />} onClick={c.toggle} />
        <IconButton label="Next step" shortcut="→" icon={<SkipForward />} onClick={c.next} disabled={c.step === story.steps.length - 1} />
        <Scrubber durations={durations} titles={story.steps.map((s) => s.title)} time={c.time} total={c.total} step={c.step} onSeek={c.seek} onScrubStart={c.pause} />
        <Tooltip content="Playback is paced for reading; it does not show processing speed. Measured times appear as text.">
          <span className="tnum ml-1 hidden shrink-0 font-mono text-mono-sm text-text-2 sm:inline" tabIndex={0}>
            step {c.step + 1}/{story.steps.length} · {(c.time / 1000).toFixed(1)}s
          </span>
        </Tooltip>
        <SegmentedControl
          aria-label="Playback speed"
          size="sm"
          className="hidden md:inline-flex"
          value={String(c.speed)}
          onValueChange={(v) => c.setSpeed(Number(v))}
          items={[
            { value: "0.5", label: "0.5×" },
            { value: "1", label: "1×" },
            { value: "2", label: "2×" },
          ]}
        />
        {reducedMotion && (
          <Tooltip content="Reduced motion: each step cuts to its end state.">
            <Badge tone="neutral" className="hidden lg:inline-flex">reduced motion</Badge>
          </Tooltip>
        )}
        <IconButton label="Copy link to this step" icon={<Link2 />} onClick={copyLink} />
        <IconButton label="Receipts" shortcut="R" icon={<ReceiptText />} active={receipts} onClick={() => setReceipts(!receipts)} className={cn(receipts && "text-text")} />
      </div>

      <Receipts open={receipts} onOpenChange={setReceipts} story={story} payload={loaded.payload} step={c.step} />
      <span className="sr-only">
        Keyboard: <Kbd>Space</Kbd> play or pause, arrows step, comma and period scrub, R receipts.
      </span>
    </div>
  );
}

/**
 * The AWS services a step stands for. In a replay of a local run they are what *will* run on AWS, not what
 * produced these numbers, so they are dimmed, dashed and say so.
 */
function ServiceChips({ services, live }: { services: string[]; live: boolean }) {
  const chips = (
    <div className="flex flex-wrap items-center gap-1">
      {!live && <span className="text-xs text-text-2">As deployed</span>}
      {services.map((s) => (
        <Chip key={s} className={cn(!live && "border-dashed bg-transparent text-text-2")}>
          {s}
        </Chip>
      ))}
    </div>
  );
  return live ? chips : <Tooltip content="The services this step runs on when deployed. This replay ran on the local pipeline, not AWS.">{chips}</Tooltip>;
}

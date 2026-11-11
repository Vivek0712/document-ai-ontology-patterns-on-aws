/**
 * The story clock: (step, local ms) advanced by requestAnimationFrame at `speed`. Global time is derived, so the
 * scrubber can seek anywhere. `local === duration` is the step's end state; deep links open there, paused.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

export interface StoryClock {
  step: number;
  local: number;
  progress: number;
  playing: boolean;
  speed: number;
  total: number;
  time: number;
  starts: number[];
  play(): void;
  pause(): void;
  toggle(): void;
  next(): void;
  prev(): void;
  goTo(step: number, at?: "start" | "end"): void;
  seek(globalMs: number): void;
  nudge(deltaMs: number): void;
  setSpeed(s: number): void;
}

interface ClockState {
  step: number;
  local: number;
}

/** Advance (step, local) by `dt` ms; `stop` once the last step reaches its end. Pure: safe in StrictMode. */
export function advance(s: ClockState, dt: number, durations: number[]): ClockState & { stop: boolean } {
  const n = durations.length;
  let { step, local } = s;
  local += dt;
  while (local >= durations[step] && step < n - 1) {
    local -= durations[step];
    step += 1;
  }
  const stop = step === n - 1 && local >= durations[step];
  return { step, local: stop ? durations[step] : local, stop };
}

export function useStoryClock(durations: number[], opts: { initialStep?: number; autoplay?: boolean }): StoryClock {
  const n = durations.length;
  const starts = useMemo(() => durations.map((_, i) => durations.slice(0, i).reduce((a, b) => a + b, 0)), [durations]);
  const total = durations.reduce((a, b) => a + b, 0);
  const init = Math.min(n - 1, Math.max(0, opts.initialStep ?? 0));
  const [state, setStateRaw] = useState<ClockState>(() => ({ step: init, local: opts.autoplay ? 0 : durations[init] ?? 0 }));
  // the clock lives in a ref too, so the rAF loop computes the next state outside any setState updater
  const stateRef = useRef(state);
  const setState = useCallback((next: ClockState) => {
    stateRef.current = next;
    setStateRaw(next);
  }, []);
  const [playing, setPlaying] = useState(Boolean(opts.autoplay));
  const [speed, setSpeed] = useState(1);
  const speedRef = useRef(speed);
  speedRef.current = speed;

  useEffect(() => {
    if (!playing) return;
    let last = performance.now();
    let raf = 0;
    const tick = (now: number) => {
      const dt = Math.min(64, now - last) * speedRef.current;
      last = now;
      const { stop, ...next } = advance(stateRef.current, dt, durations);
      setState(next);
      if (stop) setPlaying(false);
      else raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, durations, setState]);

  const goTo = useCallback(
    (step: number, at: "start" | "end" = "end") => {
      const s = Math.min(n - 1, Math.max(0, step));
      setState({ step: s, local: at === "start" ? 0 : durations[s] });
    },
    [durations, n, setState],
  );

  const seek = useCallback(
    (ms: number) => {
      const t = Math.min(total, Math.max(0, ms));
      let i = n - 1;
      while (i > 0 && starts[i] > t) i--;
      setState({ step: i, local: Math.min(durations[i], t - starts[i]) });
    },
    [durations, n, starts, total, setState],
  );

  const time = starts[state.step] + state.local;
  const stepDone = state.local >= durations[state.step];
  const atEnd = state.step === n - 1 && stepDone;

  /** Play from here; a finished step continues with the next one, the finished story restarts. */
  const play = () => {
    if (atEnd) setState({ step: 0, local: 0 });
    else if (stepDone) setState({ step: state.step + 1, local: 0 });
    setPlaying(true);
  };

  return {
    step: state.step,
    local: state.local,
    progress: durations[state.step] ? Math.min(1, state.local / durations[state.step]) : 1,
    playing,
    speed,
    total,
    time,
    starts,
    play,
    pause: () => setPlaying(false),
    toggle: () => (playing ? setPlaying(false) : play()),
    next: () => goTo(state.step + 1, playing ? "start" : "end"),
    prev: () => goTo(state.step - 1, playing ? "start" : "end"),
    goTo,
    seek,
    nudge: (d: number) => {
      setPlaying(false);
      seek(time + d);
    },
    setSpeed,
  };
}

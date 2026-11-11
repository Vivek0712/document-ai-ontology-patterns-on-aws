import { type DependencyList, useCallback, useEffect, useRef, useState } from "react";
import { RpApiError } from "../api/types";

export interface AsyncState<T> {
  data?: T;
  error?: RpApiError | Error;
  loading: boolean;
  reload: () => void;
}

/**
 * Run `fn` when deps change; with `pollMs`, re-run on an interval (keeps the last data while refreshing,
 * no overlapping requests). Polling pauses while the tab is hidden.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: DependencyList, pollMs?: number | false): AsyncState<T> {
  const [data, setData] = useState<T>();
  const [error, setError] = useState<RpApiError | Error>();
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(() => setTick((t) => t + 1), []);

  useEffect(() => {
    setData(undefined);
    setError(undefined);
    setLoading(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  useEffect(() => {
    let alive = true;
    let busy = false;
    const run = async () => {
      if (busy) return;
      busy = true;
      try {
        const d = await fnRef.current();
        if (alive) {
          setData(d);
          setError(undefined);
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e : new Error(String(e)));
      } finally {
        busy = false;
        if (alive) setLoading(false);
      }
    };
    void run();
    let timer: number | undefined;
    if (pollMs) {
      timer = window.setInterval(() => {
        if (document.visibilityState === "visible") void run();
      }, pollMs);
    }
    return () => {
      alive = false;
      if (timer) window.clearInterval(timer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick, pollMs]);

  return { data, error, loading, reload };
}

/** Re-render once per second (running timers tick once per second). */
export function useNow(active = true, everyMs = 1000): number {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), everyMs);
    return () => window.clearInterval(t);
  }, [active, everyMs]);
  return now;
}

import { useCallback } from "react";
import { useSearchParams } from "react-router-dom";

/**
 * One URL query parameter as state (: the URL is the state). Setting a value replaces the history entry;
 * `undefined` or the fallback removes the key, so default views keep clean URLs.
 */
export function useSearchParam(key: string, fallback = ""): [string, (value?: string) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key) ?? fallback;
  const set = useCallback(
    (v?: string) =>
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (v === undefined || v === "" || v === fallback) next.delete(key);
          else next.set(key, v);
          return next;
        },
        { replace: true },
      ),
    [key, fallback, setParams],
  );
  return [value, set];
}

/**
 * Viewer preferences: theme (dark | light | system), motion (system | reduced | full), sidebar collapsed.
 * Applied as `data-theme` / `data-motion` on <html>. `?theme=` and `?motion=` in the URL override once
 * (handy for links and screenshots) and are remembered.
 */
import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { readPref, writePref } from "../lib/storage";

export type ThemePref = "dark" | "light" | "system";
export type MotionPref = "system" | "reduced" | "full";

interface Prefs {
  theme: ThemePref;
  resolvedTheme: "dark" | "light";
  setTheme(t: ThemePref): void;
  toggleTheme(): void;
  motion: MotionPref;
  /** true when showcases must cut steps instead of animating them */
  reducedMotion: boolean;
  setMotion(m: MotionPref): void;
  sidebarCollapsed: boolean;
  setSidebarCollapsed(v: boolean): void;
}

const Ctx = createContext<Prefs | null>(null);

function media(query: string): boolean {
  return typeof window !== "undefined" && window.matchMedia?.(query).matches === true;
}

function useMedia(query: string): boolean {
  const [hit, setHit] = useState(() => media(query));
  useEffect(() => {
    const m = window.matchMedia?.(query);
    if (!m) return;
    const on = () => setHit(m.matches);
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return hit;
}

function initial<T extends string>(key: string, allowed: readonly T[], fallback: T): T {
  const fromUrl = new URLSearchParams(window.location.search).get(key);
  if (fromUrl && (allowed as readonly string[]).includes(fromUrl)) {
    writePref(key, fromUrl);
    return fromUrl as T;
  }
  const saved = readPref(key);
  return saved && (allowed as readonly string[]).includes(saved) ? (saved as T) : fallback;
}

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemePref>(() => initial("theme", ["dark", "light", "system"] as const, "dark"));
  const [motion, setMotionState] = useState<MotionPref>(() => initial("motion", ["system", "reduced", "full"] as const, "system"));
  const [sidebarCollapsed, setCollapsed] = useState(() => readPref("sidebar") === "collapsed");
  const prefersLight = useMedia("(prefers-color-scheme: light)");
  const prefersReduced = useMedia("(prefers-reduced-motion: reduce)");

  const resolvedTheme = theme === "system" ? (prefersLight ? "light" : "dark") : theme;
  const reducedMotion = motion === "reduced" || (motion === "system" && prefersReduced);

  useEffect(() => {
    document.documentElement.dataset.theme = resolvedTheme;
  }, [resolvedTheme]);
  useEffect(() => {
    if (reducedMotion) document.documentElement.dataset.motion = "reduced";
    else delete document.documentElement.dataset.motion;
  }, [reducedMotion]);

  const setTheme = useCallback((t: ThemePref) => {
    setThemeState(t);
    writePref("theme", t);
  }, []);
  const toggleTheme = useCallback(() => setTheme(resolvedTheme === "dark" ? "light" : "dark"), [resolvedTheme, setTheme]);
  const setMotion = useCallback((m: MotionPref) => {
    setMotionState(m);
    writePref("motion", m);
  }, []);
  const setSidebarCollapsed = useCallback((v: boolean) => {
    setCollapsed(v);
    writePref("sidebar", v ? "collapsed" : undefined);
  }, []);

  const value = useMemo(
    () => ({ theme, resolvedTheme, setTheme, toggleTheme, motion, reducedMotion, setMotion, sidebarCollapsed, setSidebarCollapsed }),
    [theme, resolvedTheme, setTheme, toggleTheme, motion, reducedMotion, setMotion, sidebarCollapsed, setSidebarCollapsed],
  ) satisfies Prefs;
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function usePrefs(): Prefs {
  const c = useContext(Ctx);
  if (!c) throw new Error("usePrefs outside PrefsProvider");
  return c;
}

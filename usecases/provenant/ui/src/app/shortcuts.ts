/** Global keyboard map. Ignored while typing; showcase keys live in the Player. */
import { useEffect, useRef } from "react";
import { useNavigate } from "react-router-dom";

/** `g` then a key. */
const GO: Record<string, string> = {
  h: "/",
  s: "/showcase",
  w: "/workbench/packages",
  k: "/knowledge/ontology",
  g: "/governance/pii",
  e: "/evaluate",
  c: "/cost",
  r: "/workbench/packages",
  u: "/workbench/units",
};

export function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  return Boolean(t?.closest("input, textarea, select, [contenteditable=true], [cmdk-input]"));
}

export function useGlobalShortcuts(actions: { palette(): void; theme(): void; sidebar(): void; help(): void }) {
  const nav = useNavigate();
  const pendingG = useRef<number>();
  const ref = useRef(actions);
  ref.current = actions;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        ref.current.palette();
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey || isTyping(e) || e.defaultPrevented) return;
      if (pendingG.current) {
        window.clearTimeout(pendingG.current);
        pendingG.current = undefined;
        const to = GO[e.key.toLowerCase()];
        if (to) {
          e.preventDefault();
          nav(to);
        }
        return;
      }
      if (e.key === "g") pendingG.current = window.setTimeout(() => (pendingG.current = undefined), 900);
      else if (e.key === "t") ref.current.theme();
      else if (e.key === "[") ref.current.sidebar();
      else if (e.key === "?") ref.current.help();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [nav]);
}

export const SHORTCUTS: [string[], string][] = [
  [["⌘", "K"], "Command palette"],
  [["G", "H"], "Go to Home"],
  [["G", "S"], "Go to Showcases"],
  [["G", "W"], "Go to Workbench"],
  [["G", "K"], "Go to Knowledge"],
  [["G", "G"], "Go to Governance"],
  [["G", "U"], "Go to Units explorer"],
  [["T"], "Toggle theme"],
  [["["], "Toggle sidebar"],
  [["Space"], "Play / pause (showcase)"],
  [["←", "→"], "Previous / next step"],
  [[",", "."], "Scrub ±250 ms"],
  [["R"], "Receipts drawer"],
  [["?"], "This sheet"],
];

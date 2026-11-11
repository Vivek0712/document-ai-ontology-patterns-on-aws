import type { ReactNode } from "react";
import { cn } from "../cn";

/** Keyboard key: 18 px, mono 11 px. Use one Kbd per key: <Kbd>⌘</Kbd><Kbd>K</Kbd>. */
export function Kbd({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <kbd
      className={cn(
        "inline-flex h-5 min-w-5 items-center justify-center rounded-xs border border-border-strong",
        "bg-surface-2 px-1 font-mono text-xs leading-none text-text-2",
        className,
      )}
    >
      {children}
    </kbd>
  );
}

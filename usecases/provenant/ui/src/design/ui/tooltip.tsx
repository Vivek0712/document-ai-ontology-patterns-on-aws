import * as T from "@radix-ui/react-tooltip";
import type { ReactNode } from "react";
import { cn } from "../cn";

export const TooltipProvider = ({ children }: { children: ReactNode }) => (
  <T.Provider delayDuration={300} skipDelayDuration={150}>
    {children}
  </T.Provider>
);

interface TooltipProps {
  content: ReactNode;
  children: ReactNode;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  className?: string;
}

/** Radix tooltip: 300 ms delay, 12/16 text, never the only way to reach information. */
export function Tooltip({ content, children, side = "top", align = "center", className }: TooltipProps) {
  if (content === undefined || content === null || content === "") return <>{children}</>;
  return (
    <T.Root>
      <T.Trigger asChild>{children}</T.Trigger>
      <T.Portal>
        <T.Content
          side={side}
          align={align}
          sideOffset={6}
          collisionPadding={8}
          className={cn(
            "z-50 max-w-xs rounded-sm border border-border-strong bg-surface-2 px-2 py-1 text-xs text-text shadow-md",
            "data-[state=delayed-open]:animate-in data-[state=delayed-open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
            className,
          )}
        >
          {content}
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}

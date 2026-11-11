import * as D from "@radix-ui/react-dialog";
import type { ReactNode } from "react";
import { cn } from "../cn";

/** Centred dialog (radius 12, shadow-lg). Used by the command palette and the shortcut sheet. */
export function Dialog({ open, onOpenChange, title, children, className }: { open: boolean; onOpenChange: (o: boolean) => void; title: string; children: ReactNode; className?: string }) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange}>
      <D.Portal>
        <D.Overlay className="fixed inset-0 z-[60] bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <D.Content
          className={cn(
            "fixed left-1/2 top-[14vh] z-[61] w-[min(640px,calc(100vw-32px))] -translate-x-1/2 overflow-hidden rounded-lg border border-border-strong bg-surface shadow-lg",
            "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[0.98]",
            className,
          )}
        >
          <D.Title className="sr-only">{title}</D.Title>
          <D.Description className="sr-only">{title}</D.Description>
          {children}
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

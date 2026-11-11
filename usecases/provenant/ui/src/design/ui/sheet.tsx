import * as D from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "../cn";
import { IconButton } from "./button";

interface SheetProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  /** non-modal sheets keep the page interactive (e.g. the receipts drawer next to a playing showcase) */
  modal?: boolean;
  width?: number;
}

/** Right drawer, 420 px, radius-lg on the leading edge; Esc closes. */
export function Sheet({ open, onOpenChange, title, description, children, modal = true, width = 420 }: SheetProps) {
  return (
    <D.Root open={open} onOpenChange={onOpenChange} modal={modal}>
      <D.Portal>
        {modal && <D.Overlay className="fixed inset-0 z-40 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0" />}
        <D.Content
          onInteractOutside={(e) => !modal && e.preventDefault()}
          // a non-modal drawer opens beside the content: keep focus where it was (no tooltip pops on the close button)
          onOpenAutoFocus={(e) => !modal && e.preventDefault()}
          className={cn(
            "fixed bottom-2 right-2 top-2 z-50 flex max-w-[calc(100vw-16px)] flex-col rounded-lg border border-border-strong bg-surface shadow-lg",
            "data-[state=open]:animate-in data-[state=open]:slide-in-from-right-4 data-[state=open]:fade-in-0 data-[state=closed]:animate-out data-[state=closed]:fade-out-0",
          )}
          style={{ width }}
        >
          <div className="flex items-start gap-2 border-b border-border px-4 py-3">
            <div className="min-w-0 flex-1">
              <D.Title className="text-md font-medium text-text">{title}</D.Title>
              {description ? <D.Description className="text-xs text-text-2">{description}</D.Description> : <D.Description className="sr-only">drawer</D.Description>}
            </div>
            <D.Close asChild>
              <IconButton label="Close" shortcut="Esc" size="sm" icon={<X />} />
            </D.Close>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
        </D.Content>
      </D.Portal>
    </D.Root>
  );
}

import * as M from "@radix-ui/react-dropdown-menu";
import { Check } from "lucide-react";
import type { ComponentPropsWithoutRef, ReactNode } from "react";
import { cn } from "../cn";

export const DropdownMenu = M.Root;
export const DropdownMenuTrigger = M.Trigger;

export function DropdownMenuContent({ className, align = "start", ...props }: ComponentPropsWithoutRef<typeof M.Content>) {
  return (
    <M.Portal>
      <M.Content
        align={align}
        sideOffset={6}
        collisionPadding={8}
        className={cn(
          "z-50 min-w-[220px] rounded-md border border-border-strong bg-surface p-1 text-sm text-text shadow-lg",
          "data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-[0.98]",
          className,
        )}
        {...props}
      />
    </M.Portal>
  );
}

export function DropdownMenuItem({ className, icon, shortcut, children, ...props }: ComponentPropsWithoutRef<typeof M.Item> & { icon?: ReactNode; shortcut?: ReactNode }) {
  return (
    <M.Item
      className={cn(
        "flex h-8 cursor-default select-none items-center gap-2 rounded-sm px-2 outline-none [&_svg]:size-4 [&_svg]:text-text-2",
        "data-[highlighted]:bg-surface-2 data-[disabled]:opacity-45",
        className,
      )}
      {...props}
    >
      {icon}
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {shortcut && <span className="text-xs text-text-2">{shortcut}</span>}
    </M.Item>
  );
}

export function DropdownMenuCheckItem({ checked, children, onSelect, detail }: { checked: boolean; children: ReactNode; onSelect: () => void; detail?: ReactNode }) {
  return (
    <M.Item
      onSelect={onSelect}
      className="flex min-h-8 cursor-default select-none items-center gap-2 rounded-sm px-2 py-1 outline-none data-[highlighted]:bg-surface-2"
    >
      <Check className={cn("size-3.5 shrink-0", checked ? "text-text" : "invisible")} aria-hidden />
      <span className="min-w-0 flex-1">
        <span className="block truncate">{children}</span>
        {detail && <span className="block truncate font-mono text-mono-sm text-text-2">{detail}</span>}
      </span>
    </M.Item>
  );
}

export function DropdownMenuLabel({ children }: { children: ReactNode }) {
  return <M.Label className="px-2 pb-1 pt-2 text-caps uppercase text-text-2">{children}</M.Label>;
}

export function DropdownMenuSeparator() {
  return <M.Separator className="-mx-1 my-1 h-px bg-border" />;
}

import type { HTMLAttributes, KeyboardEvent, TdHTMLAttributes, ThHTMLAttributes } from "react";
import { cn } from "../cn";

/** Compact table: caps header on --bg-subtle, 32 px rows, hover --surface-2, numeric cells right-aligned. */
export function Table({ className, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-sm", className)} {...props} />
    </div>
  );
}
export const THead = (p: HTMLAttributes<HTMLTableSectionElement>) => <thead {...p} className={cn("bg-bg-subtle", p.className)} />;
export const TBody = (p: HTMLAttributes<HTMLTableSectionElement>) => <tbody {...p} />;
/**
 * A table row. `interactive` rows are clickable and keyboard-reachable (Tab, then Enter or Space runs `onClick`);
 * put a real link or button in the first cell as well when the row opens something.
 */
export function TR({ className, interactive, onKeyDown, ...props }: HTMLAttributes<HTMLTableRowElement> & { interactive?: boolean }) {
  const keys = interactive && props.onClick
    ? {
        tabIndex: 0,
        onKeyDown: (e: KeyboardEvent<HTMLTableRowElement>) => {
          onKeyDown?.(e);
          if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) {
            e.preventDefault();
            e.currentTarget.click();
          }
        },
      }
    : { onKeyDown };
  return (
    <tr
      className={cn("border-b border-border last:border-0", interactive && "cursor-pointer hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-text-2", className)}
      {...keys}
      {...props}
    />
  );
}
export function TH({ className, numeric, ...props }: ThHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return <th className={cn("h-8 whitespace-nowrap border-b border-border px-3 text-left text-caps uppercase text-text-2", numeric && "text-right", className)} {...props} />;
}
export function TD({ className, numeric, ...props }: TdHTMLAttributes<HTMLTableCellElement> & { numeric?: boolean }) {
  return <td className={cn("h-8 px-3 py-1.5 align-middle", numeric && "tnum text-right", className)} {...props} />;
}

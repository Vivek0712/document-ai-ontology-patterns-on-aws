import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "../cn";

/** Surface + 1 px border, radius 8. Header: title (md) + meta (xs) + actions. */
export function Card({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("min-w-0 rounded-md border border-border bg-surface", className)} {...props} />;
}

export function CardHeader({ title, meta, actions, className }: { title: ReactNode; meta?: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex min-h-11 items-center gap-3 border-b border-border px-4 py-2", className)}>
      <div className="min-w-0 flex-1">
        <div className="truncate text-md font-medium text-text">{title}</div>
        {meta && <div className="truncate text-xs text-text-2">{meta}</div>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-1">{actions}</div>}
    </div>
  );
}

export function CardBody({ className, ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

/** Small label/value grid used inside cards and panels. */
export function DataList({ rows, className }: { rows: [ReactNode, ReactNode][]; className?: string }) {
  return (
    <dl className={cn("grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-1.5 text-sm", className)}>
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="text-text-2">{k}</dt>
          <dd className="min-w-0 break-words text-text">{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

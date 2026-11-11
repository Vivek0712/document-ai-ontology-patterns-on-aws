import type { ReactNode } from "react";
import { cn } from "../cn";

/** Icon + one sentence + at most one primary action. */
export function EmptyState({ icon, title, children, action, className }: { icon?: ReactNode; title: ReactNode; children?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-2 rounded-md border border-dashed border-border-strong px-6 py-10 text-center", className)}>
      {icon && <div className="mb-1 text-text-3 [&_svg]:size-5">{icon}</div>}
      <div className="text-md font-medium text-text">{title}</div>
      {children && <div className="max-w-md text-sm text-text-2">{children}</div>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** Error state: what failed, code + request id, retry. Never a stack trace. */
export function ErrorState({ title, code, requestId, message, action }: { title: string; code?: string; requestId?: string; message?: string; action?: ReactNode }) {
  return (
    <div role="alert" className="flex flex-col gap-1.5 rounded-md border border-dashed border-loss bg-loss-tint px-4 py-3">
      <div className="flex items-center gap-2 text-sm font-medium text-text">
        <span className="text-loss" aria-hidden>×</span>
        {title}
      </div>
      {message && <div className="text-sm text-text-2">{message}</div>}
      {(code || requestId) && (
        <div className="font-mono text-mono-sm text-text-2">
          {code}
          {requestId && ` · request ${requestId}`}
        </div>
      )}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );
}

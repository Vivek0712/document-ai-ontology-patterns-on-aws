/** Live vs replay is visible on every showcase frame. */
import { History, Radio } from "lucide-react";
import { Tooltip, cn } from "../../design";
import type { DataSource } from "./types";

export function ReplayBanner({ source, className }: { source: DataSource; className?: string }) {
  const live = source.mode === "live";
  return (
    <Tooltip content={source.detail}>
      <div
        data-testid="replay-banner"
        className={cn(
          "inline-flex h-6 max-w-full items-center gap-1.5 rounded-xs border px-2 text-xs",
          live ? "border-agent/50 text-text" : "border-dashed border-border-strong text-text-2",
          className,
        )}
      >
        {live ? <Radio className="size-3.5 text-agent" aria-hidden /> : <History className="size-3.5" aria-hidden />}
        <span className="truncate">
          {source.label}
          {source.recordedAt && <span className="text-text-2"> · {source.recordedAt}</span>}
        </span>
      </div>
    </Tooltip>
  );
}

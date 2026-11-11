import { ArrowUpRight } from "lucide-react";
import { Link } from "react-router-dom";
import { Badge, cn } from "../../design";
import type { ShowcaseMeta } from "../../showcase/catalog";

export function ShowcaseCard({ s, compact }: { s: ShowcaseMeta; compact?: boolean }) {
  const live = s.status === "live";
  return (
    <Link
      to={`/showcase/${s.id}`}
      className={cn(
        "group flex min-w-0 flex-col gap-2 rounded-md border bg-surface p-3.5 transition-colors duration-fast",
        live ? "border-border-strong hover:bg-surface-2" : "border-border hover:border-border-strong",
      )}
    >
      <div className="flex items-center gap-2">
        <span className="font-mono text-mono-sm text-text-2">{s.code}</span>
        <span className={cn("min-w-0 flex-1 truncate text-sm font-medium", live ? "text-text" : "text-text-2")}>{s.title}</span>
        {live ? <Badge tone="live" dot>live</Badge> : <Badge tone="neutral">coming soon</Badge>}
      </div>
      {!compact && <p className="line-clamp-2 text-xs text-text-2">{s.claim}</p>}
      {live && (
        <div className="mt-auto flex items-center gap-1.5 text-xs text-text-2">
          <span>Play the story</span>
          <ArrowUpRight className="size-3.5 transition-transform duration-fast group-hover:-translate-y-0.5 group-hover:translate-x-0.5" aria-hidden />
        </div>
      )}
    </Link>
  );
}

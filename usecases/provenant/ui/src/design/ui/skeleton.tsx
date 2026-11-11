import { cn } from "../cn";

/** Loading block in --surface-2 with a 1.2 s shimmer (static under reduced motion). */
export function Skeleton({ className }: { className?: string }) {
  return (
    <div
      aria-hidden
      className={cn(
        "animate-shimmer rounded-sm bg-surface-2 bg-[length:200%_100%]",
        "bg-[linear-gradient(90deg,transparent_0%,var(--surface-3)_50%,transparent_100%)]",
        className,
      )}
    />
  );
}

import { cva, type VariantProps } from "class-variance-authority";
import { forwardRef, type HTMLAttributes, type ReactNode } from "react";
import { cn } from "../cn";

/**
 * Badge / tag: height 20, radius 4, 12/16, optional dot. Tones map to colour roles; text stays neutral so it
 * reads at 12 px in both themes (role colour lives in the dot and the border).
 */
const badge = cva("inline-flex h-5 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-xs border px-1.5 text-xs font-medium", {
  variants: {
    tone: {
      neutral: "border-border-strong text-text-2",
      strong: "border-border-strong bg-surface-2 text-text",
      layout: "border-layout/40 text-text",
      concept: "border-concept/40 text-text",
      agent: "border-agent/40 text-text",
      running: "border-running/50 text-text",
      cited: "border-cited/60 text-text",
      loss: "border-dashed border-loss/80 text-text",
      boundary: "border-boundary text-text",
      /** status "live": neutral (green is spent on ✓ outcomes only) */
      live: "border-border-strong text-text",
    },
  },
  defaultVariants: { tone: "neutral" },
});

const DOT: Record<string, string> = {
  neutral: "bg-text-3",
  strong: "bg-text-2",
  layout: "bg-layout",
  concept: "bg-concept",
  agent: "bg-agent",
  running: "bg-running",
  cited: "bg-cited",
  loss: "bg-loss",
  boundary: "bg-boundary",
  live: "bg-text",
};

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement>, VariantProps<typeof badge> {
  dot?: boolean;
  icon?: ReactNode;
}

/** forwardRef so a Badge can be a Tooltip/Popover trigger. */
export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(({ tone, dot, icon, className, children, ...props }, ref) => (
  <span ref={ref} className={cn(badge({ tone }), className)} {...props}>
    {dot && <span className={cn("size-1.5 rounded-full", DOT[tone ?? "neutral"])} aria-hidden />}
    {icon}
    {children}
  </span>
));
Badge.displayName = "Badge";

export const LANE_NAME: Record<string, string> = { "1.0": "Naive", "2.3.1": "Evidence units", "2.4.0": "Evidence units + invariants" };

/** Lane tag: name and version, never the version alone; `1.0` outline, others filled. */
export function LaneBadge({ pv, className }: { pv: string; className?: string }) {
  return (
    <Badge tone={pv === "1.0" ? "neutral" : "strong"} className={className}>
      {LANE_NAME[pv] ?? "Lane"} · <span className="font-mono">{pv}</span>
    </Badge>
  );
}

interface DecisionLike {
  decision: "allow" | "deny" | "unknown";
  source: "access-log" | "run-record";
  reason?: string;
}

/**
 * An access decision as text. `rp-access-log` rows are the gateway's decision; rows rebuilt from a run record carry
 * the tool's decision (the call's logged result), so they say "tool …", and a call with no readable decision says
 * "not logged", never "allow".
 */
export function decisionLabel(d: DecisionLike): string {
  if (d.source === "access-log") return d.decision;
  return d.decision === "unknown" ? "not logged" : `tool ${d.decision === "allow" ? "allowed" : "denied"}`;
}

/** The decision chip: deny in the failure tone, "not logged" neutral and dashed, allow neutral. */
export function DecisionBadge({ d, className }: { d: DecisionLike; className?: string }) {
  const tone = d.decision === "deny" ? "loss" : "neutral";
  return (
    <Badge tone={tone} className={cn(d.decision === "unknown" && "border-dashed text-text-2", className)} title={d.reason ?? (d.source === "run-record" ? "From the run record: the tool's logged result. The gateway's own decision is only in rp-access-log." : undefined)}>
      {decisionLabel(d)}
    </Badge>
  );
}

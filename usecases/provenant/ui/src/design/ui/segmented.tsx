import * as TG from "@radix-ui/react-toggle-group";
import { m } from "framer-motion";
import { type ReactNode, useId } from "react";
import { cn } from "../cn";
import { Tooltip } from "./tooltip";

export interface SegmentItem<V extends string> {
  value: V;
  label: ReactNode;
  icon?: ReactNode;
  title?: string;
  disabled?: boolean;
}

interface Props<V extends string> {
  value: V;
  onValueChange: (v: V) => void;
  items: SegmentItem<V>[];
  size?: "sm" | "md";
  "aria-label": string;
  className?: string;
}

/** Radix ToggleGroup on a --surface-2 track; the selected thumb slides (200 ms layout transition). */
export function SegmentedControl<V extends string>({ value, onValueChange, items, size = "md", className, ...aria }: Props<V>) {
  const id = useId();
  return (
    <TG.Root
      type="single"
      value={value}
      onValueChange={(v) => v && onValueChange(v as V)}
      aria-label={aria["aria-label"]}
      className={cn("inline-flex items-center gap-0.5 rounded-sm border border-border bg-surface-2 p-0.5", className)}
    >
      {items.map((it) => {
        const on = it.value === value;
        const item = (
          <TG.Item
            key={it.value}
            value={it.value}
            disabled={it.disabled}
            className={cn(
              "relative inline-flex items-center gap-1 rounded-xs font-medium text-text-2 transition-colors duration-fast",
              "hover:text-text disabled:opacity-40 data-[state=on]:text-text [&_svg]:size-3.5",
              size === "sm" ? "h-6 px-2 text-xs" : "h-7 px-2.5 text-sm",
            )}
          >
            {on && (
              <m.span
                layoutId={`seg-${id}`}
                transition={{ type: "spring", stiffness: 500, damping: 40 }}
                className="absolute inset-0 rounded-xs border border-border bg-surface shadow-sm"
                aria-hidden
              />
            )}
            <span className="relative inline-flex items-center gap-1">
              {it.icon}
              {it.label}
            </span>
          </TG.Item>
        );
        return it.title ? (
          <Tooltip key={it.value} content={it.title}>
            {item}
          </Tooltip>
        ) : (
          item
        );
      })}
    </TG.Root>
  );
}

/** Multi-select toggles (e.g. canvas layers). Each item shows a swatch so the legend is the control. */
export function ToggleChips<V extends string>({ value, onValueChange, items, className, ...aria }: {
  value: V[];
  onValueChange: (v: V[]) => void;
  items: (SegmentItem<V> & { swatch?: ReactNode })[];
  className?: string;
  "aria-label": string;
}) {
  return (
    <TG.Root type="multiple" value={value} onValueChange={(v) => onValueChange(v as V[])} aria-label={aria["aria-label"]} className={cn("flex flex-wrap items-center gap-1", className)}>
      {items.map((it) => (
        <TG.Item
          key={it.value}
          value={it.value}
          disabled={it.disabled}
          title={it.title}
          className={cn(
            "inline-flex h-6 items-center gap-1.5 rounded-xs border border-border px-2 text-xs text-text-2 transition-colors duration-fast",
            "hover:text-text-2 disabled:opacity-40 data-[state=on]:border-border-strong data-[state=on]:bg-surface-2 data-[state=on]:text-text",
          )}
        >
          {it.swatch}
          {it.label}
        </TG.Item>
      ))}
    </TG.Root>
  );
}

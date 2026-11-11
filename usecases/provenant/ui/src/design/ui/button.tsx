import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { type ButtonHTMLAttributes, forwardRef, type ReactNode } from "react";
import { cn } from "../cn";
import { Tooltip } from "./tooltip";

export const buttonVariants = cva(
  [
    "inline-flex select-none items-center justify-center gap-1.5 whitespace-nowrap rounded-sm font-medium",
    "transition-colors duration-fast ease-standard disabled:pointer-events-none disabled:opacity-45",
    "[&_svg]:size-4 [&_svg]:shrink-0",
  ],
  {
    variants: {
      variant: {
        primary: "bg-text text-inverse hover:bg-text/85",
        secondary: "border border-border-strong bg-surface text-text hover:bg-surface-2",
        ghost: "text-text-2 hover:bg-surface-2 hover:text-text",
        danger: "border border-dashed border-loss text-text hover:bg-loss-tint",
      },
      size: {
        sm: "h-7 px-2.5 text-xs [&_svg]:size-3.5",
        md: "h-8 px-3 text-sm",
      },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement>, VariantProps<typeof buttonVariants> {
  /** render the child element (e.g. a router Link) with button styling */
  asChild?: boolean;
  loading?: boolean;
  icon?: ReactNode;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, loading, icon, children, disabled, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp ref={ref} className={cn(buttonVariants({ variant, size }), className)} disabled={disabled || loading} {...props}>
        {asChild ? (
          children
        ) : (
          <>
            {loading ? <Loader2 className="animate-spin" aria-hidden /> : icon}
            {children}
          </>
        )}
      </Comp>
    );
  },
);
Button.displayName = "Button";

export interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  /** required: becomes the tooltip and the accessible name */
  label: string;
  icon: ReactNode;
  size?: "sm" | "md";
  variant?: "ghost" | "secondary";
  active?: boolean;
  shortcut?: string;
}

/** Square icon button; the tooltip is mandatory. */
export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(
  ({ label, icon, size = "md", variant = "ghost", active, shortcut, className, ...props }, ref) => (
    <Tooltip content={shortcut ? <span className="flex items-center gap-2">{label}<span className="font-mono text-text-2">{shortcut}</span></span> : label}>
      <button
        ref={ref}
        type="button"
        aria-label={label}
        aria-pressed={active}
        className={cn(
          buttonVariants({ variant, size }),
          size === "sm" ? "w-7 px-0" : "w-8 px-0",
          active && "bg-surface-2 text-text",
          className,
        )}
        {...props}
      >
        {icon}
      </button>
    </Tooltip>
  ),
);
IconButton.displayName = "IconButton";

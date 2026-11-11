import { Toaster as Sonner, toast } from "sonner";

/** Bottom-right toasts, 13 px; success ✓ green, error × pink; errors persist. */
export function Toaster({ theme }: { theme: "dark" | "light" }) {
  return (
    <Sonner
      theme={theme}
      position="bottom-right"
      duration={4000}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast: "flex w-[340px] items-start gap-2 rounded-md border border-border-strong bg-surface-2 px-3 py-2.5 text-sm text-text shadow-lg",
          title: "font-medium",
          description: "text-xs text-text-2",
          success: "[&_[data-icon]]:text-cited",
          error: "[&_[data-icon]]:text-loss",
          actionButton: "ml-auto rounded-xs bg-text px-2 text-xs text-inverse",
        },
      }}
    />
  );
}

export { toast };

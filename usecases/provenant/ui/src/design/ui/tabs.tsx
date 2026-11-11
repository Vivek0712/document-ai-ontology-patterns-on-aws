import * as T from "@radix-ui/react-tabs";
import type { ComponentPropsWithoutRef } from "react";
import { cn } from "../cn";

/** Underline tabs: 13/20; active text + 2 px underline in --text; inactive --text-2. */
export const Tabs = T.Root;

export function TabsList({ className, ...props }: ComponentPropsWithoutRef<typeof T.List>) {
  return <T.List className={cn("flex items-center gap-4 border-b border-border", className)} {...props} />;
}

export function TabsTrigger({ className, ...props }: ComponentPropsWithoutRef<typeof T.Trigger>) {
  return (
    <T.Trigger
      className={cn(
        "-mb-px flex h-9 items-center gap-1.5 border-b-2 border-transparent text-sm text-text-2 transition-colors duration-fast",
        "hover:text-text data-[state=active]:border-text data-[state=active]:text-text [&_svg]:size-3.5",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: ComponentPropsWithoutRef<typeof T.Content>) {
  return <T.Content className={cn("pt-4 focus-visible:outline-none", className)} {...props} />;
}

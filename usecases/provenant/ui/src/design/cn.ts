import { type ClassValue, clsx } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/** tailwind-merge that knows our compact type scale, so `text-mono-sm` is a size and `text-text-2` a colour. */
const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: ["caps", "2xs", "md", "figure", "mono-sm", "mono-md"] }],
    },
  },
});

export function cn(...inputs: ClassValue[]): string {
  return merge(clsx(inputs));
}

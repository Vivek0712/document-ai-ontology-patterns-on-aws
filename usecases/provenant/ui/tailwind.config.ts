/**
 * Tailwind mapped onto the design tokens in src/design/tokens.css.
 * Colours resolve to CSS variables (with alpha via color-mix), so `bg-surface`, `text-text-2`,
 * `border-border` and `bg-boundary/10` all follow the active theme. The type scale is compact by design:
 * base 13/20, secondary 12/16, mono 12–13. Spacing stays on Tailwind's 4 px grid.
 */
import type { Config } from "tailwindcss";
import animate from "tailwindcss-animate";

const token = (name: string) => `color-mix(in srgb, var(--${name}) calc(<alpha-value> * 100%), transparent)`;

const colors = [
  "bg", "bg-subtle", "surface", "surface-2", "surface-3", "canvas", "border", "border-strong",
  "text", "text-2", "text-3", "inverse", "focus", "boundary", "loss", "cited", "agent", "concept", "layout",
  "running", "chunk-1", "chunk-2", "chunk-3", "chunk-4", "tier-bronze", "tier-silver", "tier-gold",
] as const;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      black: "#000",
      white: "#fff",
      ...Object.fromEntries(colors.map((c) => [c, token(c)])),
      "boundary-tint": "var(--boundary-tint)",
      "loss-tint": "var(--loss-tint)",
      "cited-tint": "var(--cited-tint)",
    },
    fontFamily: {
      sans: ["var(--font-sans)"],
      mono: ["var(--font-mono)"],
    },
    fontSize: {
      caps: ["12px", { lineHeight: "16px", letterSpacing: "0.02em", fontWeight: "500" }],
      // the smallest text in the app is 12 px; 2xs is kept as an alias for meta lines
      "2xs": ["12px", "16px"],
      xs: ["12px", "16px"],
      sm: ["13px", "20px"],
      md: ["14px", "20px"],
      lg: ["16px", "24px"],
      xl: ["20px", "28px"],
      // the one display size inside showcases and screens: every answer total (Num size="lg"), 20/28 600, tabular
      hero: ["20px", { lineHeight: "28px", fontWeight: "600" }],
      figure: ["28px", { lineHeight: "36px", fontWeight: "600" }],
      "mono-sm": ["12px", "18px"],
      "mono-md": ["13px", "20px"],
    },
    borderRadius: {
      none: "0",
      xs: "4px",
      sm: "6px",
      md: "8px",
      lg: "12px",
      full: "9999px",
    },
    boxShadow: {
      none: "none",
      sm: "var(--shadow-sm)",
      md: "var(--shadow-md)",
      lg: "var(--shadow-lg)",
      page: "var(--page-shadow)",
    },
    extend: {
      transitionDuration: { fast: "120ms", base: "200ms", slow: "320ms", deliberate: "600ms", narrative: "900ms" },
      transitionTimingFunction: {
        standard: "cubic-bezier(0.2, 0, 0, 1)",
        enter: "cubic-bezier(0, 0, 0, 1)",
        exit: "cubic-bezier(0.3, 0, 1, 1)",
      },
      spacing: { sidebar: "var(--sidebar-w)", topbar: "var(--topbar-h)" },
      maxWidth: { content: "1440px" },
      keyframes: {
        shimmer: { "0%": { backgroundPosition: "-200% 0" }, "100%": { backgroundPosition: "200% 0" } },
      },
      animation: { shimmer: "shimmer 1.2s linear infinite" },
    },
  },
  plugins: [animate],
} satisfies Config;

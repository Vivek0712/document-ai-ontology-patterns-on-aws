/**
 * Sidebar and command-palette navigation. Every item routes to a built screen; `status: "next"`
 * stays in the type for a future screen (the sidebar folds such items into one "coming" row per group).
 */
import {
  BookOpenCheck,
  Boxes,
  CloudCog,
  Coins,
  FileStack,
  FlaskConical,
  GitCompareArrows,
  Home,
  type LucideIcon,
  MessageSquareText,
  Network,
  Palette,
  PlayCircle,
  ScanSearch,
  ShieldCheck,
  Waypoints,
} from "lucide-react";
import { SHOWCASES, SHOWCASE_GROUPS } from "../showcase/catalog";

export interface NavItem {
  label: string;
  to: string;
  status?: "live" | "next";
  /** extra path prefixes that keep this item active */
  activePrefixes?: string[];
  hint?: string;
  /** a sub-heading the item sits under (the showcases' acts, as the index page groups them) */
  group?: string;
}

export interface NavSection {
  id: string;
  label: string;
  icon: LucideIcon;
  items: NavItem[];
  /** folds to its header outside `openOn` (a path prefix) unless the reader opened it */
  collapsible?: { openOn: string };
}

export const isLive = (s: NavSection) => s.items.some((i) => i.status !== "next");

/** Sidebar labels short enough for its width; the full title is on the showcase and in the palette's hint. */
const SHORT_LABEL: Record<string, string> = { "sc-10": "Bigger model", "sc-3": "Footnote → property", "sc-5": "Resolve · hop · cite" };

export function navSections(packageId: string): NavSection[] {
  const pkg = encodeURIComponent(packageId);
  return [
    {
      id: "showcases",
      label: "Showcases",
      icon: PlayCircle,
      collapsible: { openOn: "/showcase" },
      items: [
        { label: "All showcases", to: "/showcase" },
        // grouped by act as the index page groups them; every row carries its SC code; a title that names a product
        // page too says it is the story
        ...SHOWCASE_GROUPS.flatMap((group) =>
          SHOWCASES.filter((s) => s.group === group && (s.hero || s.status === "live")).map((s) => ({
            label: SHORT_LABEL[s.id] ?? (s.id === "sc-6" ? `${s.title} (story)` : s.title),
            to: `/showcase/${s.id}`,
            status: s.status,
            hint: s.code,
            group,
          })),
        ),
      ],
    },
    {
      id: "workbench",
      label: "Workbench",
      icon: Boxes,
      items: [
        { label: "Packages", to: "/workbench/packages", activePrefixes: ["/workbench/packages"] },
        { label: "Pipeline board", to: `/workbench/packages/${pkg}` },
        { label: "Units explorer", to: `/workbench/units?pkg=${pkg}`, activePrefixes: ["/workbench/units"] },
        { label: "Ask", to: `/workbench/packages/${pkg}/ask` },
        { label: "Runs", to: `/workbench/packages/${pkg}/runs`, activePrefixes: ["/runs"] },
      ],
    },
    {
      id: "knowledge",
      label: "Knowledge",
      icon: Network,
      items: [
        { label: "Ontology", to: "/knowledge/ontology" },
        { label: "Governed meaning", to: "/knowledge/governed" },
        { label: "Graph", to: "/knowledge/graph" },
      ],
    },
    {
      id: "governance",
      label: "Governance",
      icon: ShieldCheck,
      items: [
        { label: "PII gate", to: "/governance/pii" },
        { label: "Access and security", to: "/governance/access" },
        { label: "Review queue", to: "/governance/review" },
      ],
    },
    { id: "evaluate", label: "Evaluate", icon: FlaskConical, items: [{ label: "Golden set and results", to: "/evaluate" }] },
    { id: "cost", label: "Cost", icon: Coins, items: [{ label: "Cost and tiering", to: "/cost" }] },
  ];
}

/** Icons used by the palette for routes that are not sidebar sections. */
export const ROUTE_ICONS: Record<string, LucideIcon> = {
  home: Home,
  units: ScanSearch,
  ask: MessageSquareText,
  diff: GitCompareArrows,
  design: Palette,
  packages: FileStack,
  trace: Waypoints,
  docs: BookOpenCheck,
  environment: CloudCog,
};

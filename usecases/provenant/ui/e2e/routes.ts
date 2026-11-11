/**
 * Every route the app serves in mock mode, for the smoke and axe suites. Ids are the mock world's (the export of
 * the local pipeline run), read from `mock-public/mock/world.json` and its package shards so the list follows the
 * data, not this file.
 */
import { readFileSync } from "node:fs";

interface WorldLite {
  packages: { package: { packageId: string }; documents: { docId: string; docType?: string }[] }[];
  runSummaries: { runId: string; packageId: string; pipelineVersion: string }[];
  units: Record<string, { unit: { unitId: string; docId: string; unitType: string; fields?: { fieldId: string }[] } }>;
}

const MOCK = new URL("../mock-public/mock/", import.meta.url);
const read = <T,>(path: string): T => JSON.parse(readFileSync(new URL(path, MOCK), "utf8")) as T;
// world.json is the core; units live in the per-package shards (fetched lazily by the app)
const core = read<Omit<WorldLite, "units"> & { shards?: Record<string, string>; units?: WorldLite["units"] }>("world.json");
const world: WorldLite = {
  ...core,
  units: Object.assign({}, core.units, ...Object.values(core.shards ?? {}).map((p) => read<{ units?: WorldLite["units"] }>(p).units ?? {})),
};
const lending = world.packages[0];
const pkg = lending.package.packageId;
const doc = lending.documents.find((d) => d.docType === "doc:W2") ?? lending.documents[0];
const run = world.runSummaries.find((r) => r.packageId === pkg && r.pipelineVersion === "2.3.1")!;
const unit = Object.values(world.units).find((u) => u.unit.docId === doc.docId && u.unit.unitType === "field_group")!.unit;
const field = unit.fields?.[0]?.fieldId;

export interface RouteCase {
  name: string;
  path: string;
  /** a selector that proves the page rendered its content (not only the shell) */
  ready: string;
}

/** Every showcase in the catalog (all 15 have a storyboard). */
export const SHOWCASE_IDS = ["sc-0", "sc-1", "sc-2", "sc-3", "sc-4", "sc-5", "sc-6", "sc-7", "sc-8", "sc-9", "sc-10", "sc-11", "sc-12", "sc-13", "sc-14"];
/** SC-11's examples (`?ex=`), from the catalog. */
export const SC11_EXAMPLES = ["d3", "d1", "d2", "lending-02"];
/** The other packages, to check the package switcher's lazy shard loading on each. */
export const OTHER_PACKAGES = world.packages.slice(1).map((p) => p.package.packageId);

export const ROUTES: RouteCase[] = [
  { name: "home", path: "/", ready: "text=Start the story" },
  { name: "showcases", path: "/showcase", ready: "main >> text=SC-0" },
  ...SHOWCASE_IDS.map((id) => ({ name: id, path: `/showcase/${id}?step=1`, ready: "[data-testid=caption]" })),
  ...SC11_EXAMPLES.slice(1).map((ex) => ({ name: `sc-11-${ex}`, path: `/showcase/sc-11?step=3&ex=${ex}`, ready: "[data-testid=caption]" })),
  { name: "packages", path: "/workbench/packages", ready: `text=${pkg}` },
  { name: "package", path: `/workbench/packages/${pkg}`, ready: "main h1" },
  { name: "ask", path: `/workbench/packages/${pkg}/ask`, ready: "main textarea, main input" },
  { name: "runs", path: `/workbench/packages/${pkg}/runs`, ready: "main table" },
  { name: "run", path: `/runs/${run.runId}`, ready: `text=${run.runId}` },
  { name: "document", path: `/workbench/docs/${doc.docId}`, ready: "main h1" },
  { name: "doc-units", path: `/workbench/docs/${doc.docId}/units`, ready: "main h1" },
  { name: "units-explorer", path: `/workbench/units?pkg=${pkg}&doc=${doc.docId}`, ready: "main svg[role=img]" },
  { name: "unit", path: `/workbench/units/${encodeURIComponent(unit.unitId)}`, ready: "main h1" },
  { name: "trace", path: `/workbench/trace/${encodeURIComponent(unit.unitId)}${field ? `?field=${field}` : ""}`, ready: "main svg[role=img]" },
  { name: "pipelines", path: "/settings/pipelines", ready: "main h1" },
  { name: "pii", path: "/governance/pii", ready: "figure[data-body-height]" },
  { name: "ontology", path: "/knowledge/ontology?iri=fin:W2WagesBox1", ready: "[data-testid=term-page]" },
  { name: "ontology-schema", path: "/knowledge/ontology?tab=schema", ready: "main table" },
  { name: "governed", path: "/knowledge/governed", ready: "[data-testid=governed]" },
  { name: "governed-approval", path: "/knowledge/governed?tab=approval", ready: "[data-coa-slot]" },
  { name: "governed-metric", path: "/knowledge/governed?tab=metric", ready: "[data-testid=coa-metric], [data-coa-slot]" },
  { name: "graph", path: "/knowledge/graph?focus=fin:W2WagesBox1", ready: "[data-testid=graph-canvas] svg" },
  { name: "access", path: "/governance/access", ready: "[data-testid=access-decisions]" },
  { name: "security", path: "/governance/access?tab=security", ready: "[data-testid=security]" },
  { name: "review", path: "/governance/review", ready: "[data-testid=review-queue]" },
  { name: "evaluate", path: "/evaluate", ready: "[data-testid=eval-results]" },
  { name: "evaluate-golden", path: "/evaluate?tab=golden", ready: "[data-testid=golden-set]" },
  { name: "cost", path: "/cost", ready: "[data-testid=cost-ledger]" },
  { name: "not-found", path: "/no/such/page", ready: "text=There is no page at" },
];

/** `?ingest=done`: the mock lending package opens already ingested; `autoplay=0`: showcases open paused. */
export function url(path: string, extra: Record<string, string> = {}): string {
  const q = new URLSearchParams({ ingest: "done", autoplay: "0", ...extra });
  return `${path}${path.includes("?") ? "&" : "?"}${q.toString()}`;
}

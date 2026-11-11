#!/usr/bin/env node
/**
 * Screenshots of the key routes in both themes, for visual review (dev only).
 *
 *   npm run dev:mock                     # in another shell (port 5188)
 *   npm run shots -- --out /tmp/shots    # or: node scripts/screenshots.mjs --base http://localhost:5188 --out DIR
 *
 * Uses playwright-core with the locally installed Google Chrome (no browser download).
 * Showcase steps are captured through deep links (?step=n opens paused at the step's end state). Every URL carries
 * `ingest=done`, so the mock lending package opens already ingested.
 */
import { mkdirSync } from "node:fs";
import { join } from "node:path";
import { parseArgs } from "node:util";
import { chromium } from "playwright-core";

const { values: args } = parseArgs({
  options: {
    base: { type: "string", default: "http://localhost:5188" },
    out: { type: "string", default: "shots" },
    chrome: { type: "string", default: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" },
    only: { type: "string" },
    width: { type: "string", default: "1440" },
    height: { type: "string", default: "900" },
  },
});

const PKG = "pkg-lending-01";
const MAYA = "w2-2025-maya-fictus-827d37";
const steps = (id, n) => Array.from({ length: n }, (_, i) => ({ name: `${id.replace("-", "")}-step${i + 1}`, path: `/showcase/${id}?step=${i + 1}`, wait: "[data-testid=caption]" }));
const shots = [
  { name: "home", path: "/" },
  { name: "showcases", path: "/showcase" },
  ...steps("sc-0", 9),
  ...steps("sc-1", 6),
  ...steps("sc-9", 8),
  ...steps("sc-6", 7),
  ...steps("sc-5", 6),
  ...steps("sc-2", 8),
  ...steps("sc-3", 6),
  ...steps("sc-4", 4),
  ...steps("sc-7", 5),
  ...steps("sc-8", 6),
  ...steps("sc-10", 4),
  ...["d3", "d1", "d2", "lending-02"].flatMap((ex) => steps("sc-11", 6).map((s) => ({ ...s, name: s.name.replace("sc11", `sc11-${ex}`), path: `${s.path}&ex=${ex}` }))),
  ...steps("sc-12", 4),
  ...steps("sc-13", 5),
  ...steps("sc-14", 7),
  { name: "workbench-units-xray", path: `/workbench/units?pkg=${PKG}&doc=${MAYA}&layers=units,fields,chunks,path,held` },
  { name: "workbench-units-whatif", path: `/workbench/units?pkg=${PKG}&doc=${MAYA}&layers=units,chunks,pairs&size=128` },
  { name: "workbench-units-bank", path: `/workbench/units?pkg=${PKG}&doc=bank-statement-2026-09-7c2e0d&layers=units,chunks,held` },
  { name: "workbench-packages", path: "/workbench/packages" },
  { name: "workbench-board", path: `/workbench/packages/${PKG}` },
  { name: "workbench-ask", path: `/workbench/packages/${PKG}/ask?q=q-lend-01`, wait: "[data-testid=ask-done]" },
  { name: "workbench-runs", path: `/workbench/packages/${PKG}/runs`, wait: "text=held by PII gate" },
  { name: "workbench-run", path: "/runs/r-99bf456c5a2c" },
  { name: "workbench-trace", path: `/workbench/trace/${encodeURIComponent(`${MAYA}:2.3.1:eu-02`)}?field=f-box1&run=r-99bf456c5a2c` },
  { name: "governance-pii", path: "/governance/pii", wait: "figure[data-body-height]" },
  { name: "knowledge-ontology", path: "/knowledge/ontology?iri=fin:W2WagesBox1", wait: "[data-testid=term-page]" },
  { name: "knowledge-governed", path: "/knowledge/governed", wait: "[data-testid=governed]" },
  { name: "knowledge-governed-approval", path: "/knowledge/governed?tab=approval", wait: "[data-testid=governed]" },
  { name: "knowledge-governed-metric", path: "/knowledge/governed?tab=metric", wait: "[data-testid=governed]" },
  { name: "knowledge-governed-binding", path: "/knowledge/governed?tab=binding", wait: "[data-testid=governed]" },
  { name: "knowledge-governed-discovery", path: "/knowledge/governed?tab=discovery", wait: "[data-testid=governed]" },
  { name: "knowledge-graph", path: "/knowledge/graph?focus=fin:W2WagesBox1", wait: "[data-testid=graph-canvas]" },
  { name: "governance-access", path: "/governance/access", wait: "[data-testid=access-decisions]" },
  { name: "governance-security", path: "/governance/access?tab=security", wait: "[data-testid=security]" },
  { name: "governance-review", path: "/governance/review", wait: "[data-testid=review-queue]" },
  { name: "evaluate-results", path: "/evaluate?tab=results", wait: "[data-testid=eval-results]" },
  { name: "evaluate-golden", path: "/evaluate?tab=golden", wait: "[data-testid=golden-set]" },
  { name: "cost", path: "/cost", wait: "[data-testid=cost-ledger]" },
  { name: "workbench-board-quarterly", path: "/workbench/packages/pkg-quarterly-01" },
  { name: "governance-pii-tradeconf", path: "/governance/pii?pkg=pkg-tradeconf-01", wait: "figure[data-body-height]" },
  { name: "dev-ui", path: "/dev/ui", height: 3900 },
];

mkdirSync(args.out, { recursive: true });
const browser = await chromium.launch({ executablePath: args.chrome, headless: true });
const written = [];
try {
  for (const theme of ["dark", "light"]) {
    const ctx = await browser.newContext({ viewport: { width: Number(args.width), height: Number(args.height) }, colorScheme: theme });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => console.error(`[${theme}] page error:`, e.message));
    for (const s of shots) {
      // --only takes one or more comma-separated name parts ("sc7-,sc8-,knowledge-")
      if (args.only && !args.only.split(",").some((o) => s.name.includes(o))) continue;
      const sep = s.path.includes("?") ? "&" : "?";
      // ingest=done: the mock lending package opens already ingested (showcases replay finished artifacts)
      await page.goto(`${args.base}${s.path}${sep}theme=${theme}&autoplay=0&ingest=done`, { waitUntil: "networkidle" });
      if (s.wait) await page.waitForSelector(s.wait, { timeout: 30000 });
      // the shell scrolls inside <main>, so tall pages are captured with a tall viewport
      if (s.height) await page.setViewportSize({ width: Number(args.width), height: s.height });
      await page.waitForTimeout(500);
      const file = join(args.out, `${s.name}-${theme}.png`);
      await page.screenshot({ path: file });
      if (s.height) await page.setViewportSize({ width: Number(args.width), height: Number(args.height) });
      written.push(file);
    }
    await ctx.close();
  }
} finally {
  await browser.close();
}
console.log(written.join("\n"));

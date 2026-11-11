/**
 * Storyboards on the real mock world: SC-2 Evidence units, SC-3 The footnote becomes a property, SC-4
 * Filters inside the query, SC-5 Resolve, retrieve, hop, cite; the v2 inspection routes in the mock API; and the
 * GraphCanvas layout. Every expectation is read from the same export the UI replays (world.json, the run records,
 * the X-ray exports), never typed here.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { CouponTermsData } from "@contracts/api";
import { RpApiError } from "./api/types";
import { PREFIXES, curie } from "./lib/iris";
import { captionOf } from "./showcase/engine/types";
import { sc2EvidenceUnits } from "./showcase/stories/sc2-evidence-units";
import { type Sc2Payload, checksCaption, gutterRoute, invariantRows, splitCaption, subjectUnit } from "./showcase/stories/sc2-payload";
import { sc3FootnoteProperty } from "./showcase/stories/sc3-footnote-property";
import { type Sc3Payload, STEP_PROPERTIES, naiveCaption, spansOf } from "./showcase/stories/sc3-payload";
import { sc4Filters } from "./showcase/stories/sc4-filters";
import { type Sc4Payload, filterKeys, subQueries } from "./showcase/stories/sc4-payload";
import { sc5ResolveHopCite } from "./showcase/stories/sc5-resolve-hop-cite";
import { citeCaption, hopCaption, resolveCaption, runSegments } from "./showcase/stories/sc5-payload";
import { api, commonChecks, hasWorld, loggedRun, render, stepOf, stubFixtures, world } from "./testing/storyHarness";
import { GraphCanvas, layoutGraph } from "./viz/GraphCanvas";

const LENDING = "pkg-lending-01";
const SPELLED = /\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b/i;

beforeAll(stubFixtures);
afterAll(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const load = {
  sc2: async () => (await sc2EvidenceUnits.load({ api: api(), packageId: LENDING })).payload,
  sc3: async () => (await sc3FootnoteProperty.load({ api: api(), packageId: LENDING })).payload,
  sc4: async () => (await sc4Filters.load({ api: api(), packageId: LENDING })).payload,
  sc5: async () => (await sc5ResolveHopCite.load({ api: api(), packageId: LENDING })).payload,
};

// --------------------------------------------------------------------------------------------- mock routes

describe.skipIf(!hasWorld)("v2 inspection routes (mock API over the real handlers' responses)", () => {
  it("build replay: 2.3.1 only; scores and tau are the export's", async () => {
    const w = world();
    const [docId, lanes] = Object.entries(w.builds!).find(([, l]) => l["2.3.1"].allocations.some((a) => a.phase === "B"))!;
    expect(await api().getBuild(docId, "2.3.1")).toEqual(lanes["2.3.1"]);
    await expect(api().getBuild(docId, "1.0")).rejects.toMatchObject({ status: 404 });
  });
  it("filters, call paths and the sc-3 payload check the caller's access first", async () => {
    const w = world();
    const [key, inspection] = Object.entries(w.filterInspections!).find(([, f]) => f.packageId === LENDING)!;
    const [runId, callId] = key.split("|");
    expect(await api().inspectFilters(runId, callId)).toEqual(inspection);
    const b = api("b");
    await expect(b.inspectFilters(runId, callId)).rejects.toBeInstanceOf(RpApiError);
    const [pathKey] = Object.keys(w.graphPaths!);
    expect((await api().getCallPath(...(pathKey.split("|") as [string, string]))).source).toBe("logged-rows");
    const [scKey, payload] = Object.entries(w.showcasePayloads!).find(([k]) => k.startsWith("sc-3|"))!;
    const [, packageId, security] = scKey.split("|");
    expect((await api().getShowcasePayload<CouponTermsData>("sc-3", { packageId, security: security.toLowerCase() })).payloadSha).toBe(payload.payloadSha);
  });
  it("serves each package's own X-ray export, with its page renders", async () => {
    const w = world();
    const sc3 = Object.entries(w.showcasePayloads!).find(([k]) => k.startsWith("sc-3|"))![1];
    const doc = sc3.data.docId as string;
    const x = await api().getXray(doc, 1);
    expect(x.docId).toBe(doc);
    expect(x.image.url).toMatch(new RegExp(`^/mock/xray/${sc3.packageId}/pages/`));
  });
});

// --------------------------------------------------------------------------------------------- SC-5

describe.skipIf(!hasWorld)("SC-5 Resolve, retrieve, hop, cite", () => {
  commonChecks(sc5ResolveHopCite, load.sc5);
  it("every stop is a tool call of the logged run, at its measured time", async () => {
    const p = await load.sc5();
    const r = loggedRun(p.run.runId);
    expect(p.calls.map((c) => c.callId)).toEqual(r.toolCalls.map((c) => c.callId));
    for (const c of p.calls) {
      const rec = r.toolCalls.find((x) => x.callId === c.callId)!;
      expect(c.at).toBe(Date.parse(rec.startedAt) - Date.parse(r.createdAt));
      expect(c.ms).toBe(rec.ms);
    }
    const segs = runSegments(p);
    expect(segs[0].from).toBe(0);
    expect(segs.at(-1)!.to).toBe(r.latencyMs);
  });
  it("the hop graph is the call's logged-rows path, drawn in full at the step's end", async () => {
    const p = await load.sc5();
    expect(p.hop).toBeDefined();
    const w = world();
    expect(p.hop!.path).toEqual(w.graphPaths![`${p.run.runId}|${p.hop!.call.callId}`]);
    const html = render(sc5ResolveHopCite, p, stepOf(sc5ResolveHopCite, "hop"), 1);
    expect(html.match(/data-hop="drawn"/g)?.length).toBe(p.hop!.path.edges.length);
  });
  it("captions follow the run: hop, resolve and cite variants", async () => {
    const p = await load.sc5();
    expect(hopCaption({ hop: undefined })).toBe("No hop in this run.");
    expect(hopCaption({ hop: { ...p.hop!, template: "coupon_terms_v1" } })).toMatch(/footnote/);
    expect(resolveCaption(p)).toBe(p.resolve.call ? "Resolve the words against the ontology." : "No concept lookup in this run; the tool inputs name the ontology terms.");
    expect(p.regions.length).toBe(p.answer.claims.filter((c) => c.resolvable).length);
    expect(citeCaption({ ...p, regions: p.regions.slice(1) })).toMatch(/of/);
    for (const s of sc5ResolveHopCite.steps) expect(captionOf(s, p)).not.toMatch(SPELLED);
  });
  it("no two hop-graph nodes read the same, and the operands print as money", async () => {
    const p = await load.sc5();
    const labels = p.hop!.path.nodes.filter((n) => n.kind !== "literal").map((n) => n.label); // values may repeat (2025)
    expect(new Set(labels).size).toBe(labels.length);
    const html = render(sc5ResolveHopCite, p, stepOf(sc5ResolveHopCite, "hop"), 1);
    for (const r of p.hop!.rows) expect(html).toContain(Number(r.wagesBox1).toLocaleString("en-US", { minimumFractionDigits: 2 }));
  });
  it("each cited figure's thumbnail draws its unit boundary and a ✓ on the field", async () => {
    const p = await load.sc5();
    const html = render(sc5ResolveHopCite, p, stepOf(sc5ResolveHopCite, "cite"), 1);
    expect(html.match(/var\(--boundary\)/g)?.length).toBeGreaterThanOrEqual(p.regions.length);
    expect(html).not.toMatch(/aria-label="wrong"/);
  });
});

// --------------------------------------------------------------------------------------------- SC-4

describe.skipIf(!hasWorld)("SC-4 Filters inside the query", () => {
  commonChecks(sc4Filters, load.sc4);
  it("funnel counts are the _count export for the exact logged call", async () => {
    const p: Sc4Payload = await load.sc4();
    expect(p.inspection).toEqual(world().filterInspections![`${p.runId}|${p.callId}`]);
    expect(p.inspection.filter).toEqual(p.echo.filter);
  });
  it("the same filter object sits inside both sub-queries", async () => {
    const p = await load.sc4();
    const q = subQueries(p.echo, p.inspection.queryText) as { dense: { query: { knn: { embedding: { filter: unknown } } } }; bm25: { query: { bool: { filter: unknown[] } } } };
    expect(q.dense.query.knn.embedding.filter).toEqual(p.echo.filter);
    expect(q.bm25.query.bool.filter[0]).toEqual(p.echo.filter);
  });
  it("metadata rows flag exactly the fields the filter tests", async () => {
    const p = await load.sc4();
    const keys = filterKeys(p.echo.filter);
    for (const r of p.groups.flatMap((g) => g.rows)) expect(r.inFilter, r.key).toBe(keys.has(r.key));
    expect([...keys].every((k) => p.groups.some((g) => g.rows.some((r) => r.key === k)))).toBe(true);
  });
});

// --------------------------------------------------------------------------------------------- SC-3

describe.skipIf(!hasWorld)("SC-3 The footnote becomes a property", () => {
  commonChecks(sc3FootnoteProperty, load.sc3);
  it("property values are the graph's row; the UI only points at where each is printed", async () => {
    const p: Sc3Payload = await load.sc3();
    const row = (p.data.graph?.rows as Record<string, string>[])[0];
    const html = render(sc3FootnoteProperty, p, stepOf(sc3FootnoteProperty, "properties"), 1);
    for (const k of STEP_PROPERTIES) {
      expect(p.data.stepEvent?.[k]).toBe(row[k]);
      expect(html).toContain(`>${row[k]}<`);
      expect(p.spans[k], k).toBeDefined();
    }
    expect(spansOf(["3", "6.00%", "from", "year", "3"], "3", { newRate: "6", effectiveFromYear: "3", triggerRating: "X" })).toEqual({ newRate: 1, effectiveFromYear: 4, triggerRating: undefined });
  });
  it("the cross-page link carries the 'our builder's extension' tag; classify shows its scores", async () => {
    const p = await load.sc3();
    expect(p.link.crossPage).toBe(true);
    expect(render(sc3FootnoteProperty, p, stepOf(sc3FootnoteProperty, "marker-link"), 1)).toContain("our builder&#x27;s extension");
    const classify = render(sc3FootnoteProperty, p, stepOf(sc3FootnoteProperty, "classify"), 1);
    const out = p.data.classify!.output as { scores: Record<string, number>; tieBreakUsed: boolean };
    for (const k of Object.keys(out.scores)) expect(classify).toContain(k);
    expect(classify).toContain(out.tieBreakUsed ? "used" : "not used");
  });
  it("the naive caption is chosen from the X-ray: shared chunk vs different chunks", async () => {
    const p = await load.sc3();
    const shared = p.naive.couponChunks.some((c) => p.naive.footnoteChunks.includes(c));
    expect(naiveCaption(p)).toMatch(shared ? /share a chunk/ : /different chunks/);
    expect(naiveCaption({ ...p, naive: { couponChunks: ["c-01"], footnoteChunks: ["c-02"] } })).toMatch(/different chunks, with no link/);
    const html = render(sc3FootnoteProperty, { ...p, naive: { couponChunks: ["c-01"], footnoteChunks: ["c-02"] } }, stepOf(sc3FootnoteProperty, "naive"), 1);
    expect(html).toMatch(/var\(--loss\)/);
  });
  it("the naive contrast draws each chunk once (a tinted slice per page), not a box per word", async () => {
    const p = await load.sc3();
    const html = render(sc3FootnoteProperty, p, stepOf(sc3FootnoteProperty, "naive"), 1);
    const ids = new Set(p.pages.flatMap((pg) => pg.chunks.map((c) => c.chunkId)));
    expect((html.match(/data-chunk="/g) ?? []).length).toBe(ids.size);
    const loaded = await sc3FootnoteProperty.load({ api: api(), packageId: LENDING });
    expect(loaded.subjectPackageId).toBe(p.document.packageId);
  });
  it("the graph step draws the payload's path from the template row", async () => {
    const p = await load.sc3();
    expect(p.path?.template).toBe("coupon_terms_v1");
    const html = render(sc3FootnoteProperty, p, stepOf(sc3FootnoteProperty, "graph"), 1);
    expect(html.match(/data-hop="drawn"/g)?.length).toBe(p.path!.edges.length);
  });
});

// --------------------------------------------------------------------------------------------- SC-2

describe.skipIf(!hasWorld)("SC-2 Evidence units", () => {
  commonChecks(sc2EvidenceUnits, load.sc2);
  it("the subject unit used a seed, a marker link and a phase B join, per its build report", async () => {
    const p: Sc2Payload = await load.sc2();
    const build = world().builds![p.docId]["2.3.1"];
    expect(subjectUnit(build)).toBe(p.unit.unitId);
    const scored = p.blocks.filter((b) => b.score !== null && b.score !== undefined);
    const html = render(sc2EvidenceUnits, p, stepOf(sc2EvidenceUnits, "pass-b"), 1);
    for (const b of scored) expect(html).toContain(b.score!.toFixed(3));
    for (const b of scored) expect(b.phase === "B", b.blockPointer).toBe(b.score! >= p.build.tau);
  });
  it("cross-page merge draws only when the record merged", async () => {
    const p = await load.sc2();
    expect(p.split).toBeDefined();
    const step = stepOf(sc2EvidenceUnits, "page-break");
    expect(splitCaption(p)).toMatch(p.split!.mergedTables > 0 ? /become one unit/ : /its own unit/);
    const arcs = (q: Sc2Payload) => (render(sc2EvidenceUnits, q, step, 1).match(/stroke-linecap="round"/g) ?? []).length;
    const merged = { ...p, split: { ...p.split!, mergedTables: 1 } };
    expect(splitCaption(merged)).toMatch(/become one unit/);
    expect(arcs(merged)).toBeGreaterThan(arcs({ ...p, split: { ...p.split!, mergedTables: 0 } }));
  });
  it("connectors run through the gutter and never cross a block's text (collision check on the real page)", async () => {
    const p = await load.sc2();
    const rects = p.blocks.map((b) => b.rect);
    const crosses = (a: [number, number], b: [number, number], r: number[]) => {
      // axis-aligned segment against the open interior of r
      const [x0, x1] = [Math.min(a[0], b[0]), Math.max(a[0], b[0])];
      const [y0, y1] = [Math.min(a[1], b[1]), Math.max(a[1], b[1])];
      return x1 > r[0] + 0.5 && x0 < r[2] - 0.5 && y1 > r[1] + 0.5 && y0 < r[3] - 0.5;
    };
    const members = p.blocks.filter((b) => b.unitId === p.unit.unitId && b !== p.seed && b.phase !== "marker");
    expect(members.length).toBeGreaterThan(0);
    for (const m of members) {
      const pts = gutterRoute(m.rect, p.seed!.rect, rects);
      for (let i = 1; i < pts.length; i++) for (const r of rects) expect(crosses(pts[i - 1], pts[i], r), `${m.blockPointer} segment ${i} vs ${r.join(",")}`).toBe(false);
    }
  });
  it("pass B prints each score on the page, and the checks note reads as one row per check", async () => {
    const p = await load.sc2();
    const html = render(sc2EvidenceUnits, p, stepOf(sc2EvidenceUnits, "pass-b"), 1);
    for (const b of p.blocks.filter((x) => x.score !== null && x.score !== undefined)) expect(html).toContain(`${b.score!.toFixed(3)} ${b.phase === "B" ? "≥" : "&lt;"} τ`);
    expect(invariantRows("I1: deferred (pipelineVersion 2.4.0, post-lock); I2: deferred (pipelineVersion 2.4.0, post-lock)")).toEqual([
      ["I1", "deferred to 2.4.0"],
      ["I2", "deferred to 2.4.0"],
    ]);
    const checks = render(sc2EvidenceUnits, p, stepOf(sc2EvidenceUnits, "checks"), 1);
    for (const [k, v] of invariantRows(p.build.invariants?.note)) expect(checks).toContain(`${k}</dt><dd class="text-text">${v}`);
  });
  it("the story names the package it plays on, for the top bar (it is not the workspace's)", async () => {
    const loaded = await sc2EvidenceUnits.load({ api: api(), packageId: LENDING });
    expect(loaded.subjectPackageId).toBe(loaded.payload.packageId);
    expect(loaded.subjectPackageId).not.toBe(LENDING);
  });
  it("the checks caption follows the invariants record", async () => {
    const p = await load.sc2();
    expect(checksCaption(p)).toMatch(p.build.invariants?.mode === "off" ? /off in this lane/ : /violations logged/);
    expect(checksCaption({ ...p, build: { ...p.build, invariants: { mode: "log", violations: [{}], note: null } } })).toMatch(/1 violations logged/);
  });
});

describe.skipIf(!hasWorld)("pattern captions spell out no counts", () => {
  it.each([
    ["sc-2", sc2EvidenceUnits, load.sc2],
    ["sc-3", sc3FootnoteProperty, load.sc3],
    ["sc-4", sc4Filters, load.sc4],
    ["sc-5", sc5ResolveHopCite, load.sc5],
  ] as const)("%s", async (_, story, l) => {
    const p = await (l as () => Promise<unknown>)();
    for (const s of (story as { steps: { caption: unknown }[] }).steps) expect(captionOf(s as never, p as never)).not.toMatch(SPELLED);
  });
});

// --------------------------------------------------------------------------------------------- GraphCanvas

describe("GraphCanvas", () => {
  const g = {
    nodes: [
      { iri: "urn:rp:ont:fin:A", kind: "class" as const, label: "fin:A", types: [], graph: "t" },
      { iri: "urn:rp:field:x/f-1", kind: "field" as const, label: "field f-1", types: ["urn:rp:ont:fin:FieldValue"], graph: "a" },
      { iri: "literal:1", kind: "literal" as const, label: "42", types: [], graph: "a" },
    ],
    edges: [
      { s: "urn:rp:field:x/f-1", p: "urn:rp:ont:fin:concept", o: "urn:rp:ont:fin:A", label: "fin:concept", graph: "a" },
      { s: "urn:rp:field:x/f-1", p: "urn:rp:ont:fin:amount", o: "literal:1", label: "fin:amount", graph: "a" },
    ],
  };
  it("an interactive graph is a group of named buttons; a static one an image; marker ids are per canvas", () => {
    const one = renderToString(createElement(GraphCanvas, { graph: g, ariaLabel: "g", onOpen: () => undefined }));
    expect(one).toMatch(/<svg role="group"/);
    expect(one).toMatch(/role="button"[^>]*aria-label="Open field f-1 in Trace"/);
    expect(renderToString(createElement(GraphCanvas, { graph: g, ariaLabel: "g" }))).toMatch(/<svg role="img"/);
    const two = renderToString(createElement("div", null, createElement(GraphCanvas, { graph: g, ariaLabel: "a" }), createElement(GraphCanvas, { graph: g, ariaLabel: "b" })));
    const ids = [...two.matchAll(/<marker id="([^"]+)"/g)].map((m) => m[1]);
    expect(new Set(ids).size).toBe(ids.length);
  });
  it("edges with one predicate into one node share a label (×n), drawn after every path", () => {
    const twin = { ...g, nodes: [...g.nodes, { iri: "urn:rp:field:y/f-1", kind: "field" as const, label: "field f-1 · y", types: [], graph: "a" }], edges: [...g.edges, { s: "urn:rp:field:y/f-1", p: "urn:rp:ont:fin:concept", o: "urn:rp:ont:fin:A", label: "fin:concept", graph: "a" }] };
    const laid = layoutGraph(twin);
    expect(laid.edges.map((e) => e.label.text).filter(Boolean)).toEqual(["fin:concept ×2", "fin:amount"]);
    expect(laid.nodes.find((n) => n.node.iri === "urn:rp:field:y/f-1")).toMatchObject({ title: "field f-1", detail: "y" });
    const html = renderToString(createElement(GraphCanvas, { graph: twin, ariaLabel: "g" }));
    expect(html.lastIndexOf('data-edge="')).toBeLessThan(html.indexOf("data-edge-label"));
  });
  it("lays out deterministically", () => {
    expect(layoutGraph(g)).toEqual(layoutGraph(g));
  });
  it("draws the hop path in order: half the progress lights half the edges", () => {
    const html = (progress: number) => renderToString(createElement(GraphCanvas, { graph: g, hop: { order: g.edges.map((e) => `${e.s}|${e.p}|${e.o}`), progress }, ariaLabel: "g" }));
    expect(html(0).match(/data-hop="drawn"/g)).toBeNull();
    expect(html(0.5).match(/data-hop="drawn"/g)?.length).toBe(1);
    expect(html(1).match(/data-hop="drawn"/g)?.length).toBe(2);
    expect(html(1)).toContain("<caption>g</caption>"); // the text alternative lists every triple
  });
  it("the CURIE table mirrors evidence_core/iris.py", () => {
    const py = readFileSync(new URL("../../../../packages/evidence-core/evidence_core/iris.py", import.meta.url), "utf8");
    const block = py.slice(py.indexOf("PREFIXES: dict[str, str] = {"), py.indexOf("}", py.indexOf("PREFIXES: dict[str, str] = {")));
    const pairs = Object.fromEntries([...block.matchAll(/"([\w-]+)":\s*"([^"]+)"/g)].map((m) => [m[1], m[2]]));
    expect(PREFIXES).toEqual(pairs);
    expect(curie("urn:rp:ont:fin:CouponRate")).toBe("fin:CouponRate");
  });
});

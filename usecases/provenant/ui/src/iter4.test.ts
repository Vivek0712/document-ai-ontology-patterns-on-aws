/**
 * v2 data on the mock world: the per-package shards load on demand, and the new routes (access decisions,
 * SC-7/SC-10 composers, cost, eval, ontology, graph neighbourhood) serve the real handlers' exported responses with
 * the same access checks as the API.
 */
import { readdirSync, readFileSync } from "node:fs";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { RunSummary } from "@contracts/api";
import { RpApiError } from "./api/types";
import { MockApi } from "./mocks/server";
import type { World } from "./mocks/world";
import { api, hasWorld, MOCK, stubFixtures, world } from "./testing/storyHarness";

const LENDING = "pkg-lending-01";
const core = (): World => JSON.parse(readFileSync(new URL("mock/world.json", MOCK), "utf8")) as World;

beforeAll(stubFixtures);
afterAll(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe.skipIf(!hasWorld)("per-package lazy mock world", () => {
  it("world.json is the core: units live in the shards, fetched the first time a route needs them", async () => {
    const w = core();
    expect(Object.keys(w.shards ?? {}).sort()).toEqual(w.packages.map((p) => p.package.packageId).sort());
    expect((w as unknown as Record<string, unknown>).units).toBeUndefined();
    const spy = vi.fn(fetch);
    vi.stubGlobal("fetch", spy);
    const mock = new MockApi(w, "a", "mock", Date.now() - 60 * 60_000);
    const doc = w.packages.find((p) => p.package.packageId === LENDING)!.documents[0].docId;
    const list = await mock.listUnits(doc, "2.3.1");
    expect(list.items.length).toBeGreaterThan(0);
    await mock.getUnit(list.items[0].unitId);
    const shardFetches = spy.mock.calls.map(([u]) => String(u)).filter((u) => u.includes("/packages/"));
    expect(shardFetches).toEqual([`/mock/packages/${LENDING}.json`]); // once, and only the package asked for
    stubFixtures();
  });
});

describe.skipIf(!hasWorld)("v2 routes on the mock world", () => {
  const runsOf = (pred: (r: RunSummary) => boolean) => world().runSummaries.filter(pred);

  it("access decisions: what the gateway injected per tool call, behind the run's package ACL", async () => {
    const frontier = runsOf((r) => r.packageId === LENDING && r.generationProfile === "frontier")[0];
    const page = await api().getAccessDecisions(frontier.runId);
    expect(page.items.length).toBeGreaterThan(0);
    for (const d of page.items) expect(d.injected).toMatchObject({ packageId: LENDING, runId: frontier.runId });
    await expect(api("b").getAccessDecisions(frontier.runId)).rejects.toMatchObject({ status: 403 });
  });

  it("SC-10: the bigger model's runs beside the lane's own, and only the model id differs", async () => {
    const q = world().headlineQuestion;
    const p = await api().getShowcasePayload<{ profiles: Record<string, { n: number; modelIds: string[] }>; configDiff: { differing: { key: string }[] } }>(
      "sc-10",
      { packageId: LENDING, question: q, lane: "1.0" },
    );
    expect(p.data.profiles.default.n).toBeGreaterThan(0);
    expect(p.data.profiles.frontier.n).toBeGreaterThan(0);
    expect(p.data.configDiff.differing.map((d) => d.key)).toEqual(["modelId"]);
  });

  it("SC-7: User B's region request answers 403 as the region handler did; B's ask is refused at the package", async () => {
    const q = world().headlineQuestion;
    const p = await api().getShowcasePayload<{ regionProbe: { unitId: string; fieldId?: string } }>("sc-7", { packageId: LENDING, question: q });
    const { unitId, fieldId } = p.data.regionProbe;
    await expect(api("a").getRegion(unitId, fieldId)).resolves.toMatchObject({ unitId });
    const err = await api("b").getRegion(unitId, fieldId).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(RpApiError);
    expect(err).toMatchObject({ status: 403, message: "You don't have access to this region." });
    expect(Object.values(world().askChecks?.[LENDING] ?? {}).every((c) => c.status === 403)).toBe(true);
  });

  it("an unpublished export holds no published view: no dollar amount or rate in any mock-public file", () => {
    const dir = new URL("../mock-public/mock/", import.meta.url);
    const files = ["world.json", ...readdirSync(new URL("packages/", dir)).map((f) => `packages/${f}`)];
    for (const f of files) {
      const text = readFileSync(new URL(f, dir), "utf8");
      expect(text, f).not.toMatch(/"estimateUsd":\s*-?\d/);
      expect(text, f).not.toMatch(/"unitPriceUsd":\s*-?\d/);
      expect(text, f).not.toMatch(/"rate":\s*-?\d/);
    }
    const w = JSON.parse(readFileSync(new URL("world.json", dir), "utf8")) as { evalRuns?: { list: Record<string, unknown> } };
    expect(Object.keys(w.evalRuns?.list ?? {})).toEqual(["off"]);
  });

  it("cost and eval are unpublished by default: usage and outcomes only, no dollar amount or rate", async () => {
    const c = await api().getCost(LENDING);
    expect(c.published).toBe(false);
    expect(JSON.stringify(c)).not.toMatch(/estimateUsd|unitPriceUsd/);
    expect(c.lines.some((l) => l.stageId === "ask" && l.usage.measured)).toBe(true);
    const list = await api().listEvalRuns();
    const run = await api().getEvalRun(list.items[0].evalRunId);
    expect(run.published).toBe(false);
    expect(run.lanes).toBeUndefined();
    expect(JSON.stringify(run)).not.toMatch(/"rate"/);
    expect(run.caption).toBe("Measured on our constructed corpus. Your documents will differ.");
    await expect(api("b").getEvalRun(run.evalRunId)).rejects.toMatchObject({ status: 403 });
  });

  it("ontology and graph: the built TBox, and the neighbourhood of a concept in the package's graph", async () => {
    const fin = await api().listOntologyClasses("fin");
    expect(fin.items.every((t) => t.module === "fin")).toBe(true);
    const box1 = await api().getOntologyTerm("fin:W2WagesBox1");
    expect(box1.labels.find((l) => l.value === "Box 1")?.triple).toBe('fin:W2WagesBox1 skos:altLabel "Box 1"');
    expect(box1.usedBy?.[LENDING]).toBeGreaterThan(0);
    const g = await api().getGraphNeighbourhood("fin:W2WagesBox1", LENDING);
    expect(g.nodes.some((n) => n.kind === "field")).toBe(true);
    const none = await api().getGraphNeighbourhood("fin:W2WagesBox1", LENDING, 1, "1.0");
    expect(none.nodes).toEqual([]);
  });
});

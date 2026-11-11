import { existsSync, readFileSync } from "node:fs";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { AskEvent } from "@contracts/api";
import { parseSse, STAGE_ORDER } from "@contracts/api";
import { HttpApi } from "./api/http";
import { RpApiError } from "./api/types";
import { ms, projectLane, shiftTimes } from "./mocks/clock";
import { MockApi } from "./mocks/server";
import { MOCK_MODEL_ID, laneRunStats } from "./api/runs";
import type { World } from "./mocks/world";
import { fullWorld } from "./testing/worldFiles";
import { duration, maskLongDigits, mmss, money } from "./lib/format";
import { doclingBlocks, textractBlocks, toPixels } from "./lib/geometry";
import { emptyAsk, reduceAsk, retrievedOf } from "./features/workbench/screens/AskScreen";

const hasWorld = existsSync(new URL("../mock-public/mock/world.json", import.meta.url));
if (!hasWorld && process.env.RP_ALLOW_MISSING_FIXTURES !== "1") throw new Error("mock-public/mock is missing: run `make mock-world` (or set RP_ALLOW_MISSING_FIXTURES=1)");
const worldText = hasWorld ? JSON.stringify(fullWorld()) : "{}";
const world = (): World => JSON.parse(worldText) as World;
const LENDING = "pkg-lending-01";

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

/** The gate cohort's rows of the run index (default generation profile, User A): the N of N the UI counts. */
const gateRows = <T extends { generationProfile?: string; caller?: string }>(rows: T[]) => rows.filter((r) => (r.generationProfile ?? "default") === "default" && (r.caller ?? "a") === "a");

describe("format", () => {
  it("formats timers, durations, money and masks long digit runs", () => {
    expect(mmss(48_900)).toBe("00:48");
    expect(duration(870)).toBe("870 ms");
    expect(duration(11_000)).toBe("11.0 s");
    expect(money("169650.00")).toBe("169,650.00");
    expect(maskLongDigits("acct 90021873364512")).toBe("acct ••••••••••4512");
  });
});

describe("geometry", () => {
  it("scales canonical bboxes without flipping", () => {
    expect(toPixels({ page: 1, x0: 50, y0: 216, x1: 306, y1: 284 }, 612, 1224)).toEqual({ x: 100, y: 432, w: 512, h: 136 });
  });
  it("converts Docling BOTTOMLEFT and Textract normalised boxes to top-left PDF points", () => {
    const d = doclingBlocks({
      pages: { "1": { size: { width: 612, height: 792 } } },
      texts: [{ self_ref: "#/texts/0", label: "title", prov: [{ page_no: 1, bbox: { l: 50, t: 735, r: 476, b: 702, coord_origin: "BOTTOMLEFT" } }] }],
    });
    expect(d[0].bbox).toEqual({ page: 1, x0: 50, y0: 57, x1: 476, y1: 90 });
    expect(d[0].role).toBe("title");
    const t = textractBlocks({ Blocks: [{ BlockType: "CELL", Id: "x", Geometry: { BoundingBox: { Left: 0.5, Top: 0.25, Width: 0.25, Height: 0.125 } } }] }, 1);
    expect(t[0].bbox).toEqual({ page: 1, x0: 306, y0: 198, x1: 459, y1: 297 });
  });
});

describe("SSE", () => {
  it("parses frames across chunk boundaries", () => {
    const a = parseSse('data: {"type":"text","delta":"he"}\n\ndata: {"type":"te');
    expect(a.events).toEqual([{ type: "text", delta: "he" }]);
    const b = parseSse(a.rest + 'xt","delta":"llo"}\n\n');
    expect(b.events).toEqual([{ type: "text", delta: "llo" }]);
  });

  it.skipIf(!hasWorld)("HttpApi.ask streams events with the access token", async () => {
    const frames = world().asks["2.3.1"].map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
    const bytes = new TextEncoder().encode(frames);
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (let i = 0; i < bytes.length; i += 333) c.enqueue(bytes.slice(i, i + 333));
        c.close();
      },
    });
    const fetchMock = vi.fn(async () => new Response(body, { status: 200, headers: { "Content-Type": "text/event-stream" } }));
    vi.stubGlobal("fetch", fetchMock);
    const api = new HttpApi("https://api.example/v1", async () => "tok");
    const got: AskEvent[] = [];
    await api.ask({ question: "q", packageId: LENDING, pipelineVersion: "2.3.1" }, { onEvent: (e) => got.push(e) });
    expect(got.map((e) => e.type)).toEqual(world().asks["2.3.1"].map((e) => e.type));
    const [, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer tok");
  });

  it("HttpApi maps ApiError bodies", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "forbidden", message: "no", requestId: "r1" } }), { status: 403 })));
    const api = new HttpApi("https://api.example/v1", async () => "tok");
    await expect(api.getRegion("a:2.3.1:eu-01")).rejects.toMatchObject({ status: 403, code: "forbidden", requestId: "r1" });
  });
});

describe.skipIf(!hasWorld)("ask reducer (the logged local runs)", () => {
  const toolSeq = (events: AskEvent[]) => events.flatMap((e) => (e.type === "tool_start" ? [e.tool] : []));

  it("2.3.1: the answer, check and trace come from the logged run; claims open a region; fusion is 0.7/0.3", () => {
    const events = world().asks["2.3.1"];
    const s = events.reduce(reduceAsk, emptyAsk());
    const answer = events.find((e) => e.type === "answer");
    const check = events.find((e) => e.type === "check");
    expect(s.done).toBe(true);
    expect(s.answer?.total).toBe(answer?.type === "answer" ? answer.total : undefined);
    expect(s.check?.match).toBe(check?.type === "check" ? check.match : undefined);
    expect(s.calls.map((c) => c.start.tool)).toEqual(toolSeq(events));
    if (s.check?.match) expect(s.answer?.claims.every((c) => c.resolvable && c.bbox)).toBe(true);
    expect(retrievedOf(s).length).toBeGreaterThan(0);
    for (const r of retrievedOf(s)) expect(r.scores.fused).toBeCloseTo(0.7 * r.scores.denseNorm + 0.3 * r.scores.bm25Norm, 2);
  });

  it("1.0: chunk claims never open a region, whatever the total", () => {
    const s = world().asks["1.0"].reduce(reduceAsk, emptyAsk());
    expect(s.done).toBe(true);
    expect(s.check).toBeDefined();
    expect(s.answer?.claims.length).toBeGreaterThan(0);
    expect(s.answer?.claims.every((c) => !c.resolvable && c.chunkLabel)).toBe(true);
  });

  it("the headline replay is the representative run of the N-of-N summary", () => {
    const w = world();
    for (const pv of ["1.0", "2.3.1"]) {
      const start = w.asks[pv][0];
      const row = gateRows(w.localRuns?.summary ?? []).find((r) => r.qid === "q-lend-01" && r.pipelineVersion === pv);
      expect(start.type === "run_start" && start.runId).toBe(row?.representativeRunId);
      expect(row!.n).toBeGreaterThanOrEqual(1);
    }
  });
});

describe.skipIf(!hasWorld)("mock clock", () => {
  it("replays a recorded lane: PENDING -> RUNNING -> SUCCEEDED; 1.0 graph SKIPPED with a reason", () => {
    const w = world();
    const doc = w.packages[0].documents.find((d) => d.fileName === "bank_statement_2026-09.pdf")!;
    const lane = w.lanes[doc.docId]["2.3.1"];
    const ex = lane.stages.find((s) => s.stageId === "extract")!;
    const before = projectLane(lane, ms(lane.lane.startedAt) - 1);
    expect(before.stages.every((s) => s.state === "PENDING")).toBe(true);
    const mid = projectLane(lane, (ms(ex.startedAt) + ms(ex.endedAt)) / 2);
    const midEx = mid.stages.find((s) => s.stageId === "extract")!;
    expect(midEx.state).toBe("RUNNING");
    expect(midEx.artifacts).toEqual([]);
    expect(mid.lane.state).toBe("RUNNING");
    // mark_ready closes the lane record a few ms before its own stage record: wait for the last event
    const last = Math.max(ms(lane.lane.endedAt), ...lane.events.map((e) => ms(e.at)));
    const end = projectLane(lane, last + 1);
    expect(end.stages.every((s) => s.state === "SUCCEEDED")).toBe(true);
    expect(end.lane.state).toBe("SUCCEEDED");
    const naive = projectLane(w.lanes[doc.docId]["1.0"], ms(w.lanes[doc.docId]["1.0"].lane.endedAt) + 1);
    const graph = naive.stages.find((s) => s.stageId === "graph")!;
    expect(graph.state).toBe("SKIPPED");
    expect(graph.skipReason).toBeTruthy();
  });

  it("shiftTimes moves only timestamp fields", () => {
    const out = shiftTimes({ at: "2026-11-16T17:00:00.000Z", startedAt: "2026-11-16T17:00:01.000Z", key: "2026-11-16T17:00:00.000Z" }, 1000);
    expect(out).toEqual({ at: "2026-11-16T17:00:01.000Z", startedAt: "2026-11-16T17:00:02.000Z", key: "2026-11-16T17:00:00.000Z" });
  });
});

describe.skipIf(!hasWorld)("fixtures match the contracts", () => {
  it("every lane has all ten stage records in order, with config sha and versions on run stages", () => {
    const w = world();
    for (const [docId, lanes] of Object.entries(w.lanes)) {
      for (const [pv, l] of Object.entries(lanes)) {
        expect(l.stages.map((s) => s.stageId), `${docId}@${pv}`).toEqual(STAGE_ORDER);
        for (const s of l.stages) if (s.state === "SUCCEEDED") expect(s.versions?.configSha).toBe(l.lane.configSha);
      }
    }
  });
  it("held units never reach Gold, the index or a region", () => {
    const w = world();
    const held = Object.values(w.units).filter((u) => u.held);
    expect(held.length).toBeGreaterThan(0);
    for (const u of held) {
      expect(u.unit.text).toBe("[held: PII]");
      expect(u.status?.goldKey).toBeUndefined();
      expect(w.regions[u.unit.unitId]).toBeUndefined();
    }
  });
  it("the world is the real local run: sha256 of the uploaded bytes, no hand-made ids", () => {
    const w = world();
    expect(w.generatedBy).toBe("scripts/mock_world.py");
    for (const d of w.packages[0].documents) expect(d.sha256).toMatch(/^[0-9a-f]{64}$/);
    expect(worldText).not.toMatch(/pkg-ops-sandbox|f-ytd-gross/);
  });
  it("config diff: only preprocessing differs; prompt and model identical", () => {
    const d = world().diff;
    expect(d.changedBlocks).toEqual(["preprocessing"]);
    expect(d.promptSha.equal && d.modelId.equal).toBe(true);
  });
});

/** Serves mock-public/ the way Vite does (run records and large previews are fetched on demand). */
function serveMockPublic() {
  const root = new URL("../mock-public/", import.meta.url);
  vi.stubGlobal("fetch", async (url: string) => {
    const path = new URL(url.replace(/^\//, ""), root);
    return existsSync(path) ? new Response(readFileSync(path), { status: 200 }) : new Response("not found", { status: 404 });
  });
}

describe.skipIf(!hasWorld)("MockApi", () => {
  it("enforces package ACL for user B", async () => {
    const api = new MockApi(world(), "b");
    // user B (markets-analyst) sees exactly the packages whose ACL names that group, never the lending package
    const shared = world().packages.filter((p) => p.package.allowedPrincipals.includes("group:markets-analyst"));
    expect((await api.listPackages()).items.map((p) => p.packageId).sort()).toEqual(shared.map((p) => p.package.packageId).sort());
    await expect(api.getPackage(LENDING)).rejects.toBeInstanceOf(RpApiError);
    await expect(api.getRegion(Object.keys(world().regions)[0].split("|")[0])).rejects.toMatchObject({ status: 403, message: "You don't have access to this region." });
  });

  it("status goes live then ready; ask replays the recorded run", async () => {
    serveMockPublic();
    const w = world();
    const api = new MockApi(w, "a");
    const s0 = await api.packageStatus(LENDING);
    expect(s0.askReady).toBe(false);
    api.fastForward();
    const s1 = await api.packageStatus(LENDING);
    expect(s1.askReady).toBe(true);
    expect(s1.objectsInBronze).toBe(w.packages[0].documents.length);
    const held = Object.values(w.units).filter((u) => u.held && u.unit.pipelineVersion === "2.3.1" && u.unit.packageId === LENDING).length;
    expect(s1.counters["2.3.1"].held).toBe(held);
    const events: AskEvent[] = [];
    await api.ask({ question: "What is the total income across this package?", packageId: LENDING, pipelineVersion: "2.3.1" }, { onEvent: (e) => events.push(e) });
    // a golden question replays its logged run under that run's own id; the run store does not grow
    const run = events.find((e) => e.type === "run");
    const logged = w.asks["2.3.1"][0];
    expect(run && run.type === "run" && run.runId).toBe(logged.type === "run_start" ? logged.runId : "missing");
    const rec = await api.getRun((run as { runId: string }).runId);
    const answer = w.asks["2.3.1"].find((e) => e.type === "answer");
    expect(rec.answer?.total).toBe(answer?.type === "answer" ? answer.total : "missing");
    expect(rec.createdAt).toBe(w.runSummaries.find((r) => r.runId === rec.runId)!.createdAt); // the recorded time, not shifted
    expect((await api.listRuns(LENDING)).items.length).toBe(w.runSummaries.filter((r) => r.packageId === LENDING).length);
  }, 20_000);

  it("the keyword stand-in is labelled as no model and is never counted as a lane run", async () => {
    serveMockPublic();
    const w = world();
    const api = new MockApi(w, "a");
    api.fastForward();
    const events: AskEvent[] = [];
    await api.ask({ question: "Which employer pays Maya Fictus?", packageId: LENDING, pipelineVersion: "2.3.1", toolsDisabled: ["graph_hop"] }, { onEvent: (e) => events.push(e) });
    const run = events.find((e) => e.type === "run");
    expect(run && run.type === "run" && [run.modelId, run.promptSha, run.configSha, Object.keys(run.usage).length]).toEqual([MOCK_MODEL_ID, "", "", 0]);
    const rec = await api.getRun((run as { runId: string }).runId);
    expect(rec.modelId).toBe(MOCK_MODEL_ID);
    expect(rec.usage).toEqual({});
    expect(rec.toolCalls.every((c) => c.ms < 600)).toBe(true); // wall clock, not a typed constant
    const summaries = (await api.listRuns(LENDING)).items;
    for (const p of w.pipelines.items) expect(laneRunStats(summaries, "q-lend-01", p.pipelineVersion, p.configSha).runs.map((r) => r.runId)).not.toContain(rec.runId);
  }, 20_000);

  it("stage overlay: engine blocks, units + fields, held regions; 1.0 extract explains why it has none", async () => {
    serveMockPublic();
    const api = new MockApi(world(), "a");
    api.fastForward();
    const docs = (await api.packageStatus(LENDING)).docs;
    const w2 = docs.find((d) => d.fileName.startsWith("w2_2025_maya"))!.docId;
    const bank = docs.find((d) => d.fileName.startsWith("bank_statement"))!.docId;
    const ext = await api.getOverlay(w2, "2.3.1", "extract", 1);
    expect(ext.boxes.length).toBeGreaterThan(0);
    expect(ext.boxes.every((b) => b.kind === "block")).toBe(true);
    const units = await api.getOverlay(w2, "2.3.1", "units", 1);
    expect(units.boxes.some((b) => b.kind === "field" && b.ref === "f-box1")).toBe(true);
    const gate = await api.getOverlay(bank, "2.3.1", "pii_gate", 1);
    expect(gate.boxes.some((b) => b.kind === "held")).toBe(true);
    const naive = await api.getOverlay(w2, "1.0", "extract", 1);
    expect(naive.boxes).toEqual([]);
    expect(naive.note).toMatch(/no regions/);
  }, 20_000);

  it("upload creates a document with the same docId scheme as ids.doc_id_for and starts both lanes", async () => {
    const w = world();
    const api = new MockApi(w, "a");
    const pkg = await api.createPackage({ name: "Test pkg", shareWithGroups: ["lending-analyst"] });
    const file = new File([new Uint8Array(2048)], "w2_2025_maya_fictus.pdf", { type: "application/pdf" });
    const h = await api.upload(pkg.packageId, file, () => undefined);
    await h.done;
    const st = await api.packageStatus(pkg.packageId);
    expect(st.docs).toHaveLength(1);
    expect(Object.keys(st.docs[0].lanes)).toEqual(["1.0", "2.3.1"]);
    // docId scheme parity with Python: same (package, file) as a fixture doc gives the fixture docId
    const seeded = await (api as unknown as { requestUploadUrl(p: string, f: File): Promise<{ docId: string }> }).requestUploadUrl(LENDING, file);
    expect(seeded.docId).toBe(w.packages[0].documents.find((d) => d.fileName === file.name)!.docId);
    api.fastForward();
    const units = await api.listUnits(h.docId, "2.3.1");
    expect(units.items.length).toBeGreaterThan(0);
    expect(units.items[0].unitId.startsWith(`${h.docId}:2.3.1:`)).toBe(true);
  }, 20_000);

  it("asking another golden question replays its logged run on that lane", async () => {
    serveMockPublic();
    const w = world();
    const replay = Object.values(w.askReplays ?? {}).find((r) => r.qid !== "q-lend-01" && r.lanes["2.3.1"]);
    if (!replay) return; // no other logged questions yet
    const api = new MockApi(w, "a");
    api.fastForward();
    const events: AskEvent[] = [];
    await api.ask({ question: replay.question, packageId: LENDING, pipelineVersion: "2.3.1" }, { onEvent: (e) => events.push(e) });
    const want = replay.lanes["2.3.1"].find((e) => e.type === "answer");
    const got = events.find((e) => e.type === "answer");
    expect(got && got.type === "answer" && got.answer).toBe(want && want.type === "answer" && want.answer);
  }, 20_000);
});

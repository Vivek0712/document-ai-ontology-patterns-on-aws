/**
 * Storyboards on the real mock world: SC-0 Story tour, SC-9 Follow the number, SC-6 PII gate, and the
 * composers behind them (trace, PII view, logged-run stats). The API is the dev MockApi over world.json (the
 * real rp-api responses over the local run) with the X-ray export served from mock-public, so every assertion
 * reads its expectation from the same data the UI replays. Each storyboard renders every step at 0, 0.5 and 1.
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { coaOf } from "./api/governance";
import { composePii } from "./api/pii";
import { type RunScore, goldRegionOf, laneRunStats, loggedRuns, scoreOf } from "./api/runs";
import { composeTrace } from "./api/trace";
import { emptyAsk, reduceAsk, stateOfRun } from "./features/workbench/screens/AskScreen";
import { captionOf, type Storyboard } from "./showcase/engine/types";
import { SHOWCASES } from "./showcase/catalog";
import { type Sc0Payload, beforeCaption, definitionCaption, matchLine, sc0StoryTour } from "./showcase/stories/sc0";
import { hypothesisOf } from "./showcase/stories/sc1-payload";
import { type Sc6Payload, indexCaption, sc6PiiGate } from "./showcase/stories/sc6-pii-gate";
import { type Sc9Payload, sc9FollowTheNumber } from "./showcase/stories/sc9-follow-the-number";
import { GateFlow } from "./viz/GateFlow";
import { api, commonChecks, hasWorld, loggedRun, render, stubFixtures, world } from "./testing/storyHarness";
import { coaSnapshotFile } from "./testing/coaSnapshot";

const LENDING = "pkg-lending-01";
const QID = "q-lend-01";
const LOSS = /(?:stroke|fill)="var\(--loss\)"|aria-label="wrong"/;
/** A step's index by its id (tests never address steps by position). */
const stepIndex = (id: string) => sc0StoryTour.steps.findIndex((s) => s.id === id);

beforeAll(stubFixtures);
afterAll(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** The gate cohort's rows of the run index (default generation profile, User A): the N of N the UI counts. */
const gateRows = <T extends { generationProfile?: string; caller?: string }>(rows: T[]) => rows.filter((r) => (r.generationProfile ?? "default") === "default" && (r.caller ?? "a") === "a");

// --------------------------------------------------------------------------------------------- composers

describe.skipIf(!hasWorld)("logged runs (N of N)", () => {
  it("for every golden question and lane, counts and the representative run equal the harness's run index", async () => {
    const w = world();
    const rows = gateRows(w.localRuns!.summary);
    expect(rows.length).toBeGreaterThan(2);
    for (const qid of new Set(rows.map((r) => r.qid))) {
      const pkg = rows.find((r) => r.qid === qid)?.packageId ?? LENDING;
      const stats = await loggedRuns(api(), pkg, qid, ["1.0", "2.3.1"]);
      for (const row of rows.filter((r) => r.qid === qid)) {
        const s = stats[row.pipelineVersion];
        expect([qid, row.pipelineVersion, s.n, s.scored, s.correct, s.cited, s.representative?.runId ?? null]).toEqual([qid, row.pipelineVersion, row.n, row.n, row.correct, row.citationsValid, row.representativeRunId]);
        expect(s.scoredBy).toEqual(["eval"]);
      }
    }
  });
  it("where a run has both, the agent's golden check agrees with the eval harness's score", () => {
    const w = world();
    const both = w.runSummaries.filter((r) => r.checkMatch !== undefined && w.runScores?.[r.runId]);
    expect(both.length).toBeGreaterThan(0);
    for (const r of both) expect(scoreOf(r, {}), r.runId).toMatchObject({ correct: w.runScores![r.runId].answerCorrect, cited: w.runScores![r.runId].citationValid });
  });
  it("a miss is explained only from the unit views: q-lend-04 is held by the PII gate on evidence units", async () => {
    const w = world();
    for (const row of gateRows(w.localRuns!.summary).filter((r) => r.goldRegion)) {
      const gold = Object.values(w.runScores ?? {}).find((s: RunScore) => s.qid === row.qid)!.gold!;
      const got = await goldRegionOf(api(), gold, row.pipelineVersion);
      expect(got, `${row.qid} ${row.pipelineVersion}`).toMatchObject(row.goldRegion!.state === "held" ? { state: "held", unitId: row.goldRegion!.unitId } : { state: "no-units" });
    }
  });
  it("the lanes of every logged run differ only in preprocessing (harness parity check)", () => {
    const parity = world().localRuns!.laneParity!;
    expect(parity.ok).toBe(true);
    expect(parity.checked).toEqual(expect.arrayContaining(["modelId", "promptSha", "toolsEnabled", "toolSpecSha", "blockShas.retrieval", "blockShas.generation"]));
    // runs logged before the tool specs were hashed cannot prove the specs matched: the index says so
    if (!parity.complete) expect(parity.unverified).toContain("toolSpecSha");
  });
  it("an agent error is counted beside N, never dropped (local run index errors)", async () => {
    const errs = gateRows(world().localRuns?.summary ?? []).filter((s) => (s.errors ?? 0) > 0);
    for (const row of errs) {
      const got = await api().getAskErrors(row.packageId!);
      expect(got[`${row.qid}|${row.pipelineVersion}`]).toBe(row.errors);
    }
    const ok = world().runSummaries.find((r) => r.status === "ok" && (r.generationProfile ?? "default") === "default")!;
    const failed = { ...ok, runId: "r-failed", status: "error" as const };
    const qid = Object.values(world().runScores ?? {})[0]?.qid ?? "";
    const scores = { ...world().runScores, "r-failed": world().runScores![ok.runId] } as Record<string, RunScore>;
    if (world().runScores?.[ok.runId]) expect(laneRunStats([ok, failed], world().runScores![ok.runId].qid, ok.pipelineVersion, ok.configSha, scores).errors).toBe(1);
    expect(qid).toBeTruthy();
  });
  it("runs on another config are not counted", () => {
    const runs = world().runSummaries;
    expect(laneRunStats(runs, QID, "2.3.1", "not-the-config").n).toBe(0);
  });
  it("a reopened run record is the state its event stream ends in", () => {
    const w = world();
    for (const pv of ["1.0", "2.3.1"]) {
      const streamed = w.asks[pv].reduce(reduceAsk, emptyAsk());
      const reopened = stateOfRun(loggedRun(streamed.run!.runId));
      expect(reopened.answer).toEqual(streamed.answer);
      expect(reopened.check).toEqual(streamed.check);
      expect(reopened.calls.map((c) => [c.start.tool, c.end?.ms])).toEqual(streamed.calls.map((c) => [c.start.tool, c.end?.ms]));
    }
  });
});

describe.skipIf(!hasWorld)("trace composer (W-08, SC-9)", () => {
  it("follows Maya's Box 1 to the Bronze bytes; the sha check is computed against register's", async () => {
    const w = world();
    const run = loggedRun(gateRows(w.localRuns!.summary).find((s) => s.qid === QID && s.pipelineVersion === "2.3.1")!.representativeRunId!);
    const claim = run.answer!.claims.find((c) => c.resolvable && c.label?.includes("Maya"))!;
    const t = await composeTrace(api(), { unitId: claim.unitId, fieldId: claim.fieldId, runId: run.runId });
    const doc = w.packages[0].documents.find((d) => d.docId === t.unit.unitId.split(":")[0])!;
    expect(t.field?.concept).toBe("fin:W2WagesBox1");
    expect(t.source.sha256).toBe(doc.sha256);
    expect(t.source.matchesRegister).toBe(true);
    expect(t.steps.map((s) => s.kind)).toEqual(["field", "unit", "blocks", "region", "page_image", "source", "index", "graph"]);
    expect(t.operand?.operands.map((o) => o.value).sort()).toEqual(run.answer!.claims.map((c) => c.value).sort());
    // codeSha, promptSha and modelId are present
    expect(t.pipeline.codeSha).toBeTruthy();
    expect(t.run?.promptSha).toBe(run.promptSha);
    expect(t.run?.modelId).toBe(run.modelId);
  });
  it("User B gets the API's 403, never a client-side decision", async () => {
    const w = world();
    const id = Object.keys(w.regions).find((k) => k.includes("|f-box1"))!.split("|")[0];
    await expect(composeTrace(api("b"), { unitId: id, fieldId: "f-box1" })).rejects.toMatchObject({ status: 403 });
  });
});

describe.skipIf(!hasWorld)("PII composer (W-12, SC-6)", () => {
  it("counts per lane equal the gate stage's own outputs; held units are never indexed or in the graph", async () => {
    const w = world();
    for (const pv of ["1.0", "2.3.1"]) {
      const v = await composePii(api(), LENDING, pv);
      const lendingDocs = w.packages.find((p) => p.package.packageId === LENDING)!.documents.map((d) => d.docId);
      const outputs = lendingDocs.map((id) => w.lanes[id]).map((l) => l?.[pv]?.stages.find((s) => s.stageId === "pii_gate")?.output as { units: number; held: number; passed: number } | undefined);
      expect(v.counters.held).toBe(outputs.reduce((s, o) => s + (o?.held ?? 0), 0));
      expect(v.counters.passed).toBe(outputs.reduce((s, o) => s + (o?.passed ?? 0), 0));
      for (const u of v.units.filter((x) => x.action === "hold")) {
        expect(u.indexed, u.unitId).toBe(false);
        expect(u.inGraph, u.unitId).not.toBe(true);
        expect(u.tier).toBe("silver");
      }
    }
  });
  it("carries no span text: no account number, no SSN, no unit text", async () => {
    const v = JSON.stringify(await composePii(api(), LENDING, "2.3.1"));
    expect(v).not.toMatch(/\b\d{12,17}\b/);
    expect(v).not.toMatch(/(?<![X\d])\d{3}-\d{2}-\d{4}\b/);
    expect(v).not.toMatch(/"text":/);
  });
});

// --------------------------------------------------------------------------------------------- SC-9

describe.skipIf(!hasWorld)("SC-9 Follow the number", () => {
  let cached: Promise<Sc9Payload> | undefined;
  const load = () => (cached ??= sc9FollowTheNumber.load({ api: api(), packageId: LENDING }).then((l) => l.payload));
  commonChecks(sc9FollowTheNumber, load);

  it("the upload step shows the sha comparison as computed", async () => {
    const p = await load();
    expect(render(sc9FollowTheNumber, p, 5, 1)).toContain(`data-verdict="${p.trace.source.matchesRegister ? "ok" : "fail"}"`);
  });
  it("contrast: the naive claim for the same figure has no region and stops at the chunk", async () => {
    const p = await load();
    expect(p.contrast?.claim.resolvable).toBe(false);
    expect(p.contrast?.claim.value).toBe(p.trace.claim.value);
    expect(p.contrast?.stops.map((s) => s.kind)).not.toContain("region");
    expect(p.contrast?.missing).toEqual(expect.arrayContaining(["field", "region", "page_image"]));
    const html = render(sc9FollowTheNumber, p, 7, 1);
    expect(html).toContain("no region");
    // the chunk is drawn off the page (a card), never as a tag placed on the page
    expect(html).toContain('data-testid="chunk-card"');
  });
});

// --------------------------------------------------------------------------------------------- SC-6

describe.skipIf(!hasWorld)("SC-6 PII gate", () => {
  let cached: Promise<Sc6Payload> | undefined;
  const load = () => (cached ??= sc6PiiGate.load({ api: api(), packageId: LENDING }).then((l) => l.payload));
  commonChecks(sc6PiiGate, load);

  it("resolves the held account header and a clean unit from the same page", async () => {
    const p = await load();
    expect(p.doc.docType).toBe("doc:BankStatement");
    expect(p.held.action).toBe("hold");
    expect(p.held.hits.some((h) => h.type === "BANK_ACCOUNT_NUMBER" && !h.masked && h.digits >= 8)).toBe(true);
    expect(p.clean.action).toBe("pass");
    expect(p.clean.pages).toContain(p.held.pages[0]);
    expect([p.held.indexed, p.held.inGraph]).toEqual([false, false]);
    expect([p.clean.indexed, p.clean.inGraph]).toEqual([true, true]);
    expect(p.page.heldRegions.length).toBeGreaterThan(0);
    expect(p.heldChunk?.action).toBe("hold");
  });
  it("headline steps use only hold and pass; Silver keeps every unit, the held one is held there", async () => {
    const p = await load();
    const flowSteps = sc6PiiGate.steps.map((s, i) => [s.id, i] as const).filter(([id]) => id !== "gold-page");
    for (const [id, step] of flowSteps) {
      for (const t of [0.5, 1]) {
        const html = render(sc6PiiGate, p, step, t);
        expect(html, id).not.toMatch(/below.threshold/i);
        if (id === "both-lanes") continue;
        for (const u of p.units["2.3.1"]) expect(html, `${id} @${t}: ${u.unitId} in Silver`).toMatch(new RegExp(`data-token="${u.unitId}" data-state="\\w+" data-column="silver"`));
      }
      // once the gate has decided, the held unit is held in Silver and no copy of it is anywhere else
      if (step < sc6PiiGate.steps.findIndex((s) => s.id === "held") || id === "both-lanes") continue;
      const end = render(sc6PiiGate, p, step, 1);
      expect(end).toContain(`data-token="${p.held.unitId}" data-state="held" data-column="silver"`);
      expect(end, `${id}: no held copy outside Silver`).not.toContain(`data-copy="${p.held.unitId}"`);
    }
    const clean = sc6PiiGate.steps.findIndex((s) => s.id === "clean");
    expect(render(sc6PiiGate, p, clean, 1)).toContain(`data-copy="${p.clean.unitId}" data-state="passed" data-column="gold"`);
  });
  it("the never-indexed caption follows the held unit's own index and graph rows", async () => {
    const p = await load();
    expect(indexCaption(p)).toBe("Held units never reach the index or the graph.");
    expect(indexCaption({ ...p, held: { ...p.held, indexed: true } })).not.toMatch(/never/);
    expect(indexCaption({ ...p, held: { ...p.held, inGraph: true } })).not.toMatch(/never/);
  });
  it("GateFlow: the last row's token and note sit inside the figure, compact or not", () => {
    for (const compact of [false, true]) {
      const tokens = Array.from({ length: 3 }, (_, i) => ({ id: `t${i}`, label: "unit", state: "held" as const, note: createElement("span", null, "a", createElement("br"), "b") }));
      const html = renderToString(createElement(GateFlow, { docs: [], tokens, compact }));
      const body = Number(html.match(/data-body-height="(\d+)"/)![1]);
      const tops = [...html.matchAll(/top:(\d+)px/g)].map((m) => Number(m[1]));
      // token 36 px + gap 4 px + a two-line note (2 × 16 px)
      expect(Math.max(...tops) + 36 + 4 + 32, `compact=${compact}`).toBeLessThanOrEqual(body);
    }
  });
  it("no span text anywhere in the payload", async () => {
    const json = JSON.stringify(await load());
    expect(json).not.toMatch(/\b\d{12,17}\b/);
    expect(json).not.toMatch(/(?<![X\d])\d{3}-\d{2}-\d{4}\b/);
  });
});

// --------------------------------------------------------------------------------------------- SC-0

describe.skipIf(!hasWorld)("SC-0 Story tour", () => {
  let cached: Promise<Sc0Payload> | undefined;
  const load = () => (cached ??= sc0StoryTour.load({ api: api(), packageId: LENDING }).then((l) => l.payload));
  commonChecks(sc0StoryTour, load);

  it("before and after come from the logged runs, with N of N", async () => {
    const p = await load();
    const w = world();
    for (const pv of ["1.0", "2.3.1"] as const) {
      const row = gateRows(w.localRuns!.summary).find((r) => r.qid === QID && r.pipelineVersion === pv)!;
      expect(p.runs.runs[pv].runId).toBe(row.representativeRunId);
      expect(p.runs.stats[pv].n).toBe(row.n);
    }
  });
  it("the 'before' caption follows the check and its stability", async () => {
    const p = await load();
    const s = p.runs.stats["1.0"];
    const naive = p.runs.runs["1.0"];
    const withCheck = (match: boolean, correct: number): Sc0Payload => ({
      ...p,
      runs: { ...p.runs, stats: { ...p.runs.stats, "1.0": { ...s, correct } }, runs: { ...p.runs.runs, "1.0": { ...naive, check: { ...naive.check!, match } } } },
    });
    expect(beforeCaption(withCheck(false, 0))).toBe("Naive pipeline: a total with no region.");
    expect(beforeCaption(withCheck(true, s.scored - 1))).toBe("Naive pipeline: right this time, still no region.");
    expect(beforeCaption(withCheck(true, s.scored))).toBe(`Naive pipeline: right in ${s.scored} of ${s.scored} logged runs, still no region.`);
  });
  it("the 'before' answers read the same way in both lanes: operand chips, ✓ region against × no region", async () => {
    const p = await load();
    const html = render(sc0StoryTour, p, stepIndex("after"), 1);
    for (const c of [...p.runs.runs["1.0"].answer!.claims, ...p.runs.runs["2.3.1"].answer!.claims]) expect(c.value).toBeTruthy();
    expect(html.match(/: no region"/g)?.length ?? 0).toBe(p.runs.runs["1.0"].answer!.claims.length);
    // the evidence-units card is a solid placeholder before it is asked (dashed means "no region")
    const before = render(sc0StoryTour, p, stepIndex("before"), 1);
    expect(before.match(/<section[^>]*data-lane="2\.3\.1"[^>]*>/)?.[0]).not.toMatch(/border-dashed/);
  });
  it("'why' says the reading order is constructed; the positional arc is × only when the logged answer used it", async () => {
    const p = await load();
    const why = stepIndex("why");
    expect(render(sc0StoryTour, p, why, 1)).toContain("The generator writes this order on purpose");
    if (p.xray.hypothesis.state !== "confirmed") expect(render(sc0StoryTour, p, why, 1)).not.toMatch(LOSS);
    const pos = p.xray.box1.positionalValue!;
    const control = { ...p.xray.control, answerNumbers: [{ runId: "r-test", value: pos.text, matchedLines: [pos.ordinal], isPositionalValueOf: p.xray.box1.fieldId }] };
    const confirmed = { ...p, xray: { ...p.xray, control, hypothesis: hypothesisOf(control, p.xray.box1) } };
    expect(render(sc0StoryTour, confirmed, why, 1)).toMatch(LOSS);
    expect(matchLine(confirmed)).toContain(pos.text);
    // the X-ray's logged answer is the representative naive run
    expect(p.xray.control.sources.answerRun?.runId).toBe(p.runs.runs["1.0"].runId);
  });
  it("the sha followed in 'follow' is the sha the upload step shows", async () => {
    const p = await load();
    const doc = p.documents.find((d) => d.docId === p.trace.unit.unitId.split(":")[0])!;
    expect(p.trace.source.sha256).toBe(doc.sha256);
    expect(render(sc0StoryTour, p, stepIndex("upload"), 1)).toContain(`data-sha="${doc.sha256}"`);
    expect(render(sc0StoryTour, p, stepIndex("follow"), 1).match(/data-verdict="ok"/g)?.length).toBe(2);
  });
  it("the diff strip is ConfigDiff.summary; 'definition' is the Git card plus COA's recorded cross-check", async () => {
    const p = await load();
    expect(render(sc0StoryTour, p, stepIndex("after"), 1)).toContain(p.diff.summary);
    const def = render(sc0StoryTour, p, stepIndex("definition"), 1);
    expect(def).toContain(world().governance!.definition.description!);
    const coa = coaOf(p.definition);
    if (coa) {
      expect(def).toContain("Governed in Git · cross-checked in COA");
      expect(def).toContain('data-coa-crosscheck="match"');
      expect(def).toContain(`COA snapshot · captured ${coa.capturedAt}`);
      expect(coa.crossCheck.value).toBe(coaSnapshotFile().query.response.result.resultRows[0].qualifying_income);
      expect(definitionCaption(p)).toMatch(/recomputed by COA\.$/);
    }
    // with COA off (the Git fallback) the card says so and draws no cross-check
    const off = { ...p, definition: { ...p.definition!, coa: { mode: "off" as const, state: "not-captured" as const, note: "off" } } };
    const git = render(sc0StoryTour, off, stepIndex("definition"), 1);
    expect(git).toContain("Governed in Git · COA off");
    expect(git).not.toContain("data-coa-crosscheck");
    expect(definitionCaption(off)).toMatch(/governed in Git\.$/);
  });
  it("captions that state facts are functions of the payload: document count, definition or its absence", async () => {
    const p = await load();
    expect(captionOf(sc0StoryTour.steps[stepIndex("question")], p)).toContain(String(p.documents.length));
    expect(definitionCaption(p)).toContain(p.definition!.definition.label!);
    const missing = { ...p, definition: undefined, definitionError: "no route" };
    expect(definitionCaption(missing)).toBe("Governed definition unavailable on this stack.");
    expect(render(sc0StoryTour, missing, stepIndex("definition"), 1)).toContain("not available on this stack");
  });
});

describe("honesty rules for the storyboards", () => {
  const files = [
    "sc0/index.tsx",
    "sc0/load.ts",
    "sc0/captions.ts",
    "sc0/frames/board.tsx",
    "sc0/frames/canvas.tsx",
    "sc1-read-the-page.tsx",
    "sc1-payload.ts",
    "sc6-pii-gate.tsx",
    "sc9-follow-the-number.tsx",
    "lending.ts",
    "steps.ts",
    "panel.tsx",
    "subjects.ts",
    "sc2-evidence-units.tsx",
    "sc2-payload.ts",
    "sc3-footnote-property.tsx",
    "sc3-payload.ts",
    "sc4-filters.tsx",
    "sc4-payload.ts",
    "sc5-resolve-hop-cite.tsx",
    "sc5-payload.ts",
  ];
  // "one" is left out: "one unit" states a structure, not a count read from the data
  const SPELLED = /\b(two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\b/i;
  /** string literals and JSX text, without comments (what a viewer can read) */
  const visibleText = (src: string) =>
    src
      .replace(/\/\*[\s\S]*?\*\//g, "")
      .replace(/^\s*\/\/.*$/gm, "")
      .replace(/\{\/\*[\s\S]*?\*\/\}/g, "")
      .match(/"[^"\n]*"|`[^`]*`|>[^<>{}\n]+</g) ?? [];
  it.each(files)("%s types no data values", (f) => {
    const src = readFileSync(new URL(`./showcase/stories/${f}`, import.meta.url), "utf8");
    expect(src).not.toMatch(/\d{1,3}(,\d{3})+\.\d{2}/);
    expect(src).not.toMatch(/\b(98400|71250|169650|6600)\b/);
    expect(src).not.toMatch(/(?<![\w-])(?:eu|c|w)-\d{2}\b|827d37|7c2e0d/);
    expect(src).not.toMatch(/\bq-[a-z]+-\d+\b/);
    expect(src).not.toMatch(/\br-[0-9a-f]{12}\b/);
    // borrowers and document types are found through the *_SUBJECT constants, never written into copy
    const outsideSubjects = src.split("\n").filter((l) => !/_SUBJECT = /.test(l)).join("\n");
    for (const t of visibleText(outsideSubjects)) expect(t, `${f}: ${t}`).not.toMatch(/\b(Maya|Arjun|Fictus)\b|\bW-?2\b(?! ·| Box)|payslip|bank statement/i);
    for (const t of visibleText(src)) expect(t, `${f}: spelled-out count in ${t}`).not.toMatch(SPELLED);
  });
  it.skipIf(!hasWorld)("every live storyboard's captions spell out no counts", async () => {
    for (const [story, load] of [
      [sc0StoryTour, () => sc0StoryTour.load({ api: api(), packageId: LENDING })],
      [sc6PiiGate, () => sc6PiiGate.load({ api: api(), packageId: LENDING })],
      [sc9FollowTheNumber, () => sc9FollowTheNumber.load({ api: api(), packageId: LENDING })],
    ] as const) {
      const { payload } = await (load as () => Promise<{ payload: unknown }>)();
      for (const s of (story as Storyboard<unknown>).steps) expect(captionOf(s, payload), `${story.id} ${s.id}`).not.toMatch(SPELLED);
    }
  });
});

describe.skipIf(!hasWorld)("showcase catalog claims follow the logged runs", () => {
  it("no claim says the naive total is wrong unless a logged naive run got it wrong", async () => {
    const stats = await loggedRuns(api(), LENDING, QID, ["1.0"]);
    const naiveWrong = stats["1.0"].scored > stats["1.0"].correct;
    for (const s of SHOWCASES) if (!naiveWrong) expect(`${s.title} · ${s.claim}`, s.id).not.toMatch(/wrong (total|answer)|total stays wrong|gets? (it|the total) wrong/i);
  });
});

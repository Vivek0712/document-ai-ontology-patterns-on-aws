/**
 * Storyboards on the real mock world: SC-7 Identity and scoping, SC-8 Governed meaning (Git fallback),
 * SC-10 A bigger model, SC-11 Before and after (every example), SC-12 Evaluate, SC-13 Cost, SC-14 Three planes.
 * Each passes the common checks (steps match the palette, captions ≤ 90 characters quoting only payload numbers,
 * receipts on every step, pure frames, reduced motion = the end state) and its own honesty assertions. Expected
 * values are read from the same export the UI replays, never typed here.
 */
import { readFileSync } from "node:fs";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import { tally } from "./showcase/stories/compare";
import { showcaseMeta } from "./showcase/catalog";
import { type Storyboard, captionOf } from "./showcase/engine/types";
import { sc7Identity } from "./showcase/stories/sc7-identity";
import { GATE_LABEL, type Sc7Payload, refusalsOf, regionCaption, scopedCaption } from "./showcase/stories/sc7-payload";
import { sc8GovernedMeaning } from "./showcase/stories/sc8-governed-meaning";
import { sc10BiggerModel } from "./showcase/stories/sc10-bigger-model";
import { oneChangeCaption, resultsCaption } from "./showcase/stories/sc10-payload";
import { sc11BeforeAfter } from "./showcase/stories/sc11-before-after";
import { beforeCaption, xrayFindings } from "./showcase/stories/sc11-payload";
import { sc12Evaluate } from "./showcase/stories/sc12-evaluate";
import { type Sc8Payload, executorCaption, labelChecks } from "./showcase/stories/sc8-payload";
import { crossCheckLabel, governanceLabel } from "./api/governance";
import { formatNum } from "./design";
import { coaSnapshotFile } from "./testing/coaSnapshot";
import { sc13Cost } from "./showcase/stories/sc13-cost";
import { sc14ThreePlanes } from "./showcase/stories/sc14-three-planes";
import { api, commonChecks, hasWorld, render, stepOf, stubFixtures, world } from "./testing/storyHarness";

const LENDING = "pkg-lending-01";
const ctx = (extra: Record<string, unknown> = {}) => ({ api: api(), packageId: LENDING, peer: async (u: "a" | "b") => api(u), ...extra });

beforeAll(stubFixtures);
afterAll(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const load = {
  sc7: async () => (await sc7Identity.load(ctx())).payload,
  sc8: async () => (await sc8GovernedMeaning.load(ctx())).payload,
  sc10: async () => (await sc10BiggerModel.load(ctx())).payload,
  sc11: (ex: string) => async () => (await sc11BeforeAfter.load(ctx({ params: { ex } }))).payload,
  sc12: async () => (await sc12Evaluate.load(ctx())).payload,
  sc13: async () => (await sc13Cost.load(ctx())).payload,
  sc14: async () => (await sc14ThreePlanes.load(ctx())).payload,
};

describe.skipIf(!hasWorld)("SC-7 Identity and scoping", () => {
  commonChecks(sc7Identity, load.sc7);
  it("User B's region and package requests are refused by the API, A's region opens", async () => {
    const p = await load.sc7();
    expect(p.probes.regionA.state).toBe("ok");
    expect(p.probes.regionB).toMatchObject({ state: "refused", status: 403, message: "You don't have access to this region." });
    expect(p.probes.packageB).toMatchObject({ state: "refused", status: 403 });
    expect(captionOf(sc7Identity.steps[stepOf(sc7Identity, "region")], p)).toBe("The region service checks access again.");
  });
  it("without a second session the region step says so; it never simulates the 403", async () => {
    const p = (await sc7Identity.load(ctx({ peer: async () => undefined }))).payload;
    expect(p.probes.regionB.state).toBe("no-session");
    expect(regionCaption(p)).toMatch(/^Sign in as User B/);
    expect(render(sc7Identity, p, stepOf(sc7Identity, "region"), 1)).not.toContain("You don&#x27;t have access to this region.");
  });
  it("the scoped-retrieval caption follows User B's logged search calls (all three variants)", async () => {
    const p = await load.sc7();
    const b = p.data.users.find((u) => u.role === "other")!;
    const calls = Object.values(b.runs).flatMap((r) => r?.search ?? []);
    expect(scopedCaption(p)).toBe(calls.every((c) => c.status === "denied") ? "The search tool refuses User B: no access to this package." : "User B's filter matches nothing in this package.");
    const withUnits = structuredClone(p) as Sc7Payload;
    withUnits.data.users.find((u) => u.role === "other")!.runs["2.3.1"]!.search = [{ callId: "x", status: "ok", units: 3, index: "i", filter: {}, error: null }];
    expect(scopedCaption(withUnits)).toBe("User B retrieves 3 units; see the access log.");
  });
  it("a call the tool refused is never shown as allow: User B's rows are tool denials", async () => {
    const p = await load.sc7();
    const b = p.data.users.find((u) => u.role === "other")!;
    const rows = Object.values(b.runs).flatMap((r) => (r ? p.data.access[r.runId] ?? [] : []));
    expect(rows.length).toBeGreaterThan(0);
    for (const a of rows) expect(a.decision).toBe("deny");
    const html = render(sc7Identity, p, stepOf(sc7Identity, "injection"), 1);
    expect(html).toContain("tool denied");
    expect(html).not.toMatch(/>allow</);
  });
  it("subs read whole or as a marked prefix, never a bare 6-character cut", async () => {
    const p = await load.sc7();
    const html = render(sc7Identity, p, stepOf(sc7Identity, "users"), 1);
    expect(html).toContain(`sub <!-- -->${p.me.a!.sub}`);
    expect(html).not.toMatch(/sub (<!-- -->)?local-</);
  });
  it("on AWS User B has no run: the gate's logged refusals are B's evidence, labelled, never a made-up run", async () => {
    const p = await load.sc7();
    const gate = structuredClone(p) as Sc7Payload;
    const b = gate.data.users.find((u) => u.role === "other")!;
    const refusal = (pv: string, requestId: string, path: "api" | "runtime") => ({ at: "2026-11-11T12:00:00.000Z", requestId, path, target: path === "api" ? "rp-api-ask" : "rp-agent-runtime", code: "forbidden", reason: "You don't have access to this package.", decision: "deny" as const, pipelineVersion: pv, packageId: gate.data.packageId, questionId: null, questionSha: "s", label: GATE_LABEL, provenance: "measured" as const });
    Object.assign(b, { evidence: "gate-refusals", runs: {}, n: {}, refusals: [refusal("1.0", "req-b1", "api"), refusal("2.3.1", "sess-b2", "runtime")], refusedN: { "1.0": 1, "2.3.1": 1 } });
    expect(refusalsOf(b)).toHaveLength(2);
    expect(refusalsOf(gate.data.users.find((u) => u.role === "owner"))).toEqual([]);
    expect(scopedCaption(gate)).toBe("User B never reaches search: the gate refuses the ask first.");
    for (const step of ["same-question", "injection"] as const) {
      const html = render(sc7Identity, gate, stepOf(sc7Identity, step), 1);
      expect(html).toContain("refused at the gate, before any model call");
      expect(html).toContain("req-b1");
      expect(html).toContain("sess-b2");
      expect(html).toContain('data-provenance="measured"');
    }
    expect(render(sc7Identity, gate, stepOf(sc7Identity, "scoped"), 1)).toContain("the ask never reached the agent");
    const receipts = sc7Identity.steps[stepOf(sc7Identity, "same-question")].receipts!(gate);
    expect(receipts.filter((r) => r.label.startsWith("User B"))).toEqual([
      expect.objectContaining({ label: "User B · 1.0 · refused at the gate, before any model call", value: expect.stringContaining("request req-b1"), provenance: "measured" }),
      expect.objectContaining({ label: "User B · 2.3.1 · refused at the gate, before any model call", value: expect.stringContaining("AgentCore Runtime entrypoint") }),
    ]);
    expect(receipts.some((r) => r.label.startsWith("User B") && r.value === "no run")).toBe(false);
  });
  it("injected identity comes from the access rows, one per tool call of the run", async () => {
    const p = await load.sc7();
    for (const u of p.data.users) {
      const r = u.runs["2.3.1"]!;
      const rows = p.data.access[r.runId];
      expect(rows.length).toBe(r.toolCalls);
      for (const a of rows) expect(a.callerSub6).toBe(u.callerSub6);
    }
  });
});

describe.skipIf(!hasWorld)("SC-8 Governed meaning (COA snapshot, Git fallback when COA is off)", () => {
  commonChecks(sc8GovernedMeaning, load.sc8);
  const all = (p: Sc8Payload) => sc8GovernedMeaning.steps.map((_, i) => render(sc8GovernedMeaning, p, i, 1)).join("\n");
  /** The Git fallback: the same payload with the record's COA slot off (what `make ontology-check COA_MODE=off` writes). */
  const off = (p: Sc8Payload): Sc8Payload => ({ ...p, coa: undefined, definition: { ...p.definition, coa: { mode: "off", state: "not-captured", note: "COA is off in this build: the Git record governs the definition." } } });
  it("never says COA reviewed, approved or published the uploaded ontology; no slot says pending", async () => {
    const p = await load.sc8();
    for (const html of [all(p), all(off(p))]) {
      expect(html).not.toMatch(/(approved|reviewed|published) (by|in) COA/i);
      expect(html).not.toMatch(/pending/i);
    }
  });
  it("the COA snapshot fills the slots: metric card, cross-check row with trace chips, hashes, latency, token rule", async () => {
    const p = await load.sc8();
    const coa = coaSnapshotFile();
    expect(p.coa, "make ontology-check COA_MODE=snapshot && make mock-world").toBeDefined();
    const s = p.coa!;
    expect(s.capturedAt).toBe(coa.capturedAt);
    const html = all(p);
    expect(html).toContain(`COA snapshot · captured ${s.capturedAt}`);
    expect(html).toContain("uploaded, not steward-reviewed");
    expect(html.replace(/<[^>]+>/g, "")).toContain(s.metric.sql.split(" FROM ")[0]); // the recorded SQL, verbatim
    expect(html).toContain("set by COA");
    expect(html).not.toContain("&lt;EMAIL&gt;"); // the redacted principal is described, never shown as a placeholder
    const exec = render(sc8GovernedMeaning, p, stepOf(sc8GovernedMeaning, "executor"), 1);
    expect(exec).toContain('data-coa-crosscheck="match"');
    expect(exec).toContain(`${crossCheckLabel(s)}`);
    expect(exec).toContain(formatNum(s.crossCheck.value, "money"));
    for (const chip of ["metric_match", "authorize (cedar)", "firewall", "execute (athena)"]) expect(exec).toContain(chip);
    expect(exec).toContain("Measured · laptop → ");
    const text = html.replace(/<!-- -->/g, "").replace(/<[^>]+>/g, "");
    for (const h of s.hashes) expect(text).toContain(`sha256 ${h.sha256.slice(0, 12)}`);
    expect(render(sc8GovernedMeaning, p, stepOf(sc8GovernedMeaning, "discovery"), 1).replace(/&#x27;/g, "'")).toContain(s.access!.rule);
    expect(executorCaption(p)).toBe("Our template computes it; COA's governed metric recomputes the same total.");
  });
  it("honesty: the cross-check value is the recorded Tier-1 result, and the row follows the payload, not the code", async () => {
    const p = await load.sc8();
    const recorded = coaSnapshotFile().query.response.result.resultRows[0].qualifying_income;
    expect(p.coa!.crossCheck.value).toBe(recorded);
    expect(Number(recorded)).toBe(Number(p.hop!.total)); // COA recomputed the same total our template logged
    const edited = { ...p, coa: { ...p.coa!, crossCheck: { ...p.coa!.crossCheck, value: "1.00" } } };
    const exec = render(sc8GovernedMeaning, edited, stepOf(sc8GovernedMeaning, "executor"), 1);
    const row = exec.slice(exec.indexOf("data-coa-crosscheck"), exec.indexOf("COA resolver trace"));
    expect(row).toContain('data-coa-crosscheck="differs"');
    expect(row).toContain(">1.00<");
    expect(row).not.toContain(formatNum(recorded, "money"));
    expect(executorCaption(edited)).not.toMatch(/same total/);
    const otherPkg = { ...p, ours: { ...p.ours!, packageId: "pkg-other" } };
    expect(render(sc8GovernedMeaning, otherPkg, stepOf(sc8GovernedMeaning, "executor"), 1)).toContain('data-coa-crosscheck="not-compared"');
  });
  it("COA off keeps the Git fallback: no COA value, no cross-check row, every COA place says off", async () => {
    const p = off(await load.sc8());
    const html = all(p);
    expect(html).not.toContain("data-coa-crosscheck");
    expect(html).not.toContain("COA snapshot");
    expect(html).toContain("COA is off in this build");
    expect(executorCaption(p)).toBe("Our template computes it, with the field each operand came from.");
    expect(governanceLabel(p.definition)).toMatch(/· COA off$/);
  });
  it("the governance label follows the record: no commit says so; 'Reviewed in Git' only with a review", async () => {
    const p = await load.sc8();
    const label = governanceLabel(p.definition);
    if (!p.definition.git?.commit) expect(label).toBe(`Defined in Git · not committed · ${p.coa ? "COA cross-check (snapshot)" : "COA off"}`);
    expect(governanceLabel({ ...p.definition, review: undefined })).not.toMatch(/Reviewed/);
    expect(sc8GovernedMeaning.steps[stepOf(sc8GovernedMeaning, "review")].title).toBe("Checked in Git");
  });
  it("discovery shows the concept each label resolves to, the definition's own label marked", async () => {
    const p = await load.sc8();
    const checks = labelChecks(p);
    expect(checks.length).toBeGreaterThan(0);
    for (const c of checks) expect(c.concepts.length, c.label).toBeGreaterThan(0);
    const own = checks.filter((c) => c.concepts.includes(p.definition.definition.iri));
    expect(own.length).toBeGreaterThan(0);
    const html = render(sc8GovernedMeaning, p, stepOf(sc8GovernedMeaning, "discovery"), 1);
    expect((html.match(/data-resolves="definition"/g) ?? []).length).toBe(own.length);
  });
  it("the executor step shows the gate run's own rows: operands sum to the logged total", async () => {
    const p = await load.sc8();
    expect(p.hop?.template).toBe(p.definition.definition.computedBy);
    const sum = p.hop!.operands.reduce((s, o) => s + Number(o.value), 0);
    expect(sum.toFixed(2)).toBe(Number(p.hop!.total).toFixed(2));
  });
  it("binding: the evidence-units lane binds the ontology, the naive lane does not", async () => {
    const p = await load.sc8();
    const { bindingLines } = await import("./api/governance");
    expect(bindingLines(p.lanes.units.yaml).find((r) => r.key === "ontology")?.binds).toBe(true);
    expect(bindingLines(p.lanes.naive.yaml).every((r) => !r.binds)).toBe(true);
  });
});

describe.skipIf(!hasWorld)("SC-10 A bigger model", () => {
  commonChecks(sc10BiggerModel, load.sc10);
  it("only the model id differs between the representative records", async () => {
    const p = await load.sc10();
    expect(p.lead.configDiff?.differing.map((d) => d.key)).toEqual(["modelId"]);
  });
  it("results come from eval scores; the caption is the honest variant chosen from them", async () => {
    const p = await load.sc10();
    const f = tally(p.lead.profiles.frontier.dots);
    const scores = world().runScores ?? {};
    for (const d of p.lead.profiles.frontier.dots) expect(d.status === "correct").toBe(Boolean(scores[d.runId]?.answerCorrect));
    const cite = f.cited === 0 ? " It still cannot cite a region." : "";
    expect(resultsCaption(p)).toBe(f.correct ? `The bigger model got ${f.correct} of ${f.n} right.${cite}` : `The bigger model got none of ${f.n} right.${cite}`);
  });
  it("tool specs one cohort never logged are unverified, and step 1 says so instead of 'only the model'", async () => {
    const p = await load.sc10();
    const unverified = p.lead.configDiff?.unverified?.map((u) => u.key) ?? [];
    expect(p.lead.configDiff?.identical).not.toContain("toolSpecSha");
    if (unverified.includes("toolSpecSha")) expect(oneChangeCaption(p)).toBe("Same prompt and config; tool schema not recorded for the default runs.");
    else expect(oneChangeCaption(p)).not.toMatch(/not recorded/);
  });
  it("agent errors are dots of their own, never counted as wrong (q-lend2-01 frontier)", async () => {
    const p = await load.sc10();
    const errored = p.others.flatMap((o) => o.profiles.frontier.dots).filter((d) => d.status === "error");
    expect(errored.length).toBeGreaterThan(0);
    for (const o of p.others) expect(tally(o.profiles.frontier.dots).n).toBe(o.profiles.frontier.dots.filter((d) => d.status !== "error").length);
  });
});

describe.skipIf(!hasWorld)("SC-11 Before and after", () => {
  const examples = showcaseMeta("sc-11")!.examples!;
  for (const e of examples) {
    describe(e.code, () => commonChecks(sc11BeforeAfter, load.sc11(e.id)));
  }
  it("the switcher shows every example's outcome; the second lending package is the stable naive failure", async () => {
    const p = await load.sc11("lending-02")();
    expect(p.examples.map((o) => o.example.id)).toEqual(examples.map((e) => e.id));
    const naive = tally(p.runs.lanes["1.0"].dots);
    expect(naive.n).toBeGreaterThan(0);
    expect(naive.wrong).toBe(naive.n);
    expect(beforeCaption(p)).toBe(`Naive pipeline: wrong in all ${naive.n} logged runs.`);
    const html = render(sc11BeforeAfter, p, 0, 1);
    for (const ex of examples) expect(html).toContain(`data-example="${ex.id}"`);
  });
  it("right but uncited: D3's naive lane is correct with no region in every run", async () => {
    const p = await load.sc11("d3")();
    const t = tally(p.runs.lanes["1.0"].dots);
    expect(t.correct).toBe(t.n);
    expect(t.cited).toBe(0);
    expect(beforeCaption(p)).toBe("Naive pipeline: the right figure, with no region to cite.");
  });
  it("why: the X-ray says where the naive answer's numbers are printed, and says the reading order is constructed", async () => {
    const p = await load.sc11("lending-02")();
    expect(p.xray).toBeDefined();
    const found = xrayFindings(p.xray!).filter((f) => f.lines.length);
    expect(found.length).toBeGreaterThan(0);
    const html = render(sc11BeforeAfter, p, stepOf(sc11BeforeAfter, "why"), 1);
    expect(html).toContain("This page&#x27;s reading order is constructed on purpose.");
    // the Box 1 figure the evidence-units lane cites is not one of the naive answer's numbers
    const box1 = p.xray!.pairs.find((pr) => pr.concept === "fin:W2WagesBox1")?.geometricValue?.text;
    expect(found.map((f) => f.value)).not.toContain(box1);
  });
  it("D1 and D2 run the same retrieval K on both lanes", async () => {
    for (const ex of ["d1", "d2"]) {
      const p = await load.sc11(ex)();
      expect(p.retrieved["1.0"].k).toBeDefined();
      expect(p.retrieved["1.0"].k).toBe(p.retrieved["2.3.1"].k);
    }
  });
});

describe.skipIf(!hasWorld)("SC-12 Evaluate", () => {
  commonChecks(sc12Evaluate, load.sc12);
  it("unpublished: no rate anywhere in the payload, the last step says Not published", async () => {
    const p = await load.sc12();
    expect(p.run.published).toBe(false);
    expect(JSON.stringify(p)).not.toMatch(/"rate"/);
    const html = render(sc12Evaluate, p, stepOf(sc12Evaluate, "rates"), 1);
    expect(html).toContain("Not published");
    expect(html).toContain('data-not-in-build="unpublished-metrics"');
    expect(html).not.toMatch(/\d+\.\d %/);
  });
  it("one dot per golden question, n per type from the golden set", async () => {
    const p = await load.sc12();
    for (const r of p.rows) expect(r.dots.length).toBe(r.n);
    expect(p.questions).toBe(Object.values(p.run.goldenTypes).reduce((s, n) => s + n, 0));
    expect(captionOf(sc12Evaluate.steps[stepOf(sc12Evaluate, "rates")], p)).toBe("Measured on our constructed corpus. Your documents will differ.");
  });
  it("questions no single unit answers sit in their own bin; region hit is shown for the evidence-units lane", async () => {
    const p = await load.sc12();
    expect(p.multiUnit.length).toBeGreaterThan(0);
    const dots = p.rows.flatMap((r) => r.dots);
    for (const qid of p.multiUnit) {
      const d = dots.find((x) => x.qid === qid);
      if (d) for (const pv of ["1.0", "2.3.1"] as const) if (d.lanes[pv].n) expect(d.lanes[pv].bin).toBe("multi");
    }
    // a total the evidence-units lane cannot hit at top-1 still hits its region in every run
    const total = dots.find((x) => x.qid && p.multiUnit.includes(x.qid) && x.lanes["2.3.1"].region.n);
    if (total) expect(total.lanes["2.3.1"].region.bin).toBe("hit");
    const html = render(sc12Evaluate, p, stepOf(sc12Evaluate, "units"), 1);
    expect(html).toContain('data-metric="region"');
    expect(html).toContain("Needs 2+ units");
    // an empty "Mixed" column is not drawn
    const anyMixed = dots.some((d) => (["1.0", "2.3.1"] as const).some((pv) => d.lanes[pv].bin === "mixed" || d.lanes[pv].region.bin === "mixed"));
    expect(html.includes(">Mixed<")).toBe(anyMixed);
  });
});

describe.skipIf(!hasWorld)("SC-13 Cost and model tiering", () => {
  commonChecks(sc13Cost, load.sc13);
  it("unpublished: no dollar amount in the payload or any frame", async () => {
    const p = await load.sc13();
    expect(p.cost.published).toBe(false);
    expect(JSON.stringify(p)).not.toMatch(/estimateUsd|unitPriceUsd/);
    for (let i = 0; i < sc13Cost.steps.length; i++) expect(render(sc13Cost, p, i, 1)).not.toMatch(/\$\d|USD/);
  });
  it("tokens per answer are the gate cohort's logged usage with the lane's own model only", async () => {
    const p = await load.sc13();
    expect(p.asks.length).toBeGreaterThan(0);
    const pkg = await api().getPackage(p.cost.packageId);
    const runs = (await import("./api/runs")).allRuns(api(), p.cost.packageId);
    const byId = new Map((await runs).map((r) => [r.runId, r]));
    for (const a of p.asks) {
      expect(a.modelId).toBe(p.answerModel);
      const r = byId.get(a.runId)!;
      expect(r.callerSub).toBe(pkg.package.ownerSub);
      expect(r.generationProfile ?? "default").toBe("default");
      expect(r.status).toBe("ok");
    }
  });
});

describe.skipIf(!hasWorld)("SC-14 Three planes", () => {
  commonChecks(sc14ThreePlanes, load.sc14);
  it("every lit stage is a SUCCEEDED stage record; skipped stages say skipped", async () => {
    const p = await load.sc14();
    const html = render(sc14ThreePlanes, p, stepOf(sc14ThreePlanes, "agent"), 1);
    const lit = [...html.matchAll(/data-stage="([a-z_]+)" data-lit="true"/g)].map((m) => m[1]);
    const ok = ["1.0", "2.3.1"].flatMap((pv) => p.lanes[pv as "1.0"].stages.filter((s) => s.state === "SUCCEEDED").map((s) => s.stageId));
    expect(lit.length).toBe(ok.length);
    const skipped = ["1.0", "2.3.1"].flatMap((pv) => p.lanes[pv as "1.0"].stages.filter((s) => s.state === "SKIPPED"));
    expect((html.match(/>skipped</g) ?? []).length).toBe(skipped.length);
  });
  it("the agent plane lights one box per logged tool call, each with its access row", async () => {
    const p = await load.sc14();
    for (const c of p.run.toolCalls) expect(p.access.some((a) => a.callId === c.callId)).toBe(true);
  });
  it("the layer map is a labelled schematic with no numbers", async () => {
    const p = await load.sc14();
    const html = render(sc14ThreePlanes, p, stepOf(sc14ThreePlanes, "layers"), 1);
    expect(html).toContain('data-provenance="illustrative"');
    expect(html.replace(/<[^>]+>/g, " ")).not.toMatch(/\d{2,}/);
  });
});

describe.skipIf(!hasWorld)("plan codes stay off the stage: no cut, decision or plan-section id in any frame's text or tooltip", () => {
  // internal decision, cut, rule, acceptance and milestone ids (letters + numbers); the example ids D1-D3 name
  // datasets and stay in title=
  const CODE = /\b(?:DD-?\d+|D-\d+|C\d+|H\d+|AC-\d+(?:\.\d+)?|M\d)\b|plan §|PLAN|COA spike|post-lock|stage[- ]cut/;
  const visible = (html: string) => html.replace(/<title>[^<]*<\/title>/g, " ").replace(/<[^>]+>/g, " ").replace(/<!-- -->/g, "");
  /** title, aria-label and data-* attribute values (tooltips and test hooks), never SVG path data */
  const attrs = (html: string) => [...html.matchAll(/\s(?:title|aria-label|data-[a-z-]+)="([^"]*)"/g)].map((m) => m[1]).join(" | ");
  // eslint-disable-next-line @typescript-eslint/no-explicit-any -- a heterogeneous list of storyboards and their loaders
  type Case = [string, Storyboard<any>, () => Promise<unknown>];
  const cases: Case[] = [
    ["SC-7", sc7Identity, load.sc7],
    ["SC-8", sc8GovernedMeaning, load.sc8],
    ["SC-10", sc10BiggerModel, load.sc10],
    ...showcaseMeta("sc-11")!.examples!.map((e): Case => [`SC-11 ${e.id}`, sc11BeforeAfter, load.sc11(e.id)]),
    ["SC-12", sc12Evaluate, load.sc12],
    ["SC-13", sc13Cost, load.sc13],
    ["SC-14", sc14ThreePlanes, load.sc14],
  ];
  for (const [name, story, loader] of cases) {
    it(name, async () => {
      const p = await loader();
      for (let i = 0; i < story.steps.length; i++) {
        const html = render(story, p as never, i, 1);
        expect(visible(html).match(CODE)?.[0], `${name} step ${i + 1}`).toBeUndefined();
        expect(attrs(html).match(CODE)?.[0], `${name} step ${i + 1} (attributes)`).toBeUndefined();
        expect(captionOf(story.steps[i], p as never)).not.toMatch(CODE);
      }
    });
  }
});

describe("honesty rules: story files type no data values", () => {
  const files = ["sc7-identity.tsx", "sc7-payload.ts", "sc8-governed-meaning.tsx", "sc8-payload.ts", "sc10-bigger-model.tsx", "sc10-payload.ts", "sc11-before-after.tsx", "sc11-payload.ts", "sc12-evaluate.tsx", "sc12-payload.ts", "sc13-cost.tsx", "sc13-payload.ts", "sc14-three-planes.tsx", "sc14-payload.ts", "compare.ts", "parts.tsx"];
  for (const f of files) {
    it(f, () => {
      const src = readFileSync(new URL(`./showcase/stories/${f}`, import.meta.url), "utf8");
      expect(src).not.toMatch(/\d{1,3}(,\d{3})+\.\d{2}/); // no money literals
      expect(src).not.toMatch(/\b(98400|71250|169650|188090|199000|4890)\b/);
      expect(src).not.toMatch(/(?<![\w-])(?:eu|c|w)-\d{2}\b|827d37/); // ids are resolved, never typed
      expect(src).not.toMatch(/\bq-[a-z0-9]+-\d+\b/); // question ids come from the catalog
      expect(src).not.toMatch(/\br-[0-9a-f]{12}\b/); // run ids come from the payload
    });
  }
});

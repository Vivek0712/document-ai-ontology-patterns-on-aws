/**
 * v2 foundation tests: number formatting, camera maths, and the SC-1 storyboard on the real X-ray export
 * (mock-public/mock/xray, written by `make xray-fixtures` from the local pipeline outputs).
 */
import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { PackageDetail } from "@contracts/api";
import type { RpApi } from "./api/types";
import { XRAY_PAGE_KEYS, type XrayIndex, type XrayPage, xrayUnusable } from "./api/xray";
import { formatNum } from "./design/Num";
import { TooltipProvider } from "./design/ui/tooltip";
import { docDisplayName, middleEllipsis } from "./features/units/UnitsExplorer";
import { keyBelongsToTarget } from "./showcase/engine/Player";
import { captionOf } from "./showcase/engine/types";
import { advance } from "./showcase/engine/useStoryClock";
import { type Sc1Payload, byBoxNumber, hypothesisOf, loadSc1, snapToLines } from "./showcase/stories/sc1-payload";
import { sc1ReadThePage } from "./showcase/stories/sc1-read-the-page";
import { distinctRegions } from "./viz/PageCanvas/layers";
import { CanvasSizeHint } from "./viz/PageCanvas/PageCanvas";
import { fitView, viewInterpolator, zoomAt } from "./viz/PageCanvas/camera";
import { lerpRect, seg, union } from "./viz/geom";

const XRAY = new URL("../mock-public/mock/xray/pkg-lending-01/", import.meta.url);
const hasFixtures = existsSync(new URL("index.json", XRAY));
// locally the fixture suites skip until `make xray-fixtures` has run; in CI a missing export is a failure
if (!hasFixtures && process.env.CI && process.env.RP_ALLOW_MISSING_FIXTURES !== "1") throw new Error("usecases/provenant/ui/mock-public/mock/xray is missing: run `make xray-fixtures` before the UI tests");
const index = (): XrayIndex => JSON.parse(readFileSync(new URL("index.json", XRAY), "utf8")) as XrayIndex;

/** The three API calls the SC-1 loader makes, served from the export. */
function fixtureApi(): RpApi {
  const idx = index();
  const api: Partial<RpApi> = {
    mode: "mock",
    getPackage: async (packageId: string) =>
      ({
        package: { packageId },
        documents: idx.documents.map((d) => ({ docId: d.docId, fileName: d.fileName, docType: d.docType, packageId, sha256: d.sha256 })),
        canWrite: true,
      }) as unknown as PackageDetail,
    getXray: async (docId: string, page: number, size?: number) => {
      const file = idx.documents.find((d) => d.docId === docId)!.files[String(page)][String(size ?? idx.control.sizeTokens)];
      return JSON.parse(readFileSync(new URL(file, XRAY), "utf8")) as XrayPage;
    },
  };
  return api as RpApi;
}

describe("Num formatting", () => {
  it("formats money, ints, ratios and ms", () => {
    expect(formatNum("98400.00", "money")).toBe("98,400.00");
    expect(formatNum(169650, "money")).toBe("169,650.00");
    expect(formatNum(179, "int")).toBe("179");
    expect(formatNum(0.99886, "ratio")).toBe("0.999");
    expect(formatNum(412, "ms")).toBe("412 ms");
    expect(formatNum("n/a", "money")).toBe("n/a");
  });
});

describe("PageCanvas camera", () => {
  const size = { w: 800, h: 600 };
  it("fits a rect centred with padding", () => {
    const v = fitView([0, 0, 612, 792], size, 24);
    expect(v.k).toBeCloseTo((600 - 48) / 792);
    expect(v.x + size.w / (2 * v.k)).toBeCloseTo(306);
    expect(v.y + size.h / (2 * v.k)).toBeCloseTo(396);
  });
  it("width-fit pins the top and blends continuously from contain", () => {
    const page: [number, number, number, number] = [0, 0, 612, 792];
    const w = fitView(page, size, 24, 12, 1);
    expect(w.k).toBeCloseTo((800 - 48) / 612);
    expect(w.y).toBeCloseTo(-24 / w.k);
    const half = fitView(page, size, 24, 12, 0.5);
    expect(half.k).toBeGreaterThan(fitView(page, size).k);
    expect(half.k).toBeLessThan(w.k);
  });
  it("smooth zoom starts and ends exactly on the two views", () => {
    const a = fitView([0, 0, 612, 792], size);
    const b = fitView([306, 96, 576, 204], size);
    const i = viewInterpolator(a, b, size);
    expect(i(0).k).toBeCloseTo(a.k);
    expect(i(1).k).toBeCloseTo(b.k);
    expect(i(1).x).toBeCloseTo(b.x);
  });
  it("zooms about the cursor and respects limits", () => {
    const v = { x: 0, y: 0, k: 1 };
    const z = zoomAt(v, 2, 100, 100, 0.5, 4);
    expect(z.x + 100 / z.k).toBeCloseTo(100);
    expect(zoomAt(v, 100, 0, 0, 0.5, 4).k).toBe(4);
  });
  it("geometry helpers", () => {
    expect(seg(0.5, 0.25, 0.75)).toBe(0.5);
    expect(seg(2, 0, 1)).toBe(1);
    expect(union([[0, 0, 1, 1], null, [2, -1, 3, 0]])).toEqual([0, -1, 3, 1]);
    expect(lerpRect([0, 0, 10, 10], [10, 10, 20, 20], 0.5)).toEqual([5, 5, 15, 15]);
  });
});

describe("helpers", () => {
  it("sorts box numbers as printed", () => {
    expect(["12a", "3", "1", "10", "2", "12b", "b"].sort(byBoxNumber)).toEqual(["1", "2", "3", "10", "12a", "12b", "b"]);
  });
  it("snaps a camera rect to whole text lines", () => {
    const lines: [number, number, number, number][] = [[10, 0, 60, 10], [10, 20, 60, 30], [200, 0, 400, 10]];
    expect(snapToLines([12, 5, 58, 25], lines)).toEqual([10, 0, 60, 30]);
  });
  it("names documents by type and their distinguishing words, with a middle ellipsis for the file", () => {
    expect(docDisplayName({ fileName: "w2_2025_maya_fictus.pdf", docType: "doc:W2" }).primary).toBe("W-2 · 2025 Maya Fictus");
    expect(docDisplayName({ fileName: "bank_statement_2026-09.pdf", docType: "doc:BankStatement" }).primary).toBe("Bank statement · 2026-09");
    expect(middleEllipsis("payslip_2026-09_arjun_fictus_scan.pdf", 20)).toBe("payslip_20…_scan.pdf");
    expect(middleEllipsis("short.pdf")).toBe("short.pdf");
  });
  it("draws one held region per area: no duplicates, no contained or sliver rects", () => {
    const outer: [number, number, number, number] = [0, 0, 100, 40];
    expect(distinctRegions([outer, [10, 10, 50, 20], outer, [0, 0, 2, 40]], 4)).toEqual([outer]);
  });
  it("the story clock advances purely and stops at the end", () => {
    expect(advance({ step: 0, local: 900 }, 200, [1000, 500])).toEqual({ step: 1, local: 100, stop: false });
    expect(advance({ step: 1, local: 450 }, 200, [1000, 500])).toEqual({ step: 1, local: 500, stop: true });
  });
  it("leaves arrows and Space to the focused control", () => {
    const at = (match: string) => ({ closest: (sel: string) => (sel.includes(match) ? {} : null) }) as unknown as EventTarget;
    expect(keyBelongsToTarget({ key: "ArrowRight", target: at('[role="radio"]') })).toBe(true);
    expect(keyBelongsToTarget({ key: "ArrowRight", target: at("button") })).toBe(false);
    expect(keyBelongsToTarget({ key: " ", target: at("button") })).toBe(true);
    expect(keyBelongsToTarget({ key: "r", target: at('[role="slider"]') })).toBe(false);
  });
});

describe.skipIf(!hasFixtures)("the X-ray contract on the real export", () => {
  const files = () => {
    const out: string[] = [];
    const walk = (u: URL) => {
      for (const name of readdirSync(u)) {
        const child = new URL(name, u);
        if (statSync(child).isDirectory()) walk(new URL(`${name}/`, u));
        else if (name.endsWith(".json") && name !== "index.json") out.push(child.pathname);
      }
    };
    walk(XRAY);
    return out;
  };
  it("every payload key is in the TypeScript contract, and every required one is present", () => {
    const optional = new Set(["warning", "answerNumbers"]);
    for (const f of files()) {
      const x = JSON.parse(readFileSync(f, "utf8")) as Record<string, unknown>;
      for (const k of Object.keys(x)) expect(XRAY_PAGE_KEYS as readonly string[], `${f}: ${k}`).toContain(k);
      for (const k of XRAY_PAGE_KEYS) if (!optional.has(k)) expect(x, `${f}: ${k}`).toHaveProperty(k);
    }
  });
  it("a scanned page reports no text layer, not 100 % alignment", () => {
    const scan = files().find((f) => f.includes("scan"))!;
    const x = JSON.parse(readFileSync(scan, "utf8")) as XrayPage;
    expect(x.textLayer).toBe(false);
    expect(x.coverage).toBeNull();
    expect(xrayUnusable(x)).toBe("no text layer");
  });
  it("no fixture carries an unmasked SSN or an account number (PII sweep)", () => {
    for (const f of files()) {
      const text = readFileSync(f, "utf8");
      expect(text, f).not.toMatch(/(?<![X\d])\d{3}-\d{2}-\d{4}\b/);
      expect(text, f).not.toMatch(/\b\d{12,17}\b/);
    }
  });
  it("what-if chunks never carry a stored-looking id", () => {
    for (const f of files().filter((f) => !/-s300\.json$/.test(f))) {
      const x = JSON.parse(readFileSync(f, "utf8")) as XrayPage;
      for (const c of x.chunks) {
        expect(c.chunkId, f).toMatch(/^w-\d{2}$/);
        expect(c.label, f).toMatch(/^what-if #\d+$/);
        expect(c.unitId).toBeNull();
      }
    }
  });
});

describe.skipIf(!hasFixtures)("SC-1 on the real lending export", () => {
  it("resolves Maya's W-2 by type, borrower and concept, and picks the first separating what-if", async () => {
    const { payload: p, source } = await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" });
    expect(p.doc.docType).toBe("doc:W2");
    expect(p.unit.label).toContain("Maya Fictus");
    expect(p.control.chunks).toHaveLength(1); // CAP F-1: one chunk at the 300-token control
    expect(p.whatIf.sizeTokens).toBe(128); // 200 keeps them together; 128 is the first size that separates
    expect(p.whatIfChunks).toEqual({ label: ["what-if #1"], value: ["what-if #2"] });
    expect(p.fields.map((f) => f.box)).toEqual(["1", "2", "3", "4", "5", "6", "12a"]); // by box, not stream order
    expect(p.cut).toBeDefined();
    expect(p.box1.positionalValue?.text).toBe("6,600.00"); // constructed W-2: the first amount after the label block
    expect(p.box1.positionalProvenance).toBe("derived");
    expect(p.box1.geometricValue?.text).toBe("98,400.00");
    expect(p.positionalOwner?.box).toBe("12a");
    expect(source.mode).toBe("local-run");
    // the positional hypothesis resolves from the logged naive run the export names, never from a default
    const run = p.control.sources.answerRun;
    if (!run) expect(p.hypothesis.state).toBe("untested");
    else {
      expect(run.provenance).toBe("local-run");
      expect(p.hypothesis.state === "untested" ? undefined : p.hypothesis.runId).toBe(run.runId);
      expect(p.hypothesis.state === "untested" ? undefined : p.hypothesis.provenance).toBe("local-run");
      const usedPositional = (p.control.answerNumbers ?? []).some((n) => n.isPositionalValueOf === p.box1.fieldId);
      expect(p.hypothesis.state).toBe(usedPositional ? "confirmed" : "refuted");
    }
  });

  it("captions are short, plain and quote only payload values", async () => {
    const { payload } = await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" });
    const json = JSON.stringify(payload);
    for (const step of sc1ReadThePage.steps) {
      const caption = captionOf(step, payload);
      expect(caption.length, caption).toBeLessThanOrEqual(90);
      for (const n of caption.match(/\d[\d,.]*\d/g) ?? []) expect(json, `caption number ${n}`).toContain(n);
    }
  });

  const render = (payload: Sc1Payload, step: number, progress: number) =>
    renderToString(
      createElement(MemoryRouter, null, createElement(CanvasSizeHint.Provider, { value: { w: 900, h: 640 } }, createElement(TooltipProvider, null, sc1ReadThePage.render({ payload, step, progress, reduced: false })))),
    );
  // the hypothesis marks: a loss stroke/fill on the canvas (× arc, × mark, loss highlight) or a value marked wrong.
  // The "no region" chip on a 1.0 chunk citation stays pink on purpose: that one is a fact (chunks carry no bbox).
  const LOSS = /(?:stroke|fill)="var\(--loss\)"|aria-label="wrong"/;

  /** The same page with no logged answer: the state SC-1 shows before any naive run is captured. */
  const untestedOf = (p: Sc1Payload): Sc1Payload => ({ ...p, control: { ...p.control, answerNumbers: undefined }, hypothesis: { state: "untested" } });

  it("untested: no step draws a loss tone or ×, and the closing value is marked as a hypothesis", async () => {
    const payload = untestedOf((await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" })).payload);
    for (let step = 0; step < sc1ReadThePage.steps.length; step++) {
      for (const progress of [0, 0.5, 1]) {
        const html = render(payload, step, progress);
        expect(html, `step ${step + 1} @ ${progress}`).toContain(`aria-label="${payload.doc.fileName}, page 1"`); // the canvas rendered
        expect(html, `step ${step + 1} @ ${progress}`).not.toMatch(LOSS);
      }
    }
    const closing = render(payload, 5, 1);
    expect(closing).toContain('data-hypothesis="untested"');
    expect(closing).toContain(`${payload.box1.positionalValue!.text}?`);
  });

  it("refuted (or untested): the position-only arc never turns into a ×", async () => {
    const { payload } = await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" });
    if (payload.hypothesis.state === "confirmed") return; // the confirmed branch is tested below
    for (let step = 0; step < sc1ReadThePage.steps.length; step++) expect(render(payload, step, 1), `step ${step + 1}`).not.toMatch(LOSS);
  });

  it("confirmed: the × appears, on the position-only steps only", async () => {
    const { payload } = await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" });
    const pos = payload.box1.positionalValue!;
    const control = { ...payload.control, answerNumbers: [{ runId: "r-test", value: pos.text, matchedLines: [pos.ordinal], isPositionalValueOf: payload.box1.fieldId }] };
    const confirmed = { ...payload, control, hypothesis: hypothesisOf(control, payload.box1) };
    expect(render(confirmed, 3, 1)).toMatch(LOSS);
    expect(render(confirmed, 5, 1)).toMatch(LOSS);
    expect(render(confirmed, 1, 1)).not.toMatch(LOSS);
  });

  it("the what-if indicator shows exactly when the what-if re-split is on screen", async () => {
    const { payload } = await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" });
    for (let step = 0; step < sc1ReadThePage.steps.length; step++) {
      for (const progress of [0, 0.3, 0.6, 1]) {
        const on = step === 2 && progress >= 0.5;
        expect(render(payload, step, progress).includes('data-testid="whatif-on"'), `step ${step + 1} @ ${progress}`).toBe(on);
      }
    }
  });

  it("the text-layer step draws the label run, then the value run, with the stream strip", async () => {
    const { payload } = await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" });
    const html = render(payload, 1, 1);
    expect(html).toContain("Stream order");
    expect(html).toContain(`labels #${payload.stops.labels[0].ordinal}–#${payload.stops.labels.at(-1)!.ordinal}`);
    expect(html).toContain(`values #${payload.stops.values[0].ordinal}–#${payload.stops.values.at(-1)!.ordinal}`);
  });
});

describe("the positional hypothesis: the position-only caption follows the logged answer", () => {
  it.skipIf(!hasFixtures)("has a variant for untested, confirmed and refuted", async () => {
    const payload = { ...(await loadSc1({ api: fixtureApi(), packageId: "pkg-lending-01" })).payload, hypothesis: { state: "untested" as const } };
    const step = sc1ReadThePage.steps.find((s) => s.id === "position-only")!;
    const pos = payload.box1.positionalValue!;
    const withAnswer = (value: string, owner?: string) => ({
      ...payload.control,
      answerNumbers: [{ runId: "r-test", value, matchedLines: [pos.ordinal], ...(owner ? { isPositionalValueOf: owner } : {}) }],
    });
    expect(captionOf(step, payload)).toMatch(/No logged answer has tested this yet\.$/);
    const confirmed = { ...payload, hypothesis: hypothesisOf(withAnswer(pos.text, payload.box1.fieldId), payload.box1) };
    expect(confirmed.hypothesis.state).toBe("confirmed");
    expect(captionOf(step, confirmed)).toMatch(/The logged answer used it\.$/);
    const refuted = { ...payload, hypothesis: hypothesisOf(withAnswer("105,000.00"), payload.box1) };
    expect(refuted.hypothesis.state).toBe("refuted");
    expect(captionOf(step, refuted)).toMatch(/used other numbers\.$/);
  });
});

describe("design rules", () => {
  it("no text smaller than 12 px anywhere in the app", () => {
    const offenders: string[] = [];
    const walk = (dir: URL) => {
      for (const name of readdirSync(dir)) {
        const child = new URL(name, dir);
        if (statSync(child).isDirectory()) walk(new URL(`${name}/`, dir));
        else if (/\.(tsx?|css)$/.test(name) && !name.endsWith(".test.ts")) {
          const src = readFileSync(child, "utf8");
          for (const m of src.matchAll(/text-\[(\d+(?:\.\d+)?)px\]|font-size:\s*(\d+(?:\.\d+)?)px/g)) {
            if (Number(m[1] ?? m[2]) < 12) offenders.push(`${child.pathname.split("/src/")[1]}: ${m[0]}`);
          }
        }
      }
    };
    walk(new URL("./", import.meta.url));
    expect(offenders).toEqual([]);
  });
});

describe("honesty rules", () => {
  it("the storyboard types no data values", () => {
    const src = readFileSync(new URL("./showcase/stories/sc1-read-the-page.tsx", import.meta.url), "utf8");
    expect(src).not.toMatch(/\d{1,3}(,\d{3})+\.\d{2}/); // no money literals
    expect(src).not.toMatch(/\b(98400|71250|169650|6600)\b/);
    expect(src).not.toMatch(/(?<![\w-])(?:eu|c|w)-\d{2}\b|827d37/); // ids are resolved, never typed
    expect(src).not.toMatch(/\bq-[a-z]+-\d+\b/); // question / run ids come from the catalog or the payload
  });
});

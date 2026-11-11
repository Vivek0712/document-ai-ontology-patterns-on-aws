/**
 * Test harness for storyboards on the real mock world (dev only, imported by `*.test.ts`): the dev `MockApi` over
 * `mock-public/mock/world.json` with `fetch` served from `mock-public/` the way Vite serves it, a server-side frame
 * renderer, and the checks every storyboard must pass (steps match the palette, captions are short and quote only
 * payload values, receipts on every step, every step renders at 0 / 0.5 / 1, reduced motion = the end state).
 */
import { existsSync, readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { expect, it, vi } from "vitest";
import type { RunRecord } from "@contracts/api";
import type { RpApi } from "../api/types";
import { TooltipProvider } from "../design/ui/tooltip";
import { MockApi } from "../mocks/server";
import type { World } from "../mocks/world";
import { captionOf, type Storyboard } from "../showcase/engine/types";
import { STORY_STEPS } from "../showcase/stories/steps";
import { fullWorld } from "./worldFiles";
import { CanvasSizeHint } from "../viz/PageCanvas/PageCanvas";

export const MOCK = new URL("../../mock-public/", import.meta.url);
export const hasWorld = existsSync(new URL("mock/world.json", MOCK)) && existsSync(new URL("mock/xray/pkg-lending-01/index.json", MOCK));
// Missing fixtures fail the run: every honesty test reads them. Opt out explicitly, never by default.
if (!hasWorld && process.env.RP_ALLOW_MISSING_FIXTURES !== "1") throw new Error("mock-public/mock is missing: run `make mock-world` and `make xray-fixtures` (or set RP_ALLOW_MISSING_FIXTURES=1)");

/** The mock world with every package shard merged (`worldFiles.fullWorld`): no lazy fetch left for the MockApi. */
export const world = (): World => fullWorld();
/** A logged run record, as `make mock-world` wrote it next to the world. */
export const loggedRun = (runId: string): RunRecord => JSON.parse(readFileSync(new URL(`mock/runs/${runId}.json`, MOCK), "utf8")) as RunRecord;

/** The dev MockApi on the finished local run (`?ingest=done`). */
export function api(user: "a" | "b" = "a"): RpApi {
  return new MockApi(world(), user, "mock", Date.now() - 60 * 60_000);
}

/** Call in `beforeAll`: the mock's simulated latency at its floor, and `fetch` served from mock-public/. */
export function stubFixtures(): void {
  vi.spyOn(Math, "random").mockReturnValue(0);
  vi.stubGlobal("fetch", async (url: string) => {
    const path = new URL(url.replace(/^\//, ""), MOCK);
    if (!existsSync(path)) return new Response("not found", { status: 404 });
    return new Response(readFileSync(path), { status: 200, headers: { "Content-Type": "application/json" } });
  });
}

export function render<P>(story: Storyboard<P>, payload: P, step: number, progress: number, reduced = false): string {
  return renderToString(
    createElement(MemoryRouter, null, createElement(CanvasSizeHint.Provider, { value: { w: 900, h: 640 } }, createElement(TooltipProvider, null, story.render({ payload, step, progress, reduced })))),
  );
}

/** A step's index by its id (tests never address steps by position). */
export const stepOf = <P>(story: Storyboard<P>, id: string) => {
  const i = story.steps.findIndex((s) => s.id === id);
  if (i < 0) throw new Error(`${story.id} has no step ${id}`);
  return i;
};

/** The checks every storyboard passes. */
export function commonChecks<P>(story: Storyboard<P>, load: () => Promise<P>) {
  it("steps, captions and receipts", async () => {
    const p = await load();
    expect(story.steps.map((s) => s.id)).toEqual(STORY_STEPS[story.id].map((s) => s.id));
    const json = JSON.stringify(p);
    for (const step of story.steps) {
      const caption = captionOf(step, p);
      expect(caption.length, caption).toBeLessThanOrEqual(90);
      expect(caption, "no 'X, not Y' captions").not.toMatch(/, not /);
      for (const n of caption.match(/\d[\d,.]*\d/g) ?? []) expect(json, `caption number ${n}`).toContain(n);
      const receipts = step.receipts?.(p) ?? [];
      expect(receipts.length, `${step.id} receipts`).toBeGreaterThan(0);
      for (const r of receipts) expect(r.provenance, `${step.id}: ${r.label}`).toBeDefined();
    }
  });
  it("renders every step at 0, 0.5 and 1; reduced motion is the end state, fully drawn", async () => {
    const p = await load();
    for (let step = 0; step < story.steps.length; step++) {
      for (const t of [0, 0.5, 1]) expect(() => render(story, p, step, t), `step ${step + 1} @ ${t}`).not.toThrow();
      const end = render(story, p, step, 1, true);
      const ids = (h: string) => h.replace(/\bpc\d+/g, "pc"); // per-canvas SVG id prefixes
      expect(ids(end), `step ${step + 1}: reduced = the same frame`).toBe(ids(render(story, p, step, 1, false)));
      // boundaries and hop paths draw with stroke-dashoffset; at the end state none is part-drawn
      for (const m of end.matchAll(/stroke-dashoffset="([\d.]+)"/g)) expect(Number(m[1]), `step ${step + 1}`).toBe(0);
    }
  });
}

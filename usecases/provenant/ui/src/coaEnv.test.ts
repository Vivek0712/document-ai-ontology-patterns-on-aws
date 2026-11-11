/**
 * Settings → COA environment: the mock simulation follows rp-coa-control's rules, the HTTP client hits the
 * contract's routes, and the panel renders every state honestly (estimate tag, read-only for viewers, the simulation
 * label, the CLI fallback when the control stack is not deployed).
 */
import { readFileSync } from "node:fs";
import { createElement } from "react";
import { renderToString } from "react-dom/server";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { CoaEnvironmentView } from "@contracts/api";
import { HttpApi } from "./api/http";
import { RpApiError } from "./api/types";
import { TooltipProvider } from "./design/ui/tooltip";
import { EnvironmentPanel, actionHint, canSpinUp, inMinutes, who } from "./features/settings/EnvironmentScreen";
import { COA_CONFIG, CoaSimulation } from "./mocks/coaEnv";

const MIN = 60_000;

/** A simulation on a clock the test moves (real ms; the simulation runs `speed`× faster). */
function sim(start: ConstructorParameters<typeof CoaSimulation>[0] = "off", speed = 1) {
  let real = Date.UTC(2026, 10, 11, 9, 0);
  const s = new CoaSimulation(start, () => real, speed);
  return { s, advance: (minutes: number) => (real += (minutes * MIN) / speed) };
}

afterEach(() => vi.restoreAllMocks());

describe("COA simulation (mock mode)", () => {
  it("spins up through the seven phases, then runs with endpoints and a session estimate", () => {
    const { s, advance } = sim();
    expect(s.view(true).state).toBe("off");
    const v = s.up(true, {}, "7c41e2");
    expect(v.outcome).toBe("started");
    expect(v.state).toBe("starting");
    expect(v.mode).toBe("simulated");
    expect(v.build?.phases?.map((p) => p.id)).toEqual(["queued", "provisioning", "install", "pre_build", "build", "post_build", "done"]);
    advance(20);
    const mid = s.view(true);
    expect(mid.build?.phases?.find((p) => p.id === "build")?.status).toBe("running");
    expect(mid.build?.phases?.find((p) => p.id === "install")?.status).toBe("succeeded");
    advance(25);
    const up = s.view(true);
    expect(up.state).toBe("running");
    expect(up.endpoints?.consoleUrl).toContain("simulated.invalid");
    expect(up.cost?.provenance).toBe("estimate");
    expect(up.cost?.sessionHours).toBe(0.75);
    // the minutes are inside the configured ranges
    expect(45).toBeGreaterThanOrEqual(COA_CONFIG.lifecycle.upMinutes[0]);
    expect(45).toBeLessThanOrEqual(COA_CONFIG.lifecycle.upMinutes[1]);
  });

  it("refuses conflicts, treats repeats as no-ops, and keeps viewers read-only", () => {
    const { s, advance } = sim();
    s.up(true, {}, "a");
    expect(s.up(true, {}, "a").outcome).toBe("noop");
    expect(() => s.down(true, {}, "a")).toThrowError(/spinning up/);
    expect(() => s.up(false, {}, "b")).toThrowError(RpApiError);
    try {
      s.up(false, {}, "b");
    } catch (e) {
      expect((e as RpApiError).status).toBe(403);
    }
    expect(s.view(false).cost).toBeUndefined();
    expect(s.view(false).note).toMatch(/Read only/);
    advance(45);
    s.down(true, {}, "a");
    expect(() => s.up(true, {}, "a")).toThrowError(/tearing down/);
    expect(s.down(true, {}, "a").outcome).toBe("noop");
    advance(25);
    expect(s.view(true).state).toBe("off");
    expect(s.down(true, {}, "a").outcome).toBe("noop");
  });

  it("stale expectedVersion is a 409", () => {
    const { s } = sim();
    const v = s.view(true).version!;
    s.settings(true, { autoTeardown: false, idleMinutes: 30, expectedVersion: v });
    expect(() => s.settings(true, { autoTeardown: true, idleMinutes: 30, expectedVersion: v })).toThrowError(/changed since you loaded it/);
  });

  it("settings bounds are the config's (15–480)", () => {
    const { s } = sim();
    expect(() => s.settings(true, { autoTeardown: true, idleMinutes: 14 })).toThrowError(/between 15 and 480/);
    expect(() => s.settings(true, { autoTeardown: true, idleMinutes: 481 })).toThrowError(/between 15 and 480/);
    expect(s.settings(true, { autoTeardown: true, idleMinutes: 15 }).settings?.idleMinutes).toBe(15);
    expect([COA_CONFIG.idle.minMinutes, COA_CONFIG.idle.maxMinutes, COA_CONFIG.idle.defaultMinutes]).toEqual([15, 480, 60]);
  });

  it("tears down when idle for the threshold, not before; keep alive postpones it", () => {
    const { s, advance } = sim();
    s.up(true, {}, "a");
    advance(45); // running
    advance(59);
    expect(s.view(true).state).toBe("running");
    advance(1);
    const v = s.view(true);
    expect(v.state).toBe("stopping");
    expect(v.build?.requestedBy).toBe("idle-watcher");
    expect(v.reason).toBe("idle 60 min");

    const k = sim();
    k.s.up(true, {}, "a");
    k.advance(45);
    k.advance(50);
    const kept = k.s.keepAlive(true);
    expect(kept.idle?.keepAliveUntil).toBeDefined();
    k.advance(59);
    expect(k.s.view(true).state).toBe("running");
    k.advance(1);
    expect(k.s.view(true).state).toBe("stopping");
  });

  it("auto tear-down off never tears down; the cool-down holds after a state change", () => {
    const { s, advance } = sim();
    s.settings(true, { autoTeardown: false, idleMinutes: 15 });
    s.up(true, {}, "a");
    advance(45 + 600);
    expect(s.view(true).state).toBe("running");
    const c = sim();
    c.s.settings(true, { autoTeardown: true, idleMinutes: 15 });
    c.s.up(true, {}, "a");
    c.advance(45 + 14);
    expect(c.s.view(true).state).toBe("running");
    c.advance(1);
    expect(c.s.view(true).state).toBe("stopping");
  });

  it("destroy exit 3 is Partially down, then the retry finishes it", () => {
    const { s, advance } = sim("running");
    s.simulate("eni_pending");
    s.down(true, {}, "a");
    advance(25);
    const v = s.view(true);
    expect(v.state).toBe("partially_down");
    expect(v.eni).toMatchObject({ retries: 1, maxRetries: 16 });
    expect(() => s.up(true, {}, "a")).toThrowError(/network interfaces/);
    advance(30);
    const retry = s.view(true);
    expect(retry.state).toBe("stopping");
    expect(retry.build?.attempt).toBe(2);
    advance(25);
    expect(s.view(true).state).toBe("off");
  });

  it("a failed build is Failed with the phase, and a forced tear-down stops a spin-up", () => {
    const { s, advance } = sim();
    s.simulate("fail");
    s.up(true, {}, "a");
    advance(45);
    const v = s.view(true);
    expect(v.state).toBe("failed");
    expect(v.error?.phase).toBe("build");
    expect(v.build?.phases?.find((p) => p.id === "build")?.status).toBe("failed");
    const f = sim();
    f.s.up(true, {}, "a");
    expect(f.s.down(true, { force: true }, "a").outcome).toBe("stopped_and_started");
  });

  it("after a failed tear-down: spin-up refused, one automatic retry, endpoints hidden, cost frozen with a residual note", () => {
    const { s, advance } = sim("running");
    s.simulate("fail");
    s.down(true, {}, "a");
    advance(17); // halfway through the destroy step it fails
    const v = s.view(true);
    expect(v.state).toBe("failed");
    expect(v.failedOperation).toBe("down");
    expect(v.endpoints).toBeUndefined();
    expect(v.cost?.residual).toMatch(/may still be running and billing/);
    expect(canSpinUp(v)).toBe(false);
    expect(actionHint(v)).toMatch(/tear down again/);
    expect(() => s.up(true, {}, "a")).toThrowError(/Tear down again first/);
    const frozen = v.cost?.sessionHours;
    advance(29);
    expect(s.view(true).cost?.sessionHours).toBe(frozen);
    advance(1);
    const retry = s.view(true);
    expect(retry.state).toBe("stopping");
    expect(retry.reason).toMatch(/automatic retry 1 of 1/);
    advance(25);
    expect(s.view(true).state).toBe("off");
    // a failed spin-up may be retried
    const u = sim();
    u.s.simulate("fail");
    u.s.up(true, {}, "a");
    u.advance(45);
    expect(canSpinUp(u.s.view(true))).toBe(true);
  });

  it("the session cap tears down a busy session unless kept alive", () => {
    const { s, advance } = sim();
    s.settings(true, { autoTeardown: true, idleMinutes: 480 });
    s.up(true, {}, "a");
    advance(45);
    for (let h = 0; h < 9; h++) {
      advance(60);
      s.touch(); // someone (or an open console tab) uses COA every hour
    }
    advance(59); // 9 h 59 min after it came up
    expect(s.view(true).state).toBe("running");
    expect(Date.parse(s.view(true).idle!.maxSessionUntil!)).toBe(Date.UTC(2026, 10, 11, 9, 45) + 600 * MIN);
    advance(1);
    expect(s.view(true).state).toBe("stopping");
    expect(s.view(true).reason).toBe(`max session ${COA_CONFIG.idle.maxSessionHours} h`);
    // Keep alive pushes the cap out
    const k = sim();
    k.s.settings(true, { autoTeardown: true, idleMinutes: 480 });
    k.s.up(true, {}, "a");
    k.advance(45);
    for (let h = 0; h < 9; h++) {
      k.advance(60);
      k.s.touch();
    }
    k.advance(45); // 15 min before the cap
    k.s.keepAlive(true); // until 60 min from now
    k.advance(30);
    expect(k.s.view(true).state).toBe("running");
    k.advance(31);
    expect(k.s.view(true).state).toBe("stopping");
  });

  it("partially down: the session clock stopped when the destroy ran", () => {
    const { s, advance } = sim("partially_down");
    const v = s.view(true);
    expect(v.cost?.residual).toMatch(/no hourly charge of note/);
    advance(20);
    expect(s.view(true).cost?.sessionHours).toBe(v.cost?.sessionHours);
  });

  it("undeployed: the CLI and 503 for actions", () => {
    const { s } = sim("undeployed");
    const v = s.view(true);
    expect(v.deployed).toBe(false);
    expect(v.cli).toEqual(["make coa-status", "make coa-up", "make coa-down"]);
    expect(() => s.up(true, {}, "a")).toThrowError(/isn't deployed/);
  });

  it("the cost estimate is the config's price block, never a billed figure", () => {
    const prices = readFileSync(new URL("../../../../config/prices.yaml", import.meta.url), "utf8");
    expect(prices).toContain("coaEnvironment:");
    const rate = COA_CONFIG.prices!.components.reduce((a, c) => a + c.usdPerHour, 0);
    const { s } = sim("running");
    expect(s.view(true).cost?.usdPerHour).toBeCloseTo(rate, 4);
    expect(s.view(true).cost?.note).toMatch(/not a billed amount/);
  });
});

describe("COA environment over HTTP", () => {
  it("calls the contract's routes with the contract's bodies", async () => {
    const calls: [string, string, string | undefined][] = [];
    vi.stubGlobal("fetch", async (url: string, init: RequestInit) => {
      calls.push([init.method!, url.replace("https://api.example/v1", ""), init.body as string | undefined]);
      return new Response(JSON.stringify({ deployed: true, viewer: { canOperate: true } }), { status: 200 });
    });
    const api = new HttpApi("https://api.example/v1", async () => "tok");
    await api.getCoaEnvironment();
    await api.coaUp({ expectedVersion: 3 });
    await api.coaDown({ force: true });
    await api.coaSettings({ autoTeardown: true, idleMinutes: 90 });
    await api.coaKeepAlive({});
    expect(calls).toEqual([
      ["GET", "/coa/environment", undefined],
      ["POST", "/coa/environment/up", '{"expectedVersion":3}'],
      ["POST", "/coa/environment/down", '{"force":true}'],
      ["PUT", "/coa/environment/settings", '{"autoTeardown":true,"idleMinutes":90}'],
      ["POST", "/coa/environment/keep-alive", "{}"],
    ]);
    vi.unstubAllGlobals();
  });
});

function render(view: CoaEnvironmentView, meTag?: string): string {
  const now = view.serverTime ? Date.parse(view.serverTime) : Date.now();
  const noop = async () => undefined;
  return renderToString(
    createElement(MemoryRouter, null, createElement(TooltipProvider, null, createElement(EnvironmentPanel, { view, now, meTag, onUp: noop, onDown: noop, onKeepAlive: () => undefined, onSave: () => undefined, onSimulate: () => undefined }))),
  );
}

describe("COA environment panel", () => {
  it("running (admin): actions, idle countdown, estimate-tagged cost, endpoints", () => {
    const { s } = sim("running");
    const html = render(s.view(true));
    expect(html).toContain('data-state="running"');
    for (const t of ["Spin up", "Tear down", "Keep alive 60 min", "Auto tear-down", "COA console", "REST endpoint", "This session", "Estimate"]) expect(html).toContain(t);
    expect(html).toContain('data-provenance="estimate"');
    expect(html).toContain("Viewing this page is not activity");
    expect(html).toMatch(/in \d+ min/);
  });

  it("viewer: read-only, no cost", () => {
    const { s } = sim("running");
    const html = render(s.view(false));
    expect(html).toContain('data-testid="coa-read-only"');
    expect(html).not.toContain(">Spin up<");
    expect(html).not.toContain("coa-cost");
  });

  it.each(["off", "starting", "stopping", "failed", "partially_down"] as const)("%s renders its parts", (state) => {
    const { s } = sim(state);
    const html = render(s.view(true));
    expect(html).toContain(`data-state="${state}"`);
    if (state === "starting" || state === "stopping") expect(html).toContain('data-testid="coa-progress"');
    if (state === "failed") expect(html).toContain('role="alert"');
    if (state === "partially_down") expect(html).toContain("waiting for AgentCore ENIs");
    if (state === "off") expect(html).toContain("No state changes yet");
  });

  it("failed tear-down: Tear down again, Spin up disabled, the residual cost note", () => {
    const html = render(sim("failed").s.view(true));
    expect(html).toContain("Tear down again");
    expect(html).toMatch(/<button[^>]*disabled=""[^>]*>.*?Spin up/);
    expect(html).toContain("parts of COA may still be running and billing");
    expect(html).toContain('data-testid="coa-cost-residual"');
    expect(html).not.toContain("COA console");
  });

  it("requests read as you, the idle watcher, the CLI or another user", () => {
    expect(who("local-", "local-")).toBe("you");
    expect(who("b5d09f", "local-")).toBe("user b5d09f");
    expect(who("idle-watcher", "local-")).toBe("idle watcher");
    expect(who("cli", "local-")).toBe("CLI");
    const { s } = sim();
    s.up(true, {}, "local-");
    expect(render(s.view(true), "local-")).toContain("by you");
    expect(render(s.view(false), "b5d09f")).toContain("by user local-");
  });

  it("adoption notice and activity error are shown", () => {
    const v = { ...sim("running").s.view(true), notice: "COA was already running when environment control was first deployed." };
    v.idle = { ...v.idle!, activityError: "CloudWatch could not be read: Throttling", activityErrorSince: v.serverTime };
    const html = render(v);
    expect(html).toContain('data-testid="coa-notice"');
    expect(html).toContain('data-testid="coa-activity-error"');
    expect(html).toContain("successful requests in its last active 5-minute period");
  });

  it("the confirm dialogs state cost and refusals", () => {
    const src = readFileSync(new URL("./features/settings/EnvironmentScreen.tsx", import.meta.url), "utf8");
    expect(src).toContain("Stops about $");
    expect(src).toContain("refused while a previous VPC is still being deleted");
    expect(src).toContain("returnFocus.current?.focus()");
  });

  it("not deployed: the CLI fallback", () => {
    const html = render(sim("undeployed").s.view(true));
    expect(html).toContain("Environment control isn&#x27;t deployed");
    expect(html).toContain("make coa-up");
    expect(html).toContain("rp:coaControl=true");
  });

  it("countdown text", () => {
    const now = Date.UTC(2026, 10, 11, 9, 0);
    expect(inMinutes(new Date(now + 47 * MIN).toISOString(), now)).toBe("in 47 min");
    expect(inMinutes(new Date(now - MIN).toISOString(), now)).toBe("due now");
  });
});

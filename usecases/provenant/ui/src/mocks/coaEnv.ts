/**
 * Mock mode only: a simulated COA environment, so the local demo shows Settings → COA environment working without
 * AWS. It follows rp-coa-control's rules (services/lambdas/coa_control/machine.py): the same states, refusals,
 * no-ops, idle deadline, session cap, cool-down, ENI retries, the one automatic retry of a failed tear-down and the
 * cost clock that stops when the destroy has run, read from the same config (contracts/generated/coa_control.json).
 * Time runs `speed`× faster (default 30×: a 45-minute spin-up takes 90 s) and every view says
 * `mode: "simulated"`, which the panel labels "Simulated · mock mode". Nothing here is a measurement.
 */
import type {
  CoaBuild,
  CoaDownRequest,
  CoaEnvironmentView,
  CoaEnvState,
  CoaHistoryEntry,
  CoaOperation,
  CoaPhase,
  CoaPhaseId,
  CoaSettingsRequest,
  CoaUpRequest,
} from "@contracts/api";
import coaConfig from "@contracts/generated/coa_control.json";
import { RpApiError } from "../api/types";
import type { CoaSimulatedOutcome } from "../api/types";

export const COA_CONFIG = coaConfig;
const PHASES: CoaPhaseId[] = ["queued", "provisioning", "install", "pre_build", "build", "post_build", "done"];
/** Simulated minutes per phase (sums: up 45, down 25), inside the configured 40–60 / 20–30 minute ranges. */
const PHASE_MINUTES: Record<CoaOperation, number[]> = { up: [1, 2, 4, 6, 28, 4, 0], down: [1, 2, 4, 4, 12, 2, 0] };
const MIN = 60_000;
const SIM_HOST = "coa-dev.simulated.invalid";

export type CoaSimStart = CoaEnvState | "undeployed";

function labels(op: CoaOperation): Record<CoaPhaseId, string> {
  return {
    queued: "Queued",
    provisioning: "Provisioning",
    install: "Toolchain (JDK, Node, pnpm, uv, Maven)",
    pre_build: `Clone COA ${coaConfig.coa.tag} · setup`,
    build: op === "up" ? "make deploy-dev" : "make destroy-dev",
    post_build: op === "up" ? "Seed governed metric" : "Check what is left",
    done: "Done",
  };
}

interface SimBuild {
  id: string;
  operation: CoaOperation;
  startedAt: number;
  reason: string;
  requestedBy: string;
  attempt: number;
  outcome: CoaSimulatedOutcome;
  endedAt?: number;
  status: string;
  result?: "ok" | "eni_pending" | "leftovers" | "failed";
}

export class CoaSimulation {
  private state: CoaEnvState = "off";
  private failedOperation?: CoaOperation;
  private downAutoRetries = 0;
  private version = 1;
  private stateSince: number;
  private runningSince?: number;
  private sessionStartedAt?: number;
  private stoppedAt?: number;
  private reason = "simulated environment";
  private build?: SimBuild;
  private autoTeardown = coaConfig.idle.defaultAutoTeardown;
  private idleMinutes = coaConfig.idle.defaultMinutes;
  private activity: Record<string, number> = {};
  private keepAliveUntil?: number;
  private eniRetries = 0;
  private eniFirstAt?: number;
  private nextRetryAt?: number;
  private error?: { message: string; at: number; phase?: string };
  private history: { at: number; from: CoaEnvState; to: CoaEnvState; reason: string; by: string }[] = [];
  private nextOutcome: CoaSimulatedOutcome = "ok";
  private builds = 0;
  private readonly realStart: number;
  private readonly simStart: number;
  readonly deployed: boolean;

  constructor(
    start: CoaSimStart = "off",
    private readonly realNow: () => number = Date.now,
    readonly speed = 30,
  ) {
    this.realStart = realNow();
    this.simStart = this.realStart;
    this.stateSince = this.simStart;
    this.deployed = start !== "undeployed";
    this.seed(start === "undeployed" ? "off" : start);
  }

  /** Simulated wall-clock time (ms). */
  now(): number {
    return this.simStart + (this.realNow() - this.realStart) * this.speed;
  }

  private seed(state: CoaEnvState): void {
    const t = this.now();
    if (state === "off") return;
    const back = (min: number) => t - min * MIN;
    if (state === "running" || state === "stopping" || state === "partially_down" || state === "failed") {
      // a session that started earlier; the failed and partially-down ones ran for a while before the tear-down
      const up = state === "running" || state === "stopping" ? 25 : 100;
      this.sessionStartedAt = back(up + 45);
      this.runningSince = back(up);
      this.activity = { became_running: back(up), api_gateway: back(up - 13) };
      this.history.push({ at: back(up + 45), from: "off", to: "starting", reason: "spin-up requested", by: "7c41e2" });
      this.history.push({ at: back(up), from: "starting", to: "running", reason: "spin-up finished", by: "executor" });
      this.state = "running";
      this.stateSince = back(up);
      this.reason = "spin-up finished";
    }
    if (state === "starting") this.begin("up", "spin-up requested", "7c41e2");
    if (state === "stopping") this.begin("down", "tear-down requested", "7c41e2");
    if (state === "failed") {
      // a failed tear-down whose one automatic retry (failedDownRetryMinutes later) also failed: it stays failed
      this.nextOutcome = "fail";
      this.downAutoRetries = coaConfig.lifecycle.failedDownAutoRetries;
      this.begin("down", "tear-down requested", "7c41e2", back(30));
      this.advance();
    }
    if (state === "partially_down") {
      this.nextOutcome = "eni_pending";
      this.begin("down", "idle 60 min", "idle-watcher", back(26));
      this.advance();
    }
    this.nextOutcome = "ok";
  }

  // ---- helpers ----

  private iso(ms?: number): string | undefined {
    return ms === undefined ? undefined : new Date(ms).toISOString();
  }

  private move(to: CoaEnvState, reason: string, by: string, at = this.now()): void {
    if (to !== this.state) {
      this.history.push({ at, from: this.state, to, reason, by });
      this.history = this.history.slice(-20);
      this.stateSince = at;
    }
    this.failedOperation = to === "failed" ? this.build?.operation : undefined;
    this.state = to;
    this.reason = reason;
    this.version += 1;
  }

  private begin(op: CoaOperation, reason: string, by: string, at = this.now()): void {
    this.builds += 1;
    const attempt = op === "down" && this.state === "partially_down" && this.eniRetries ? this.eniRetries + 1 : 1;
    this.move(op === "up" ? "starting" : "stopping", reason, by, at);
    this.build = {
      id: `rp-coa-lifecycle:sim-${String(this.builds).padStart(4, "0")}`,
      operation: op,
      startedAt: at,
      reason,
      requestedBy: by,
      attempt,
      outcome: this.nextOutcome,
      status: "IN_PROGRESS",
    };
    this.error = undefined;
    if (op === "up") {
      this.sessionStartedAt = at;
      this.stoppedAt = undefined;
      this.runningSince = undefined;
      this.activity = {};
      this.keepAliveUntil = undefined;
      this.eniRetries = 0;
      this.eniFirstAt = undefined;
      this.nextRetryAt = undefined;
      this.downAutoRetries = 0;
    }
  }

  /** begin("down"); from failed, a fresh tear-down counts its ENI retries from zero. */
  private beginDown(reason: string, by: string, at = this.now()): void {
    if (this.state === "failed") {
      this.eniRetries = 0;
      this.eniFirstAt = undefined;
      this.nextRetryAt = undefined;
    }
    this.begin("down", reason, by, at);
  }

  private phases(b: SimBuild, t: number): CoaPhase[] {
    const mins = PHASE_MINUTES[b.operation];
    const l = labels(b.operation);
    const failAt = b.outcome === "fail" ? 4 : -1; // the build step fails
    let cursor = b.startedAt;
    return PHASES.map((id, i) => {
      const start = cursor;
      const end = start + mins[i] * MIN;
      cursor = end;
      const ended = b.endedAt !== undefined;
      if (failAt >= 0 && i > failAt) return { id, label: l[id], status: ended ? (id === "done" ? "failed" : "skipped") : "pending" };
      if (failAt === i && t >= start + (mins[i] * MIN) / 2) return { id, label: l[id], status: "failed", startedAt: this.iso(start), endedAt: this.iso(start + (mins[i] * MIN) / 2) };
      if (t < start) return { id, label: l[id], status: ended ? "skipped" : "pending" };
      if (t < end || (id === "done" && !ended)) return { id, label: l[id], status: "running", startedAt: this.iso(start) };
      return { id, label: l[id], status: "succeeded", startedAt: this.iso(start), endedAt: this.iso(end) };
    });
  }

  private buildEnd(b: SimBuild): number {
    const mins = PHASE_MINUTES[b.operation];
    if (b.outcome === "fail") return b.startedAt + (mins.slice(0, 4).reduce((a, n) => a + n, 0) + mins[4] / 2) * MIN; // halfway through the build step
    return b.startedAt + mins.reduce((a, n) => a + n, 0) * MIN;
  }

  private lastActivity(): [number | undefined, string | undefined] {
    let best: [number | undefined, string | undefined] = [undefined, undefined];
    for (const [s, at] of Object.entries(this.activity)) if (best[0] === undefined || at > best[0]) best = [at, s];
    return best;
  }

  private teardownAt(): number {
    const base = this.lastActivity()[0] ?? this.runningSince ?? this.stateSince;
    const deadline = base + this.idleMinutes * MIN;
    return Math.max(deadline, this.keepAliveUntil ?? 0);
  }

  private cooldownUntil(): number {
    return this.stateSince + coaConfig.idle.cooldownMinutes * MIN;
  }

  private autoApplies(): boolean {
    return this.state === "running" || (this.state === "failed" && this.failedOperation === "up");
  }

  /** The hard cap: maxSessionHours after it became running, pushed out by Keep alive. */
  private maxSessionUntil(): number | undefined {
    const base = this.runningSince ?? this.sessionStartedAt;
    if (base === undefined) return undefined;
    return Math.max(base + coaConfig.idle.maxSessionHours * 60 * MIN, this.keepAliveUntil ?? 0);
  }

  /** When the automatic tear-down starts (idle threshold or the cap, never inside the cool-down), if it applies. */
  private autoDue(): { at: number; reason: "idle" | "cap" } | undefined {
    if (!this.autoTeardown || !this.autoApplies()) return undefined;
    const idleAt = Math.max(this.teardownAt(), this.cooldownUntil());
    const cap = this.maxSessionUntil();
    if (cap !== undefined && Math.max(cap, this.cooldownUntil()) < idleAt) return { at: Math.max(cap, this.cooldownUntil()), reason: "cap" };
    return { at: idleAt, reason: "idle" };
  }

  /** Fold elapsed simulated time: finish builds, retry a destroy waiting for ENIs, tear down when idle. */
  advance(): void {
    for (let guard = 0; guard < 8; guard++) {
      const t = this.now();
      const b = this.build;
      if ((this.state === "starting" || this.state === "stopping") && b && b.endedAt === undefined) {
        const end = this.buildEnd(b);
        if (t < end) return;
        b.endedAt = end;
        if (b.operation === "down") this.stoppedAt ??= end; // the destroy has run: the cost clock stops here
        if (b.outcome === "fail") {
          b.status = "FAILED";
          b.result = "failed";
          const verb = b.operation === "up" ? "spin-up" : "tear-down";
          this.error = { message: `The ${verb} failed: Error while executing command: ${b.operation === "up" ? "make deploy-dev" : "./scripts/destroy.sh dev"}. Reason: exit status 2 (simulated)`, at: end, phase: "build" };
          this.move("failed", `${verb} failed`, "executor", end);
          continue;
        }
        b.status = "SUCCEEDED";
        if (b.operation === "up") {
          b.result = "ok";
          this.move("running", "spin-up finished", "executor", end);
          this.runningSince = end;
          this.activity.became_running = end;
        } else if (b.outcome === "eni_pending") {
          b.result = "eni_pending";
          this.eniRetries += 1;
          this.eniFirstAt ??= end;
          this.nextRetryAt = end + coaConfig.lifecycle.eniRetryMinutes * MIN;
          this.move("partially_down", "waiting for AgentCore ENIs", "executor", end);
        } else {
          b.result = "ok";
          this.move("off", "tear-down finished", "executor", end);
          this.stoppedAt = end;
          this.runningSince = undefined;
          this.keepAliveUntil = undefined;
          this.eniRetries = 0;
          this.eniFirstAt = undefined;
          this.nextRetryAt = undefined;
        }
        continue;
      }
      if (this.state === "partially_down" && this.nextRetryAt !== undefined && t >= this.nextRetryAt) {
        const at = this.nextRetryAt;
        this.nextOutcome = "ok"; // the simulated ENIs are released by the first retry
        this.begin("down", `retry ${this.eniRetries}: waiting for AgentCore ENIs`, "idle-watcher", at);
        continue;
      }
      const lc = coaConfig.lifecycle;
      if (this.state === "failed" && this.failedOperation === "down" && this.downAutoRetries < lc.failedDownAutoRetries) {
        const at = this.stateSince + lc.failedDownRetryMinutes * MIN;
        if (t >= at) {
          this.downAutoRetries += 1;
          this.nextOutcome = "ok";
          this.beginDown(`automatic retry ${this.downAutoRetries} of ${lc.failedDownAutoRetries} of a failed tear-down`, "idle-watcher", at);
          continue;
        }
      }
      const due = this.autoDue();
      if (due && t >= due.at) {
        const base = this.lastActivity()[0] ?? this.runningSince ?? this.stateSince;
        this.nextOutcome = "ok";
        this.begin("down", due.reason === "cap" ? `max session ${coaConfig.idle.maxSessionHours} h` : `idle ${Math.floor((due.at - base) / MIN)} min`, "idle-watcher", due.at);
        continue;
      }
      return;
    }
  }

  // ---- the API ----

  private refuse(status: number, code: "conflict" | "forbidden" | "bad_request" | "not_ready", message: string): never {
    throw new RpApiError(status, code, message);
  }

  private guard(canOperate: boolean, expected?: number): void {
    if (!this.deployed) this.refuse(503, "not_ready", "Environment control isn't deployed in this stack (CDK context rp:coaControl).");
    if (!canOperate) this.refuse(403, "forbidden", `Changing the COA environment needs the ${coaConfig.adminGroup} group.`);
    this.advance();
    if (expected !== undefined && expected !== this.version) this.refuse(409, "conflict", `The environment changed since you loaded it (version ${this.version}, you saw ${expected}); reload and try again.`);
  }

  view(canOperate: boolean, outcome?: CoaEnvironmentView["outcome"]): CoaEnvironmentView {
    if (!this.deployed) {
      return { deployed: false, mode: "simulated", viewer: { canOperate, adminGroup: coaConfig.adminGroup }, cli: ["make coa-status", "make coa-up", "make coa-down"], note: "Environment control isn't deployed in this stack (CDK context rp:coaControl). Use the CLI: make coa-status, make coa-up, make coa-down." };
    }
    this.advance();
    const t = this.now();
    const b = this.build;
    const build: CoaBuild | undefined = b && {
      id: b.id,
      operation: b.operation,
      status: b.status,
      startedAt: this.iso(b.startedAt)!,
      endedAt: this.iso(b.endedAt),
      reason: b.reason,
      requestedBy: b.requestedBy,
      attempt: b.attempt,
      phases: this.phases(b, b.endedAt ?? t),
      result: b.result,
    };
    const [lastAt, lastSource] = this.lastActivity();
    const showIdle = this.state === "running" || this.state === "starting" || this.autoApplies();
    const rate = coaConfig.prices?.components.reduce((s, c) => s + c.usdPerHour, 0) ?? 0;
    const clockRuns = this.state === "starting" || this.state === "running" || this.stoppedAt === undefined;
    const end = clockRuns ? t : this.stoppedAt!;
    const failedDown = this.state === "failed" && this.failedOperation === "down";
    const residual =
      this.state === "partially_down"
        ? "The rest of COA is deleted; only its VPC, subnets and security groups are left until AgentCore releases its network interfaces (no hourly charge of note)."
        : failedDown
          ? "The tear-down failed, so some COA stacks may still be running and billing until it is retried; this session's figure stops at the failed destroy."
          : undefined;
    const due = this.autoDue();
    const hours = this.sessionStartedAt !== undefined ? Math.round(((end - this.sessionStartedAt) / 3_600_000) * 1000) / 1000 : undefined;
    const history: CoaHistoryEntry[] = [...this.history].reverse().slice(0, 10).map((h) => ({ at: this.iso(h.at)!, fromState: h.from, toState: h.to, reason: h.reason, by: h.by }));
    return {
      deployed: true,
      mode: "simulated",
      serverTime: this.iso(t),
      state: this.state,
      failedOperation: this.state === "failed" ? this.failedOperation : undefined,
      version: this.version,
      stateSince: this.iso(this.stateSince),
      runningSince: this.iso(this.runningSince),
      sessionStartedAt: this.iso(this.sessionStartedAt),
      stoppedAt: this.iso(this.stoppedAt),
      reason: this.reason,
      build,
      settings: { autoTeardown: this.autoTeardown, idleMinutes: this.idleMinutes, minMinutes: coaConfig.idle.minMinutes, maxMinutes: coaConfig.idle.maxMinutes, keepAliveMinutes: coaConfig.idle.keepAliveMinutes, maxSessionHours: coaConfig.idle.maxSessionHours },
      idle: showIdle
        ? {
            lastActivityAt: this.iso(lastAt),
            lastActivitySource: lastSource as never,
            sources: (["api_gateway", "agentcore", "provenant", "keep_alive", "became_running"] as const).map((s) => ({
              source: s,
              at: this.iso(this.activity[s]),
              detail: s === "api_gateway" && this.activity[s] !== undefined ? "3 successful requests in its last active 5-minute period (simulated)" : undefined,
            })),
            keepAliveUntil: this.iso(this.keepAliveUntil),
            teardownAt: this.iso(due?.at),
            cooldownUntil: this.iso(this.cooldownUntil()),
            checkedAt: this.iso(t),
            maxSessionUntil: due ? this.iso(this.maxSessionUntil()) : undefined,
          }
        : undefined,
      cost: canOperate && coaConfig.prices
        ? { provenance: "estimate", usdPerHour: Math.round(rate * 10_000) / 10_000, sessionHours: hours, sessionUsd: hours === undefined ? undefined : Math.round(hours * rate * 100) / 100, components: coaConfig.prices.components, asOf: coaConfig.prices.asOf, source: "config/prices.yaml coaEnvironment", note: "List-price estimate while COA is up; not a billed amount.", residual }
        : undefined,
      endpoints: this.state === "running" || (this.state === "failed" && this.failedOperation === "up") ? { consoleUrl: `https://${SIM_HOST}/`, restEndpoint: `https://api.${SIM_HOST}/prod/`, readAt: this.iso(this.runningSince ?? t) } : undefined,
      error: this.error && { message: this.error.message, at: this.iso(this.error.at)!, phase: this.error.phase },
      eni: this.state === "partially_down" || this.eniRetries
        ? { retries: this.eniRetries, maxRetries: coaConfig.lifecycle.eniMaxRetries, nextRetryAt: this.iso(this.nextRetryAt), windowEndsAt: this.iso(this.eniFirstAt !== undefined ? this.eniFirstAt + coaConfig.lifecycle.eniWindowHours * 60 * MIN : undefined) }
        : undefined,
      history,
      viewer: { canOperate, adminGroup: coaConfig.adminGroup },
      lifecycle: { upMinutes: coaConfig.lifecycle.upMinutes as [number, number], downMinutes: coaConfig.lifecycle.downMinutes as [number, number], eniWindowHours: coaConfig.lifecycle.eniWindowHours, coaTag: coaConfig.coa.tag, stackPrefix: `${coaConfig.coa.prefix}-${coaConfig.coa.env}`, project: coaConfig.executor.project },
      cli: ["make coa-status", "make coa-up", "make coa-down"],
      detected: false,
      outcome,
      note: canOperate ? undefined : `Read only: changing the environment needs the ${coaConfig.adminGroup} group.`,
    };
  }

  up(canOperate: boolean, body: CoaUpRequest, by: string): CoaEnvironmentView {
    this.guard(canOperate, body.expectedVersion);
    if (this.state === "starting" || this.state === "running") return this.view(canOperate, "noop");
    if (this.state === "stopping") this.refuse(409, "conflict", "COA is tearing down. Spin it up again once the tear-down has finished.");
    if (this.state === "partially_down") this.refuse(409, "conflict", `COA's tear-down is waiting for AgentCore to release its network interfaces (up to ${coaConfig.lifecycle.eniWindowHours} h). COA cannot deploy over a VPC that is still being deleted; the retry runs by itself.`);
    if (this.state === "failed" && this.failedOperation === "down") this.refuse(409, "conflict", "The last tear-down failed, so parts of COA (possibly its VPC) may still exist and a deploy over them would fail. Tear down again first.");
    this.begin("up", "spin-up requested", by);
    this.nextOutcome = "ok";
    return this.view(canOperate, "started");
  }

  down(canOperate: boolean, body: CoaDownRequest, by: string): CoaEnvironmentView {
    this.guard(canOperate, body.expectedVersion);
    if (this.state === "off" || this.state === "stopping") return this.view(canOperate, "noop");
    let outcome: CoaEnvironmentView["outcome"] = "started";
    let reason = "tear-down requested";
    if (this.state === "starting") {
      if (!body.force) this.refuse(409, "conflict", "COA is spinning up. Tear it down after the spin-up finishes, or force the tear-down (stops the running build first).");
      if (this.build) {
        this.build.status = "STOPPED";
        this.build.endedAt = this.now();
      }
      outcome = "stopped_and_started";
      reason = "tear-down requested (forced during spin-up)";
    }
    this.beginDown(reason, by);
    this.nextOutcome = "ok";
    return this.view(canOperate, outcome);
  }

  settings(canOperate: boolean, body: CoaSettingsRequest): CoaEnvironmentView {
    this.guard(canOperate, body.expectedVersion);
    const { minMinutes, maxMinutes } = coaConfig.idle;
    if (!Number.isInteger(body.idleMinutes) || body.idleMinutes < minMinutes || body.idleMinutes > maxMinutes) this.refuse(400, "bad_request", `idleMinutes must be between ${minMinutes} and ${maxMinutes}`);
    this.autoTeardown = body.autoTeardown;
    this.idleMinutes = body.idleMinutes;
    this.version += 1;
    return this.view(canOperate, "updated");
  }

  keepAlive(canOperate: boolean, expected?: number): CoaEnvironmentView {
    this.guard(canOperate, expected);
    if (this.state !== "running" && this.state !== "starting") this.refuse(409, "conflict", "Keep alive applies while COA is starting or running.");
    const t = this.now();
    this.keepAliveUntil = Math.max(this.keepAliveUntil ?? 0, t + coaConfig.idle.keepAliveMinutes * MIN);
    this.activity.keep_alive = t;
    this.version += 1;
    return this.view(canOperate, "updated");
  }

  /** Simulated use of COA (a COA REST call or a Provenant call that reached it): activity, no version bump. */
  touch(source: "api_gateway" | "agentcore" | "provenant" = "api_gateway"): void {
    this.advance();
    if (this.state === "running" || this.state === "starting") this.activity[source] = this.now();
  }

  /** What the next simulated build does. */
  simulate(next: CoaSimulatedOutcome): void {
    this.nextOutcome = next;
    if (this.build && this.build.endedAt === undefined) this.build.outcome = next;
  }
}

let shared: CoaSimulation | undefined;

/** One simulation per page load, shared by User A and User B; `?coa=<state>|undeployed` picks where it starts. */
export function coaSimulation(): CoaSimulation {
  if (!shared) {
    const param = typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("coa") : null;
    const allowed: CoaSimStart[] = ["off", "starting", "running", "stopping", "failed", "partially_down", "undeployed"];
    shared = new CoaSimulation(allowed.includes(param as CoaSimStart) ? (param as CoaSimStart) : "off");
  }
  return shared;
}

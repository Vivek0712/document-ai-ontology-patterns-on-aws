/**
 * Settings → COA environment (/settings/environment): spin the Context Ontology Accelerator (COA) up and down, and
 * the idle watcher that tears it down when nobody uses it (docs/decisions/0006). The server decides everything
 * (rp-coa-control: state machine, admin check, audit); this page shows its view and asks it to act. Viewing this
 * page is never activity. Cost figures are list-price estimates, shown to environment admins only. In mock mode the
 * environment is a simulation and says so.
 */
import * as SwitchPrimitive from "@radix-ui/react-switch";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Circle, CircleDashed, ExternalLink, Info, Loader2, Power, PowerOff, Timer, XCircle } from "lucide-react";
import { type ReactNode, type RefObject, useEffect, useId, useRef, useState } from "react";
import type { CoaEnvironmentView, CoaEnvState, CoaPhase } from "@contracts/api";
import { useApi, useMe } from "../../api/context";
import { type CoaSimulatedOutcome, RpApiError } from "../../api/types";
import { Page, PageHeader } from "../../app/Page";
import { Badge, Button, Card, CardHeader, CodeBlock, DataList, Dialog, ErrorState, Num, ProvenanceScope, ScopeTag, Skeleton, Tooltip, cn, toast } from "../../design";

export const STATE_LABEL: Record<CoaEnvState, string> = {
  off: "Off",
  starting: "Starting",
  running: "Running",
  stopping: "Stopping",
  failed: "Failed",
  partially_down: "Partially down",
};
const STATE_TONE: Record<CoaEnvState, "neutral" | "running" | "live" | "loss" | "boundary"> = {
  off: "neutral",
  starting: "running",
  running: "live",
  stopping: "running",
  failed: "loss",
  partially_down: "boundary",
};
const SOURCE_LABEL: Record<string, string> = {
  api_gateway: "COA REST API requests",
  agentcore: "COA AgentCore invocations",
  provenant: "Provenant calls to COA",
  keep_alive: "Keep alive",
  became_running: "Became running",
};
export const TRANSITIONING: CoaEnvState[] = ["starting", "stopping", "partially_down"];

/** Who started a build or a state change, from the viewer's side ("you" for their own requests). */
export function who(by: string | undefined, meTag: string | undefined): string {
  if (!by) return "—";
  if (by === "idle-watcher") return "idle watcher";
  if (by === "executor") return "executor";
  if (by === "cli") return "CLI";
  if (meTag && by === meTag) return "you";
  return `user ${by}`;
}

/** After a failed tear-down parts of COA may remain, so spin-up is refused until a tear-down succeeds. */
export function canSpinUp(view: CoaEnvironmentView): boolean {
  return view.state === "off" || (view.state === "failed" && view.failedOperation !== "down");
}

// ---- time helpers ---------------------------------------------------------------------------------------------

const MIN = 60_000;
function ago(at: string | undefined, now: number): string {
  if (!at) return "—";
  const m = Math.round((now - Date.parse(at)) / MIN);
  if (m < 1) return "just now";
  if (m < 90) return `${m} min ago`;
  return `${(m / 60).toFixed(1)} h ago`;
}
export function inMinutes(at: string | undefined, now: number): string {
  if (!at) return "—";
  const m = Math.ceil((Date.parse(at) - now) / MIN);
  if (m <= 0) return "due now";
  if (m < 90) return `in ${m} min`;
  return `in ${(m / 60).toFixed(1)} h`;
}
function span(from: string | undefined, now: number): string {
  if (!from) return "—";
  const m = Math.max(0, Math.floor((now - Date.parse(from)) / MIN));
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${String(m % 60).padStart(2, "0")} min`;
}
const clock = (at?: string) => (at ? new Date(at).toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", timeZoneName: "short" }) : "—");
function When({ at, text }: { at?: string; text: string }) {
  return <time dateTime={at} title={at ? clock(at) : undefined}>{text}</time>;
}

/** The reference "now": the server's clock (simulated time in mock mode), advanced by real time in live mode. */
function useServerNow(view: CoaEnvironmentView | undefined, fetchedAt: number): number {
  const [real, setReal] = useState(() => Date.now());
  useEffect(() => {
    const id = window.setInterval(() => setReal(Date.now()), 15_000);
    return () => window.clearInterval(id);
  }, []);
  if (!view?.serverTime) return real;
  const base = Date.parse(view.serverTime);
  return view.mode === "simulated" ? base : base + Math.max(0, real - fetchedAt);
}

// ---- the page --------------------------------------------------------------------------------------------------

type Action = "up" | "down" | "keepalive" | "settings";

export default function EnvironmentScreen() {
  const api = useApi();
  const { sub } = useMe();
  const meTag = sub ? sub.slice(0, 6) : undefined;
  const qc = useQueryClient();
  const key = ["coa-env", sub];
  const q = useQuery({
    queryKey: key,
    queryFn: () => api.getCoaEnvironment(),
    staleTime: 0,
    refetchInterval: (query) => {
      const v = query.state.data as CoaEnvironmentView | undefined;
      const moving = v?.state ? TRANSITIONING.includes(v.state) : false;
      if (v?.mode === "simulated") return 1000;
      return moving ? 5000 : 30_000;
    },
  });
  const view = q.data;
  const now = useServerNow(view, q.dataUpdatedAt);
  const [busy, setBusy] = useState<Action>();

  const act = async (action: Action, call: () => Promise<CoaEnvironmentView>, done: (v: CoaEnvironmentView) => string): Promise<void> => {
    setBusy(action);
    try {
      const v = await call();
      qc.setQueryData(key, v);
      toast.success(done(v));
    } catch (e) {
      const err = e instanceof RpApiError ? e : undefined;
      toast.error(err?.message ?? (e instanceof Error ? e.message : String(e)));
      if (err?.code === "conflict") void q.refetch();
    } finally {
      setBusy(undefined);
    }
  };

  const simulated = view?.mode === "simulated";
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Settings" }, { label: "COA environment" }]}
        title="COA environment"
        description="Spin the Context Ontology Accelerator (COA) up and down. It costs money while it runs, so it tears itself down when nobody has used it for a while."
        meta={
          <>
            {view?.state && <StateChip state={view.state} />}
            {simulated && (
              <Badge tone="boundary" data-testid="coa-simulated" title="No AWS call: a simulated state machine in the mock world, with time running faster.">
                Simulated · mock mode
              </Badge>
            )}
          </>
        }
      />
      {q.error ? (
        <ErrorState title="The COA environment could not load" message={q.error instanceof Error ? q.error.message : String(q.error)} code={q.error instanceof RpApiError ? q.error.code : undefined} />
      ) : !view ? (
        <Skeleton className="h-[420px] rounded-md" />
      ) : (
        <EnvironmentPanel
          view={view}
          now={now}
          meTag={meTag}
          busy={busy}
          onUp={() => act("up", () => api.coaUp({ expectedVersion: view.version }), (v) => (v.outcome === "noop" ? `Nothing to do: COA is ${STATE_LABEL[v.state!].toLowerCase()}` : "Spin-up started"))}
          onDown={(force) =>
            act("down", () => api.coaDown({ expectedVersion: view.version, force }), (v) =>
              v.outcome === "noop" ? `Nothing to do: COA is ${STATE_LABEL[v.state!].toLowerCase()}` : v.outcome === "stopped_and_started" ? "Spin-up stopped; tear-down started" : "Tear-down started",
            )
          }
          onKeepAlive={() => void act("keepalive", () => api.coaKeepAlive({ expectedVersion: view.version }), (v) => `Kept alive until ${clock(v.idle?.keepAliveUntil)}`)}
          onSave={(autoTeardown, idleMinutes) =>
            void act("settings", () => api.coaSettings({ autoTeardown, idleMinutes, expectedVersion: view.version }), (v) => (v.settings?.autoTeardown ? `Auto tear-down after ${v.settings.idleMinutes} min idle` : "Auto tear-down off"))
          }
          onSimulate={api.coaSimulate ? (o) => api.coaSimulate!(o) : undefined}
        />
      )}
    </Page>
  );
}

export interface EnvironmentPanelProps {
  view: CoaEnvironmentView;
  now: number;
  /** the viewer's own 6-character tag (requests they made read "you") */
  meTag?: string;
  busy?: Action;
  onUp(): Promise<void>;
  onDown(force: boolean): Promise<void>;
  onKeepAlive(): void;
  onSave(autoTeardown: boolean, idleMinutes: number): void;
  onSimulate?(o: CoaSimulatedOutcome): void;
}

/** The page body for one view (exported for tests): not deployed, or status, progress, settings and history. */
export function EnvironmentPanel({ view, now, meTag, busy, onUp, onDown, onKeepAlive, onSave, onSimulate }: EnvironmentPanelProps) {
  const [confirm, setConfirm] = useState<"up" | "down">();
  const upRef = useRef<HTMLButtonElement>(null);
  const downRef = useRef<HTMLButtonElement>(null);
  if (!view.deployed) return <NotDeployed view={view} />;
  const failedDown = view.state === "failed" && view.failedOperation === "down";
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.45fr)_minmax(0,1fr)]" data-testid="coa-environment" data-state={view.state}>
      <div className="flex min-w-0 flex-col gap-4">
        <p className="sr-only" aria-live="polite" data-testid="coa-live">{`COA environment ${STATE_LABEL[view.state!]}`}</p>
        {view.notice && (
          <div role="status" data-testid="coa-notice" className="flex items-start gap-2 rounded-md border border-border-strong bg-surface-2/60 px-3 py-2 text-sm text-text">
            <Info className="mt-0.5 size-4 shrink-0 text-text-2" aria-hidden />
            <span>{view.notice}</span>
          </div>
        )}
        {view.error && (
          <ErrorState
            title={view.state === "running" ? "COA is up, with a problem" : failedDown ? "The tear-down failed: parts of COA may still be running and billing" : "The last operation failed"}
            message={view.error.message}
            code={view.error.phase ? `phase ${view.error.phase} · ${clock(view.error.at)}` : clock(view.error.at)}
            action={view.error.logUrl ? <LogLink href={view.error.logUrl} /> : undefined}
          />
        )}
        <StatusCard view={view} now={now} busy={busy} upRef={upRef} downRef={downRef} onUp={() => setConfirm("up")} onDown={() => setConfirm("down")} onKeepAlive={onKeepAlive} />
        {view.eni && view.state === "partially_down" && <EniCard view={view} now={now} />}
        {view.build && <ProgressCard view={view} now={now} meTag={meTag} />}
      </div>
      <div className="flex min-w-0 flex-col gap-4">
        <AutoTeardownCard view={view} now={now} busy={busy === "settings"} onSave={onSave} />
        <HistoryCard view={view} now={now} meTag={meTag} />
        {view.mode === "simulated" && onSimulate && <SimulationCard onPick={onSimulate} />}
      </div>
      {confirm && (
        <ConfirmDialog
          kind={confirm}
          view={view}
          busy={busy === confirm}
          returnFocus={confirm === "up" ? upRef : downRef}
          onClose={() => setConfirm(undefined)}
          onConfirm={(force) => void (confirm === "up" ? onUp() : onDown(force)).then(() => setConfirm(undefined))}
        />
      )}
    </div>
  );
}

export function StateChip({ state }: { state: CoaEnvState }) {
  const moving = state === "starting" || state === "stopping";
  return (
    <Badge tone={STATE_TONE[state]} dot={!moving} data-testid="coa-state" data-coa-state={state} icon={moving ? <Loader2 className="size-3 animate-spin motion-reduce:animate-none" aria-hidden /> : undefined}>
      {STATE_LABEL[state]}
    </Badge>
  );
}

function LogLink({ href, label = "Build log" }: { href: string; label?: string }) {
  if (href.startsWith("sim:")) return <span className="text-xs text-text-2">{label}: simulated</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs text-text-2 underline decoration-border-strong underline-offset-2 hover:text-text">
      {label} <ExternalLink className="size-3" aria-hidden />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

function NotDeployed({ view }: { view: CoaEnvironmentView }) {
  const cli = (view.cli ?? []).join("\n");
  return (
    <Card data-testid="coa-not-deployed">
      <CardHeader title="Environment control isn't deployed" meta="The opt-in stack rp-coa-control is not part of this deployment" />
      <div className="flex flex-col gap-3 px-4 py-3 text-sm text-text-2">
        <p>Deploy it with the CDK context <code className="font-mono text-mono-sm text-text">rp:coaControl=true</code> to spin COA up and down from here, with automatic tear-down when idle. Until then, use the command line from the repository root:</p>
        <CodeBlock code={cli} title="CLI" maxHeight={120} />
        <p className="text-xs">Without the stack, these run COA's own make targets in your COA checkout after a typed confirmation.</p>
      </div>
    </Card>
  );
}

function StatusCard({ view, now, busy, upRef, downRef, onUp, onDown, onKeepAlive }: { view: CoaEnvironmentView; now: number; busy?: Action; upRef: RefObject<HTMLButtonElement>; downRef: RefObject<HTMLButtonElement>; onUp(): void; onDown(): void; onKeepAlive(): void }) {
  const s = view.state!;
  const can = view.viewer.canOperate;
  const upAllowed = canSpinUp(view);
  const downAllowed = s === "running" || s === "failed" || s === "partially_down" || s === "starting";
  const idle = view.idle;
  const rows: [ReactNode, ReactNode][] = [
    ["State", <span key="s">{STATE_LABEL[s]} · since <When at={view.stateSince} text={ago(view.stateSince, now)} />{view.reason ? ` · ${view.reason}` : ""}</span>],
  ];
  if (view.runningSince && (s === "running" || (s === "failed" && view.failedOperation !== "down"))) rows.push(["Uptime", <span key="u">{span(view.runningSince, now)} · up since <When at={view.runningSince} text={clock(view.runningSince)} /></span>]);
  if (view.sessionStartedAt && s !== "off") rows.push(["Session started", <When key="ss" at={view.sessionStartedAt} text={clock(view.sessionStartedAt)} />]);
  if (s === "off" && view.stoppedAt) rows.push(["Stopped", <When key="st" at={view.stoppedAt} text={`${clock(view.stoppedAt)} (${ago(view.stoppedAt, now)})`} />]);
  if (idle && (s === "running" || s === "failed" || s === "starting")) {
    rows.push(["Last activity", idle.lastActivityAt ? <span key="la">{SOURCE_LABEL[idle.lastActivitySource ?? ""] ?? idle.lastActivitySource} · <When at={idle.lastActivityAt} text={ago(idle.lastActivityAt, now)} /></span> : "none yet"]);
    rows.push([
      "Auto tear-down",
      view.settings?.autoTeardown ? (
        idle.teardownAt ? (
          <span key="td" data-testid="coa-teardown-at" className="inline-flex flex-wrap items-center gap-1">
            <Timer className="size-3.5 text-text-3" aria-hidden />
            <When at={idle.teardownAt} text={inMinutes(idle.teardownAt, now)} />{" "}
            <span className="text-text-2">
              · {idle.maxSessionUntil && idle.teardownAt === idle.maxSessionUntil ? "session cap reached" : `after ${view.settings.idleMinutes} min idle`}
              {idle.maxSessionUntil ? ` · cap ${clock(idle.maxSessionUntil)}` : ""}
            </span>
          </span>
        ) : (
          "after the spin-up finishes"
        )
      ) : (
        "off"
      ),
    ]);
  }
  if (view.endpoints && (view.endpoints.consoleUrl || view.endpoints.restEndpoint)) {
    if (view.endpoints.consoleUrl) rows.push(["COA console", <ExtLink key="c" href={view.endpoints.consoleUrl} simulated={view.mode === "simulated"} />]);
    if (view.endpoints.restEndpoint) rows.push(["REST endpoint", <span key="r" className="break-all font-mono text-mono-sm">{view.endpoints.restEndpoint}</span>]);
  }
  if (view.lifecycle) rows.push(["Release", <span key="rel" className="font-mono text-mono-sm">COA {view.lifecycle.coaTag} · {view.lifecycle.stackPrefix}-*</span>]);
  return (
    <Card data-testid="coa-status">
      <CardHeader title="Status" meta={view.detected ? "First state read from CloudFormation" : view.mode === "simulated" ? "Simulated environment: no AWS call" : `Executor ${view.lifecycle?.project ?? ""}`} />
      <div className="flex flex-col gap-3 px-4 py-3">
        <DataList rows={rows} />
        {view.cost && <CostRow view={view} />}
        {can ? (
          <div className="flex flex-wrap items-center gap-2 border-t border-border pt-3">
            <Button ref={upRef} size="sm" variant="primary" icon={<Power />} onClick={onUp} disabled={!upAllowed || Boolean(busy)} loading={busy === "up"}>
              Spin up
            </Button>
            <Button ref={downRef} size="sm" variant="danger" icon={<PowerOff />} onClick={onDown} disabled={!downAllowed || Boolean(busy)} loading={busy === "down"}>
              {s === "failed" && view.failedOperation === "down" ? "Tear down again" : "Tear down"}
            </Button>
            {(s === "running" || s === "starting") && (
              <Button size="sm" variant="secondary" icon={<Timer />} onClick={onKeepAlive} disabled={Boolean(busy)} loading={busy === "keepalive"}>
                {`Keep alive ${view.settings?.keepAliveMinutes ?? 60} min`}
              </Button>
            )}
            <span className="text-xs text-text-2" data-testid="coa-hint">{actionHint(view)}</span>
          </div>
        ) : (
          <p className="border-t border-border pt-3 text-xs text-text-2" data-testid="coa-read-only">
            {view.note ?? `Read only: changing the environment needs the ${view.viewer.adminGroup ?? "rp-admin"} group.`}
          </p>
        )}
      </div>
    </Card>
  );
}

export function actionHint(view: CoaEnvironmentView): string {
  switch (view.state) {
    case "starting":
      return "Spinning up; tearing down now stops the build first.";
    case "stopping":
      return "Tearing down; spin up again once it finishes.";
    case "partially_down":
      return "Waiting for AgentCore ENIs; COA cannot be deployed until they are released.";
    case "failed":
      return view.failedOperation === "down"
        ? "Parts of COA may still exist: tear down again (it is retried once by itself). Spin-up is possible after a tear-down succeeds."
        : "Retry the spin-up, or tear down what is left.";
    default:
      return "";
  }
}

function ExtLink({ href, simulated }: { href: string; simulated: boolean }) {
  if (simulated) return <span className="break-all font-mono text-mono-sm text-text-2">{href} (simulated)</span>;
  return (
    <a href={href} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 break-all font-mono text-mono-sm underline decoration-border-strong underline-offset-2">
      {href} <ExternalLink className="size-3 shrink-0" aria-hidden />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}

function CostRow({ view }: { view: CoaEnvironmentView }) {
  const c = view.cost!;
  return (
    <ProvenanceScope provenance="estimate" source={`${c.source ?? "config/prices.yaml"} · as of ${c.asOf}`} label="Estimate">
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1 rounded-sm border border-border bg-surface-2/50 px-3 py-2 text-sm" data-testid="coa-cost">
        <span className="text-text-2">While up</span>
        <span>
          $<Num value={c.usdPerHour} kind="money" />
          <span className="text-text-2">/h</span>
        </span>
        {c.sessionUsd !== undefined && (
          <>
            <span className="text-text-2">This session</span>
            <span>
              $<Num value={c.sessionUsd} kind="money" /> <span className="text-text-2">· {c.sessionHours?.toFixed(2)} h</span>
            </span>
          </>
        )}
        <ScopeTag className="ml-auto" />
        <Tooltip content={<span className="flex flex-col gap-0.5">{c.components.map((x) => <span key={x.id}>{x.label}: ${x.usdPerHour.toFixed(2)}/h</span>)}<span className="text-text-2">{c.note}</span></span>}>
          <button type="button" className="text-xs text-text-2 underline decoration-dotted underline-offset-2">components</button>
        </Tooltip>
        {c.residual && (
          <p className="basis-full text-xs text-text-2" data-testid="coa-cost-residual">
            {c.residual}
          </p>
        )}
      </div>
    </ProvenanceScope>
  );
}

function EniCard({ view, now }: { view: CoaEnvironmentView; now: number }) {
  const e = view.eni!;
  return (
    <Card data-testid="coa-eni">
      <CardHeader title="Partially down: waiting for AgentCore ENIs" meta="AgentCore releases a runtime's network interfaces up to 8 h after the runtime is deleted" />
      <div className="px-4 py-3">
        <DataList
          rows={[
            ["Attempts", `${e.retries} of ${e.maxRetries}`],
            ["Next retry", e.nextRetryAt ? <When at={e.nextRetryAt} text={inMinutes(e.nextRetryAt, now)} /> : "—"],
            ["Window ends", e.windowEndsAt ? <When at={e.windowEndsAt} text={clock(e.windowEndsAt)} /> : "—"],
          ]}
        />
        <p className="mt-2 text-xs text-text-2">The VPC, its subnets and two security groups stay until then. The idle watcher runs COA's destroy again; nothing else in COA is left.</p>
      </div>
    </Card>
  );
}

const PHASE_ICON: Record<string, ReactNode> = {
  pending: <Circle className="size-3.5 text-text-3" aria-hidden />,
  running: <Loader2 className="size-3.5 animate-spin text-text-2 motion-reduce:animate-none" aria-hidden />,
  succeeded: <CheckCircle2 className="size-3.5 text-text-2" aria-hidden />,
  failed: <XCircle className="size-3.5 text-loss" aria-hidden />,
  skipped: <CircleDashed className="size-3.5 text-text-3" aria-hidden />,
};

function phaseTime(p: CoaPhase, now: number): string {
  if (!p.startedAt) return "";
  const end = p.endedAt ? Date.parse(p.endedAt) : now;
  const m = Math.max(0, Math.round((end - Date.parse(p.startedAt)) / MIN));
  return m < 1 ? "< 1 min" : `${m} min`;
}

function ProgressCard({ view, now, meTag }: { view: CoaEnvironmentView; now: number; meTag?: string }) {
  const b = view.build!;
  const phases = b.phases ?? [];
  const finished = Boolean(b.endedAt);
  return (
    <Card data-testid="coa-progress">
      <CardHeader
        title={`${b.operation === "up" ? "Spin-up" : "Tear-down"}${b.attempt && b.attempt > 1 ? ` · attempt ${b.attempt}` : ""}`}
        meta={
          <span>
            {finished ? `finished ${ago(b.endedAt, now)}` : `started ${ago(b.startedAt, now)}`} · {b.requestedBy === "idle-watcher" ? "idle watcher" : `by ${who(b.requestedBy, meTag)}`} · {b.reason}
          </span>
        }
        actions={b.logUrl ? <LogLink href={b.logUrl} label="Log tail" /> : view.mode === "simulated" ? <span className="text-xs text-text-2">Log: simulated</span> : undefined}
      />
      <ol className="flex flex-col px-4 py-2" aria-label={`${b.operation === "up" ? "Spin-up" : "Tear-down"} phases`}>
        {phases.map((p) => (
          <li key={p.id} className="flex h-7 items-center gap-2 text-sm" data-phase={p.id} data-phase-status={p.status}>
            {PHASE_ICON[p.status ?? "pending"]}
            <span className={cn("min-w-0 flex-1 truncate", p.status === "pending" || p.status === "skipped" ? "text-text-2" : "text-text")}>{p.label}</span>
            <span className="sr-only">{p.status}</span>
            <span className="font-mono text-mono-sm text-text-2">{phaseTime(p, now)}</span>
          </li>
        ))}
      </ol>
      <div className="border-t border-border px-4 py-2 font-mono text-mono-sm text-text-2">
        {b.id.startsWith("pending:") ? "build requested; waiting for CodeBuild" : b.id} {b.status ? `· ${b.status}` : ""}
      </div>
    </Card>
  );
}

function AutoTeardownCard({ view, now, busy, onSave }: { view: CoaEnvironmentView; now: number; busy: boolean; onSave(auto: boolean, minutes: number): void }) {
  const s = view.settings!;
  const [auto, setAuto] = useState(s.autoTeardown);
  const [minutes, setMinutes] = useState(String(s.idleMinutes));
  useEffect(() => {
    setAuto(s.autoTeardown);
    setMinutes(String(s.idleMinutes));
  }, [s.autoTeardown, s.idleMinutes]);
  const n = Number(minutes);
  const valid = Number.isInteger(n) && n >= s.minMinutes && n <= s.maxMinutes;
  const dirty = auto !== s.autoTeardown || n !== s.idleMinutes;
  const can = view.viewer.canOperate;
  const switchId = useId();
  const inputId = useId();
  const hintId = useId();
  return (
    <Card data-testid="coa-auto-teardown">
      <CardHeader title="Auto tear-down" meta="The idle watcher checks every 5 minutes" />
      <form
        className="flex flex-col gap-3 px-4 py-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (valid && dirty) onSave(auto, n);
        }}
      >
        <div className="flex items-center gap-3">
          <SwitchPrimitive.Root
            id={switchId}
            checked={auto}
            onCheckedChange={setAuto}
            disabled={!can}
            className="relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border border-border-strong bg-surface-2 transition-colors data-[state=checked]:bg-text disabled:opacity-50"
          >
            <SwitchPrimitive.Thumb className="block size-3.5 translate-x-0.5 rounded-full bg-text-2 transition-transform data-[state=checked]:translate-x-[18px] data-[state=checked]:bg-inverse" />
          </SwitchPrimitive.Root>
          <label htmlFor={switchId} className="text-sm text-text">
            Tear down when idle
          </label>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label htmlFor={inputId} className="text-sm text-text-2">
            Idle minutes
          </label>
          <input
            id={inputId}
            type="number"
            inputMode="numeric"
            min={s.minMinutes}
            max={s.maxMinutes}
            step={5}
            value={minutes}
            disabled={!can || !auto}
            aria-invalid={!valid}
            aria-describedby={hintId}
            onChange={(e) => setMinutes(e.target.value)}
            className={cn("h-7 w-20 rounded-sm border bg-surface px-2 font-mono text-mono-sm text-text disabled:opacity-50", valid ? "border-border-strong" : "border-loss")}
          />
          <span id={hintId} className="text-xs text-text-2">
            {s.minMinutes}–{s.maxMinutes}, default 60
          </span>
          {can && (
            <Button type="submit" size="sm" variant="secondary" disabled={!valid || !dirty} loading={busy} className="ml-auto">
              Save
            </Button>
          )}
        </div>
        <p className="text-xs text-text-2">
          Activity is the latest of: successful COA REST API requests (rejected and unauthenticated calls do not count), COA AgentCore invocations, Provenant calls to COA and Keep alive. Viewing this page is not activity. No tear-down starts within 15 minutes of a state change or while a build runs. {s.maxSessionHours ? `With auto tear-down on, a session is also torn down ${s.maxSessionHours} hours after it came up unless kept alive.` : ""}
        </p>
        {view.idle?.activityError && (
          <p role="alert" data-testid="coa-activity-error" className="rounded-sm border border-dashed border-loss px-2 py-1 text-xs text-text">
            {view.idle.activityError} (since <When at={view.idle.activityErrorSince} text={ago(view.idle.activityErrorSince, now)} />). No tear-down starts on it; once it lasts longer than the idle threshold the watcher decides on the activity it last saw.
          </p>
        )}
        {view.idle?.sources && (
          <table className="w-full text-xs" aria-label="Activity sources">
            <tbody>
              {view.idle.sources.map((a) => (
                <tr key={a.source} className="h-6 border-t border-border">
                  <th scope="row" className="pr-2 text-left font-normal text-text-2">
                    {SOURCE_LABEL[a.source] ?? a.source}
                  </th>
                  <td className="text-right font-mono text-mono-sm text-text">
                    <When at={a.at} text={a.at ? ago(a.at, now) : "—"} />
                    {a.detail && <span className="block font-sans text-xs text-text-2">{a.detail}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {view.idle?.checkedAt && view.mode !== "simulated" && <p className="text-xs text-text-2">Watcher last ran {ago(view.idle.checkedAt, now)}.</p>}
      </form>
    </Card>
  );
}

function HistoryCard({ view, now, meTag }: { view: CoaEnvironmentView; now: number; meTag?: string }) {
  const h = view.history ?? [];
  return (
    <Card data-testid="coa-history">
      <CardHeader title="History" meta="Last state changes (also audited in rp-access-log)" />
      {h.length === 0 ? (
        <p className="px-4 py-3 text-sm text-text-2">No state changes yet.</p>
      ) : (
        <ol className="flex flex-col px-4 py-2">
          {h.map((e, i) => (
            <li key={i} className="flex min-h-7 flex-wrap items-center gap-x-2 border-t border-border py-1 text-xs first:border-t-0">
              <span className="text-text">
                {STATE_LABEL[e.fromState]} → {STATE_LABEL[e.toState]}
              </span>
              <span className="text-text-2">{e.reason}</span>
              <span className="ml-auto font-mono text-mono-sm text-text-2">
                {who(e.by, meTag)} · <When at={e.at} text={ago(e.at, now)} />
              </span>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function SimulationCard({ onPick }: { onPick(o: CoaSimulatedOutcome): void }) {
  const [value, setValue] = useState<CoaSimulatedOutcome>("ok");
  const id = useId();
  return (
    <Card data-testid="coa-simulation">
      <CardHeader title="Simulation" meta="Mock mode only: what the next simulated build does" />
      <div className="flex items-center gap-2 px-4 py-3">
        <label htmlFor={id} className="text-sm text-text-2">
          Next build
        </label>
        <select
          id={id}
          value={value}
          onChange={(e) => {
            const v = e.target.value as CoaSimulatedOutcome;
            setValue(v);
            onPick(v);
          }}
          className="h-7 rounded-sm border border-border-strong bg-surface px-2 text-sm text-text"
        >
          <option value="ok">succeeds</option>
          <option value="fail">fails in the build step</option>
          <option value="eni_pending">tear-down leaves ENIs (exit 3)</option>
        </select>
      </div>
    </Card>
  );
}

function ConfirmDialog({ kind, view, busy, returnFocus, onClose, onConfirm }: { kind: "up" | "down"; view: CoaEnvironmentView; busy: boolean; returnFocus: RefObject<HTMLButtonElement>; onClose(): void; onConfirm(force: boolean): void }) {
  const lc = view.lifecycle;
  const forceNeeded = kind === "down" && view.state === "starting";
  const [force, setForce] = useState(false);
  const forceId = useId();
  const rate = view.cost?.usdPerHour;
  const up = kind === "up";
  const title = up ? "Spin COA up?" : "Tear COA down?";
  const items: ReactNode[] = up
    ? [
        <>Takes about {lc?.upMinutes[0]}–{lc?.upMinutes[1]} minutes: COA {lc?.coaTag} is deployed fresh ({lc?.stackPrefix}-* stacks).</>,
        rate !== undefined ? <>Costs about ${rate.toFixed(2)} per hour while it runs (list-price estimate; Fargate, Neptune, OpenSearch Serverless, VPC endpoints and NAT).</> : <>Costs money every hour it runs (Fargate, Neptune, OpenSearch Serverless, VPC endpoints and NAT).</>,
        <>The governed-metric demo is seeded automatically afterwards: namespace, fin.ttl, Glue source and the qualifying_income metric.</>,
        <>A new COA Cognito user pool is created: the COA admin gets a new temporary-password e-mail and must sign in to COA again.</>,
        view.settings?.autoTeardown ? <>It tears itself down after {view.settings.idleMinutes} minutes without activity.</> : <>Auto tear-down is off: it runs until someone tears it down.</>,
        <>It is refused while a previous VPC is still being deleted (Partially down, up to {lc?.eniWindowHours ?? 8} hours) or after a failed tear-down.</>,
      ]
    : [
        <>Takes about {lc?.downMinutes[0]}–{lc?.downMinutes[1]} minutes.</>,
        rate !== undefined ? <>Stops about ${rate.toFixed(2)} per hour (list-price estimate). While Partially down only the VPC, subnets and security groups remain, with no hourly charge of note.</> : <>Stops COA's hourly cost. While Partially down only the VPC, subnets and security groups remain, with no hourly charge of note.</>,
        <strong key="loss" className="font-medium text-text">Deletes everything inside COA: the uploaded ontology, the metric, the namespace and the Neptune data. Spinning up again re-seeds the demo, not anything else.</strong>,
        <>AgentCore can keep its network interfaces for up to {lc?.eniWindowHours ?? 8} hours. Until then the VPC stays and the state is Partially down; the retry runs by itself.</>,
        <>Provenant's own screens keep working: SC-8 and W-10 show the recorded COA snapshot.</>,
      ];
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && !busy && onClose()}
      title={title}
      onCloseAutoFocus={(e) => {
        e.preventDefault();
        returnFocus.current?.focus();
      }}
    >
      {/* Escape also closes from here: right after opening, Radix's layer can miss a very quick Escape */}
      <div
        className="flex flex-col gap-3 p-4"
        data-testid={`coa-confirm-${kind}`}
        onKeyDown={(e) => {
          if (e.key === "Escape" && !e.defaultPrevented && !busy) {
            e.preventDefault();
            onClose();
          }
        }}
      >
        <div className="flex items-center gap-2 text-md font-medium text-text">
          {!up && <AlertTriangle className="size-4 text-loss" aria-hidden />}
          {title}
          {view.mode === "simulated" && <Badge tone="boundary">Simulated</Badge>}
        </div>
        <ul className="flex list-disc flex-col gap-1.5 pl-5 text-sm text-text-2">
          {items.map((it, i) => (
            <li key={i}>{it}</li>
          ))}
        </ul>
        {forceNeeded && (
          <div className="flex items-start gap-2 rounded-sm border border-dashed border-loss px-3 py-2 text-sm">
            <input id={forceId} type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} className="mt-0.5" />
            <label htmlFor={forceId} className="text-text">
              Force: COA is still spinning up. Stop that build first, then tear down.
            </label>
          </div>
        )}
        <div className="flex justify-end gap-2 border-t border-border pt-3">
          <Button size="sm" variant="ghost" onClick={onClose} disabled={busy}>
            Cancel
          </Button>
          <Button size="sm" variant={up ? "primary" : "danger"} onClick={() => onConfirm(force)} disabled={forceNeeded && !force} loading={busy} data-testid={`coa-confirm-${kind}-go`}>
            {up ? "Spin up" : "Tear down"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}

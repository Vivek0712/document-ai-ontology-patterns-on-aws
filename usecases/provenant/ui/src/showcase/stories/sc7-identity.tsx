/**
 * SC-7 · Identity and scoping. Two analysts ask the same question. The gateway injects who is asking
 * into every tool call (the model never supplies it), `search_units` filters by those principals inside the query,
 * and the region service checks access again: User B's request for User A's cited region is refused by the API.
 *
 * Two columns, one per identity, stay put across the steps; each step changes what they show. Frames are pure
 * functions of (payload, step, progress); every value comes from the payload; captions follow the data.
 */
import { easeCubicInOut, easeCubicOut } from "d3-ease";
import { ArrowRight, Lock, ShieldCheck } from "lucide-react";
import type { AccessDecision } from "@contracts/api";
import { Badge, CodeBlock, DecisionBadge, LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, cn, decisionLabel } from "../../design";
import { subLabel } from "../../lib/format";
import { type Rect, pad, seg, toRect, union } from "../../viz/geom";
import { Boundary, BoxOutline, PageCanvas } from "../../viz/PageCanvas";
import { showcaseMeta } from "../catalog";
import { StoryStage } from "../engine/StoryStage";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { LANES } from "./lending";
import { Typed } from "./parts";
import { compactJson } from "./sc4-filters";
import { FOCUS_LANE, type Probe, type Sc7Payload, type Sc7User, USERS, injectionCaption, loadSc7, regionCaption, scopedCaption, userOf } from "./sc7-payload";
import { SC7_STEPS } from "./steps";

const STEP = Object.fromEntries(SC7_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC7_STEPS)[number]["id"], number>;
const ease = easeCubicInOut;

/** The identities' full subs as their sessions report them: what a 6-character `callerSub6` is shown against. */
const knownSubs = (p: Sc7Payload) => [p.me.a?.sub, p.me.b?.sub];

const runReceipts = (p: Sc7Payload) =>
  p.data.users.flatMap((u) => LANES.map((pv) => ({ label: `${u.role === "owner" ? "User A" : "User B"} · ${pv}`, value: u.runs[pv]?.runId ?? "no run", source: u.runs[pv] ? `runs/${u.runs[pv]!.runId}.json` : undefined, provenance: p.provenance })));

const steps: StoryStep<Sc7Payload>[] = [
  {
    ...SC7_STEPS[0],
    durationMs: 1800,
    caption: "Two analysts, two groups.",
    services: ["Amazon Cognito"],
    receipts: (p) => [
      ...USERS.map((u) => ({ label: `User ${u.letter} · groups`, value: userOf(p, u.role)?.groups.join(", "), path: `$.data.users[?role=='${u.role}'].groups`, provenance: p.provenance })),
      { label: "Package admits", value: p.packagePrincipals.join(", "), source: `GET /packages/${p.data.packageId}`, provenance: p.provenance },
    ],
  },
  {
    ...SC7_STEPS[1],
    durationMs: 2600,
    caption: "The same question from each user.",
    services: ["Amazon API Gateway", "Amazon Bedrock AgentCore"],
    receipts: runReceipts,
  },
  {
    ...SC7_STEPS[2],
    durationMs: 3000,
    caption: injectionCaption,
    services: ["AgentCore Gateway · interceptor"],
    receipts: (p) =>
      USERS.flatMap((u) => {
        const r = userOf(p, u.role)?.runs[FOCUS_LANE];
        return (r ? p.data.access[r.runId] ?? [] : []).map((a) => ({ label: `User ${u.letter} · ${a.tool} · ${decisionLabel(a)}`, value: `injected ${subLabel(a.callerSub6, knownSubs(p))} · ${a.groups.join(",")} · ${a.injected.packageId} · ${a.injected.pipelineVersion} · via ${a.injectionPoint}`, source: `GET /access/decisions?runId=${a.runId} (${a.source})`, provenance: p.provenance }));
      }),
  },
  {
    ...SC7_STEPS[3],
    durationMs: 2600,
    caption: scopedCaption,
    services: ["AWS Lambda · search_units", "Amazon OpenSearch Serverless"],
    receipts: (p) =>
      USERS.flatMap((u) =>
        (userOf(p, u.role)?.runs[FOCUS_LANE]?.search ?? []).map((c) => ({ label: `User ${u.letter} · search_units ${c.status}`, value: c.error ?? `${c.units} units`, path: `$.data.users[?role=='${u.role}'].runs['${FOCUS_LANE}'].search`, provenance: p.provenance })),
      ),
  },
  {
    ...SC7_STEPS[4],
    durationMs: 2200,
    caption: regionCaption,
    services: ["Amazon API Gateway", "AWS Lambda · rp-get-region"],
    receipts: (p) => [
      { label: "Region probed (User A's cited Box 1)", value: `${p.data.regionProbe.unitId} · ${p.data.regionProbe.fieldId ?? ""}`, path: "$.data.regionProbe", provenance: "derived" },
      { label: "GET /region as User A", value: probeLine(p.probes.regionA), provenance: p.provenance },
      { label: "GET /region as User B", value: probeLine(p.probes.regionB), provenance: p.provenance },
      { label: "GET /packages as User B", value: probeLine(p.probes.packageB), provenance: p.provenance },
    ],
  },
];

const probeLine = (pr: Probe) => (pr.state === "ok" ? "200" : pr.state === "refused" ? `${pr.status} ${pr.message}` : "no session for this identity");

// --------------------------------------------------------------------------------------------- frame

function Sc7Stage({ payload: p, step, progress: t }: Frame<Sc7Payload>) {
  return (
    <ProvenanceScope provenance={p.provenance} source={`GET /showcases/sc-7/payload · ${p.payloadSha.slice(0, 12)}`}>
      <StoryStage
        maxWidth={1280}
        header={
          <header className="flex flex-wrap items-center gap-2 text-sm">
            <p className="text-md text-text">“{p.data.question}”</p>
            <span className="font-mono text-mono-sm text-text-2">{p.qid}</span>
            <span className="ml-auto flex items-center gap-2 text-xs text-text-2">
              {p.packageName}
              <span className="font-mono">{p.data.packageId}</span>
              <ScopeTag />
            </span>
          </header>
        }
      >
        {step === STEP.region ? (
          <RegionCheck p={p} t={t} />
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {USERS.map((u, i) => {
              const user = userOf(p, u.role);
              return (
                <section key={u.role} className="flex min-w-0 flex-col rounded-md border border-border bg-surface" data-user={u.letter}>
                  <IdentityHead p={p} letter={u.letter} keyName={u.key} user={user} />
                  <div className="flex flex-col gap-3 p-4">
                    {step === STEP.users && <Principals p={p} user={user} t={seg(t, i * 0.3, 0.6 + i * 0.3)} />}
                    {step === STEP["same-question"] && user && <Answers user={user} t={seg(t, i * 0.15, 0.85 + i * 0.15)} />}
                    {step === STEP.injection && user && <Injection p={p} user={user} t={t} />}
                    {step === STEP.scoped && user && <Scoped user={user} t={seg(t, i * 0.2, 0.8 + i * 0.2)} />}
                  </div>
                </section>
              );
            })}
          </div>
        )}
      </StoryStage>
    </ProvenanceScope>
  );
}

function IdentityHead({ p, letter, keyName, user }: { p: Sc7Payload; letter: "A" | "B"; keyName: "a" | "b"; user?: Sc7User }) {
  const me = p.me[keyName];
  return (
    <header className="flex items-center gap-2.5 border-b border-border px-4 py-2.5">
      <span className="flex size-6 items-center justify-center rounded-full bg-surface-3 text-xs font-semibold text-text">{letter}</span>
      <span className="flex min-w-0 flex-col">
        <span className="truncate text-sm text-text">{me?.email ?? `User ${letter}`}</span>
        <span className="font-mono text-mono-sm text-text-2" title={me?.sub ?? user?.callerSub6}>
          sub {subLabel(user?.callerSub6, knownSubs(p))}
        </span>
      </span>
      <span className="ml-auto flex flex-wrap justify-end gap-1">
        {(user?.groups ?? []).map((g) => (
          <Badge key={g} tone="neutral">
            {g}
          </Badge>
        ))}
        <Badge tone="strong">{user?.role === "owner" ? "package owner" : "another analyst"}</Badge>
      </span>
    </header>
  );
}

/** Step 1: the package's principals, and which of them the user's token carries. */
function Principals({ p, user, t }: { p: Sc7Payload; user?: Sc7User; t: number }) {
  const mine = new Set([...(user?.groups ?? []).map((g) => `group:${g}`)]);
  const owner = user?.role === "owner";
  return (
    <div className="flex flex-col gap-2">
      <span className="text-xs text-text-2">The package admits</span>
      <ul className="flex flex-col gap-1.5">
        {p.packagePrincipals.map((pr, i) => {
          const match = mine.has(pr) || (owner && pr.startsWith("user:"));
          const on = easeCubicOut(seg(t, 0.2 + i * 0.2, 0.6 + i * 0.2));
          return (
            <li key={pr} className={cn("flex items-center gap-2 rounded-xs border px-2.5 py-1.5 font-mono text-mono-sm", match && on > 0.5 ? "border-text-2 text-text" : "border-border text-text-2")}>
              <span className="min-w-0 flex-1 truncate">{pr}</span>
              {on > 0.5 && (match ? <span className="text-xs text-text">token carries it</span> : <span className="text-xs text-text-3">not in this token</span>)}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/** Step 2: each user's logged answer per lane, replaying. */
function Answers({ user, t }: { user: Sc7User; t: number }) {
  return (
    <ul className="flex flex-col gap-3">
      {LANES.map((pv, i) => {
        const r = user.runs[pv];
        return (
          <li key={pv} className="flex flex-col gap-1.5 border-b border-border pb-3 last:border-0 last:pb-0">
            <span className="flex items-center gap-2">
              <LaneBadge pv={pv} />
              <span className="font-mono text-mono-sm text-text-2">{r?.runId ?? "no run"}</span>
              {r?.total ? <Num value={r.total} kind="money" className="ml-auto" /> : <span className="ml-auto text-xs text-text-2">no total</span>}
            </span>
            {r?.answer && <Typed text={r.answer} t={ease(seg(t, i * 0.4, 0.6 + i * 0.4))} className="line-clamp-3 text-text-2" />}
          </li>
        );
      })}
      <li className="text-xs text-text-2">
        <Num value={user.n[FOCUS_LANE] ?? 0} kind="int" size="xs" bare /> logged runs of this user on {FOCUS_LANE}
      </li>
    </ul>
  );
}

/** Step 3: per tool call, what the model sent (names only) and what the gateway injected. */
function Injection({ p, user, t }: { p: Sc7Payload; user: Sc7User; t: number }) {
  const r = user.runs[FOCUS_LANE];
  const rows = r ? p.data.access[r.runId] ?? [] : [];
  return (
    <div className="flex flex-col gap-2">
      <span className="flex items-center gap-2 text-xs text-text-2">
        <LaneBadge pv={FOCUS_LANE} /> {rows.length} tool calls · access log {rows[0]?.source === "run-record" && <ProvenanceTag provenance="derived" source="rebuilt from the run record's injected values (this run has no access-log rows)" />}
      </span>
      <ol className="flex flex-col gap-2">
        {rows.map((a, i) => (
          <InjectionRow key={a.callId} a={a} known={knownSubs(p)} on={easeCubicOut(seg(t, i * 0.22, i * 0.22 + 0.35))} />
        ))}
      </ol>
    </div>
  );
}

function InjectionRow({ a, known, on }: { a: AccessDecision; known: (string | undefined)[]; on: number }) {
  const injected: [string, string][] = [
    ["callerSub", subLabel(a.callerSub6, known)],
    ["groups", a.groups.join(", ")],
    ["packageId", a.injected.packageId],
    ["pipelineVersion", a.injected.pipelineVersion],
  ];
  return (
    <li className="grid grid-cols-[minmax(0,0.9fr)_20px_minmax(0,1.4fr)] items-start gap-2 rounded-sm border border-border p-2.5" style={{ opacity: 0.15 + 0.85 * on }}>
      <div className="flex min-w-0 flex-col gap-1">
        <span className="font-mono text-mono-sm text-text">{a.tool}</span>
        <span className="text-xs text-text-2">model sent</span>
        <span className="flex flex-wrap gap-1">
          {a.modelArgs.map((m) => (
            <span key={m} className="rounded-xs border border-border px-1.5 font-mono text-mono-sm text-text-2">
              {m}
            </span>
          ))}
        </span>
        {a.strippedModelKeys.length > 0 && <span className="text-xs text-text-2">stripped: {a.strippedModelKeys.join(", ")}</span>}
      </div>
      <ArrowRight className="mt-6 size-3.5 text-text-3" aria-hidden />
      <div className="flex min-w-0 flex-col gap-1">
        <span className="flex items-center gap-1.5 text-xs text-text-2">
          <ShieldCheck className="size-3.5" aria-hidden /> injected by <span className="font-mono">{a.injectionPoint}</span>
          <DecisionBadge d={a} className="ml-auto" />
        </span>
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-2 font-mono text-mono-sm">
          {injected.map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="text-text-2">{k}</dt>
              <dd className="truncate text-text" title={v}>
                {v}
              </dd>
            </div>
          ))}
        </dl>
      </div>
    </li>
  );
}

/** Step 4: what `search_units` returned for this user, and the filter (principals first) it ran. */
function Scoped({ user, t }: { user: Sc7User; t: number }) {
  const calls = user.runs[FOCUS_LANE]?.search ?? [];
  const units = calls.reduce((s, c) => s + c.units, 0);
  const shown = calls.find((c) => c.filter) ?? calls[0];
  const on = easeCubicOut(seg(t, 0, 0.5));
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-2" style={{ opacity: 0.2 + 0.8 * on }}>
        <Num value={Math.round(units * on)} kind="int" size="lg" />
        <span className="text-sm text-text-2">units returned across {calls.length} search_units {calls.length === 1 ? "call" : "calls"}</span>
      </div>
      {shown?.status === "denied" ? (
        <p className="flex items-start gap-2 rounded-sm border border-dashed border-loss px-3 py-2 text-sm text-text" style={{ opacity: seg(t, 0.4, 0.8) }}>
          <Lock className="mt-0.5 size-3.5 shrink-0 text-loss" aria-hidden />
          <span>
            <span className="font-mono text-mono-sm">search_units · denied</span>
            <span className="block text-text-2">{shown.error}</span>
          </span>
        </p>
      ) : shown?.filter ? (
        <div style={{ opacity: seg(t, 0.4, 0.8) }}>
          <FilterCode index={shown.index} filter={shown.filter} />
        </div>
      ) : (
        <p className="text-sm text-text-2">No search call in this run.</p>
      )}
    </div>
  );
}

/** The filter the search ran, with the principal clause (who may read) highlighted. */
function FilterCode({ index, filter }: { index: string | null; filter: Record<string, unknown> }) {
  const code = compactJson(filter, "", 60);
  const lines = code.split("\n");
  const start = lines.findIndex((l) => l.includes("allowedPrincipals"));
  const end = start < 0 ? -1 : lines.findIndex((l, i) => i > start && l.trim().startsWith("]"));
  const highlight = start < 0 ? [] : Array.from({ length: (end < 0 ? start : end) - start + 1 }, (_, i) => start + i + 1);
  return <CodeBlock title={`filter · ${index ?? ""}`} code={code} highlight={highlight} maxHeight={220} />;
}

/** Step 5: the same region request with each identity's session; the answer is the API's. */
function RegionCheck({ p, t }: { p: Sc7Payload; t: number }) {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <ProbeCard letter="A" title="GET /region · User A's cited Box 1" probe={p.probes.regionA} t={seg(t, 0, 0.5)} />
      <div className="flex flex-col gap-3">
        <ProbeCard letter="B" title="GET /region · the same link, User B's token" probe={p.probes.regionB} t={seg(t, 0.35, 0.85)} />
        <ProbeCard letter="B" title={`GET /packages/${p.data.packageId} · User B's token`} probe={p.probes.packageB} t={seg(t, 0.55, 1)} compact />
      </div>
    </div>
  );
}

function ProbeCard({ letter, title, probe, t, compact }: { letter: "A" | "B"; title: string; probe: Probe; t: number; compact?: boolean }) {
  const on = easeCubicOut(t);
  return (
    <section className={cn("flex min-w-0 flex-col rounded-md border bg-surface", probe.state === "refused" ? "border-dashed border-loss" : "border-border")} style={{ opacity: 0.2 + 0.8 * on }} data-probe={probe.state}>
      <header className="flex items-center gap-2 border-b border-border px-4 py-2">
        <span className="flex size-5 items-center justify-center rounded-full bg-surface-3 text-xs font-semibold text-text">{letter}</span>
        <span className="truncate font-mono text-mono-sm text-text-2">{title}</span>
        <span className="ml-auto font-mono text-mono-sm text-text">{probe.state === "no-session" ? "not sent" : probe.status}</span>
      </header>
      {probe.state === "ok" && probe.region ? (
        <RegionCrop region={probe.region} t={t} />
      ) : probe.state === "ok" ? (
        <p className="px-4 py-3 text-sm text-text">Allowed.</p>
      ) : probe.state === "refused" ? (
        <div className={cn("flex items-start gap-2.5 px-4", compact ? "py-2.5" : "py-4")}>
          <Lock className="mt-0.5 size-4 shrink-0 text-loss" aria-hidden />
          <div className="flex flex-col gap-0.5">
            <p className="text-sm text-text">
              <span className="font-semibold text-loss" aria-hidden>
                ×{" "}
              </span>
              {probe.message}
            </p>
            <p className="text-xs text-text-2">
              {probe.code}
              {probe.requestId ? ` · request ${probe.requestId}` : ""} · answered by the API, not the browser
            </p>
          </div>
        </div>
      ) : (
        <p className="px-4 py-3 text-sm text-text-2">There is no User B session in this browser. Sign in as User B (Settings · Identities) to send this request with B's token.</p>
      )}
    </section>
  );
}

function RegionCrop({ region: r, t }: { region: NonNullable<Extract<Probe, { state: "ok" }>["region"]>; t: number }) {
  const unit: Rect = toRect(r.unitBboxes.find((b) => b.page === r.page) ?? r.bbox);
  const field: Rect = toRect(r.fieldBbox ?? r.bbox);
  const focus = union([unit, field])!;
  return (
    <div className="flex flex-col">
      <div className="h-[260px]">
        <PageCanvas ariaLabel={`${r.fileName}, page ${r.page}: the cited region`} image={r.url ? { src: r.url, dpi: 150 } : undefined} pageWidth={r.pageWidth} pageHeight={r.pageHeight} focus={pad(focus, 24)} animate={false} padding={12}>
          <Boundary rect={unit} progress={seg(t, 0.2, 0.9)} width={1.5} />
          <BoxOutline rect={pad(field, 2)} tone="cited" width={1.5} opacity={seg(t, 0.7, 1)} />
        </PageCanvas>
      </div>
      <p className="border-t border-border px-4 py-2 text-xs text-text-2">
        <span className="font-semibold text-cited" aria-hidden>
          ✓{" "}
        </span>
        {r.fileName} · p{r.page} · the region opens for the owner
      </p>
    </div>
  );
}

export const sc7Identity: Storyboard<Sc7Payload> = {
  id: "sc-7",
  code: "SC-7",
  title: showcaseMeta("sc-7")?.title ?? "",
  claim: showcaseMeta("sc-7")?.claim ?? "",
  load: loadSc7,
  steps,
  render: (frame) => <Sc7Stage {...frame} />,
  receiptsPayload: (p) => ({ data: p.data, probes: { regionA: p.probes.regionA.state === "ok" ? { state: "ok", status: 200 } : p.probes.regionA, regionB: p.probes.regionB, packageB: p.probes.packageB }, packagePrincipals: p.packagePrincipals }),
};

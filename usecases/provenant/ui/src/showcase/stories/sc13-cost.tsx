/**
 * SC-13 · Cost and model tiering. Usage per document and lane (bars to scale within each
 * usage kind), the fast path that skips OCR for digital pages, the model tier per call type, and input tokens per
 * answer per lane. Amounts are `estimate` (usage × list price, `config/prices.yaml`) and appear only when the build
 * publishes them; Cost Explorer actuals and AWS Budgets are not in this build and say so.
 */
import { easeCubicOut } from "d3-ease";
import type { CostLine } from "@contracts/api";
import { LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, Tooltip, cn } from "../../design";
import { seg } from "../../viz/geom";
import { showcaseMeta } from "../catalog";
import { StoryStage } from "../engine/StoryStage";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { LANES, type Lane } from "./lending";
import { NotInBuild } from "./parts";
import { type Sc13Payload, USAGE, actualsCaption, loadSc13, median, perDocCaption, tieringCaption, tokensCaption } from "./sc13-payload";
import { SC13_STEPS } from "./steps";

const STEP = Object.fromEntries(SC13_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC13_STEPS)[number]["id"], number>;

const costReceipt = (p: Sc13Payload) => ({ label: "Cost view", value: `${p.cost.label} · published ${p.cost.published}`, source: `GET /cost?packageId=${p.cost.packageId}&groupBy=${p.cost.groupBy}`, provenance: p.provenance });
const priceReceipt = (p: Sc13Payload) => ({ label: "Prices", value: `${p.cost.prices.source} · as of ${p.cost.prices.asOf}`, source: p.cost.prices.source, provenance: "estimate" as const });

const steps: StoryStep<Sc13Payload>[] = [
  {
    ...SC13_STEPS[0],
    durationMs: 2600,
    caption: perDocCaption,
    services: ["AWS Fargate", "Amazon Textract", "Amazon Comprehend"],
    receipts: (p) => [costReceipt(p), priceReceipt(p), ...p.docs.flatMap((d) => LANES.flatMap((pv) => d.lanes[pv].lines.map((l) => ({ label: `${d.fileName} · ${pv} · ${l.service}`, value: `${l.usage.quantity} ${l.usage.unit}${l.usage.measured ? "" : ` (derived: ${l.usage.basis})`}`, provenance: l.usage.measured ? p.provenance : ("derived" as const) }))))],
  },
  {
    ...SC13_STEPS[1],
    durationMs: 2400,
    caption: "Digital pages skip OCR.",
    services: ["AWS Step Functions · route", "AWS Fargate", "Amazon Textract"],
    receipts: (p) => p.docs.flatMap((d) => LANES.map((pv) => ({ label: `${d.fileName} · ${pv} · extract`, value: d.lanes[pv].extract ? `${d.lanes[pv].extract!.engine} · ${d.lanes[pv].extract!.durationMs} ms` : "no extract record", source: "rp-stages", provenance: p.provenance }))),
  },
  {
    ...SC13_STEPS[2],
    durationMs: 2400,
    caption: tieringCaption,
    services: ["Amazon Bedrock"],
    receipts: (p) => [priceReceipt(p), ...p.cost.tiering.map((t) => ({ label: `${t.callType} · ${t.modelKey}`, value: `${t.modelId} · ${t.calls} calls · ${t.tokensIn} in / ${t.tokensOut} out`, source: t.source, provenance: p.provenance }))],
  },
  {
    ...SC13_STEPS[3],
    durationMs: 2200,
    caption: tokensCaption,
    services: ["Amazon Bedrock"],
    receipts: (p) => LANES.map((pv) => ({ label: `Input tokens per ask · ${pv}`, value: p.asks.filter((a) => a.lane === pv).map((a) => `${a.runId} ${a.tokensIn}`).join(" · "), source: "RunRecord usage.inputTokens", provenance: p.provenance })),
  },
  {
    ...SC13_STEPS[4],
    durationMs: 1800,
    caption: actualsCaption,
    services: ["AWS Cost Explorer", "AWS Budgets"],
    receipts: (p) => [costReceipt(p), { label: "Notes", value: p.cost.notes.join(" "), provenance: p.provenance }],
  },
];

// --------------------------------------------------------------------------------------------- frame

function Sc13Stage({ payload: p, step, progress: t }: Frame<Sc13Payload>) {
  return (
    <ProvenanceScope provenance={p.provenance} source={`GET /cost?packageId=${p.cost.packageId}`}>
      <StoryStage
        header={
          <header className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-2">
            <span className="font-mono text-mono-sm text-text">{p.cost.packageId}</span>
            <span>
              prices {p.cost.prices.source} · as of {p.cost.prices.asOf}
            </span>
            {!p.cost.published && <span className="rounded-xs border border-dashed border-border-strong px-1.5" title="Amounts are withheld until the publication flag RP_PUBLIC_METRICS is on.">
                amounts not published
              </span>}
            <span className="ml-auto">
              <ScopeTag />
            </span>
          </header>
        }
      >
        {step === STEP["per-document"] && <PerDocument p={p} t={t} />}
        {step === STEP["digital-scan"] && <DigitalScan p={p} t={t} />}
        {step === STEP.tiering && <Tiering p={p} t={t} />}
        {step === STEP.tokens && <Tokens p={p} t={t} />}
        {step === STEP.actuals && <Actuals p={p} />}
      </StoryStage>
    </ProvenanceScope>
  );
}

/** Step 1: per document and lane, each usage kind as a bar scaled to that kind's largest value in the package. */
function PerDocument({ p, t }: { p: Sc13Payload; t: number }) {
  const all = p.docs.flatMap((d) => LANES.flatMap((pv) => d.lanes[pv].lines));
  const max = Object.fromEntries(USAGE.map((u) => [u.service, Math.max(0, ...all.filter((l) => l.service === u.service).map((l) => l.usage.quantity))]));
  const derived = USAGE.filter((u) => all.some((l) => l.service === u.service && !l.usage.measured));
  const measured = USAGE.filter((u) => !derived.includes(u));
  const cols = { gridTemplateColumns: `minmax(200px,1.2fr) 150px repeat(${USAGE.length},minmax(0,1fr))${p.cost.published ? " 96px" : ""}` };
  return (
    <section className="rounded-md border border-border bg-surface">
      <div className="grid gap-3 border-b border-border bg-bg-subtle px-4 py-2 text-caps uppercase text-text-2" style={cols}>
        <span>Document</span>
        <span>Lane</span>
        {USAGE.map((u) => (
          <span key={u.service} className="whitespace-nowrap">
            {u.label} <span className="normal-case tracking-normal">· {u.unit}</span>
          </span>
        ))}
        {p.cost.published && <span className="text-right">estimate</span>}
      </div>
      <p className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-border px-4 py-1.5 text-xs text-text-2">
        {derived.length > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <ProvenanceTag provenance="derived" source={all.find((l) => !l.usage.measured)?.usage.basis} /> {derived.map((u) => u.unit).join(", ")}
          </span>
        )}
        {measured.length > 0 && (
          <span className="inline-flex items-center gap-1.5">
            <ScopeTag /> {measured.map((u) => u.unit).join(", ")}
          </span>
        )}
        <span>Bars share one scale per column.</span>
      </p>
      <ol>
        {p.docs.map((d, di) => (
          <li key={d.docId} className="border-b border-border last:border-0">
            {LANES.map((pv, li) => {
              const on = easeCubicOut(seg(t, di * 0.1 + li * 0.04, di * 0.1 + li * 0.04 + 0.4));
              return (
                <div key={pv} className="grid items-center gap-3 px-4 py-1.5" style={cols}>
                  {li === 0 ? (
                    <span className="min-w-0">
                      <span className="block truncate text-sm text-text">{d.fileName}</span>
                      <span className="text-xs text-text-2">
                        {d.docType?.replace("doc:", "")} · <Num value={d.pages} kind="int" size="xs" bare /> {d.pages === 1 ? "page" : "pages"}
                      </span>
                    </span>
                  ) : (
                    <span />
                  )}
                  <LaneBadge pv={pv} className="justify-self-start" />
                  {USAGE.map((u) => {
                    const l = d.lanes[pv].lines.find((x) => x.service === u.service);
                    return <UsageBar key={u.service} l={l} kind={u.kind} max={max[u.service]} on={on} />;
                  })}
                  {p.cost.published && (
                    <span className="text-right">
                      <Num value={d.lanes[pv].estimateUsd} kind="raw" provenance="estimate" size="xs" unit="USD" />
                    </span>
                  )}
                </div>
              );
            })}
          </li>
        ))}
      </ol>
    </section>
  );
}

/** A 64 px bar on the column's shared scale and the quantity, right-aligned in one format per column. */
function UsageBar({ l, kind, max, on }: { l?: CostLine; kind: "int" | "dec2"; max: number; on: number }) {
  if (!l) return <span className="text-xs text-text-3">—</span>;
  const w = max > 0 ? (l.usage.quantity / max) * 100 : 0;
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="h-1.5 w-[64px] shrink-0 overflow-hidden rounded-full bg-surface-3">
        <span className={cn("block h-full rounded-full", l.usage.measured ? "bg-text" : "bg-text-3")} style={{ width: `${w * on}%` }} />
      </span>
      <span className="w-[56px] text-right" title={l.usage.basis}>
        <Num value={l.usage.quantity} kind={kind} size="xs" bare />
      </span>
    </span>
  );
}

/** Step 2: the route per document: digital pages go to Docling, scans to Textract; the naive lane reads the text layer. */
function DigitalScan({ p, t }: { p: Sc13Payload; t: number }) {
  const groups: { engine: string; title: string; note: string }[] = [
    { engine: "docling", title: "Digital pages → Docling on AWS Fargate", note: "no OCR: the text layer and layout come from the PDF" },
    { engine: "textract", title: "Scanned pages → Amazon Textract", note: "OCR with layout, billed per page" },
  ];
  const units = (d: Sc13Payload["docs"][number]) => d.lanes["2.3.1"].extract;
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        {groups.map((g, gi) => {
          const docs = p.docs.filter((d) => units(d)?.engine === g.engine);
          return (
            <section key={g.engine} className="rounded-md border border-border bg-surface" style={{ opacity: 0.25 + 0.75 * easeCubicOut(seg(t, gi * 0.3, gi * 0.3 + 0.45)) }}>
              <header className="border-b border-border px-4 py-2">
                <p className="text-sm text-text">{g.title}</p>
                <p className="text-xs text-text-2">{g.note}</p>
              </header>
              <ul className="divide-y divide-border">
                {docs.map((d) => (
                  <li key={d.docId} className="flex items-center gap-3 px-4 py-2 text-sm">
                    <LaneBadge pv="2.3.1" />
                    <span className="min-w-0 flex-1 truncate">{d.fileName}</span>
                    <span className="text-xs text-text-2">
                      <Num value={d.pages} kind="int" size="xs" bare /> p · extract <Num value={units(d)?.durationMs} kind="ms" size="xs" bare />
                    </span>
                  </li>
                ))}
                {!docs.length && <li className="px-4 py-2 text-sm text-text-2">No document took this route.</li>}
              </ul>
            </section>
          );
        })}
      </div>
      <section className="rounded-md border border-border bg-surface px-4 py-3" style={{ opacity: 0.25 + 0.75 * seg(t, 0.6, 1) }}>
        <p className="flex items-center gap-2 text-sm text-text">
          <LaneBadge pv="1.0" /> reads the text layer only
        </p>
        <ul className="mt-2 flex flex-wrap gap-1.5">
          {p.docs.map((d) => {
            const e = d.lanes["1.0"].extract;
            return (
              <li key={d.docId} className="rounded-xs border border-border px-2 py-0.5 text-xs text-text-2">
                {d.fileName} · {e?.engine ?? "—"} · <Num value={e?.durationMs} kind="ms" size="xs" bare />
              </li>
            );
          })}
        </ul>
        <p className="mt-2 text-xs text-text-2">Stage durations are measured from the stage records (playback is paced).</p>
      </section>
    </div>
  );
}

/** Step 3: the tier per call type, from the run records and the classify stage. */
function Tiering({ p, t }: { p: Sc13Payload; t: number }) {
  return (
    <section className="rounded-md border border-border bg-surface">
      <table className="w-full text-sm">
        <thead className="bg-bg-subtle text-left text-caps uppercase text-text-2">
          <tr className="border-b border-border">
            <th className="px-4 py-2 font-medium">Call type</th>
            <th className="px-4 py-2 font-medium">Model key</th>
            <th className="px-4 py-2 font-medium">Model id</th>
            <th className="px-4 py-2 text-right font-medium">Calls</th>
            <th className="px-4 py-2 text-right font-medium">Tokens in · out</th>
            {p.cost.published && (
              <th className="px-4 py-2 text-right font-medium">
                Estimate <ProvenanceTag provenance="estimate" source={p.cost.prices.source} />
              </th>
            )}
          </tr>
        </thead>
        <tbody>
          {p.cost.tiering.map((r, i) => (
            <tr key={`${r.callType}-${r.modelId}`} className="border-b border-border last:border-0" style={{ opacity: 0.2 + 0.8 * easeCubicOut(seg(t, i * 0.15, i * 0.15 + 0.4)) }}>
              <td className="px-4 py-2 text-text">{r.callType}</td>
              <td className="px-4 py-2 font-mono text-mono-sm text-text-2">{r.modelKey}</td>
              <td className="px-4 py-2 font-mono text-mono-sm text-text">{r.modelId}</td>
              <td className="px-4 py-2 text-right">
                <Num value={r.calls} kind="int" />
              </td>
              <td className="px-4 py-2 text-right">
                <Tooltip content={r.source}>
                  <span tabIndex={0}>
                    <Num value={r.tokensIn} kind="int" bare /> · <Num value={r.tokensOut} kind="int" bare />
                  </span>
                </Tooltip>
              </td>
              {p.cost.published && (
                <td className="px-4 py-2 text-right">
                  <Num value={r.estimateUsd} kind="raw" provenance="estimate" unit="USD" />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="border-t border-border px-4 py-2 text-xs text-text-2">A call type with 0 calls is shown as such: the classify tie-break never ran on this package.</p>
    </section>
  );
}

/** A round axis maximum (10k steps) at or above the largest value, so the largest dot sits inside the track. */
const axisMax = (v: number) => Math.max(10_000, Math.ceil((v * 1.05) / 10_000) * 10_000);
const kTick = (v: number) => (v === 0 ? "0" : `${Math.round(v / 1000)}k`);

/** Step 4: input tokens per gate-cohort answer with the lane's own model, one dot per run (jittered), the median marked. */
function Tokens({ p, t }: { p: Sc13Payload; t: number }) {
  const max = axisMax(Math.max(1, ...p.asks.map((a) => a.tokensIn)));
  const ticks = [0, max / 2, max];
  return (
    <section className="rounded-md border border-border bg-surface">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2 text-caps uppercase text-text-2">
        Input tokens per answer
        <span className="ml-auto font-mono normal-case tracking-normal">{p.answerModel}</span>
      </header>
      <div className="flex flex-col gap-4 px-4 py-4">
        {LANES.map((pv: Lane, li) => {
          const xs = p.asks.filter((a) => a.lane === pv);
          const m = median(xs.map((a) => a.tokensIn));
          const on = easeCubicOut(seg(t, li * 0.3, li * 0.3 + 0.5));
          return (
            <div key={pv} className="grid grid-cols-[150px_minmax(0,1fr)_160px] items-center gap-3">
              <LaneBadge pv={pv} className="justify-self-start" />
              <span className="relative h-7 rounded-sm bg-surface-2">
                {xs.map((a, i) => (
                  // deterministic 4 px vertical jitter (by rank) so runs with near-equal usage stay countable
                  <span key={a.runId} className="absolute size-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full border border-text-2 bg-surface" style={{ left: `${(a.tokensIn / max) * 100 * on}%`, top: `calc(50% + ${((i % 3) - 1) * 4}px)` }} title={`${a.runId} · ${a.tokensIn}`} />
                ))}
                {m !== undefined && <span className="absolute inset-y-0 w-px bg-text" style={{ left: `${(m / max) * 100 * on}%`, opacity: seg(t, 0.6, 1) }} aria-hidden />}
              </span>
              <span className="text-xs text-text-2">
                median <Num value={m} kind="int" size="xs" bare /> · n <Num value={xs.length} kind="int" size="xs" bare />
              </span>
            </div>
          );
        })}
        <div className="grid grid-cols-[150px_minmax(0,1fr)_160px] gap-3" aria-hidden>
          <span />
          <span className="relative h-4 font-mono text-mono-sm leading-4 text-text-2 tnum">
            {ticks.map((v, i) => (
              <span key={v} className={cn("absolute top-0", i === 0 ? "" : i === ticks.length - 1 ? "-translate-x-full" : "-translate-x-1/2")} style={{ left: `${(v / max) * 100}%` }}>
                {kTick(v)}
              </span>
            ))}
          </span>
          <span className="text-xs text-text-2">input tokens</span>
        </div>
      </div>
      <p className="border-t border-border px-4 py-2 text-xs text-text-2">The package owner's completed asks with the lane's own model, any question. More tool turns read more tokens.</p>
    </section>
  );
}

function Actuals({ p }: { p: Sc13Payload }) {
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      <NotInBuild title="Billed cost from AWS Cost Explorer" cut="billing-actuals">
        Estimates only: usage × list price ({p.cost.prices.source}, as of {p.cost.prices.asOf}). Actuals lag about a day and need the deployed stack.
      </NotInBuild>
      <NotInBuild title="AWS Budgets alerts" note="Route not built">
        GET /cost/budgets reads budgets:DescribeBudgets once the stack is deployed.
      </NotInBuild>
      <section className="rounded-md border border-border bg-surface px-4 py-3 text-xs text-text-2 lg:col-span-2">
        {p.cost.notes.map((n) => (
          <p key={n}>{n}</p>
        ))}
      </section>
    </div>
  );
}

export const sc13Cost: Storyboard<Sc13Payload> = {
  id: "sc-13",
  code: "SC-13",
  title: showcaseMeta("sc-13")?.title ?? "",
  claim: showcaseMeta("sc-13")?.claim ?? "",
  load: loadSc13,
  steps,
  render: (frame) => <Sc13Stage {...frame} />,
  receiptsPayload: (p) => ({ cost: p.cost, docs: p.docs.map((d) => ({ docId: d.docId, lanes: d.lanes })), asks: p.asks }),
};

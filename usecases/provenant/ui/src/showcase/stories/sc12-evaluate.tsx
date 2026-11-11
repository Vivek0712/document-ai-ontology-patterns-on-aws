/**
 * SC-12 · Evaluate. The golden set as dots grouped by question type; each lane's dots then
 * drop into Recall@1 bins (hit in every logged run, miss in every run, mixed), always with n per type. Questions whose
 * answer no single unit prints (totals) get their own bin ("needs 2+ units"): top-1 cannot score them. The evidence
 * units lane also shows region hit, measured on every question. Rates per type appear only in a published build
 * (`RP_PUBLIC_METRICS=on`); otherwise the last step says "Not published".
 * There is no single aggregate accuracy number. Dot positions are a pure function of (bin, index, t).
 */
import { easeCubicInOut } from "d3-ease";
import { Check, X } from "lucide-react";
import { LANE_NAME, LaneBadge, Num, ProvenanceScope, ScopeTag, Tooltip, cn } from "../../design";
import { lerp, seg } from "../../viz/geom";
import { showcaseMeta } from "../catalog";
import { StoryStage } from "../engine/StoryStage";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { LANES, type Lane } from "./lending";
import { NotInBuild } from "./parts";
import { BINS, type Bin, type Metric, type QuestionDot, type Sc12Payload, loadSc12, metricNote, outcomeOf, questionsCaption } from "./sc12-payload";
import { SC12_STEPS } from "./steps";

const STEP = Object.fromEntries(SC12_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC12_STEPS)[number]["id"], number>;
const ease = easeCubicInOut;
const PITCH = 17;
const LABEL_COL = 224;
const METRICS: Metric[] = ["top1", "region"];
/** Bins any lane or metric uses: an empty "mixed" or "needs 2+ units" column is not drawn. */
const shownBins = (p: Sc12Payload): Bin[] => BINS.filter((b) => b === "hit" || b === "miss" || b === "not-run" || p.rows.some((r) => r.dots.some((d) => LANES.some((pv) => METRICS.some((m) => outcomeOf(d, pv, m).bin === b)))));
/** Each bin's width from its fullest row in any lane and metric (at least room for its label), so every dot stays
 *  inside its bin and a sparse bin stays narrow. */
const binWidths = (p: Sc12Payload): Record<Bin, number> =>
  Object.fromEntries(BINS.map((b) => [b, Math.max(132, (Math.max(0, ...p.rows.flatMap((r) => LANES.flatMap((pv) => METRICS.map((m) => r.dots.filter((d) => outcomeOf(d, pv, m).bin === b).length)))) + 1) * PITCH + 16)])) as Record<Bin, number>;
/** x where `bin` starts: the widths of the bins drawn before it. */
const binStart = (bins: Bin[], w: Record<Bin, number>, bin: Bin) => bins.slice(0, Math.max(0, bins.indexOf(bin))).reduce((s, b) => s + w[b], 0);
const BIN_LABEL: Record<Metric, Record<Bin, string>> = {
  top1: { hit: "Top-1 hit, every run", miss: "Missed, every run", mixed: "Mixed", multi: "Needs 2+ units · top-1 n/a", "not-run": "Not run yet" },
  region: { hit: "Region hit, every run", miss: "Missed, every run", mixed: "Mixed", multi: "", "not-run": "Not measured" },
};

const evalReceipt = (p: Sc12Payload) => ({ label: "Eval run", value: `${p.run.evalRunId} · ${p.run.mode} · golden ${p.run.goldenSha.slice(0, 12)}`, source: `GET /eval/runs/${p.run.evalRunId}`, provenance: p.provenance });
const laneReceipts = (p: Sc12Payload, pv: Lane) => [
  evalReceipt(p),
  ...p.rows.map((r) => ({ label: `${pv} · ${r.type}`, value: r.dots.filter((d) => d.qid).map((d) => `${d.qid} ${d.lanes[pv].hits}/${d.lanes[pv].n}`).join(" · "), path: `$.rows[?type=='${r.type}']`, provenance: p.provenance })),
];

const steps: StoryStep<Sc12Payload>[] = [
  {
    ...SC12_STEPS[0],
    durationMs: 2000,
    caption: questionsCaption,
    services: ["Amazon S3 · config/golden.jsonl"],
    receipts: (p) => [evalReceipt(p), { label: "Golden set by type", value: Object.entries(p.run.goldenTypes).map(([k, v]) => `${k} ${v}`).join(" · "), source: p.run.goldenPath, provenance: "generator" }],
  },
  { ...SC12_STEPS[1], durationMs: 2800, caption: "Naive pipeline: top-1 hits by question type.", services: ["AWS Lambda · search_units", "Amazon OpenSearch Serverless"], receipts: (p) => laneReceipts(p, "1.0") },
  { ...SC12_STEPS[2], durationMs: 2800, caption: "Evidence units: the same questions, top-1 and region hit.", services: ["AWS Lambda · search_units", "Amazon OpenSearch Serverless"], receipts: (p) => laneReceipts(p, "2.3.1") },
  {
    ...SC12_STEPS[3],
    durationMs: 2400,
    caption: (p) => p.run.caption,
    services: ["Amazon S3 · eval results"],
    receipts: (p) => [evalReceipt(p), { label: "Published", value: p.run.published, provenance: p.provenance }, { label: "Withheld", value: p.run.withheld.join("; ") || "nothing", provenance: p.provenance }],
  },
];

// --------------------------------------------------------------------------------------------- frame

function Sc12Stage({ payload: p, step, progress: t }: Frame<Sc12Payload>) {
  return (
    <ProvenanceScope provenance={p.provenance} source={`GET /eval/runs/${p.run.evalRunId}`}>
      <StoryStage
        header={
          <header className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-2">
            <span className="font-mono text-mono-sm text-text">{p.run.evalRunId}</span>
            <span>
              <Num value={p.scored} kind="int" size="xs" bare /> of <Num value={p.questions} kind="int" size="xs" bare /> golden questions have logged runs
            </span>
            <span>repeats up to {p.run.repeats}</span>
            <ScopeTag />
            <span className="basis-full text-text-2" title={p.run.modeNote}>
              {metricNote(p)}
            </span>
          </header>
        }
      >
        {step === STEP.questions && <LaneDots p={p} pv={null} t={t} />}
        {step >= STEP.naive && step <= STEP.units && <LaneDots p={p} pv="1.0" t={step === STEP.naive ? t : 1} />}
        {step === STEP.units && <LaneDots p={p} pv="2.3.1" t={t} />}
        {step === STEP.units && <LaneDots p={p} pv="2.3.1" metric="region" t={seg(t, 0.4, 1)} />}
        {step === STEP.rates && <Rates p={p} t={t} />}
      </StoryStage>
    </ProvenanceScope>
  );
}

/**
 * One lane's dots by type for one metric; `pv` null = the golden set before any lane (one row of dots per type, no
 * bins). Region hit on the naive lane is never drawn: its chunks carry no region.
 */
function LaneDots({ p, pv, t, metric = "top1" }: { p: Sc12Payload; pv: Lane | null; t: number; metric?: Metric }) {
  const widths = binWidths(p);
  const bins = shownBins(p).filter((b) => metric === "top1" || b !== "multi");
  const grid = { gridTemplateColumns: `${LABEL_COL}px minmax(0,1fr)` };
  return (
    <section
      className="overflow-x-auto rounded-md border border-border bg-surface"
      data-lane={pv ?? "golden"}
      data-metric={metric}
      tabIndex={0}
      aria-label={pv ? `${LANE_NAME[pv]} ${pv}: ${metric === "top1" ? "top-1" : "region hit"} by question type` : "Golden set by question type"}
    >
      <div style={{ minWidth: LABEL_COL + 40 + bins.reduce((s, b) => s + widths[b], 0) }}>
        <header className="grid items-center gap-2 border-b border-border bg-bg-subtle px-4 py-2" style={grid}>
          <span className="flex min-w-0 items-center gap-1.5">
            {pv ? <LaneBadge pv={pv} /> : <span className="text-caps uppercase text-text-2">Golden set</span>}
            {pv && <span className="whitespace-nowrap text-xs text-text-2">{metric === "top1" ? "top-1" : "region hit"}</span>}
          </span>
          <span className="flex">
            {pv &&
              bins.map((b) => (
                <span key={b} className="pr-2 text-caps uppercase leading-tight text-text-2" style={{ width: widths[b] }}>
                  {BIN_LABEL[metric][b]}
                </span>
              ))}
          </span>
        </header>
        <ol>
          {p.rows.map((r, ri) => (
            <li key={r.type} className="grid items-center gap-2 border-b border-border px-4 py-2 last:border-0" style={grid}>
              <span className="flex items-baseline gap-1.5 text-sm text-text">
                {r.type}
                <span className="text-xs text-text-2">
                  n <Num value={r.n} kind="int" size="xs" provenance="generator" bare />
                </span>
              </span>
              <span className="relative h-[18px]">
                {r.dots.map((d, i) => {
                  const k = pv ? ease(seg(t, (ri * 0.12 + (i / r.dots.length) * 0.4) * 0.9, (ri * 0.12 + (i / r.dots.length) * 0.4) * 0.9 + 0.3)) : 1;
                  const x = lerp(i * PITCH, pv ? binX(r.dots, d, pv, widths, bins, metric) : i * PITCH, k);
                  return <QDot key={d.qid ?? `${r.type}-${i}`} d={d} pv={pv} metric={metric} x={x} on={pv ? k : seg(t, (i / r.dots.length) * 0.6, (i / r.dots.length) * 0.6 + 0.3)} />;
                })}
              </span>
            </li>
          ))}
        </ol>
      </div>
    </section>
  );
}

/** A dot's x in its bin: bins side by side, dots in qid order inside each. */
function binX(dots: QuestionDot[], d: QuestionDot, pv: Lane, widths: Record<Bin, number>, bins: Bin[], metric: Metric): number {
  const bin = outcomeOf(d, pv, metric).bin;
  const idx = dots.filter((x) => outcomeOf(x, pv, metric).bin === bin).indexOf(d);
  return binStart(bins, widths, bin) + idx * PITCH;
}

function QDot({ d, pv, x, on, metric }: { d: QuestionDot; pv: Lane | null; x: number; on: number; metric: Metric }) {
  const l = pv ? outcomeOf(d, pv, metric) : undefined;
  const bin = pv && on >= 0.98 ? l!.bin : undefined;
  const what = metric === "top1" ? (bin === "multi" ? "needs 2+ units: no single unit prints the answer" : "ranked the gold unit first") : "top-1 region covers the gold region";
  const label = d.qid ? `${d.qid}${l ? (bin === "multi" ? ` · ${what}` : ` · ${l.hits} of ${l.n} runs: ${what}`) : ""}` : "not in a logged run yet";
  return (
    <Tooltip content={<span className="font-mono">{label}</span>}>
      <span
        role="img"
        aria-label={label}
        className={cn(
          "absolute top-[2px] flex size-3.5 items-center justify-center rounded-full border [&_svg]:size-2.5",
          !d.qid && "border-border-strong",
          d.qid && !bin && "border-text-3 bg-text-3",
          bin === "hit" && "border-cited text-cited",
          bin === "miss" && "border-dashed border-loss text-loss",
          bin === "mixed" && "border-text-2 bg-[linear-gradient(90deg,var(--text-2)_50%,transparent_50%)]",
          bin === "multi" && "border-dashed border-text-2",
          bin === "not-run" && "border-border-strong",
        )}
        style={{ left: x, opacity: pv ? 1 : 0.2 + 0.8 * on }}
        data-bin={bin}
      >
        {bin === "hit" ? <Check strokeWidth={3} /> : bin === "miss" ? <X strokeWidth={3} /> : null}
      </span>
    </Tooltip>
  );
}

/** Step 4: rates per type with n (published builds only). */
function Rates({ p, t }: { p: Sc12Payload; t: number }) {
  const lanes = p.run.lanes;
  if (!lanes)
    return (
      <div className="flex flex-col gap-3">
        <NotInBuild title="Rates by question type" note="Not published" cut="unpublished-metrics">
          Top-1 on single-unit questions; region hit on every question. Rates are not published in this build; the dots below are the per-question outcomes.
        </NotInBuild>
        <LaneDots p={p} pv="1.0" t={1} />
        <LaneDots p={p} pv="2.3.1" t={1} />
        <LaneDots p={p} pv="2.3.1" metric="region" t={1} />
      </div>
    );
  const types = Object.keys(p.run.goldenTypes);
  const ratio = lanes["1.0"]?.meanUnitChars && lanes["2.3.1"]?.meanUnitChars ? lanes["1.0"].meanUnitChars / lanes["2.3.1"].meanUnitChars : undefined;
  return (
    <section className="rounded-md border border-border bg-surface">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2 text-caps uppercase text-text-2">
        Recall@1 by question type
        <span className="ml-auto normal-case tracking-normal">{p.run.caption}</span>
      </header>
      <ol className="divide-y divide-border">
        {types.map((type, ti) => (
          <li key={type} className="grid grid-cols-[120px_minmax(0,1fr)] gap-3 px-4 py-3">
            <span className="text-sm text-text">{type}</span>
            <div className="flex flex-col gap-1.5">
              {LANES.map((pv) => {
                const m = lanes[pv]?.byType[type];
                if (!m) return null;
                const k = ease(seg(t, ti * 0.12, ti * 0.12 + 0.5));
                return (
                  <div key={pv} className="grid grid-cols-[150px_minmax(0,1fr)_180px] items-center gap-3 text-xs text-text-2">
                    <LaneBadge pv={pv} />
                    <span className="h-1.5 overflow-hidden rounded-full bg-surface-3">
                      <span className="block h-full rounded-full bg-text" style={{ width: `${(m.recallAt1.rate ?? 0) * 100 * k}%` }} />
                    </span>
                    <span className="flex items-baseline gap-1.5">
                      <Num value={m.recallAt1.rate ?? 0} kind="pct" size="xs" bare /> · n <Num value={m.recallAt1.n} kind="int" size="xs" bare />
                      {pv === "2.3.1" && (
                        <>
                          · region <Num value={m.regionHitAt1.rate ?? 0} kind="pct" size="xs" bare />
                        </>
                      )}
                    </span>
                  </div>
                );
              })}
            </div>
          </li>
        ))}
      </ol>
      {ratio !== undefined && (
        <p className="border-t border-border px-4 py-2 text-xs text-text-2">
          Mean unit size: naive <Num value={lanes["1.0"].meanUnitChars} kind="int" size="xs" bare /> chars, evidence units <Num value={lanes["2.3.1"].meanUnitChars} kind="int" size="xs" bare /> chars (ratio{" "}
          <Num value={ratio.toFixed(1)} kind="raw" size="xs" provenance="derived" />)
        </p>
      )}
    </section>
  );
}

export const sc12Evaluate: Storyboard<Sc12Payload> = {
  id: "sc-12",
  code: "SC-12",
  title: showcaseMeta("sc-12")?.title ?? "",
  claim: showcaseMeta("sc-12")?.claim ?? "",
  load: loadSc12,
  steps,
  render: (frame) => <Sc12Stage {...frame} />,
  receiptsPayload: (p) => ({ run: p.run, rows: p.rows }),
};

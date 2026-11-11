/**
 * SC-11 · Before and after. The same question on both pipelines, which differ only in
 * preprocessing: the naive answer (replayed from its logged run), what the naive index handed the model, the
 * evidence-units answer with its regions, the config diff and the stability over every logged run.
 *
 * An example switcher (`?ex=`, from the catalog) shows each example's outcome up front, so the one stable naive
 * failure (a wrong total) sits beside the "right but uncited" cases. Every value is read from the payload;
 * captions that state a result are chosen from the eval scores. Frames are pure (payload, step, progress).
 */
import { easeCubicInOut, easeCubicOut } from "d3-ease";
import { Link } from "react-router-dom";
import type { RetrievedUnit, RunRecord } from "@contracts/api";
import { Badge, LANE_NAME, LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, Skeleton, cn, formatNum } from "../../design";
import { AnswerChips, RetrievedList } from "../../features/ask/parts";
import { shortSub } from "../../lib/format";
import { type Rect, pad, seg, toRect, union } from "../../viz/geom";
import { BoxOutline, ChunkSlices, Mark, PageCanvas, Tag } from "../../viz/PageCanvas";
import { sliceOf } from "../../viz/xray";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { tally } from "./compare";
import { FOLLOW_SUBJECT, LANES, type Lane } from "./lending";
import { DiffStrip, Dot, DotRow, OfN, Typed } from "./parts";
import { type Sc11Payload, afterCaption, beforeCaption, loadSc11, repDot, runOf, xrayFindings } from "./sc11-payload";
import { SC11_STEPS } from "./steps";

const STEP = Object.fromEntries(SC11_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC11_STEPS)[number]["id"], number>;
const ease = easeCubicInOut;

const laneReceipts = (p: Sc11Payload, pv: Lane) => {
  const r = runOf(p, pv);
  return [
    { label: `Representative run · ${pv}`, value: r?.runId ?? "none", source: r?.s3Key, provenance: p.runs.provenance },
    { label: `Answer · ${pv}`, value: r?.answer?.total ?? r?.answer?.answer.slice(0, 160), path: `$.runs.lanes['${pv}'].run.answer`, provenance: p.runs.provenance },
    { label: `Eval scores · ${pv}`, value: p.runs.lanes[pv].dots.map((d) => `${d.runId} ${d.status}${d.cited ? " · region" : ""}`).join("; "), source: "eval/run.py outcome_from_events", provenance: p.runs.provenance },
  ];
};

const steps: StoryStep<Sc11Payload>[] = [
  {
    ...SC11_STEPS[0],
    durationMs: 1600,
    caption: "Same question in both pipelines.",
    services: ["Amazon API Gateway", "Amazon Bedrock AgentCore"],
    receipts: (p) => [
      { label: "Golden question", value: `${p.runs.qid} · ${p.runs.question}`, source: "eval/golden.jsonl", provenance: "generator" },
      { label: "Expected", value: p.runs.expected, source: "eval/golden.jsonl", provenance: "generator" },
      { label: "Package", value: p.runs.packageId, provenance: p.runs.provenance },
      ...LANES.map((pv) => ({ label: `Retrieval K · ${pv}`, value: p.retrieved[pv].k, path: `$.retrieved['${pv}'].k`, provenance: p.runs.provenance })),
    ],
  },
  {
    ...SC11_STEPS[1],
    durationMs: 2800,
    caption: beforeCaption,
    services: ["Amazon Bedrock AgentCore", "Amazon Bedrock", "Amazon OpenSearch Serverless"],
    receipts: (p) => laneReceipts(p, "1.0"),
  },
  {
    ...SC11_STEPS[2],
    durationMs: 3000,
    caption: "What the naive index handed the model.",
    services: ["Amazon OpenSearch Serverless", "AWS Lambda · rp-xray"],
    receipts: (p) => [
      { label: "First search_units call (naive)", value: p.retrieved["1.0"].callId ?? "none", source: runOf(p, "1.0")?.s3Key, path: "$.retrieved['1.0'].units", provenance: p.runs.provenance },
      ...p.retrieved["1.0"].units.map((u) => ({ label: `#${u.rank} ${u.chunkLabel ?? u.label}`, value: `fused ${u.scores.fused}`, provenance: p.runs.provenance })),
      ...(p.xray
        ? [
            { label: "X-ray of the page", value: `${p.xray.sources.fileName ?? p.xray.docId} p${p.xray.page}`, source: p.xray.sources.bronze.key, provenance: "derived" as const },
            { label: "Numbers the logged answer used", value: xrayFindings(p.xray).map((f) => `${f.value} → ${f.lines.length ? f.lines.map((l) => `line ${l.ordinal}${l.box ? ` (${l.box})` : ""}`).join(", ") : "not on this page"}`).join("; "), path: "$.xray.answerNumbers", provenance: "derived" as const },
          ]
        : []),
    ],
  },
  {
    ...SC11_STEPS[3],
    durationMs: 2800,
    caption: afterCaption,
    services: ["Amazon Bedrock AgentCore", "Amazon Neptune", "Amazon OpenSearch Serverless"],
    receipts: (p) => laneReceipts(p, "2.3.1"),
  },
  {
    ...SC11_STEPS[4],
    durationMs: 1800,
    caption: "Only preprocessing changed.",
    services: ["Amazon S3 · config"],
    receipts: (p) => [{ label: "GET /pipelines/diff", value: p.runs.diff.summary, path: "$.runs.diff", source: `${p.runs.diff.aConfigSha.slice(0, 12)} vs ${p.runs.diff.bConfigSha.slice(0, 12)}`, provenance: p.runs.provenance }],
  },
  {
    ...SC11_STEPS[5],
    durationMs: 2000,
    caption: "Every logged run per pipeline, scored by the eval harness.",
    services: ["Amazon S3 · runs"],
    receipts: (p) => LANES.map((pv) => ({ label: `N of N · ${pv}`, value: `${tally(p.runs.lanes[pv].dots).correct} of ${tally(p.runs.lanes[pv].dots).n} correct · ${tally(p.runs.lanes[pv].dots).cited} cite a region`, path: `$.runs.lanes['${pv}'].dots`, provenance: p.runs.provenance })),
  },
];

// --------------------------------------------------------------------------------------------- frame

function Sc11Stage({ payload: p, step, progress: t }: Frame<Sc11Payload>) {
  return (
    <ProvenanceScope provenance={p.runs.provenance} source={`GET /runs · ${p.runs.packageId}`}>
      <div className="flex h-full min-h-0 flex-col">
        <ExampleSwitcher p={p} step={step} />
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto flex max-w-[1280px] flex-col gap-3 px-4 py-4 sm:px-6">
            <QuestionStrip p={p} t={step === STEP.question ? t : 1} />
            {step === STEP.why ? <Why p={p} t={t} /> : <Columns p={p} step={step} t={t} />}
            {step >= STEP["only-preprocessing"] && <DiffStrip diff={p.runs.diff} t={step === STEP["only-preprocessing"] ? t : 1} />}
          </div>
        </div>
      </div>
    </ProvenanceScope>
  );
}

/** The examples with their outcome per lane; links keep the step (a deep link to the same moment). */
function ExampleSwitcher({ p, step }: { p: Sc11Payload; step: number }) {
  return (
    <nav aria-label="Example" className="shrink-0 border-b border-border bg-bg-subtle px-4 py-2">
      <ul className="grid grid-cols-2 gap-1.5 lg:grid-cols-[repeat(4,minmax(0,1fr))]">
        {p.examples.map((o) => {
          const on = o.example.id === p.example.id;
          return (
            <li key={o.example.id} className="min-w-0">
              <Link
                to={`?ex=${o.example.id}&step=${step + 1}`}
                aria-current={on ? "page" : undefined}
                className={cn("flex min-w-0 flex-col gap-0.5 overflow-hidden rounded-sm border px-2.5 py-1.5", on ? "border-border-strong bg-surface" : "border-transparent hover:bg-surface-2")}
                title={o.example.code}
                data-example={o.example.id}
              >
                <span className={cn("truncate text-sm", on ? "text-text" : "text-text-2")}>{o.example.title}</span>
                <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5 overflow-hidden text-xs">
                  {LANES.map((pv) => (
                    <OutcomePip key={pv} pv={pv} o={o.lanes[pv]} />
                  ))}
                </span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

/** "Naive ✓ 10/10 · no region": the lane's outcome at a glance (the full counts are in step 6). */
function OutcomePip({ pv, o }: { pv: Lane; o: ReturnType<typeof tally> }) {
  const allWrong = o.n > 0 && o.wrong === o.n;
  const allRight = o.n > 0 && o.correct === o.n;
  return (
    <span className="flex items-center gap-1 whitespace-nowrap text-text-2" title={`${LANE_NAME[pv]} · ${pv}: ${o.correct} of ${o.n} correct · ${o.cited} cite a region${o.errors ? ` · ${o.errors} errors` : ""}`}>
      <span className="font-mono text-mono-sm">{pv}</span>
      <span className={cn("font-semibold", allRight ? "text-cited" : allWrong ? "text-loss" : "text-text-2")} aria-hidden>
        {allRight ? "✓" : allWrong ? "×" : "~"}
      </span>
      <span className="tnum">
        {o.correct}/{o.n}
      </span>
      {o.correct > 0 && o.cited === 0 && <span className="text-text-3">no region</span>}
    </span>
  );
}

function QuestionStrip({ p, t }: { p: Sc11Payload; t: number }) {
  const k = LANES.map((pv) => p.retrieved[pv].k);
  return (
    <section className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-surface px-4 py-2.5" style={{ opacity: 0.35 + 0.65 * ease(seg(t, 0, 0.5)) }}>
      <p className="text-md text-text" title={p.example.code}>
        “{p.runs.question}”
      </p>
      <span className="font-mono text-mono-sm text-text-2">{p.runs.qid}</span>
      <span className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-2">
        {p.runs.expected && (
          <span className="flex items-baseline gap-1">
            expected <Num value={p.runs.expected} kind="raw" provenance="generator" size="xs" />
          </span>
        )}
        <span>
          retrieval profile default · K{" "}
          {k[0] === k[1] ? <Num value={k[0]} kind="int" size="xs" bare /> : k.map((x, i) => <Num key={i} value={x} kind="int" size="xs" bare />)} · both lanes
        </span>
        <span>{p.runs.packageName}</span>
        <ScopeTag />
      </span>
    </section>
  );
}

function Columns({ p, step, t }: { p: Sc11Payload; step: number; t: number }) {
  const before = step === STEP.before;
  const after = step === STEP.after;
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {LANES.map((pv) => {
        const naive = pv === "1.0";
        const answered = step >= STEP.after || (naive && step >= STEP.before);
        const reveal = (naive && before) || (!naive && after) ? t : 1;
        return (
          <section key={pv} className={cn("flex min-w-0 flex-col rounded-md border border-border bg-surface", naive && after && "opacity-80")} data-lane={pv}>
            <header className="flex items-center gap-2 border-b border-border px-4 py-2">
              <LaneBadge pv={pv} />
              <span className="truncate font-mono text-mono-sm text-text-2">{runOf(p, pv)?.runId ?? "no run"}</span>
              {answered && repDot(p, pv) && (
                <span className="ml-auto">
                  <Dot d={repDot(p, pv)!} cited />
                </span>
              )}
            </header>
            <div className="flex min-h-[200px] flex-col gap-3 p-4">
              {step === STEP.question && <SameQuestion p={p} pv={pv} t={t} />}
              {step > STEP.question && !answered && <Pending />}
              {answered && <Answer p={p} pv={pv} t={reveal} detail={step === STEP.after && !naive} />}
              {step === STEP.stability && <Stability p={p} pv={pv} t={t} />}
            </div>
          </section>
        );
      })}
    </div>
  );
}

function SameQuestion({ p, pv, t }: { p: Sc11Payload; pv: Lane; t: number }) {
  const r = runOf(p, pv);
  return (
    <div className="flex flex-col gap-2 text-sm text-text-2" style={{ opacity: 0.3 + 0.7 * ease(seg(t, 0.3, 0.9)) }}>
      <p>
        Asked by <span className="text-text" title={r?.callerSub}>{r?.callerSub ? shortSub(r.callerSub) : "the owner"}</span> on the same agent, prompt and model:
      </p>
      <p className="font-mono text-mono-sm">
        {r?.modelId} · prompt {r?.promptSha.slice(0, 12)}
      </p>
    </div>
  );
}

function Pending() {
  return (
    <div className="flex flex-col gap-3" aria-label="Asked next">
      <Skeleton className="h-6 w-[180px]" />
      <Skeleton className="h-4 w-[300px]" />
      <Skeleton className="h-4 w-[240px]" />
    </div>
  );
}

/** A lane's representative answer: the total (or the text) as it streams, then its chips. */
function Answer({ p, pv, t, detail }: { p: Sc11Payload; pv: Lane; t: number; detail: boolean }) {
  const r = runOf(p, pv);
  if (!r?.answer) return <p className="text-sm text-text-2">This lane has no completed run of the question.</p>;
  const d = repDot(p, pv);
  const typed = ease(seg(t, 0, 0.7));
  const chips = seg(t, 0.7, 1);
  return (
    <>
      {r.answer.total && (
        <Num value={r.answer.total} kind="money" size="lg" mark={d && t >= 0.7 ? (d.status === "correct" ? "cited" : d.status === "wrong" ? "loss" : undefined) : undefined} />
      )}
      <Typed text={r.answer.answer} t={typed} className="line-clamp-4 text-text-2" />
      <div style={{ opacity: chips }}>
        <AnswerChips answer={r.answer} runId={r.runId} />
      </div>
      {detail && p.retrieved["2.3.1"].units.length > 0 && (
        <div className="border-t border-border pt-2" style={{ opacity: chips }}>
          <h3 className="mb-1 text-caps uppercase text-text-2">Retrieved units</h3>
          <RetrievedList items={p.retrieved["2.3.1"].units.slice(0, 3)} />
        </div>
      )}
    </>
  );
}

function Stability({ p, pv, t }: { p: Sc11Payload; pv: Lane; t: number }) {
  const s = tally(p.runs.lanes[pv].dots);
  return (
    <div className="flex flex-col gap-2 border-t border-border pt-3">
      <DotRow dots={p.runs.lanes[pv].dots} t={t} cited />
      <p className="flex flex-wrap items-baseline gap-x-1 text-xs text-text-2">
        <OfN k={s.correct} n={s.n} /> logged runs correct · <OfN k={s.cited} n={s.n} /> cite a region
        {s.errors > 0 && <span>· {s.errors} agent {s.errors === 1 ? "error" : "errors"} (not counted in N)</span>}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------------------------------- why

/** The naive answer's figures, formatted as money (thousands separators, two decimals), for highlighting. */
function answerFigures(r: RunRecord | undefined): string[] {
  const a = r?.answer;
  if (!a) return [];
  // as printed on the page, whatever form the claim wrote it in: with cents, and without when they are zero
  const money = [a.total, ...a.claims.map((c) => c.value)].filter((x): x is string => Boolean(x)).map((x) => formatNum(x.replace(/^\$/, ""), "money"));
  return [...new Set(money.flatMap((m) => (m.endsWith(".00") ? [m, m.slice(0, -3)] : [m])))];
}

function Why({ p, t }: { p: Sc11Payload; t: number }) {
  const figures = answerFigures(runOf(p, "1.0"));
  const units = p.retrieved["1.0"].units;
  return (
    <div className={cn("grid grid-cols-1 gap-3", p.xray && "xl:grid-cols-[minmax(0,1fr)_minmax(0,1.05fr)]")}>
      <section className="rounded-md border border-border bg-surface">
        <header className="flex items-center gap-2 border-b border-border px-4 py-2">
          <LaneBadge pv="1.0" />
          <span className="text-sm text-text-2">first search_units call · top {units.length}</span>
          <span className="ml-auto">
            <ScopeTag />
          </span>
        </header>
        <ol className="divide-y divide-border">
          {units.map((u, i) => (
            <RetrievedItem key={`${u.rank}-${u.unitId}`} u={u} figures={figures} on={easeCubicOut(seg(t, i * 0.15, i * 0.15 + 0.35))} />
          ))}
          {!units.length && <li className="px-4 py-3 text-sm text-text-2">The naive run retrieved nothing.</li>}
        </ol>
        {figures.length > 0 && (
          <p className="border-t border-border px-4 py-2 text-xs text-text-2">
            Highlighted: the figures the logged answer gave ({figures.filter((f) => f.includes(".")).join(", ")}), where the retrieved text prints them <ProvenanceTag provenance="derived" source="string match on the chunk text" />
          </p>
        )}
      </section>
      {p.xray && <XrayInset p={p} t={t} />}
    </div>
  );
}

function RetrievedItem({ u, figures, on }: { u: RetrievedUnit; figures: string[]; on: number }) {
  const hit = figures.map((f) => u.text.indexOf(f)).filter((i) => i >= 0).sort((a, b) => a - b)[0];
  const from = hit === undefined ? 0 : Math.max(0, hit - 90);
  const excerpt = u.text.slice(from, from + 260).replace(/\s+/g, " ");
  return (
    <li className="grid grid-cols-[20px_minmax(0,1fr)_88px] gap-3 px-4 py-2.5" style={{ opacity: 0.2 + 0.8 * on }}>
      <span className="tnum pt-0.5 text-right font-mono text-mono-sm text-text-2">{u.rank}</span>
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-1.5 text-sm text-text">
          <span className="truncate">{u.fileName}</span>
          <span className="font-mono text-mono-sm text-text-2">{u.chunkLabel ?? u.label}</span>
          {!u.resolvable && <Badge tone="loss">no region</Badge>}
        </p>
        <p className="mt-1 line-clamp-3 font-mono text-mono-sm text-text-2">
          {from > 0 && "…"}
          <Highlighted text={excerpt} marks={figures} />…
        </p>
      </div>
      <span className="flex flex-col items-end gap-0.5 text-xs text-text-2">
        fused
        <Num value={u.scores.fused} kind="ratio" size="xs" bare />
      </span>
    </li>
  );
}

function Highlighted({ text, marks }: { text: string; marks: string[] }) {
  if (!marks.length) return <>{text}</>;
  // whole figures only: a figure never lights up inside a longer number
  const re = new RegExp(`(?<![\\d,.])(${marks.map((m) => m.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})(?![\\d]|[.,]\\d)`, "g");
  return (
    <>
      {text.split(re).map((part, i) =>
        marks.includes(part) ? (
          <mark key={i} className="rounded-xs bg-surface-3 px-0.5 text-text underline decoration-text-2 underline-offset-2">
            {part}
          </mark>
        ) : (
          <span key={i}>{part}</span>
        ),
      )}
    </>
  );
}

/** The W-2 page as the naive lane read it: its chunk, the lines the logged answer's numbers match, Box 1's value. */
function XrayInset({ p, t }: { p: Sc11Payload; t: number }) {
  const x = p.xray!;
  const used = (x.answerNumbers ?? []).filter((n) => n.matchedLines.length > 0);
  const line = (o: number) => x.lines.find((l) => l.ordinal === o);
  const usedRects = used.flatMap((n) => n.matchedLines.map((o) => ({ n, l: line(o) }))).filter((u) => u.l?.bbox);
  const box1 = x.pairs.find((pr) => pr.concept === FOLLOW_SUBJECT.concept);
  const box1Value = box1?.geometricValue?.bbox ? toRect(box1.geometricValue.bbox) : undefined;
  const box1Label = box1?.label.bbox ? toRect(box1.label.bbox) : undefined;
  const focus = union([...usedRects.map((u) => toRect(u.l!.bbox!)), box1Value, box1Label].filter((r): r is Rect => Boolean(r)));
  const pageRect: Rect = [0, 0, x.pageWidth, x.pageHeight];
  const answerRun = x.sources.answerRun?.runId;
  const reveal = seg(t, 0.35, 0.8);
  return (
    <section className="flex min-h-[420px] flex-col overflow-hidden rounded-md border border-border bg-surface">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2">
        <span className="text-sm text-text">X-ray</span>
        <span className="truncate font-mono text-mono-sm text-text-2">
          {x.sources.fileName} · p{x.page}
        </span>
        <span className="ml-auto">
          <ProvenanceTag provenance="derived" source={x.sources.bronze.key} />
        </span>
      </header>
      <div className="min-h-[300px] flex-1">
        <PageCanvas
          ariaLabel={`${x.sources.fileName}, page ${x.page}: what the naive lane read`}
          image={{ src: x.image.url, src2x: x.image.zoomUrl, dpi: x.image.dpi }}
          pageWidth={x.pageWidth}
          pageHeight={x.pageHeight}
          focus={focus ? pad(focus, 56) : pageRect}
          animate={false}
          padding={16}
        >
          <ChunkSlices chunks={x.chunks.map(sliceOf)} opacity={0.35 * ease(seg(t, 0, 0.3))} labels={false} />
          {box1Value && <BoxOutline rect={pad(box1Value, 2)} tone="muted" width={1} opacity={reveal} />}
          {box1Value && <Tag x={box1Value[0]} y={(box1Value[1] + box1Value[3]) / 2} text={`Box 1 · ${box1?.geometricValue?.text}`} tone="muted" placement="left" opacity={reveal} />}
          {usedRects.map(({ n, l }) => (
            <g key={`${n.value}-${l!.ordinal}`}>
              <BoxOutline rect={pad(toRect(l!.bbox!), 2)} tone="text" width={1} opacity={seg(t, 0.55, 0.9)} />
              <Mark x={l!.bbox!.x1} y={(l!.bbox!.y0 + l!.bbox!.y1) / 2} dx={12} kind="used" opacity={seg(t, 0.6, 0.95)} />
            </g>
          ))}
        </PageCanvas>
      </div>
      <div className="flex flex-col gap-1 border-t border-border px-4 py-2.5 text-xs text-text-2">
        <ul className="flex flex-col gap-0.5">
          {xrayFindings(x).map((f) => (
            <li key={f.value} className="flex flex-wrap items-baseline gap-1.5">
              <span className="inline-block size-1.5 translate-y-[-1px] rounded-full bg-text-2" aria-hidden />
              <Num value={f.value} kind="money" size="xs" bare />
              <span>
                {f.lines.length ? `printed on ${f.lines.map((l) => `line #${l.ordinal}${l.box ? ` (${l.box})` : ""}`).join(", ")}` : "not printed on this page"}
              </span>
            </li>
          ))}
          {box1?.geometricValue && (
            <li className="flex flex-wrap items-baseline gap-1.5">
              <span className="inline-block size-1.5 translate-y-[-1px] rounded-full border border-text-2" aria-hidden />
              <span>
                {box1.label.text} prints <span className="tnum font-mono text-text">{box1.geometricValue.text}</span> on line #{box1.geometricValue.ordinal}
              </span>
            </li>
          )}
        </ul>
        <p>
          The answer's numbers come from run <span className="font-mono">{answerRun ?? "—"}</span>
          {answerRun && answerRun !== runOf(p, "1.0")?.runId ? " (another logged run of this question)" : ""}. <span title="Form labels are drawn first and values afterwards as an overlay, as payroll software often prints a W-2.">This page's reading order is constructed on purpose.</span>
        </p>
      </div>
    </section>
  );
}

export const sc11BeforeAfter: Storyboard<Sc11Payload> = {
  id: "sc-11",
  code: "SC-11",
  title: showcaseMeta("sc-11")?.title ?? "",
  claim: showcaseMeta("sc-11")?.claim ?? "",
  load: loadSc11,
  steps,
  render: (frame) => <Sc11Stage {...frame} />,
  receiptsPayload: (p) => ({
    example: p.example,
    examples: p.examples,
    runs: { ...p.runs, lanes: Object.fromEntries(LANES.map((pv) => [pv, { stats: { ...p.runs.lanes[pv].stats, runs: undefined }, run: p.runs.lanes[pv].run?.runId, dots: p.runs.lanes[pv].dots }])) },
    retrieved: Object.fromEntries(LANES.map((pv) => [pv, { ...p.retrieved[pv], units: p.retrieved[pv].units.map((u) => ({ rank: u.rank, unitId: u.unitId, scores: u.scores })) }])),
    xray: p.xray && { docId: p.xray.docId, page: p.xray.page, answerNumbers: p.xray.answerNumbers, sources: p.xray.sources },
  }),
};

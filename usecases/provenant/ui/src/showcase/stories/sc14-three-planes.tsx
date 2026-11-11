/**
 * SC-14 · Three planes. One document's real execution: its upload, both lanes' stage records lighting
 * in the order they ran (measured ms as text), the PII gate and the promotion to Gold, the knowledge-plane writes,
 * and one ask through the agent plane, tool call by tool call with the identity the gateway injected.
 *
 * The planes map stays on screen; each step lights its band and dims the others. Elements with no record stay unlit
 * and say "not in this execution". Items not in this build (vision, DOCX, Memory) are dashed and say so; the cut id
 * is in their tooltip.
 * Frames are pure functions of (payload, step, progress); playback pacing is labelled.
 */
import { easeCubicOut } from "d3-ease";
import { ArrowRight, Link as LinkIcon } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { StageRecord } from "@contracts/api";
import { CitationChip, LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, Tooltip, cn, decisionLabel } from "../../design";
import { subLabel } from "../../lib/format";
import { claimLabel, traceHref } from "../../features/ask/parts";
import { seg } from "../../viz/geom";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { LANES } from "./lending";
import { type Sc14Payload, gateCaption, ingestionCaption, loadSc14, stageClock } from "./sc14-payload";
import { SC14_STEPS } from "./steps";

const STEP = Object.fromEntries(SC14_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC14_STEPS)[number]["id"], number>;
const sha12 = (s?: string) => (s ? s.slice(0, 12) : "—");
const short = (arn?: string) => arn?.split(":").at(-1);

const laneReceipts = (p: Sc14Payload) =>
  LANES.flatMap((pv) => p.lanes[pv].stages.map((s) => ({ label: `${pv} · ${s.sfnState ?? s.stageId} · ${s.state.toLowerCase()}`, value: s.durationMs !== undefined ? `${s.durationMs} ms · ${s.engine ?? ""}` : s.state, source: s.artifacts[0]?.key ?? "rp-stages", provenance: p.provenance })));

const steps: StoryStep<Sc14Payload>[] = [
  { ...SC14_STEPS[0], durationMs: 2000, caption: "Four of the five patterns act before retrieval.", receipts: () => [{ label: "Layer map", value: "schematic · no values", source: "the layer model (L1 extraction, L2 representation, L3 retrieval and generation)", provenance: "illustrative" }] },
  {
    ...SC14_STEPS[1],
    durationMs: 2400,
    caption: "One upload starts both pipelines.",
    services: ["Amazon S3", "Amazon EventBridge", "AWS Step Functions"],
    receipts: (p) => [
      { label: "Bronze object", value: `${p.doc.fileName} · version ${p.doc.sourceVersionId} · sha256 ${p.doc.sha256}`, source: p.doc.bronzeKey, provenance: p.provenance },
      ...LANES.map((pv) => ({ label: `Execution · ${pv}`, value: p.lanes[pv].lane.executionArn, source: "LaneRecord", provenance: p.provenance })),
    ],
  },
  { ...SC14_STEPS[2], durationMs: 4200, caption: ingestionCaption, services: ["AWS Step Functions", "AWS Fargate", "Amazon Textract", "AWS Lambda"], receipts: laneReceipts },
  {
    ...SC14_STEPS[3],
    durationMs: 2000,
    caption: gateCaption,
    services: ["Amazon Comprehend", "Amazon S3 · Gold"],
    receipts: (p) => LANES.flatMap((pv) => p.lanes[pv].stages.filter((s) => s.stageId === "pii_gate" || s.stageId === "promote").map((s) => ({ label: `${pv} · ${s.stageId}`, value: JSON.stringify(s.metrics), source: s.artifacts.map((a) => a.key).join(", "), provenance: p.provenance }))),
  },
  {
    ...SC14_STEPS[4],
    durationMs: 2400,
    caption: "Units go to the index and facts go to the graph.",
    services: ["Amazon OpenSearch Serverless", "Amazon Neptune", "Amazon DynamoDB"],
    receipts: (p) => LANES.map((pv) => ({ label: `${pv} · write receipts`, value: `indexed ${p.lanes[pv].lane.indexed ?? 0} · triples ${p.lanes[pv].lane.triples ?? 0}`, source: p.lanes[pv].stages.find((s) => s.stageId === "index")?.artifacts[0]?.key, provenance: p.provenance })),
  },
  {
    ...SC14_STEPS[5],
    durationMs: 3000,
    caption: "The question travels through the gateway; every tool call is scoped.",
    services: ["Amazon API Gateway", "Amazon Cognito", "Amazon Bedrock AgentCore", "AWS Lambda"],
    receipts: (p) => [
      { label: "Run", value: p.run.runId, source: p.run.s3Key, provenance: p.provenance },
      ...p.access.map((a) => ({ label: `${a.tool} · ${decisionLabel(a)}`, value: `${subLabel(a.callerSub6, [p.run.callerSub])} · ${a.injected.packageId} · ${a.injected.pipelineVersion} · via ${a.injectionPoint}`, source: `GET /access/decisions?runId=${a.runId} (${a.source})`, provenance: p.provenance })),
    ],
  },
  {
    ...SC14_STEPS[6],
    durationMs: 1600,
    caption: "The answer comes back with its regions.",
    services: ["Amazon Bedrock"],
    receipts: (p) => [{ label: "Cited claim", value: p.claim ? `${p.claim.text} · ${p.claim.unitId} · ${p.claim.fieldId ?? ""}` : "none", path: "$.claim", provenance: p.provenance }],
  },
];

// --------------------------------------------------------------------------------------------- frame

type BandState = "future" | "current" | "past";

function Sc14Stage({ payload: p, step, progress: t }: Frame<Sc14Payload>) {
  if (step === STEP.layers) return <LayerMap t={t} />;
  // a band not reached yet is one quiet line (no dimmed text: every visible text keeps its contrast)
  const state = (from: number, to = from): BandState => (step < from ? "future" : step <= to ? "current" : "past");
  const local = (from: number, to = from) => (step < from ? 0 : step > to ? 1 : step === from ? t : 1);
  return (
    <ProvenanceScope provenance={p.provenance} source={`stage records · ${p.doc.docId}`}>
      <div className="h-full overflow-y-auto">
        <div className="mx-auto flex max-w-[1280px] flex-col gap-2 px-4 py-3 sm:px-6">
          <p className="flex items-center gap-2 text-xs text-text-2">
            Playback is paced; measured times are the text in each box.
            <span className="ml-auto">
              <ScopeTag />
            </span>
          </p>
          <Upload p={p} t={local(STEP.upload)} state={state(STEP.upload)} compact={step >= STEP.agent} />
          <Ingestion p={p} t={local(STEP.ingestion, STEP.gate)} gate={step === STEP.gate || step === STEP.knowledge ? (step === STEP.gate ? t : 1) : 0} state={state(STEP.ingestion, STEP.gate)} />
          <Knowledge p={p} t={local(STEP.knowledge)} state={state(STEP.knowledge)} />
          <Agent p={p} t={local(STEP.agent, STEP.answer)} answer={step === STEP.answer ? t : 0} state={state(STEP.agent, STEP.answer)} />
        </div>
      </div>
    </ProvenanceScope>
  );
}

function Band({ title, meta, state, children }: { title: string; meta?: ReactNode; state: BandState; children: ReactNode }) {
  return (
    <section className={cn("rounded-md border bg-surface", state === "current" ? "border-border-strong" : "border-border")} data-band-state={state}>
      <h3 className={cn("flex items-center gap-2 px-4 py-1.5 text-caps uppercase", state === "future" ? "text-text-3" : "text-text-2", state !== "future" && "border-b border-border")}>
        <span className="flex-1">{title}</span>
        {state === "future" ? <span className="normal-case tracking-normal">next</span> : meta}
      </h3>
      {state !== "future" && <div className="px-4 py-2.5">{children}</div>}
    </section>
  );
}

/** A greyed box for an item not in this build, with its cut id. */
function Cut({ label, cut, why }: { label: string; cut: string; why: string }) {
  return (
    <Tooltip content={why}>
      <span className="inline-flex h-6 items-center gap-1.5 rounded-xs border border-dashed border-border px-2 text-xs text-text-2" tabIndex={0} data-cut={cut}>
        {label} <span className="text-text-3">· not in this build</span>
      </span>
    </Tooltip>
  );
}

/** Step 1: the layer map (schematic, no values). */
function LayerMap({ t }: { t: number }) {
  const rows = [
    { id: "L1", title: "Extraction", patterns: ["P1 Read the page"] },
    { id: "L2", title: "Representation", patterns: ["P2 Evidence units", "P3 Ontology", "P4 Metadata"] },
    { id: "L3", title: "Retrieval and generation", patterns: ["P5 Resolve, retrieve, hop, cite"] },
  ];
  return (
    <div className="flex h-full items-center justify-center p-6">
      <section className="w-full max-w-[760px] rounded-md border border-dashed border-border-strong bg-surface">
        <h3 className="flex items-center gap-2 border-b border-border px-4 py-2 text-caps uppercase text-text-2">
          <span className="flex-1">Layers</span>
          <ProvenanceTag provenance="illustrative" source="schematic: no values" />
        </h3>
        <ol className="divide-y divide-border">
          {rows.map((r, i) => (
            <li key={r.id} className="grid grid-cols-[48px_200px_minmax(0,1fr)] items-center gap-3 px-4 py-3" style={{ opacity: 0.2 + 0.8 * easeCubicOut(seg(t, i * 0.25, i * 0.25 + 0.4)) }}>
              <span className="font-mono text-mono-sm text-text-2">{r.id}</span>
              <span className="text-sm text-text">{r.title}</span>
              <span className="flex flex-wrap gap-1.5">
                {r.patterns.map((x) => (
                  <span key={x} className="rounded-xs border border-border-strong px-2 py-0.5 text-xs text-text">
                    {x}
                  </span>
                ))}
              </span>
            </li>
          ))}
        </ol>
        <p className="border-t border-border px-4 py-2 text-xs text-text-2">Patterns 1–4 act on the documents before anything is retrieved.</p>
      </section>
    </div>
  );
}

/** Step 2: the Bronze object and the two executions its upload started. */
function Upload({ p, t, state, compact }: { p: Sc14Payload; t: number; state: BandState; compact?: boolean }) {
  const d = p.doc;
  if (compact)
    return (
      <Band state={state} title="Upload" meta={<span className="font-mono normal-case tracking-normal">{p.packageId}</span>}>
        <p className="flex flex-wrap items-center gap-x-2 text-xs text-text-2">
          <span className="text-sm text-text">{d.fileName}</span> sha256 <span className="font-mono">{sha12(d.sha256)}</span> → Amazon EventBridge → {LANES.length} executions
        </p>
      </Band>
    );
  return (
    <Band state={state} title="Upload" meta={<span className="font-mono normal-case tracking-normal">{p.packageId}</span>}>
      <div className="grid grid-cols-1 items-center gap-3 lg:grid-cols-[minmax(0,1.3fr)_20px_160px_20px_minmax(0,1.2fr)]">
        <div className="flex min-w-0 flex-col gap-0.5" style={{ opacity: 0.3 + 0.7 * seg(t, 0, 0.3) }}>
          <span className="text-sm text-text">{d.fileName}</span>
          <span className="truncate font-mono text-mono-sm text-text-2" title={d.bronzeKey}>
            {d.bronzeKey}
          </span>
          <span className="text-xs text-text-2">
            sha256 <span className="font-mono">{sha12(d.sha256)}</span> · version <span className="font-mono">{d.sourceVersionId?.slice(0, 8)}</span> · <Num value={d.sizeBytes} kind="int" size="xs" bare /> B
          </span>
        </div>
        <ArrowRight className="hidden size-4 text-text-3 lg:block" style={{ opacity: seg(t, 0.3, 0.45) }} aria-hidden />
        <span className="rounded-sm border border-border px-2.5 py-1.5 text-xs text-text" style={{ opacity: 0.3 + 0.7 * seg(t, 0.35, 0.55) }}>
          Amazon EventBridge
          <span className="block text-text-2">object created · one rule</span>
        </span>
        <ArrowRight className="hidden size-4 text-text-3 lg:block" style={{ opacity: seg(t, 0.55, 0.7) }} aria-hidden />
        <ul className="flex flex-col gap-1">
          {LANES.map((pv, i) => {
            const l = p.lanes[pv].lane;
            return (
              <li key={pv} className="flex items-center gap-2 text-xs text-text-2" style={{ opacity: 0.3 + 0.7 * seg(t, 0.6 + i * 0.15, 0.8 + i * 0.15) }}>
                <LaneBadge pv={pv} />
                <span className="truncate font-mono" title={l.executionArn}>
                  {short(l.executionArn) ?? "no execution"}
                </span>
                <span className="ml-auto">
                  <Num value={l.durationMs} kind="ms" size="xs" bare />
                </span>
              </li>
            );
          })}
        </ul>
      </div>
    </Band>
  );
}

/** Steps 3–4: both lanes' states, lit in the order they ran; the PII gate frame and Gold keys on step 4. */
function Ingestion({ p, t, gate, state }: { p: Sc14Payload; t: number; gate: number; state: BandState }) {
  const { t0, t1 } = stageClock(p);
  const at = (s: StageRecord) => (s.startedAt ? (Date.parse(s.startedAt) - t0) / Math.max(1, t1 - t0) : 2);
  return (
    <Band
      state={state}
      title="Ingestion plane · AWS Step Functions"
      meta={
        <span className="flex items-center gap-1.5 normal-case tracking-normal">
          <Cut label="Vision for figures" cut="vision" why="Not in this build: figures and formulas are not sent to a vision model." />
          <Cut label="DOCX" cut="docx" why="Not in this build: DOCX has no page geometry; a render-to-PDF step would be needed to keep regions." />
        </span>
      }
    >
      <div className="flex flex-col gap-2">
        {LANES.map((pv) => (
          <div key={pv} className="grid grid-cols-[150px_minmax(0,1fr)] items-start gap-3">
            <LaneBadge pv={pv} className="mt-1 justify-self-start" />
            <ol className="grid grid-cols-5 gap-1 xl:grid-cols-10">
              {p.lanes[pv].stages.map((s) => {
                const lit = s.state === "SUCCEEDED" && t >= Math.min(0.98, at(s) * 0.9 + 0.02);
                const isGate = s.stageId === "pii_gate";
                const gateOn = isGate && gate > 0;
                return (
                  <li
                    key={s.stageId}
                    className={cn(
                      "relative flex min-w-0 flex-col rounded-sm border px-2 py-1 text-xs",
                      s.state === "SKIPPED" ? "border-dashed border-border text-text-3" : lit ? "border-border-strong bg-surface-2 text-text" : "border-border text-text-3",
                      gateOn && "border-boundary",
                    )}
                    title={[s.engine, s.headline].filter(Boolean).join(" · ")}
                    data-stage={s.stageId}
                    data-lit={lit}
                  >
                    <span className="truncate font-medium">{s.sfnState ?? s.title}</span>
                    <span className="truncate text-text-2">{s.state === "SKIPPED" ? "skipped" : lit ? <Num value={s.durationMs} kind="ms" size="xs" bare /> : "…"}</span>
                  </li>
                );
              })}
            </ol>
          </div>
        ))}
        {gate > 0 && (
          <div className="ml-[162px] flex flex-wrap items-center gap-1.5 text-xs text-text-2" style={{ opacity: seg(gate, 0.2, 0.6) }}>
            {LANES.map((pv) => {
              const g = p.lanes[pv].stages.find((s) => s.stageId === "pii_gate");
              return g ? (
                <span key={pv} className="rounded-xs border border-boundary px-1.5 text-text">
                  {pv} · gate held <Num value={g.metrics.held ?? 0} kind="int" size="xs" bare /> of <Num value={g.metrics.units ?? 0} kind="int" size="xs" bare /> {g.metrics.units === 1 ? "unit" : "units"}
                </span>
              ) : null;
            })}
          </div>
        )}
        {gate > 0 && (
          <div className="ml-[162px] flex flex-wrap items-center gap-1.5 text-xs text-text-2" style={{ opacity: seg(gate, 0.4, 0.9) }}>
            <span className="text-text">Gold</span>
            {LANES.flatMap((pv) => p.lanes[pv].stages.find((s) => s.stageId === "promote")?.artifacts.slice(0, 2).map((a) => (
              <span key={a.key} className="truncate rounded-xs border border-border px-1.5 font-mono text-mono-sm" title={a.key}>
                {a.key.split("/").slice(-3).join("/")}
              </span>
            )) ?? [])}
          </div>
        )}
      </div>
    </Band>
  );
}

/** Step 5: what each lane wrote to the knowledge plane, from the lane records and the stage receipts. */
function Knowledge({ p, t, state }: { p: Sc14Payload; t: number; state: BandState }) {
  const receipt = (title: string, body: ReactNode, i: number) => (
    <div className="flex min-w-0 flex-col gap-1 rounded-sm border border-border px-3 py-2" style={{ opacity: 0.3 + 0.7 * easeCubicOut(seg(t, i * 0.25, i * 0.25 + 0.4)) }}>
      <span className="text-xs text-text-2">{title}</span>
      {body}
    </div>
  );
  return (
    <Band state={state} title="Knowledge plane">
      <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
        {receipt(
          "Amazon OpenSearch Serverless · units indexed",
          <ul className="flex flex-col gap-0.5 text-sm">
            {LANES.map((pv) => (
              <li key={pv} className="flex items-baseline gap-2">
                <span className="font-mono text-mono-sm text-text-2">{p.lanes[pv].index ?? pv}</span>
                <Num value={p.lanes[pv].lane.indexed ?? 0} kind="int" className="ml-auto" />
              </li>
            ))}
          </ul>,
          0,
        )}
        {receipt(
          "Amazon Neptune · triples in the package ABox",
          <ul className="flex flex-col gap-0.5 text-sm">
            {LANES.map((pv) => (
              <li key={pv} className="flex items-baseline gap-2">
                <span className="truncate font-mono text-mono-sm text-text-2">{pv === "2.3.1" ? (p.abox ?? "abox") : `${pv} · no ontology binding`}</span>
                <Num value={p.lanes[pv].lane.triples ?? 0} kind="int" className="ml-auto" />
              </li>
            ))}
          </ul>,
          1,
        )}
        {receipt(
          "Amazon DynamoDB · document status",
          <ul className="flex flex-col gap-0.5 text-sm">
            {LANES.map((pv) => (
              <li key={pv} className="flex items-baseline gap-2">
                <LaneBadge pv={pv} />
                <span className="ml-auto text-text">{p.lanes[pv].lane.state.toLowerCase()}</span>
              </li>
            ))}
          </ul>,
          2,
        )}
      </div>
    </Band>
  );
}

/** Steps 6–7: the request's hops, each tool call lit with the identity injected into it; then the cited answer. */
function Agent({ p, t, answer, state }: { p: Sc14Payload; t: number; answer: number; state: BandState }) {
  const hops = ["Amazon API Gateway · Cognito JWT", "AgentCore Runtime", "AgentCore Gateway · interceptor"];
  const calls = p.run.toolCalls;
  return (
    <Band
      state={state}
      title="Agent plane"
      meta={
        <span className="flex items-center gap-1.5 normal-case tracking-normal">
          <Cut label="AgentCore Memory" cut="agent-memory" why="Not used in this build: the agent's memory retrieval is off (W-18 shows the config value)." />
        </span>
      }
    >
      <div className="flex flex-col gap-3">
        <ol className="flex flex-wrap items-center gap-1.5">
          {hops.map((h, i) => (
            <li key={h} className="flex items-center gap-1.5" style={{ opacity: 0.3 + 0.7 * seg(t, i * 0.12, i * 0.12 + 0.15) }}>
              {i > 0 && <ArrowRight className="size-3.5 text-text-3" aria-hidden />}
              <span className="rounded-sm border border-border-strong px-2.5 py-1 text-xs text-text">{h}</span>
            </li>
          ))}
        </ol>
        <ol className="grid grid-cols-1 gap-1.5 md:grid-cols-2 xl:grid-cols-4">
          {calls.map((c, i) => {
            const a = p.access.find((x) => x.callId === c.callId);
            const on = seg(t, 0.4 + (i / calls.length) * 0.5, 0.4 + ((i + 1) / calls.length) * 0.5);
            return (
              <li key={c.callId} className={cn("flex min-w-0 flex-col gap-0.5 rounded-sm border px-2.5 py-1.5 text-xs", on >= 1 ? "border-agent/60 text-text" : "border-border text-text-3")} style={{ opacity: 0.3 + 0.7 * on }} data-call={c.tool}>
                <span className="flex items-center gap-1.5">
                  <span className="font-mono text-mono-sm">{c.tool}</span>
                  <Num value={c.ms} kind="ms" size="xs" bare className="ml-auto text-text-2" />
                </span>
                {a ? (
                  <span className="truncate text-text-2" title={`${p.run.callerSub} · ${a.groups.join(",")} · ${a.injected.packageId} · ${a.injected.pipelineVersion} · ${decisionLabel(a)}`}>
                    injected {subLabel(a.callerSub6, [p.run.callerSub])} · {a.groups.join(",")} · {a.injected.pipelineVersion}
                  </span>
                ) : (
                  <span className="text-text-3">no access row · not in this execution</span>
                )}
              </li>
            );
          })}
        </ol>
        {p.accessSource === "run-record" && <p className="text-xs text-text-2">Access rows rebuilt from the run record: this run has no interceptor log rows.</p>}
        {answer > 0 && p.claim && (
          <div className="flex flex-wrap items-center gap-3 border-t border-border pt-3" style={{ opacity: seg(answer, 0, 0.5) }}>
            {p.run.answer?.total && <Num value={p.run.answer.total} kind="money" size="lg" />}
            <CitationChip kind="cited" label={claimLabel(p.claim)} page={p.claim.page} to={traceHref(p.claim, p.run.runId)} />
            <Link to="/showcase/sc-9" className="ml-auto flex items-center gap-1 text-xs text-text-2 hover:text-text">
              <LinkIcon className="size-3.5" aria-hidden /> Follow the number (SC-9)
            </Link>
          </div>
        )}
      </div>
    </Band>
  );
}

export const sc14ThreePlanes: Storyboard<Sc14Payload> = {
  id: "sc-14",
  code: "SC-14",
  title: showcaseMeta("sc-14")?.title ?? "",
  claim: showcaseMeta("sc-14")?.claim ?? "",
  load: loadSc14,
  steps,
  render: (frame) => <Sc14Stage {...frame} />,
  receiptsPayload: (p) => ({ doc: p.doc, lanes: p.lanes, run: p.run.runId, access: p.access, claim: p.claim }),
};

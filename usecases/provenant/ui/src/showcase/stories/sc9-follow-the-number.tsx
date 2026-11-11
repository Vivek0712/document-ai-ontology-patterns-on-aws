/**
 * SC-9 · Follow the number. Pick a figure in the logged evidence-units answer and follow it: the
 * typed field, the unit boundary, the region in PDF points, the engine blocks, the uploaded bytes (sha256 checked
 * against what register wrote) and the pipeline, prompt and model. The naive lane's citation for the same figure
 * stops at its chunk.
 *
 * Every frame is `render({ payload, step, progress })`: no state, no timers, no typed values.
 */
import { easeCubicInOut } from "d3-ease";
import { X } from "lucide-react";
import type { Check, Claim } from "@contracts/api";
import { waitForLanes } from "../../api/runs";
import type { Trace, TraceContrast } from "../../api/trace";
import { CitationChip, LaneBadge, Num, type Provenance, ProvenanceScope, ScopeTag, cn } from "../../design";
import { type HopId, TraceChain } from "../../features/trace/TraceChain";
import { TraceCanvas, traceFrames } from "../../features/trace/TraceCanvas";
import { type Rect, lerpRect, seg } from "../../viz/geom";
import { Tag } from "../../viz/PageCanvas";
import { showcaseMeta } from "../catalog";
import type { Frame, Loaded, StoryContext, StoryStep, Storyboard } from "../engine/types";
import { followTrace, gateQuestion, headlineRuns, naiveContrast, runsSource } from "./lending";
import { SC9_STEPS } from "./steps";

const ease = easeCubicInOut;

export interface Sc9Payload {
  trace: Trace;
  contrast?: TraceContrast;
  /** the evidence-units answer the figure comes from */
  answer: { runId: string; total?: string; arithmetic?: string; claims: Claim[]; s3Key: string; check?: Check };
  /** the followed claim's index in `answer.claims` */
  selected: number;
  naiveRun: { runId: string; s3Key: string };
  provenance: Provenance;
  frames: { page: Rect; unit: Rect; field: Rect; zoom: Rect };
}

export async function loadSc9({ api, packageId }: StoryContext): Promise<Loaded<Sc9Payload>> {
  await waitForLanes(api, packageId);
  const h = await headlineRuns(api, packageId, gateQuestion("sc-9").questionId);
  const run = h.runs["2.3.1"];
  const trace = await followTrace(api, run);
  const contrast = await naiveContrast(api, h.runs["1.0"], trace);
  const claims = run.answer?.claims ?? [];
  return {
    source: runsSource(api, [run.runId, h.runs["1.0"].runId], run.createdAt),
    payload: {
      trace,
      contrast,
      answer: { runId: run.runId, total: run.answer?.total, arithmetic: run.answer?.arithmetic, claims, s3Key: run.s3Key, check: run.check },
      selected: claims.findIndex((c) => c.unitId === trace.unit.unitId && c.fieldId === trace.field?.fieldId),
      naiveRun: { runId: h.runs["1.0"].runId, s3Key: h.runs["1.0"].s3Key },
      provenance: h.provenance,
      frames: traceFrames(trace),
    },
  };
}

/** The hops each step has revealed (the newest last). */
const HOPS_BY_STEP: HopId[][] = [["claim"], ["field"], ["unit"], ["region"], ["blocks"], ["source"], ["pipeline", "run"], ["contrast"]];
const hopsUpTo = (step: number): HopId[] => HOPS_BY_STEP.slice(0, step + 1).flat();

const steps: StoryStep<Sc9Payload>[] = [
  {
    ...SC9_STEPS[0],
    durationMs: 1800,
    caption: "Pick any figure in the answer.",
    services: ["Amazon API Gateway", "Amazon Bedrock AgentCore"],
    receipts: (p) => [
      { label: "Logged evidence-units run", value: p.answer.runId, source: p.answer.s3Key, provenance: p.provenance },
      { label: "Followed claim", value: p.trace.claim.text, path: `$.answer.claims[${p.selected}]`, provenance: p.provenance },
      { label: "Answer total", value: p.answer.total, path: "$.answer.total", provenance: p.provenance },
    ],
  },
  {
    ...SC9_STEPS[1],
    durationMs: 2000,
    caption: "It is a typed field with its printed label.",
    services: ["AWS Lambda · rp-api", "Amazon S3"],
    receipts: (p) => [
      { label: "Field", value: `${p.trace.field?.label} = ${p.trace.field?.value}`, path: "$.trace.field", provenance: p.provenance },
      { label: "Concept · period", value: `${p.trace.field?.concept} · ${p.trace.field?.period}`, path: "$.trace.field.concept", provenance: p.provenance },
      { label: "Gold unit object", source: p.trace.steps.find((s) => s.kind === "field")?.artifacts[0]?.key, provenance: p.provenance },
    ],
  },
  {
    ...SC9_STEPS[2],
    durationMs: 2200,
    caption: "The field sits inside one evidence unit.",
    services: ["Amazon S3", "Amazon DynamoDB"],
    receipts: (p) => {
      const a = p.trace.steps.find((s) => s.kind === "unit")?.artifacts.find((x) => x.kind === "units_jsonl");
      return [
        { label: "Unit", value: p.trace.unit.unitId, path: "$.trace.unit", provenance: p.provenance },
        { label: "Silver units", value: a?.versionId, source: a?.key, provenance: p.provenance },
        { label: "Units sha256", value: a?.sha256, provenance: p.provenance },
      ];
    },
  },
  {
    ...SC9_STEPS[3],
    durationMs: 2600,
    caption: "Down to the region on the page.",
    services: ["AWS Lambda · rp-get-region", "Amazon S3"],
    receipts: (p) => {
      const img = p.trace.steps.find((s) => s.kind === "page_image")?.artifacts.find((a) => a.kind === "page_image");
      const b = p.trace.region.bbox;
      return [
        { label: "Region (PDF points, top-left)", value: `(${b.x0}, ${b.y0}) – (${b.x1}, ${b.y1}) · p${p.trace.region.page}`, path: "$.trace.region.bbox", provenance: p.provenance },
        { label: "Gold page image", value: img?.versionId, source: img?.key, provenance: p.provenance },
        { label: "Page image sha256", value: img?.sha256, provenance: p.provenance },
      ];
    },
  },
  {
    ...SC9_STEPS[4],
    durationMs: 2000,
    caption: "What the extraction engine returned for that region.",
    services: ["AWS Fargate · docling-serve", "Amazon S3"],
    receipts: (p) => {
      const a = p.trace.steps.find((s) => s.kind === "blocks")?.artifacts[0];
      return [
        { label: "Engine blocks", value: p.trace.blocks.items.map((b) => `${b.pointer} ${b.engineLabel ?? ""}`).join(", "), path: "$.trace.blocks.items", provenance: p.provenance },
        { label: "Engine output", value: a?.versionId, source: a?.key, provenance: p.provenance },
        { label: "Engine", value: p.trace.blocks.engineVersion ?? p.trace.blocks.engine, provenance: p.provenance },
      ];
    },
  },
  {
    ...SC9_STEPS[5],
    durationMs: 2200,
    caption: "The exact uploaded file, by its hash.",
    services: ["Amazon S3", "Amazon DynamoDB"],
    receipts: (p) => [
      { label: "Bronze object", value: p.trace.source.versionId, source: p.trace.source.bronzeKey, provenance: p.provenance },
      { label: "sha256 (lineage)", value: p.trace.source.sha256, path: "$.trace.source.sha256", provenance: p.provenance },
      { label: "sha256 (DocumentItem, written by register)", value: p.trace.source.registerSha256, path: "$.trace.source.registerSha256", provenance: p.provenance },
      { label: "Equal (compared in the browser)", value: p.trace.source.matchesRegister, path: "$.trace.source.matchesRegister", provenance: "derived" },
    ],
  },
  {
    ...SC9_STEPS[6],
    durationMs: 2400,
    caption: "And the exact pipeline, prompt and model that produced it.",
    services: ["Amazon S3", "Amazon DynamoDB"],
    receipts: (p) => [
      { label: "configSha", value: p.trace.pipeline.configSha, path: "$.trace.pipeline.configSha", provenance: p.provenance },
      { label: "preprocessing block sha", value: p.trace.pipeline.preprocessingSha, path: "$.trace.pipeline.preprocessingSha", provenance: p.provenance },
      { label: "codeSha (units stage record)", value: p.trace.pipeline.codeSha, path: "$.trace.pipeline.codeSha", provenance: p.provenance },
      { label: "Model · promptSha", value: `${p.trace.run?.modelId} · ${p.trace.run?.promptSha}`, path: "$.trace.run", provenance: p.provenance },
      { label: "corpusHash", value: p.trace.run?.corpusHash, path: "$.trace.run.corpusHash", provenance: p.provenance },
    ],
  },
  {
    ...SC9_STEPS[7],
    durationMs: 2200,
    caption: "The naive answer cannot be followed past the chunk.",
    services: ["Amazon S3"],
    receipts: (p) => [
      { label: "Logged naive run", value: p.naiveRun.runId, source: p.naiveRun.s3Key, provenance: p.provenance },
      { label: "Its claim for the same figure", value: p.contrast ? `${p.contrast.claim.unitId} · ${p.contrast.claim.reason ?? ""}` : "no matching claim", path: "$.contrast.claim", provenance: p.provenance },
      { label: "Chunk lineage", value: p.contrast?.stops.map((s) => s.kind).join(" → "), path: "$.contrast.stops", provenance: p.provenance },
    ],
  },
];

// --------------------------------------------------------------------------------------------- frame

/** The page's top half (where the form is printed), so the figure is findable on the first frame. */
const topHalf = (f: Sc9Payload["frames"]): Rect => [f.page[0], f.page[1], f.page[2], (f.page[1] + f.page[3]) / 2];

function cameraFor(p: Sc9Payload, step: number, t: number): { rect: Rect; widthFit: number } {
  const f = p.frames;
  switch (step) {
    case 0:
    case 1:
      return { rect: topHalf(f), widthFit: 0 };
    case 2:
      return { rect: lerpRect(topHalf(f), f.unit, ease(seg(t, 0, 0.35))), widthFit: 0 };
    case 3:
    case 4:
      return { rect: step === 3 ? lerpRect(f.unit, f.zoom, ease(seg(t, 0, 0.45))) : f.zoom, widthFit: 0 };
    case 5:
      return { rect: lerpRect(f.zoom, topHalf(f), ease(seg(t, 0, 0.4))), widthFit: 0 };
    default:
      return { rect: f.page, widthFit: 0 };
  }
}

function Sc9Stage({ payload: p, step, progress: t }: Frame<Sc9Payload>) {
  const cam = cameraFor(p, step, t);
  const at = (s: number, a = 0, b = 1) => (step > s ? 1 : step === s ? seg(t, a, b) : 0);
  const contrast = step === 7;
  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-1">
      <div className="flex min-h-[320px] min-w-0 flex-col">
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-xs text-text-2">
          <LaneBadge pv={contrast ? "1.0" : p.trace.pipeline.pipelineVersion} />
          <span className="truncate">{contrast ? "the naive citation: a chunk with no page region" : `${p.trace.source.fileName} · page ${p.trace.region.page} · masked Gold render`}</span>
        </div>
        <div className="relative min-h-0 flex-1">
          <TraceCanvas
            trace={p.trace}
            focus={cam.rect}
            widthFit={cam.widthFit}
            imageOpacity={contrast ? 1 - 0.7 * seg(t, 0, 0.4) : step === 0 ? 0.55 + 0.45 * ease(seg(t, 0, 0.6)) : 1}
            layers={
              contrast
                ? {}
                : {
                    field: step === 0 ? seg(t, 0.6, 1) * 0.6 : 1,
                    unit: step >= 2 ? at(2, 0.3, 0.9) : 0,
                    mark: at(1, 0.5, 0.8),
                    crosshair: step === 3 || step === 4 ? at(3, 0.5, 0.8) : 0,
                    blocks: step === 4 ? seg(t, 0.15, 0.5) : 0,
                  }
            }
          >
            {step === 1 && p.trace.field && <Tag x={p.frames.field[0]} y={p.frames.field[1]} text={`${p.trace.field.concept} · ${p.trace.field.period}`} placement="above" mono opacity={seg(t, 0.4, 0.7)} />}
          </TraceCanvas>
          {/* the naive citation has no place on the page, so it is drawn off the page, never on it */}
          {contrast && p.contrast && <ChunkCard contrast={p.contrast} opacity={seg(t, 0.3, 0.6)} provenance={p.provenance} />}
        </div>
      </div>
      <aside className="scroll-fade-b min-h-0 overflow-y-auto border-t border-border bg-bg-subtle pb-6 lg:border-l lg:border-t-0">
        <ProvenanceScope provenance={p.provenance} source={p.answer.s3Key}>
          <AnswerStrip p={p} />
          <ChainPanel p={p} step={step} t={t} />
        </ProvenanceScope>
      </aside>
    </div>
  );
}

/** What the naive chunk does hold: text, a chunk id and its own short lineage; no field, region or page. */
function ChunkCard({ contrast, opacity, provenance }: { contrast: TraceContrast; opacity: number; provenance: Provenance }) {
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center p-6" style={{ opacity }}>
      <div className="flex max-w-[420px] flex-col gap-2 rounded-md border border-dashed border-loss bg-surface px-4 py-3 shadow-sm" data-testid="chunk-card">
        <div className="flex items-center gap-2 text-sm text-text">
          <X className="size-3.5 text-loss" aria-hidden />
          <span className="font-mono text-mono-sm">{contrast.claim.chunkLabel ?? contrast.claim.label ?? "chunk"}</span>
          <span className="text-text-3">·</span>
          <Num value={contrast.claim.value ?? contrast.claim.text} kind="money" provenance={provenance} bare />
          <span className="ml-auto text-xs text-text-2">text only</span>
        </div>
        <ol className="flex flex-wrap items-center gap-1 text-xs text-text-2">
          {contrast.stops.map((s, i) => (
            <li key={s.kind} className="flex items-center gap-1">
              {i > 0 && <span className="text-text-3">›</span>}
              <span>{s.title}</span>
            </li>
          ))}
        </ol>
        {contrast.missing.length > 0 && <p className="text-xs text-text-2">no {contrast.missing.map((k) => k.replace("_", " ")).join(" · ")}</p>}
      </div>
    </div>
  );
}

/** The answer the figure comes from: total, then each operand as a chip; the followed one is selected. */
function AnswerStrip({ p }: { p: Sc9Payload }) {
  return (
    <section className="border-b border-border px-4 py-3.5">
      <h2 className="mb-2 flex items-center gap-2 text-caps uppercase text-text-2">
        <span className="flex-1">Logged answer</span>
        <ScopeTag />
      </h2>
      <div className="flex items-baseline gap-2">
        <Num value={p.answer.total} kind="money" size="lg" mark={p.answer.check ? (p.answer.check.match ? "cited" : "loss") : undefined} />
        <span className="truncate font-mono text-mono-sm text-text-2">{p.answer.runId}</span>
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5">
        {p.answer.claims.map((c, i) => (
          <CitationChip
            key={`${c.unitId}-${c.fieldId}`}
            kind={c.resolvable ? "operand" : "noregion"}
            label={c.label?.split(" · ").slice(1, 2).join("") || c.unitId}
            value={<Num value={c.value ?? c.text} kind="money" bare />}
            className={cn(i === p.selected ? "bg-surface-2 text-text" : "opacity-60")}
          />
        ))}
      </div>
    </section>
  );
}

/** The revealed hops: earlier ones fold into a trail, the last two are cards. */
function ChainPanel({ p, step, t }: { p: Sc9Payload; step: number; t: number }) {
  const hops = hopsUpTo(step);
  const shownCards = hops.slice(-2);
  const trail = hops.slice(0, -2);
  const newest = HOPS_BY_STEP[step];
  return (
    <section className="px-4 py-3.5">
      <h2 className="mb-2 text-caps uppercase text-text-2">Lineage</h2>
      {trail.length > 0 && (
        <p className="mb-2 flex flex-wrap items-center gap-1 text-xs text-text-2" aria-label="Earlier hops">
          {trail.map((h, i) => (
            <span key={h} className="flex items-center gap-1">
              {i > 0 && <span className="text-text-3">›</span>}
              <span className="rounded-xs border border-border px-1.5 py-0.5">{HOP_LABEL[h]}</span>
            </span>
          ))}
        </p>
      )}
      <TraceChain
        trace={p.trace}
        contrast={p.contrast}
        hops={shownCards}
        shown={shownCards.length}
        progress={newest.length > 1 ? Math.min(1, t * 2) : t}
        focus={newest.at(-1)}
      />
    </section>
  );
}

const HOP_LABEL: Record<HopId, string> = {
  claim: "figure",
  field: "field",
  unit: "unit",
  blocks: "engine",
  region: "region",
  page: "page",
  source: "upload",
  index: "index",
  graph: "graph",
  pipeline: "pipeline",
  run: "run",
  definition: "definition",
  contrast: "contrast",
};

export const sc9FollowTheNumber: Storyboard<Sc9Payload> = {
  id: "sc-9",
  code: "SC-9",
  title: "Follow the number",
  claim: showcaseMeta("sc-9")?.claim ?? "",
  load: loadSc9,
  steps,
  render: (frame) => <Sc9Stage {...frame} />,
  receiptsPayload: (p) => ({ trace: p.trace, contrast: p.contrast, answer: p.answer }),
};

/**
 * SC-1 · Read the page (Pattern 1, X-ray). The naive lane reads a W-2 as a flat text stream: every box label
 * first, then the amounts (a fact, constructed on purpose by the generator). Read by position alone, Box 1 would
 * pair with another box's amount: a derived hypothesis until a logged naive answer confirms or refutes it.
 * An evidence unit keeps each value with its printed label inside one boundary.
 *
 * Every frame is `render({ payload, step, progress })`: no state, no timers, no typed values.
 */
import { easeCubicInOut } from "d3-ease";
import { Badge, CitationChip, DataList, HypothesisValue, LaneBadge, Num, ProvenanceTag, cn } from "../../design";
import { type Rect, center, lerpRect, seg, toRect, union } from "../../viz/geom";
import { Boundary, BoxOutline, ChunkSlices, CutLine, Highlight, Mark, PageCanvas, PairArc, ReadingPath, Tag, reachedStops } from "../../viz/PageCanvas";
import { StreamStrip } from "../../viz/StreamStrip";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { LaneCard, PanelSection } from "./panel";
import { type Sc1Payload, boxNumber, loadSc1 } from "./sc1-payload";
import { SC1_STEPS } from "./sc1-steps";

const ease = easeCubicInOut;
const XRAY_SERVICES = ["AWS Lambda · rp-xray", "Amazon S3"];
const tested = (p: Sc1Payload) => p.hypothesis.state !== "untested";
const shortUnit = (unitId: string) => unitId.split(":").pop() ?? unitId;
const GATE_RUN = showcaseMeta("sc-1")?.gateRun;

/** The positional hypothesis: the position-only caption is chosen from the logged answer, never written ahead of it. */
function positionCaption(p: Sc1Payload): string {
  const head = `Position-only, Box 1 pairs with ${p.box1.positionalValue?.text}.`;
  switch (p.hypothesis.state) {
    case "confirmed":
      return `${head} The logged answer used it.`;
    case "refuted":
      return `${head} The logged answer used other numbers.`;
    default:
      return `${head} No logged answer has tested this yet.`;
  }
}

/** The positional hypothesis again, for the closing comparison: "untested" stays on the value until a run says otherwise. */
function sideBySideCaption(p: Sc1Payload): string {
  const [pos, geo] = [p.box1.positionalValue?.text, p.box1.geometricValue?.text];
  switch (p.hypothesis.state) {
    case "confirmed":
      return `The logged answer read Box 1 as ${pos}; inside its field box it is ${geo}.`;
    case "refuted":
      return `By position Box 1 lands on ${pos}; the logged answer did not use it. Printed: ${geo}.`;
    default:
      return `By position Box 1 lands on ${pos} (untested); inside its field box it is ${geo}.`;
  }
}

const steps: StoryStep<Sc1Payload>[] = [
  {
    ...SC1_STEPS[0],
    durationMs: 2600,
    caption: (p) => `${p.borrower} · W-2 · the digital route. Both lanes read these exact bytes.`,
    services: ["Amazon S3"],
    receipts: (p) => [
      { label: "Bronze object", source: p.control.sources.bronze.key, value: p.control.sources.bronze.versionId, provenance: "local-run" },
      { label: "sha256", value: p.doc.sha256 ?? undefined, path: "$.control.sources.bronze.sha256", provenance: "local-run" },
      { label: "Page image (Bronze, both lanes' held regions filled)", value: `${p.control.image.dpi} dpi`, source: p.control.image.url, provenance: "local-run" },
    ],
  },
  {
    ...SC1_STEPS[1],
    durationMs: 6400,
    caption: "A text-layer read lists the box labels first, then the values. Constructed on purpose.",
    services: XRAY_SERVICES,
    receipts: (p) => [
      { label: "Text-layer lines in stream order", value: p.control.lines.length, path: "$.control.lines", provenance: "derived" },
      { label: "Characters aligned to pdfium char boxes (same bytes)", value: p.control.coverage, path: "$.control.coverage", provenance: "local-run" },
      { label: "Text layer", value: p.control.control.library, source: p.control.sources.units.key, provenance: "local-run" },
    ],
  },
  {
    ...SC1_STEPS[2],
    durationMs: 6400,
    caption: (p) => `At ${p.control.sizeTokens} tokens the page is ${p.control.chunks.length === 1 ? "a single chunk" : `${p.control.chunks.length} chunks`}. A ${p.whatIf.sizeTokens}-token what-if cuts Box 1 from its value.`,
    services: XRAY_SERVICES,
    receipts: (p) => [
      { label: "Control chunks (stored v1.0 units)", value: p.control.chunks.map((c) => `${c.label} · ${c.tokens} tokens`).join(", "), path: "$.control.chunks", source: p.control.sources.units.key, provenance: "local-run" },
      { label: "The real splitter reproduces the stored chunks", value: p.control.splitterMatchesControl, path: "$.control.splitterMatchesControl", provenance: "derived" },
      { label: `What-if re-split at ${p.whatIf.sizeTokens} tokens`, value: p.whatIf.chunks.map((c) => `${c.label} · ${c.tokens} tokens`).join(", "), path: "$.whatIf.chunks", source: p.control.sources.splitter, provenance: "what-if" },
      { label: "Box 1 label · value chunks (what-if)", value: `${p.whatIfChunks.label.join(", ")} · ${p.whatIfChunks.value.join(", ")}`, provenance: "what-if" },
    ],
  },
  {
    ...SC1_STEPS[3],
    durationMs: 4600,
    caption: positionCaption,
    services: XRAY_SERVICES,
    receipts: (p) => [
      { label: "Positional value for Box 1", value: p.box1.positionalValue?.text, path: `$.control.pairs[${p.control.pairs.indexOf(p.box1)}].positionalValue`, provenance: "hypothesis" },
      { label: "Rule", value: "i-th numbered label with the i-th amount, in stream order", provenance: "derived" },
      {
        label: "Logged naive answer",
        value: p.hypothesis.state === "untested" ? `not captured yet${GATE_RUN ? ` (${GATE_RUN.milestone}: ${GATE_RUN.questionId} × ${GATE_RUN.repeats})` : ""}` : `${p.hypothesis.runId} · ${p.hypothesis.used.join(", ")}`,
        path: "$.control.answerNumbers",
        provenance: p.hypothesis.state === "untested" ? "hypothesis" : p.hypothesis.provenance,
      },
    ],
  },
  {
    ...SC1_STEPS[4],
    durationMs: 5200,
    caption: "Each value stays with its printed label in one unit.",
    services: ["AWS Fargate · docling-serve", "AWS Lambda · eu-builder"],
    receipts: (p) => [
      { label: "Evidence unit", value: p.unit.unitId, path: `$.control.units[${p.control.units.indexOf(p.unit)}]`, source: p.control.sources.evidence?.key, provenance: "local-run" },
      { label: "Members (Docling blocks)", value: p.unit.members.length, path: `$.control.units[${p.control.units.indexOf(p.unit)}].members`, provenance: "local-run" },
      { label: "Box 1 value inside its field box", value: p.box1.geometricValue?.text, path: `$.control.pairs[${p.control.pairs.indexOf(p.box1)}].geometricValue`, provenance: "local-run" },
    ],
  },
  {
    ...SC1_STEPS[5],
    durationMs: 4000,
    caption: sideBySideCaption,
    services: ["Amazon S3", "AWS Lambda", "AWS Fargate"],
    receipts: (p) => [
      { label: "1.0 · Box 1, position-only", value: p.box1.positionalValue?.text, provenance: tested(p) ? "measured" : "hypothesis" },
      { label: "2.3.1 · Box 1 field", value: p.box1.geometricValue?.text, source: p.control.sources.evidence?.key, provenance: "local-run" },
      { label: "1.0 chunk citation", value: "no region (chunks carry no bbox; the X-ray is inspection only)", provenance: "derived" },
    ],
  },
];

// --------------------------------------------------------------------------------------------- camera

/** The camera per frame: a focus rect and how much of it is width-fitted (step 1 fills the column, pinned top). */
function cameraFor(p: Sc1Payload, step: number, t: number): { rect: Rect; widthFit: number } {
  const f = p.frames;
  switch (step) {
    case 0:
      return { rect: f.page, widthFit: 1 };
    case 1: {
      const e = ease(seg(t, 0, 0.25));
      return { rect: lerpRect(f.page, f.content, e), widthFit: 1 - e };
    }
    case 2:
      return { rect: f.content, widthFit: 0 };
    case 3:
      return { rect: lerpRect(f.content, f.pairing, ease(seg(t, 0, 0.35))), widthFit: 0 };
    case 4:
      return { rect: lerpRect(f.pairing, f.unit, ease(seg(t, 0, 0.3))), widthFit: 0 };
    default:
      return { rect: f.unit, widthFit: 0 };
  }
}

/** Critically damped spring, closed form (scrubbable): 0 → 1 with no overshoot. */
const spring = (t: number) => 1 - (1 + 9 * t) * Math.exp(-9 * t);

// --------------------------------------------------------------------------------------------- frame

/** Step 2's two runs: the labels draw, a beat of pause, then the values. */
const LABEL_RUN = [0.22, 0.6] as const;
const VALUE_RUN = [0.7, 0.97] as const;

function streamCursor(p: Sc1Payload, t: number): number {
  const lp = seg(t, ...LABEL_RUN);
  const vp = seg(t, ...VALUE_RUN);
  const { labels, values } = p.stops;
  if (vp > 0) return values[reachedStops(values.length, vp) - 1]?.ordinal ?? 0;
  if (lp > 0) return labels[reachedStops(labels.length, lp) - 1]?.ordinal ?? 0;
  return 0;
}

function Sc1Stage({ payload: p, step, progress: t }: Frame<Sc1Payload>) {
  const { label, value, positional } = p;
  const labels = p.stops.labels;
  const lastLabel = labels[labels.length - 1];
  const unitBox = toRect(p.unit.bboxes[0]);
  const box1Field = union([p.box1.fieldBbox && toRect(p.box1.fieldBbox), label, value])!;
  const [ucx, ucy] = center(unitBox);
  const control = step === 2 ? 1 - seg(t, 0.42, 0.55) : 0;
  const whatIfOn = step === 2 ? seg(t, 0.45, 0.6) : 0;
  const arcTone = p.hypothesis.state === "confirmed" ? "loss" : "hypothesis";
  const answered = (p.control.answerNumbers ?? []).flatMap((a) => a.matchedLines);
  const cam = cameraFor(p, step, t);
  const dimPath = step === 2 ? 0.18 : step === 3 ? 0.18 * (1 - seg(t, 0, 0.3)) : 0;

  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_340px] lg:grid-rows-1">
      <div className="flex min-h-[320px] min-w-0 flex-col">
        <CanvasToolbar p={p} step={step} whatIf={whatIfOn >= 0.5} cursor={step === 1 ? streamCursor(p, t) : 0} />
        <div className="relative min-h-0 flex-1">
          <PageCanvas
            ariaLabel={`${p.doc.fileName}, page ${p.page}`}
            image={{ src: p.control.image.url, src2x: p.control.image.zoomUrl, dpi: p.control.image.dpi }}
            pageWidth={p.control.pageWidth}
            pageHeight={p.control.pageHeight}
            focus={cam.rect}
            widthFit={cam.widthFit}
            animate={false}
            imageOpacity={step === 0 ? ease(seg(t, 0, 0.5)) : 1}
            frame={
              <>
                {whatIfOn > 0 && <div className="pointer-events-none absolute inset-2 rounded-md border border-dashed border-text-2" style={{ opacity: whatIfOn }} />}
              </>
            }
          >
            {/* the text layer: the label run, a pause, then the value run */}
            {step === 1 && (
              <>
                <ReadingPath stops={labels} variant="label" progress={seg(t, ...LABEL_RUN)} />
                <ReadingPath stops={p.stops.values} lead={lastLabel} variant="value" progress={seg(t, ...VALUE_RUN)} />
              </>
            )}
            {dimPath > 0 && (
              <>
                <ReadingPath stops={labels} variant="label" opacity={dimPath} />
                <ReadingPath stops={p.stops.values} lead={lastLabel} variant="value" opacity={dimPath} />
              </>
            )}

            {/* chunk slices: the stored control chunk, then the labelled what-if, and where its first chunk ends */}
            {step === 2 && (
              <>
                <ChunkSlices chunks={p.control.chunks.map((c) => ({ chunkId: c.chunkId, label: `${c.chunkId} · ${c.tokens} tokens`, rects: c.rects.map(toRect) }))} opacity={seg(t, 0, 0.15) * control} />
                <ChunkSlices chunks={p.whatIf.chunks.map((c) => ({ chunkId: c.chunkId, label: `${c.label} · ${c.tokens} tokens`, rects: c.rects.map(toRect) }))} opacity={whatIfOn} />
                {p.cut && <CutLine {...p.cut} opacity={seg(t, 0.6, 0.68)} pulse={seg(t, 0.72, 0.9)} label={`${p.whatIf.chunks[0]?.label} ends`} />}
                <Highlight rect={label} opacity={seg(t, 0.72, 0.85)} />
                <Highlight rect={value} opacity={seg(t, 0.72, 0.85)} />
                <Tag x={label[0]} y={label[1]} text={`Box 1 label · ${p.whatIfChunks.label.join(", ")}`} placement="above" opacity={seg(t, 0.78, 0.9)} />
                <Tag x={value[0]} y={value[3]} text={`${p.box1.geometricValue?.text} · ${p.whatIfChunks.value.join(", ")}`} placement="below" opacity={seg(t, 0.82, 0.95)} mono />
              </>
            )}

            {/* position-only reading: a derived hypothesis, × only when a logged answer used the value */}
            {step === 3 && (
              <>
                <Highlight rect={label} opacity={seg(t, 0.15, 0.3)} />
                <Highlight rect={positional} tone={arcTone === "loss" ? "loss" : "muted"} opacity={seg(t, 0.7, 0.85)} />
                <PairArc from={label} to={positional} tone={arcTone} progress={ease(seg(t, 0.3, 0.75))} />
                <Tag
                  x={positional[2]}
                  y={positional[3]}
                  align="end"
                  text={p.positionalOwner ? `derived · hypothesis · printed in Box ${p.positionalOwner.box}` : "derived · hypothesis"}
                  tone="muted"
                  dashed
                  placement="below"
                  opacity={seg(t, 0.8, 0.95)}
                />
                {/* the numbers the logged naive answer used: × only on Box 1's positional value when confirmed */}
                {p.control.lines
                  .filter((l) => answered.includes(l.ordinal) && l.bbox)
                  .map((l) => (
                    <Mark
                      key={l.ordinal}
                      x={l.bbox!.x1}
                      y={(l.bbox!.y0 + l.bbox!.y1) / 2}
                      kind={arcTone === "loss" && l.ordinal === p.box1.positionalValue?.ordinal ? "loss" : "used"}
                      opacity={seg(t, 0.85, 1)}
                    />
                  ))}
              </>
            )}

            {/* the boundary: members snap in, the unit draws, then a tight boundary lands on Box 1's own field */}
            {step >= 4 && (
              <>
                {step === 4 && <PairArc from={label} to={positional} tone={arcTone} opacity={1 - seg(t, 0, 0.2)} />}
                {p.unit.members.map((m, i) => {
                  const local = step === 4 ? seg(t, 0.05 + 0.03 * Math.min(i, 8), 0.45) : 1;
                  const r0 = toRect(m.bbox);
                  const [mx, my] = center(r0);
                  const k = (1 - spring(local)) * 0.12;
                  const r: Rect = [r0[0] + (mx - ucx) * k, r0[1] + (my - ucy) * k, r0[2] + (mx - ucx) * k, r0[3] + (my - ucy) * k];
                  return <BoxOutline key={m.memberId} rect={r} tone="border" dashed opacity={local * (step === 5 ? 0.5 : 0.9)} title={`${m.memberId} · ${m.role}`} />;
                })}
                <Boundary rect={unitBox} width={1} tint={0.8} progress={step === 4 ? seg(t, 0.4, 0.75) : 1} label={step === 5 ? shortUnit(p.unit.unitId) : undefined} />
                <Boundary rect={box1Field} pad={3} width={1.5} tint={0} progress={step === 4 ? seg(t, 0.6, 0.9) : 1} />
                <PairArc from={label} to={value} tone="cited" progress={step === 4 ? ease(seg(t, 0.78, 1)) : 1} bend={-0.35} />
                {step === 5 && <PairArc from={label} to={positional} tone={arcTone} opacity={0.55} />}
              </>
            )}
          </PageCanvas>
        </div>
      </div>
      <aside className="min-h-0 overflow-y-auto border-t border-border bg-bg-subtle lg:border-l lg:border-t-0">
        <StepPanel p={p} step={step} />
      </aside>
    </div>
  );
}

/** The row above the paper: which lane is being read; the stream strip (step 2); the chunk size (step 3). */
function CanvasToolbar({ p, step, whatIf, cursor }: { p: Sc1Payload; step: number; whatIf: boolean; cursor: number }) {
  const lanes = step === 4 ? ["2.3.1"] : step === 5 ? ["1.0", "2.3.1"] : ["1.0"];
  return (
    <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-xs text-text-2">
      {lanes.map((pv) => (
        <LaneBadge key={pv} pv={pv} />
      ))}
      <span className="truncate">{step === 4 ? "layout read: blocks, roles, boxes" : step === 5 ? "same bytes, both lanes" : `${p.control.control.library} text layer`}</span>
      {step === 1 && <StreamStrip variant="inline" className="ml-auto" lines={p.stops.all} cursor={cursor} />}
      {step === 2 && (
        <div className="ml-auto flex items-center gap-2" aria-live="polite">
          <span>Chunk size</span>
          <span className={cn("tnum rounded-xs border px-1.5 py-0.5 font-mono", whatIf ? "border-border text-text-3" : "border-border-strong bg-surface-2 text-text")}>{p.control.sizeTokens}</span>
          <span className={cn("tnum inline-flex items-center gap-1.5 rounded-xs border border-dashed px-1.5 py-0.5 font-mono", whatIf ? "border-text-2 bg-surface-2 text-text" : "border-border text-text-3")}>
            {p.whatIf.sizeTokens}
          </span>
          {whatIf && (
            <span className="inline-flex items-center gap-1" data-testid="whatif-on">
              <ProvenanceTag provenance="what-if" />
              <span>re-split · control stays {p.control.sizeTokens}</span>
            </span>
          )}
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------------------------- panel

function StepPanel({ p, step }: { p: Sc1Payload; step: number }) {
  const labels = p.control.lines.filter((l) => l.kind === "label");
  const values = p.control.lines.filter((l) => l.kind === "value");
  const ownerOf = (line: number) => p.control.pairs.find((x) => x.geometricValue?.ordinal === line);
  switch (step) {
    case 0:
      return (
        <PanelSection title="The document" provenance="local-run" source={p.control.sources.bronze.key}>
          <DataList
            rows={[
              ["File", <span className="font-mono text-mono-sm">{p.doc.fileName}</span>],
              ["Type", <Badge tone="concept" dot>{p.doc.docType}</Badge>],
              ["Pages", <Num value={p.control.pageCount} kind="int" />],
              ["sha256", <span className="font-mono text-mono-sm" title={p.doc.sha256 ?? undefined}>{p.doc.sha256?.slice(0, 12)}</span>],
              ["Image", <span className="text-text-2">held regions filled · {p.control.image.dpi} dpi</span>],
            ]}
          />
          <p className="mt-3 text-xs text-text-2">Fictitious data. The generator built this document for this repository; both lanes ingest the same Bronze object.</p>
        </PanelSection>
      );
    case 1:
      return (
        <>
          <PanelSection title="Stream order" provenance="derived" source="pypdf text layer · pdfium char boxes">
            <DataList
              rows={[
                ["Lines", <Num value={p.control.lines.length} kind="int" />],
                ["Box labels", <span className="font-mono text-mono-sm">#{labels[0]?.ordinal}–#{labels[labels.length - 1]?.ordinal}</span>],
                ["Values", <span className="font-mono text-mono-sm">#{values[0]?.ordinal}–#{values[values.length - 1]?.ordinal}</span>],
                ["Char alignment", <Num value={p.control.coverage} kind="ratio" provenance="local-run" source="pypdf text vs pdfium char boxes, same bytes" />],
              ]}
            />
          </PanelSection>
          <PanelSection title="Values as the stream lists them">
            <ol className="flex flex-col gap-1">
              {values.map((v) => {
                const o = ownerOf(v.ordinal);
                return (
                  <li key={v.ordinal} className="flex items-center gap-2 text-sm">
                    <span className="flex h-5 min-w-5 items-center justify-center rounded-full bg-text px-1 font-mono text-xs text-inverse">{v.ordinal}</span>
                    <span className="tnum flex-1 font-mono text-mono-sm text-text">{v.text}</span>
                    <span className="text-xs text-text-2">{o ? `printed in Box ${boxNumber(o.label.text)}` : ""}</span>
                  </li>
                );
              })}
            </ol>
            <p className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-2">
              <span className="inline-flex items-center gap-1"><span className="inline-block size-3 rounded-full border border-text-2" /> label</span>
              <span className="inline-flex items-center gap-1"><span className="inline-block size-3 rounded-full bg-text" /> value</span>
              <span>The generator writes this order on purpose.</span>
            </p>
          </PanelSection>
        </>
      );
    case 2:
      return (
        <>
          <PanelSection title="Control · v1.0 config" provenance="local-run" source={p.control.sources.units.key}>
            <DataList
              rows={[
                ["Chunk size", <Num value={p.control.sizeTokens} kind="int" unit="tokens" provenance="design-target" source="config/pipelines/v1.0.yaml" />],
                ["Chunks", p.control.chunks.map((c) => <Num key={c.chunkId} value={c.tokens} kind="int" unit={`tokens · ${c.chunkId}`} />)],
                ["Splitter", <span className="text-sm text-text">{p.control.splitterMatchesControl ? "reproduces the stored chunks" : "differs from the stored chunks"}</span>],
              ]}
            />
          </PanelSection>
          <PanelSection title={`What-if · ${p.whatIf.sizeTokens} tokens`} provenance="what-if" source={p.control.sources.splitter}>
            <div className="flex flex-col gap-1.5">
              {p.whatIf.chunks.map((c) => (
                <div key={c.chunkId} className="flex items-center gap-2">
                  <span className="min-w-20 text-sm text-text-2">{c.label}</span>
                  <Num value={c.tokens} kind="int" unit="tokens" />
                </div>
              ))}
            </div>
            <DataList
              className="mt-3"
              rows={[
                ["Box 1 label", <span className="text-sm">{p.whatIfChunks.label.join(", ")}</span>],
                [<span className="tnum font-mono text-mono-sm">{p.box1.geometricValue?.text}</span>, <span className="text-sm">{p.whatIfChunks.value.join(", ")}</span>],
              ]}
            />
            <p className="mt-3 text-xs text-text-2">The same text layer re-split by the real v1.0 splitter. The control lane is not changed.</p>
          </PanelSection>
        </>
      );
    case 3:
      return (
        <>
          <PanelSection title="Position-only" provenance="hypothesis" source="i-th numbered label with the i-th value, stream order">
            <DataList
              rows={[
                ["Label", <span className="text-sm">{p.box1.label.text}</span>],
                ["Pairs with", p.hypothesis.state === "confirmed" ? <Num value={p.box1.positionalValue?.text} kind="money" mark="loss" /> : <HypothesisValue value={p.box1.positionalValue?.text} />],
                ["Printed in", p.positionalOwner ? <span className="text-sm">Box {p.positionalOwner.box}</span> : "—"],
                ["Printed value", <Num value={p.box1.geometricValue?.text} kind="money" provenance="local-run" />],
              ]}
            />
          </PanelSection>
          <PanelSection title="Logged answer" provenance={p.hypothesis.state === "untested" ? undefined : p.hypothesis.provenance} source={p.control.sources.answerRun?.file ?? p.control.sources.answerRun?.runId}>
            {p.hypothesis.state === "untested" ? (
              <p className="text-sm text-text-2">
                Not captured yet.{" "}
                {GATE_RUN && (
                  <>
                    The {GATE_RUN.milestone} (<span className="font-mono text-mono-sm">{GATE_RUN.questionId} × {GATE_RUN.repeats}</span>) on AWS supplies the numbers the answer used;{" "}
                  </>
                )}
                until then this pairing stays a hypothesis.
              </p>
            ) : (
              <DataList
                rows={[
                  ["Run", <span className="font-mono text-mono-sm">{p.hypothesis.runId}</span>],
                  ["Numbers used", <span className="tnum font-mono text-mono-sm">{p.hypothesis.used.join(" · ")}</span>],
                  ["Verdict", <span className="text-sm">{p.hypothesis.state === "confirmed" ? "used the position-only value" : "used other numbers"}</span>],
                ]}
              />
            )}
          </PanelSection>
          <PanelSection title="Every box" provenance="hypothesis">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-text-2">
                  <th className="pb-1 font-medium">Box</th>
                  <th className="pb-1 text-right font-medium">By position</th>
                  <th className="pb-1 text-right font-medium">Printed</th>
                </tr>
              </thead>
              <tbody>
                {p.control.pairs
                  .filter((x) => x.positionalValue && x.geometricValue)
                  .sort((a, b) => a.labelIndex - b.labelIndex)
                  .map((x) => (
                    <tr key={x.fieldId} className={cn("border-t border-border", x === p.box1 && "bg-surface-2")} data-same-value={x.positionalSameValue}>
                      <td className="py-1 font-mono text-mono-sm">{boxNumber(x.label.text)}</td>
                      <td className="tnum py-1 text-right font-mono text-mono-sm" title={x.positionalSameValue && !x.positionalSameLine ? "same figure, printed in a different box" : undefined}>
                        <span className="text-text-2">{x.positionalSameValue ? "= " : "≠ "}</span>
                        {x.positionalValue!.text}
                      </td>
                      <td className="tnum py-1 text-right font-mono text-mono-sm text-text-2">{x.geometricValue!.text}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
            {p.control.pairs.some((x) => x.positionalSameValue && !x.positionalSameLine) && (
              <p className="mt-2 text-xs text-text-2">= with a different box: the same figure is printed twice, so position-only happens to land on it.</p>
            )}
          </PanelSection>
        </>
      );
    case 4: {
      const boxes = p.fields.map((f) => f.box);
      return (
        <>
          <PanelSection title="Evidence unit" provenance="local-run" source={p.control.sources.evidence?.key}>
            <DataList
              rows={[
                ["Lane", <LaneBadge pv="2.3.1" />],
                ["Unit", <span className="text-sm">{p.unit.label}</span>],
                ["Id", <span className="font-mono text-mono-sm">{shortUnit(p.unit.unitId)}</span>],
                ["Type", <Badge tone="concept" dot>{p.unit.unitType}</Badge>],
                ["Members", <Num value={p.unit.members.length} kind="int" unit="Docling blocks" />],
              ]}
            />
          </PanelSection>
          <PanelSection title="Fields inside the boundary" provenance="local-run" source="local Docling, not AWS" meta={`${p.fields.length} fields · boxes ${boxes.join(", ")}`}>
            <ul className="-mx-2 flex flex-col">
              {p.fields.map((f) => (
                <li key={f.fieldId} className={cn("grid grid-cols-[2rem_minmax(0,1fr)_auto_auto] items-baseline gap-2 rounded-xs px-2 py-1 text-sm", f.fieldId === p.box1.fieldId && "bg-surface-2")}>
                  <span className="font-mono text-mono-sm text-text-2">{f.box}</span>
                  <span className="truncate text-text-2" title={f.label}>
                    {f.label.replace(/^\d{1,2}[a-d]?\s+/, "")}
                  </span>
                  <span className="text-text-3" aria-hidden>→</span>
                  <span className="tnum font-mono text-mono-sm text-text">{f.value}</span>
                </li>
              ))}
            </ul>
          </PanelSection>
        </>
      );
    }
    default:
      return (
        <div className="flex flex-col gap-3 p-4">
          <LaneCard lane="1.0" title="Text layer" provenance="local-run" source={p.control.sources.units.key}>
            <DataList
              rows={[
                [
                  "Box 1 · by position",
                  p.hypothesis.state === "untested" ? (
                    <span className="inline-flex flex-wrap items-baseline gap-x-2">
                      <HypothesisValue value={p.box1.positionalValue?.text} />
                      <span className="text-xs text-text-3">untested hypothesis</span>
                    </span>
                  ) : (
                    <Num value={p.box1.positionalValue?.text} kind="money" provenance={p.hypothesis.provenance} mark={p.hypothesis.state === "confirmed" ? "loss" : undefined} />
                  ),
                ],
                ["Chunk", <Num value={p.control.chunks[0]?.tokens} kind="int" unit={`tokens · ${p.control.chunks[0]?.chunkId}`} />],
                ["Citation", <CitationChip kind="noregion" label={p.control.chunks[0]?.chunkId ?? "chunk"} title={`${p.control.chunks[0]?.label}: v1.0 chunks carry no page region`} />],
              ]}
            />
          </LaneCard>
          <LaneCard lane="2.3.1" title="Layout" provenance="local-run" source={p.control.sources.evidence?.key}>
            <DataList
              rows={[
                ["Box 1 · in its field", <Num value={p.box1.geometricValue?.text} kind="money" mark="cited" size="md" />],
                ["Concept", <Badge tone="concept" dot className="font-mono">{p.box1.concept}</Badge>],
                ["Citation", <CitationChip kind="cited" label={`W-2 · ${p.borrower} · Box 1`} page={p.page} to={`/workbench/trace/${encodeURIComponent(p.unit.unitId)}?field=${encodeURIComponent(p.box1.fieldId)}`} />],
              ]}
            />
          </LaneCard>
          <p className="text-xs text-text-2">Same bytes, same page. Only preprocessing differs. The X-ray is an inspection: 1.0 chunks still carry no region.</p>
        </div>
      );
  }
}

export const sc1ReadThePage: Storyboard<Sc1Payload> = {
  id: "sc-1",
  code: "SC-1",
  title: "Read the page",
  claim: "Read the page before you chunk it: extraction must return regions with roles and boxes.",
  load: (ctx) => loadSc1(ctx),
  steps,
  render: (frame) => <Sc1Stage {...frame} />,
  receiptsPayload: (p) => ({ doc: p.doc, control: p.control, whatIf: { sizeTokens: p.whatIf.sizeTokens, chunks: p.whatIf.chunks }, box1: p.box1, hypothesis: p.hypothesis }),
};

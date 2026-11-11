/**
 * SC-0 page frames: `why` (what the naive lane's text layer reads, and what the logged answer used), `boundary`
 * (the evidence unit and Box 1's own field) and `follow` (the compact SC-9 sequence).
 */
import { easeCubicInOut } from "d3-ease";
import { CitationChip, LaneBadge, Num, ScopeTag, cn } from "../../../../design";
import { TraceCanvas, traceFrames } from "../../../../features/trace/TraceCanvas";
import { TraceChain } from "../../../../features/trace/TraceChain";
import { type Rect, lerpRect, seg, toRect, union } from "../../../../viz/geom";
import { Boundary, BoxOutline, Mark, PageCanvas, PairArc, ReadingPath, Tag } from "../../../../viz/PageCanvas";
import { matchLine } from "../captions";
import { type Sc0Payload, followedDoc } from "../load";

const ease = easeCubicInOut;
/** Page points of air around the form when the camera fits it to the page width. */
const INSET = 12;

export function CanvasStep({ p, mode, t }: { p: Sc0Payload; mode: "why" | "boundary"; t: number }) {
  const why = mode === "why";
  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_340px] lg:grid-rows-1">
      <div className="flex min-h-[320px] min-w-0 flex-col">
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-xs text-text-2">
          <LaneBadge pv={why ? "1.0" : "2.3.1"} />
          <span className="truncate">{why ? `${p.xray.doc.fileName} · what the naive lane's text layer reads, in order` : "layout read: the unit and Box 1's own field"}</span>
        </div>
        <div className="relative min-h-0 flex-1">{why ? <WhyCanvas p={p} t={t} /> : <BoundaryCanvas p={p} t={t} />}</div>
      </div>
      <aside className="min-h-0 overflow-y-auto border-t border-border bg-bg-subtle px-4 py-3.5 lg:border-l lg:border-t-0">{why ? <WhyPanel p={p} t={t} /> : <BoundaryPanel p={p} />}</aside>
    </div>
  );
}

/**
 * The text layer's reading order, then only what matters: Box 1's label → its value (solid, neutral), the
 * position-only pairing (dashed, a derived hypothesis; × only when a logged answer used it) and the chunk that
 * holds both, as text without a box. Every other arc fades to a trace.
 */
function WhyCanvas({ p, t }: { p: Sc0Payload; t: number }) {
  const x = p.xray;
  const answered = (x.control.answerNumbers ?? []).flatMap((a) => a.matchedLines);
  const confirmed = x.hypothesis.state === "confirmed";
  // the whole form, then the wages panel at the page's width (the left column stays in frame)
  const fitWidth: Rect = [x.frames.content[0] - INSET, x.frames.pairing[1], x.frames.content[2] + INSET, x.frames.pairing[3]];
  const focus = lerpRect(lerpRect(x.frames.page, x.frames.content, ease(seg(t, 0, 0.2))), fitWidth, ease(seg(t, 0.74, 0.88)));
  const settle = seg(t, 0.8, 0.9);
  const pathOpacity = 1 - 0.88 * settle;
  const valueLine = x.control.lines.find((l) => l.ordinal === x.box1.geometricValue?.ordinal);
  const chunk = x.control.chunks.find((c) => valueLine?.chunkOrdinals.includes(c.ordinal));
  const chunkRect = chunk ? union(chunk.rects.map((r) => toRect(r))) : undefined;
  return (
    <PageCanvas ariaLabel={`${x.doc.fileName}, page ${x.page}`} image={{ src: x.control.image.url, src2x: x.control.image.zoomUrl, dpi: x.control.image.dpi }} pageWidth={x.control.pageWidth} pageHeight={x.control.pageHeight} focus={focus} animate={false}>
      <ReadingPath stops={x.stops.labels} variant="label" progress={seg(t, 0.15, 0.45)} opacity={pathOpacity} />
      <ReadingPath stops={x.stops.values} lead={x.stops.labels.at(-1)} variant="value" progress={seg(t, 0.5, 0.75)} opacity={pathOpacity} />
      {chunkRect && (
        <g opacity={settle}>
          <BoxOutline rect={chunkRect} tone="chunk-1" width={1} />
          <Tag x={chunkRect[2]} y={chunkRect[1]} align="end" placement="above" text={`${chunk!.label} · text, no box`} tone="chunk-1" mono />
        </g>
      )}
      <PairArc from={x.label} to={x.value} tone="neutral" bend={-0.3} progress={ease(seg(t, 0.82, 0.95))} />
      <PairArc from={x.label} to={x.positional} tone={confirmed ? "loss" : "hypothesis"} progress={ease(seg(t, 0.86, 1))} />
      {/* the numbers the logged answer used: a neutral dot beside each; × only on a confirmed positional value */}
      {x.control.lines
        .filter((l) => answered.includes(l.ordinal) && l.bbox)
        .map((l) => (
          <Mark key={l.ordinal} x={l.bbox!.x1} y={(l.bbox!.y0 + l.bbox!.y1) / 2} dx={12} kind={confirmed && l.ordinal === x.box1.positionalValue?.ordinal ? "loss" : "used"} opacity={seg(t, 0.82, 0.95)} />
        ))}
      <Tag x={x.positional[2]} y={x.positional[3]} align="end" placement="below" text="derived · hypothesis" tone="muted" dashed opacity={seg(t, 0.9, 1)} />
    </PageCanvas>
  );
}

function BoundaryCanvas({ p, t }: { p: Sc0Payload; t: number }) {
  const x = p.xray;
  const box1Field = union([x.box1.fieldBbox && toRect(x.box1.fieldBbox), x.label, x.value])!;
  return (
    <PageCanvas ariaLabel={`${x.doc.fileName}, page ${x.page}`} image={{ src: x.control.image.url, src2x: x.control.image.zoomUrl, dpi: x.control.image.dpi }} pageWidth={x.control.pageWidth} pageHeight={x.control.pageHeight} focus={lerpRect(x.frames.content, x.frames.unit, ease(seg(t, 0, 0.3)))} animate={false}>
      {x.unit.members.map((m) => (
        <BoxOutline key={m.memberId} rect={toRect(m.bbox)} tone="border" dashed opacity={0.5 * seg(t, 0, 0.3)} />
      ))}
      <Boundary rect={toRect(x.unit.bboxes[0])} width={1.5} tint={0.8} progress={seg(t, 0.1, 0.6)} />
      <BoxOutline rect={box1Field} tone="cited" width={1.5} opacity={seg(t, 0.5, 0.7)} />
      <PairArc from={x.label} to={x.value} tone="cited" progress={ease(seg(t, 0.6, 0.95))} bend={-0.35} />
    </PageCanvas>
  );
}

function WhyPanel({ p, t }: { p: Sc0Payload; t: number }) {
  const x = p.xray;
  const labels = x.stops.labels;
  const values = x.stops.values;
  return (
    <div className="flex flex-col gap-4">
      <section>
        <h2 className="mb-2 text-caps uppercase text-text-2">Stream order</h2>
        <p className="text-sm text-text">
          Lines <span className="font-mono text-mono-sm">#{labels[0]?.ordinal}–#{labels.at(-1)?.ordinal}</span> are box labels, then <span className="font-mono text-mono-sm">#{values[0]?.ordinal}–#{values.at(-1)?.ordinal}</span> the values.
        </p>
        <p className="mt-1 text-xs text-text-2">The generator writes this order on purpose, as payroll software often prints the form. The chunk keeps both as text, with no box to pair them.</p>
      </section>
      <section style={{ opacity: seg(t, 0.75, 0.95) }}>
        <h2 className="mb-2 flex items-center gap-2 text-caps uppercase text-text-2">
          <span className="flex-1">The logged answer</span>
          <ScopeTag />
        </h2>
        <ul className="flex flex-col gap-1">
          {(x.control.answerNumbers ?? []).map((n) => (
            <li key={n.value} className="flex items-center gap-2 text-sm">
              <span className="inline-block size-2 rounded-full bg-text-2" aria-hidden />
              <Num value={n.value} kind="money" />
              <span className="text-xs text-text-2">{n.matchedLines.length ? `line #${n.matchedLines.join(", #")} on this page` : "not on this page"}</span>
            </li>
          ))}
        </ul>
        <p className="mt-2 text-sm text-text" data-testid="match-line">
          {matchLine(p)}
        </p>
        <p className="mt-1 text-xs text-text-2">
          Position-only, Box 1 would pair with <span className="tnum font-mono">{x.box1.positionalValue?.text}</span>: a derived hypothesis, drawn dashed.
        </p>
      </section>
    </div>
  );
}

function BoundaryPanel({ p }: { p: Sc0Payload }) {
  const x = p.xray;
  return (
    <section>
      <h2 className="mb-2 flex items-center gap-2 text-caps uppercase text-text-2">
        <span className="flex-1">Evidence unit</span>
        <ScopeTag />
      </h2>
      <p className="text-sm text-text">{x.unit.label}</p>
      <ul className="mt-3 flex flex-col">
        {x.fields.slice(0, 7).map((f) => (
          <li key={f.fieldId} className={cn("grid grid-cols-[2rem_minmax(0,1fr)_auto] items-baseline gap-2 rounded-xs px-2 py-1 text-sm", f.fieldId === x.box1.fieldId && "bg-surface-2")}>
            <span className="font-mono text-mono-sm text-text-2">{f.box}</span>
            <span className="truncate text-text-2">{f.label.replace(/^\d{1,2}[a-d]?\s+/, "")}</span>
            <span className="tnum font-mono text-mono-sm text-text">{f.value}</span>
          </li>
        ))}
      </ul>
      <div className="mt-3">
        <CitationChip kind="cited" label={`${x.doc.docType.replace("doc:", "")} · ${x.borrower} · Box 1`} page={x.page} />
      </div>
    </section>
  );
}

const FOLLOW_HOPS = ["field", "region", "source", "pipeline"] as const;

/** `follow`: field → region zoom → the Bronze sha equals the upload's → lane and configSha. Seen hops fold to a line. */
export function FollowStep({ p, t }: { p: Sc0Payload; t: number }) {
  const fr = traceFrames(p.trace);
  const zoom = ease(seg(t, 0.15, 0.45));
  const shown = 1 + Math.min(FOLLOW_HOPS.length - 1, Math.floor(seg(t, 0, 0.9) * FOLLOW_HOPS.length));
  const doc = followedDoc(p);
  return (
    <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-1">
      <div className="flex min-h-[320px] min-w-0 flex-col">
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-xs text-text-2">
          <LaneBadge pv="2.3.1" />
          <span className="truncate">
            {p.trace.claim.text} · {p.trace.source.fileName} · page {p.trace.region.page}
          </span>
        </div>
        <div className="relative min-h-0 flex-1">
          <TraceCanvas trace={p.trace} focus={lerpRect(fr.page, fr.zoom, zoom)} layers={{ unit: seg(t, 0, 0.2), field: 1, mark: 1, crosshair: seg(t, 0.4, 0.55) }} />
        </div>
      </div>
      <aside className="scroll-fade-b min-h-0 overflow-y-auto border-t border-border bg-bg-subtle px-4 pb-6 pt-3.5 lg:border-l lg:border-t-0">
        <h2 className="mb-2 text-caps uppercase text-text-2">Follow the number</h2>
        <TraceChain
          trace={p.trace}
          hops={[...FOLLOW_HOPS]}
          shown={shown}
          progress={(seg(t, 0, 0.9) * FOLLOW_HOPS.length) % 1 || 1}
          focus={FOLLOW_HOPS[shown - 1]}
          compact={shown > 2 ? ["field", "region"] : []}
          compareSha={doc?.sha256 ? { sha256: doc.sha256, label: "the upload in step 2" } : undefined}
        />
      </aside>
    </div>
  );
}

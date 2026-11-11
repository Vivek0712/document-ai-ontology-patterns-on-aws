/**
 * SC-3 · The footnote becomes a property (Pattern 3). The trade confirmation is classified by the
 * document ontology; its coupon carries a raised marker; the builder links the marker to the footnote on the next
 * page; the footnote's terms are properties of a step event in the graph, aligned to FIBO, with the region they
 * came from. The naive lane's chunks hold the same text with no link (from the X-ray).
 *
 * Every frame is `render({ payload, step, progress })`: no timers, no typed values. Property values
 * are read from the graph's rows.
 */
import { easeCubicInOut, easeCubicOut } from "d3-ease";
import { useNavigate } from "react-router-dom";
import { Badge, LaneBadge, Num, ProvenanceScope, ProvenanceTag, cn } from "../../design";
import { traceHref } from "../../features/ask/parts";
import { GraphCanvas, edgeKey } from "../../viz/GraphCanvas";
import { type Rect, lerpRect, pad, seg, union } from "../../viz/geom";
import { Boundary, BoxOutline, ChunkRegions, MiniMap, PageCanvas, PairArc, Tag, onSheet, spread } from "../../viz/PageCanvas";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { PanelSection } from "./panel";
import { STEP_PROPERTIES, type Sc3Payload, loadSc3, naiveCaption } from "./sc3-payload";
import { SC3_STEPS } from "./steps";

const STEP = Object.fromEntries(SC3_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC3_STEPS)[number]["id"], number>;
const ease = easeCubicInOut;

const classifyOutput = (p: Sc3Payload) => (p.data.classify?.output ?? {}) as { docType?: string; label?: string; scores?: Record<string, number>; margin?: number; tieBreakUsed?: boolean; tieBreakModel?: string };
const unitsSource = (p: Sc3Payload) => p.composer.sources.find((s) => s.kind === "s3");
const graphSource = (p: Sc3Payload) => p.composer.sources.find((s) => s.kind === "neptune" || s.kind === "local")?.key;

const steps: StoryStep<Sc3Payload>[] = [
  {
    ...SC3_STEPS[0],
    durationMs: 2000,
    caption: (p) => `The document ontology classifies the file as a ${(classifyOutput(p).label ?? "document").toLowerCase()}.`,
    services: ["AWS Lambda · classify", "Amazon S3"],
    receipts: (p) => [
      { label: "classify stage record", value: `${classifyOutput(p).docType} · margin ${classifyOutput(p).margin} · tie-break ${classifyOutput(p).tieBreakUsed ? "used" : "not used"}`, path: "$.data.classify", source: p.composer.sources.find((s) => s.kind === "ddb")?.key, provenance: p.provenance },
      { label: "Indicator scores", value: Object.entries(classifyOutput(p).scores ?? {}).map(([k, v]) => `${k} ${v}`).join(" · "), path: "$.data.classify.output.scores", provenance: p.provenance },
    ],
  },
  {
    ...SC3_STEPS[1],
    durationMs: 1800,
    caption: (p) => `${p.coupon.label} ${p.coupon.value}, with a raised marker ${p.link.marker}.`,
    services: ["AWS Fargate · docling-serve", "Amazon S3"],
    receipts: (p) => [
      { label: "Coupon field", value: `${p.coupon.label} = ${p.coupon.value} · ${p.coupon.concept} · p${p.coupon.page}`, path: "$.data.couponField", source: unitsSource(p)?.key, provenance: p.provenance },
      { label: "Gold units", value: unitsSource(p)?.versionId, source: unitsSource(p)?.key, provenance: p.provenance },
    ],
  },
  {
    ...SC3_STEPS[2],
    durationMs: 2800,
    caption: (p) => `The marker links the ${p.coupon.label.toLowerCase()} to footnote ${p.link.marker} on page ${p.link.footnotePage}.`,
    services: ["AWS Lambda · units (eu_builder)"],
    receipts: (p) => [
      { label: "Marker link (derived: raised glyph)", value: `${p.link.marker} · ${p.link.evidence} · cross-page ${p.link.crossPage}`, path: "$.data.markerLink", provenance: "derived" },
      { label: `Marker glyph box (p${p.coupon.page})`, value: p.link.markerRect.map((n) => n.toFixed(2)).join(", "), path: "$.data.markerLink.markerBox", provenance: "derived" },
      { label: `Footnote box (p${p.link.footnotePage})`, value: p.link.footnoteRect.map((n) => n.toFixed(2)).join(", "), path: "$.data.markerLink.footnoteBbox", provenance: "derived" },
    ],
  },
  {
    ...SC3_STEPS[3],
    durationMs: 3000,
    caption: "The footnote becomes properties of the coupon terms.",
    services: ["AWS Lambda · graph_write", "Amazon Neptune"],
    receipts: (p) => [
      ...STEP_PROPERTIES.map((k) => ({ label: `fin:${k}`, value: p.data.stepEvent?.[k], path: `$.data.stepEvent.${k}`, source: graphSource(p), provenance: p.provenance })),
      { label: "Footnote text (graph row)", value: p.data.stepEvent?.footnote, path: "$.data.stepEvent.footnote", provenance: p.provenance },
    ],
  },
  {
    ...SC3_STEPS[4],
    durationMs: 2800,
    caption: "Aligned to FIBO, with the page region it came from.",
    services: ["AWS Lambda · graph_hop", "Amazon Neptune"],
    receipts: (p) => [
      { label: "graph_hop coupon_terms_v1", value: `${(p.data.graph?.rows as unknown[] | undefined)?.length ?? 0} row · graphs ${((p.data.graph?.graphs as string[] | undefined) ?? []).join(", ")}`, path: "$.data.graph", source: graphSource(p), provenance: p.provenance },
      { label: "Path (the row's bound patterns)", value: p.path ? `${p.path.nodes.length} nodes · ${p.path.edges.length} edges · ${p.path.source}` : "not in this payload", path: "$.data.path", provenance: p.provenance },
      { label: "Payload sha", value: p.composer.payloadSha, source: "GET /showcases/sc-3/payload", provenance: p.provenance },
    ],
  },
  {
    ...SC3_STEPS[5],
    durationMs: 2000,
    caption: naiveCaption,
    services: ["AWS Lambda · rp-xray"],
    receipts: (p) => [
      ...p.pages.map((pg) => ({ label: `X-ray p${pg.page} · control chunks`, value: pg.xrayWarning ?? pg.chunks.map((c) => c.label).join(", "), source: pg.xraySource, provenance: "derived" as const })),
      { label: "Chunks holding the coupon · the footnote", value: `${p.naive.couponChunks.join(", ") || "none"} · ${p.naive.footnoteChunks.join(", ") || "none"}`, path: "$.naive", provenance: "derived" },
    ],
  },
];

// --------------------------------------------------------------------------------------------- frame

function geometry(p: Sc3Payload) {
  const s = spread(p.pages.map((pg) => ({ width: pg.width, height: pg.height, image: pg.image })));
  const sheetOf = (page: number) => s.sheets[Math.max(0, p.pages.findIndex((pg) => pg.page === page))];
  const coupon = onSheet(p.coupon.rect, sheetOf(p.coupon.page));
  const marker = onSheet(p.link.markerRect, sheetOf(p.coupon.page));
  const footnote = onSheet(p.link.footnoteRect, sheetOf(p.link.footnotePage ?? p.coupon.page));
  const units = p.unitRects.map((u) => onSheet(u.rect, sheetOf(u.page)));
  const all: Rect = [0, 0, s.width, s.height];
  /** the unit's region on the coupon's page (the security terms table), else the coupon itself */
  const table = union(p.unitRects.filter((u) => u.page === p.coupon.page).map((u) => onSheet(u.rect, sheetOf(u.page)))) ?? coupon;
  /** step 2: the terms table, 24 pt of context, read at ≥ 1.6× */
  const tableView = pad(table, 24);
  /** steps 3 and 6: the coupon's table and the footnote together, so the link (or its absence) is legible */
  const linkView = pad(union([table, footnote])!, 24);
  return { s, sheetOf, coupon, marker, footnote, units, all, tableView, linkView };
}

function Sc3Stage({ payload: p, step, progress: t }: Frame<Sc3Payload>) {
  const graph = step === STEP.graph;
  return (
    <ProvenanceScope provenance={p.provenance} source={unitsSource(p)?.key}>
      <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_360px] lg:grid-rows-1">
        <div className="flex min-h-[320px] min-w-0 flex-col">
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-xs text-text-2">
            <LaneBadge pv={step === STEP.naive ? "1.0" : "2.3.1"} />
            <span className="shrink-0 font-mono text-mono-sm" title="The package this showcase plays on">
              {p.document.packageId}
            </span>
            <span className="text-text-3" aria-hidden>
              ·
            </span>
            <span className="truncate">
              {graph ? `graph_hop coupon_terms_v1 · ${p.security}` : `${p.document.fileName} · pages ${p.pages.map((pg) => pg.page).join(" and ")}`}
            </span>
            {step === STEP.naive && <span className="ml-auto">X-ray · control chunks</span>}
          </div>
          <div className="relative min-h-0 flex-1">{graph ? <GraphPane p={p} t={t} /> : <Spread p={p} step={step} t={t} />}</div>
        </div>
        <aside className="min-h-0 overflow-y-auto border-t border-border bg-bg-subtle lg:border-l lg:border-t-0">
          <Side p={p} step={step} t={t} />
        </aside>
      </div>
    </ProvenanceScope>
  );
}

/** One camera per step, each move eased from the previous step's end so a step never cuts to a new framing. */
function cameraFor(g: ReturnType<typeof geometry>, step: number, t: number): Rect {
  if (step === STEP.classify) return g.all;
  if (step === STEP["pages"]) return lerpRect(g.all, g.tableView, ease(seg(t, 0, 0.5)));
  if (step === STEP["marker-link"]) return lerpRect(g.tableView, g.linkView, ease(seg(t, 0, 0.3)));
  if (step === STEP.properties) return lerpRect(g.linkView, pad(g.footnote, 110), ease(seg(t, 0, 0.4)));
  return g.linkView;
}

/** Both pages side by side in one canvas: the marker, the arc to the footnote, the unit boundary, the chunks. */
function Spread({ p, step, t }: { p: Sc3Payload; step: number; t: number }) {
  const g = geometry(p);
  const at = (s: number, a = 0, b = 1) => (step > s ? 1 : step === s ? seg(t, a, b) : 0);
  const naive = step === STEP.naive;
  const chunks = naive ? chunkSegments(p, g) : [];
  const pulse = step === STEP["marker-link"] ? Math.sin(Math.PI * seg(t, 0, 0.35)) : 0;
  const camera = cameraFor(g, step, t);
  const zoomed = step !== STEP.classify;
  return (
    <PageCanvas
      ariaLabel={`${p.document.fileName}, pages ${p.pages.map((pg) => pg.page).join(" and ")}`}
      sheets={g.s.sheets}
      pageWidth={g.s.width}
      pageHeight={g.s.height}
      focus={camera}
      align="top"
      animate={false}
      imageOpacity={step === STEP.classify ? 0.6 : naive ? 0.75 : 1}
      padding={16}
      frame={zoomed && <MiniMap sheets={g.s.sheets} width={g.s.width} height={g.s.height} view={camera} opacity={step === STEP["pages"] ? seg(t, 0.2, 0.5) : 1} />}
    >
      {naive ? (
        <>
          <ChunkRegions chunks={chunks} opacity={seg(t, 0, 0.4)} bracketY={g.linkView[1] + (g.linkView[3] - g.linkView[1]) * 0.8} />
          <BoxOutline rect={pad(g.coupon, 2)} tone="text" width={1.5} opacity={seg(t, 0.3, 0.6)} />
          <BoxOutline rect={pad(g.footnote, 2)} tone="text" width={1.5} opacity={seg(t, 0.3, 0.6)} />
          <NaiveLink p={p} g={g} t={t} />
        </>
      ) : (
        step >= STEP["pages"] && (
          <>
            <BoxOutline rect={pad(g.coupon, 2)} tone="text" width={1.5} opacity={at(STEP["pages"], 0.4, 0.7) * (step === STEP.properties ? 0.4 : 1)} />
            {step >= STEP["pages"] && <Tag x={g.coupon[2] + 8} y={(g.coupon[1] + g.coupon[3]) / 2} text={`${p.coupon.label} ${p.coupon.value} · ${p.coupon.concept}`} placement="right" mono opacity={step === STEP["pages"] ? seg(t, 0.6, 0.85) : 0} />}
            {step === STEP["marker-link"] && (
              <>
                <BoxOutline rect={pad(g.marker, 1.5 + 3 * pulse)} tone="layout" width={1.5} opacity={at(STEP["marker-link"], 0, 0.15)} title={`raised marker ${p.link.marker}`} />
                <PairArc from={g.marker} to={g.footnote} tone="neutral" progress={at(STEP["marker-link"], 0.25, 0.65)} bend={-0.18} />
                {g.units.map((r, i) => (
                  <Boundary key={i} rect={r} progress={at(STEP["marker-link"], 0.6, 1)} width={1.5} tint={0.5} />
                ))}
                <Tag x={g.footnote[0] - 6} y={g.linkView[1]} text={`footnote ${p.link.marker} · our builder's extension`} placement="above" align="end" opacity={seg(t, 0.65, 0.9)} />
              </>
            )}
            {step === STEP.properties && (
              <>
                {g.units.map((r, i) => (
                  <Boundary key={i} rect={r} width={1.5} tint={0.3} />
                ))}
                <BoxOutline rect={pad(g.footnote, 6)} tone="concept" width={1.5} opacity={seg(t, 0.3, 0.5)} />
              </>
            )}
          </>
        )
      )}
    </PageCanvas>
  );
}

/** Each control chunk as one segment per page (the union of its characters there), in sheet order. */
function chunkSegments(p: Sc3Payload, g: ReturnType<typeof geometry>) {
  const byId = new Map<string, { chunkId: string; label?: string; segments: Rect[] }>();
  p.pages.forEach((pg, i) => {
    for (const c of pg.chunks) {
      // the naive lane's own chunk id (its last segment, as the panel and the caption name it), not the X-ray's slice label
      const e = byId.get(c.chunkId) ?? { chunkId: c.chunkId, label: c.chunkId.split(":").at(-1), segments: [] };
      const u = union(c.rects.map((r) => onSheet(r, g.s.sheets[i])));
      if (u) e.segments.push(pad(u, 4));
      byId.set(c.chunkId, e);
    }
  });
  return [...byId.values()];
}

/**
 * The link the naive lane does not have, drawn in the failure grammar: a dashed pink line from the coupon to the
 * footnote ending in ×, tagged with what the chunks say (same chunk, or different chunks).
 */
function NaiveLink({ p, g, t }: { p: Sc3Payload; g: ReturnType<typeof geometry>; t: number }) {
  const { couponChunks: a, footnoteChunks: b } = p.naive;
  if (!a.length || !b.length) return null;
  const shared = a.find((c) => b.includes(c));
  return (
    <>
      <PairArc from={g.coupon} to={g.footnote} tone="loss" progress={seg(t, 0.45, 0.85)} bend={-0.18} />
      <Tag x={g.footnote[0] - 6} y={g.linkView[1]} text={shared ? `same chunk ${shared} · no link` : "different chunks · no link"} placement="above" align="end" mono tone="loss" opacity={seg(t, 0.7, 0.95)} />
    </>
  );
}

function GraphPane({ p, t }: { p: Sc3Payload; t: number }) {
  const navigate = useNavigate();
  if (!p.path) return <p className="p-6 text-sm text-text-2">This payload carries no graph path.</p>;
  const footnotePage = p.pages.find((pg) => pg.page === p.link.footnotePage);
  return (
    <GraphCanvas
      className="h-full px-4 py-4"
      rankdir="TB"
      graph={p.path}
      hop={{ order: p.path.edges.map(edgeKey), progress: seg(t, 0.1, 0.9) }}
      reveal={0.4 + 0.6 * seg(t, 0, 0.15)}
      regionThumb={() => (footnotePage?.image ? { href: footnotePage.image.src, pageWidth: footnotePage.width, pageHeight: footnotePage.height, crop: pad(p.link.footnoteRect, 4) } : undefined)}
      onOpen={(n) => {
        const m = /^urn:rp:(?:field|unit):([^/]+)(?:\/(.+))?$/.exec(n.iri);
        if (m) navigate(traceHref({ unitId: m[1], fieldId: m[2] }));
      }}
      ariaLabel={`coupon_terms_v1 for ${p.security}: ${p.path.nodes.length} nodes, ${p.path.edges.length} edges, from the template's row`}
    />
  );
}

function Side({ p, step, t }: { p: Sc3Payload; step: number; t: number }) {
  if (step === STEP.classify) return <ClassifyPanel p={p} t={t} />;
  if (step === STEP.properties || step === STEP.graph) return <PropertiesPanel p={p} t={step === STEP.graph ? 1 : t} />;
  if (step === STEP.naive) return <NaivePanel p={p} />;
  return <CouponPanel p={p} step={step} />;
}

function ClassifyPanel({ p, t }: { p: Sc3Payload; t: number }) {
  const out = classifyOutput(p);
  const scores = Object.entries(out.scores ?? {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...scores.map(([, v]) => v));
  // one label column for every row, as wide as the longest type (mono: 1ch per character), so the bars align
  const labelCh = Math.max(12, ...scores.map(([k]) => k.replace(/^doc:/, "").length)) + 1;
  return (
    <PanelSection title="Classify" provenance={p.provenance} meta={`${p.data.classify?.engine ?? ""} · doc.ttl indicators`}>
      <ul className="flex flex-col gap-1.5" aria-label="Indicator scores per document type">
        {scores.map(([k, v], i) => (
          <li key={k} className="grid items-center gap-2 text-xs" style={{ gridTemplateColumns: `${labelCh}ch minmax(0,1fr) 36px` }}>
            <span className={cn("whitespace-nowrap font-mono text-mono-sm", k === out.docType ? "text-text" : "text-text-2")} title={k}>
              {k.replace(/^doc:/, "")}
            </span>
            <span className="h-1.5 overflow-hidden rounded-full bg-surface-3">
              <span className={cn("block h-full rounded-full", k === out.docType ? "bg-concept" : "bg-text-3")} style={{ width: `${(v / max) * 100 * easeCubicOut(seg(t, 0.1 + i * 0.05, 0.6 + i * 0.05))}%` }} />
            </span>
            <Num value={v} kind="raw" bare className="justify-end" />
          </li>
        ))}
      </ul>
      <dl className="mt-3 grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm" style={{ opacity: seg(t, 0.5, 0.8) }}>
        <dt className="text-text-2">Type</dt>
        <dd className="font-mono text-mono-sm text-text">{out.docType}</dd>
        <dt className="text-text-2">Margin</dt>
        <dd>
          <Num value={out.margin ?? null} kind="raw" />
        </dd>
        <dt className="text-text-2">Tie-break</dt>
        <dd className="text-text">{out.tieBreakUsed ? (out.tieBreakModel ?? "used") : "not used"}</dd>
        <dt className="text-text-2">Stage</dt>
        <dd className="text-text">
          classify · <Num value={(p.data.classify?.durationMs as number | undefined) ?? null} kind="ms" bare />
        </dd>
      </dl>
    </PanelSection>
  );
}

function CouponPanel({ p, step }: { p: Sc3Payload; step: number }) {
  return (
    <>
      <PanelSection title="Coupon field" provenance={p.provenance} source={unitsSource(p)?.key}>
        <p className="flex items-baseline gap-2">
          <span className="text-sm text-text-2">{p.coupon.label}</span>
          <Num value={p.coupon.value} kind="raw" size="lg" bare />
          <sup className="font-mono text-mono-sm text-text-2">{p.link.marker}</sup>
        </p>
        <p className="mt-1 font-mono text-mono-sm text-text-2">
          {p.coupon.concept} · p{p.coupon.page}
        </p>
      </PanelSection>
      {step >= STEP["marker-link"] && (
        <PanelSection title="Marker link" provenance="derived">
          <dl className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            <dt className="text-text-2">Marker</dt>
            <dd className="font-mono text-mono-sm text-text">{p.link.marker}</dd>
            <dt className="text-text-2">Evidence</dt>
            <dd className="text-text">{p.link.evidence}</dd>
            <dt className="text-text-2">Footnote</dt>
            <dd className="text-text">
              page <Num value={p.link.footnotePage ?? null} kind="int" bare />
            </dd>
            <dt className="text-text-2">Cross-page</dt>
            <dd className="text-text">{p.link.crossPage ? "yes · our builder's extension" : "no"}</dd>
          </dl>
          <p className="mt-2 text-xs text-text-2">One unit now spans both regions: the terms table and the footnote.</p>
        </PanelSection>
      )}
    </>
  );
}

/** The footnote's words, then the graph's properties, each pointing at the word that prints it. */
function PropertiesPanel({ p, t }: { p: Sc3Payload; t: number }) {
  const event = p.data.stepEvent ?? {};
  const spanOwner = new Map(STEP_PROPERTIES.flatMap((k) => (p.spans[k] === undefined ? [] : [[p.spans[k]!, k] as const])));
  return (
    <>
      <PanelSection title="Footnote" provenance={p.provenance} meta={`page ${p.link.footnotePage} · as the graph holds it`}>
        <p className="flex flex-wrap gap-x-1.5 gap-y-1 font-mono text-mono-md text-text">
          {p.footnoteTokens.map((w, i) => {
            const k = spanOwner.get(i);
            const on = k ? seg(t, 0.1 + STEP_PROPERTIES.indexOf(k) * 0.12, 0.3 + STEP_PROPERTIES.indexOf(k) * 0.12) : 0;
            return (
              <span key={i} className={cn("rounded-xs px-0.5", i === 0 && w === p.link.marker && "text-text-3")} style={{ boxShadow: on ? `inset 0 -2px 0 0 color-mix(in srgb, var(--concept) ${Math.round(on * 100)}%, transparent)` : undefined }}>
                {w}
              </span>
            );
          })}
        </p>
      </PanelSection>
      <PanelSection title="fin:RatingTriggeredStepEvent" provenance={p.provenance} source={graphSource(p)}>
        <ul className="flex flex-col gap-1.5">
          {STEP_PROPERTIES.map((k, i) => {
            const in_ = easeCubicOut(seg(t, 0.35 + i * 0.12, 0.6 + i * 0.12));
            const span = p.spans[k];
            return (
              <li key={k} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 rounded-sm border border-border bg-surface px-2.5 py-1.5" style={{ opacity: 0.25 + 0.75 * in_, transform: `translateX(${(1 - in_) * 8}px)` }}>
                <span className="font-mono text-mono-sm text-text-2">
                  fin:<span className="text-text">{k}</span>
                </span>
                <span className="flex items-center gap-2">
                  <Num value={event[k] ?? null} kind="raw" bare className="font-mono" />
                  {span !== undefined && <span className="text-xs text-text-3">“{p.footnoteTokens[span]}”</span>}
                </span>
              </li>
            );
          })}
        </ul>
        <p className="mt-2.5 text-xs text-text-2">
          Hangs off <span className="font-mono text-mono-sm">fibo-sec-dbt-bnd:SteppedCouponTerms</span> of the bond. Written by graph_write from the parsed footnote; no model.
        </p>
      </PanelSection>
    </>
  );
}

function NaivePanel({ p }: { p: Sc3Payload }) {
  return (
    <PanelSection title="Naive · 1.0 chunks" provenance="derived" meta="X-ray of both pages at the control size.">
      <dl className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
        <dt className="text-text-2">Coupon in</dt>
        <dd className="font-mono text-mono-sm text-text">{p.naive.couponChunks.join(", ") || "none"}</dd>
        <dt className="text-text-2">Footnote in</dt>
        <dd className="font-mono text-mono-sm text-text">{p.naive.footnoteChunks.join(", ") || "none"}</dd>
        <dt className="text-text-2">Link</dt>
        <dd className="flex items-center gap-1.5 text-text">
          <Badge tone="loss">× none</Badge>
          <span className="text-xs text-text-2">a chunk has no fields, markers or regions</span>
        </dd>
      </dl>
      {p.pages.map((pg) => pg.xrayWarning && (
        <p key={pg.page} className="mt-2 text-xs text-text-2">
          p{pg.page}: {pg.xrayWarning}
        </p>
      ))}
      <p className="mt-3 flex items-center gap-1.5 text-xs text-text-3">
        <ProvenanceTag provenance="derived" /> X-ray: inspection only, never stored or cited
      </p>
    </PanelSection>
  );
}

export const sc3FootnoteProperty: Storyboard<Sc3Payload> = {
  id: "sc-3",
  code: "SC-3",
  title: showcaseMeta("sc-3")?.title ?? "",
  claim: showcaseMeta("sc-3")?.claim ?? "",
  load: loadSc3,
  steps,
  render: (frame) => <Sc3Stage {...frame} />,
  receiptsPayload: (p) => ({ security: p.security, document: p.document, data: { ...p.data, graph: { ...p.data.graph, sparql: "…" }, path: undefined }, naive: p.naive, composer: p.composer }),
};

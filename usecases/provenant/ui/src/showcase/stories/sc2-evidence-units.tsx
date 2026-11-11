/**
 * SC-2 · Evidence units (Pattern 2, thin). One table unit, built in three passes as the build
 * report recorded it: the engine's regions; pass A (the table seeds a unit, its label attaches); the raised marker
 * that pulls its footnote in; pass B (paragraphs scored by cosine against tau); pass C (the rest becomes section
 * text); the unit. Then the split table across a page break (drawn as merged only when the record says so,
 * as recorded) and the two checks, as the build's invariants record states them.
 *
 * Every frame is `render({ payload, step, progress })`: no timers, no typed values.
 */
import { easeCubicInOut, easeCubicOut } from "d3-ease";
import { Fragment } from "react";
import { Badge, LaneBadge, Num, ProvenanceScope, ScopeTag, cn } from "../../design";
import { type Rect, lerpRect, pad, seg, union } from "../../viz/geom";
import { Boundary, BoxOutline, PageCanvas, PairArc, Screen, Tag, onSheet, spread } from "../../viz/PageCanvas";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { PanelSection } from "./panel";
import { type Sc2Payload, checksCaption, gutterRoute, invariantRows, loadSc2, markerCaption, passACaption, passCCaption, phaseBCandidates, roleWord, splitCaption, unitCaption } from "./sc2-payload";
import { SC2_STEPS } from "./steps";

const STEP = Object.fromEntries(SC2_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC2_STEPS)[number]["id"], number>;
const ease = easeCubicInOut;

const reportReceipt = (p: Sc2Payload) => {
  const r = p.build.sources.find((s) => s.key.endsWith("build_report.json"));
  return { label: "Build report", value: r?.versionId ?? r?.sha256, source: r?.key, provenance: p.provenance };
};
const blockReceipt = (p: Sc2Payload, label: string, filter: (b: Sc2Payload["blocks"][number]) => boolean) => ({
  label,
  value: p.blocks.filter(filter).map((b) => `${b.blockPointer} ${b.role} ${b.phase}${b.score !== undefined && b.score !== null ? ` ${b.score}` : ""}`).join(" · "),
  path: "$.blocks",
  provenance: p.provenance,
});

const steps: StoryStep<Sc2Payload>[] = [
  {
    ...SC2_STEPS[0],
    durationMs: 1800,
    caption: (p) => `Page ${p.page.page} · ${p.unit.breadcrumb.at(-1)}: the engine returns regions with roles.`,
    services: ["AWS Fargate · docling-serve", "Amazon S3"],
    receipts: (p) => [reportReceipt(p), blockReceipt(p, `Blocks on page ${p.page.page}`, () => true)],
  },
  {
    ...SC2_STEPS[1],
    durationMs: 2400,
    caption: passACaption,
    services: ["AWS Lambda · units (eu_builder)"],
    receipts: (p) => [reportReceipt(p), blockReceipt(p, "Phase A", (b) => b.phase === "A")],
  },
  {
    ...SC2_STEPS[2],
    durationMs: 2400,
    caption: markerCaption,
    services: ["AWS Lambda · units (eu_builder)"],
    receipts: (p) => [
      { label: "Marker link (derived: raised glyph)", value: p.link ? `${p.link.marker} · ${p.link.evidence} · field ${p.link.fieldId} · member ${p.link.footnoteMember}` : "none", path: "$.link", provenance: "derived" },
      { label: "Marker glyph box", value: p.link?.markerRect?.map((n) => n.toFixed(2)).join(", "), path: "$.link.markerBox", provenance: "derived" },
    ],
  },
  {
    ...SC2_STEPS[3],
    durationMs: 3000,
    caption: (p) => `Pass B: a paragraph joins when its similarity clears τ = ${p.build.tau}.`,
    services: ["AWS Lambda · units (eu_builder)", "Amazon Bedrock · Titan Text Embeddings V2"],
    receipts: (p) => [blockReceipt(p, `Phase B scores (${p.build.phaseB} · ${p.build.embedModel})`, (b) => b.score !== undefined && b.score !== null), { label: "tau", value: p.build.tau, path: "$.build.tau", provenance: p.provenance }],
  },
  {
    ...SC2_STEPS[4],
    durationMs: 1600,
    caption: passCCaption,
    services: ["AWS Lambda · units (eu_builder)"],
    receipts: (p) => [blockReceipt(p, "Phase C (section text)", (b) => b.phase === "C"), { label: "Phase counts (whole document)", value: JSON.stringify(p.build.phaseCounts), path: "$.build.phaseCounts", provenance: p.provenance }],
  },
  {
    ...SC2_STEPS[5],
    durationMs: 2200,
    caption: unitCaption,
    services: ["Amazon S3"],
    receipts: (p) => [
      { label: "Unit", value: `${p.unit.unitId} · ${p.unit.unitType}`, path: "$.unit", source: `GET /units/${p.unit.unitId}`, provenance: p.provenance },
      { label: "Breadcrumb · page · box", value: `${p.unit.breadcrumb.join(" › ")} · p${p.page.page} · ${p.unit.rect.map((n) => n.toFixed(1)).join(", ")}`, path: "$.unit.rect", provenance: p.provenance },
      { label: "Silver units", value: p.build.sources.find((s) => s.key.endsWith("units.jsonl"))?.versionId, source: p.build.sources.find((s) => s.key.endsWith("units.jsonl"))?.key, provenance: p.provenance },
    ],
  },
  {
    ...SC2_STEPS[6],
    durationMs: 2800,
    caption: splitCaption,
    services: ["AWS Lambda · units (eu_builder)"],
    receipts: (p) => [
      { label: "Split table halves", value: p.split?.halves.map((h) => `${h.unitId} p${h.page}`).join(" · ") ?? "none found", path: "$.split.halves", provenance: p.provenance },
      { label: "mergedTables (unit records)", value: p.split?.mergedTables ?? p.build.mergedTables, path: "$.split.mergedTables", provenance: p.provenance },
    ],
  },
  {
    ...SC2_STEPS[7],
    durationMs: 2200,
    caption: checksCaption,
    services: ["AWS Lambda · units (eu_builder)", "Amazon CloudWatch"],
    receipts: (p) => [
      { label: "Invariants mode", value: p.build.invariants?.mode ?? "no record", path: "$.build.invariants.mode", provenance: p.provenance },
      { label: "Violations logged", value: p.build.invariants?.violations.length ?? 0, path: "$.build.invariants.violations", provenance: p.provenance },
      ...invariantRows(p.build.invariants?.note).map(([k, v]) => ({ label: k, value: v, path: "$.build.invariants.note", provenance: p.provenance })),
    ],
  },
];

// --------------------------------------------------------------------------------------------- frame

function Sc2Stage({ payload: p, step, progress: t }: Frame<Sc2Payload>) {
  const split = step === STEP["page-break"];
  return (
    <ProvenanceScope provenance={p.provenance} source={reportReceipt(p).source}>
      <div className="grid h-full min-h-0 grid-rows-[minmax(0,1fr)_auto] lg:grid-cols-[minmax(0,1fr)_380px] lg:grid-rows-1">
        <div className="flex min-h-[320px] min-w-0 flex-col">
          <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-bg-subtle px-3 text-xs text-text-2">
            <LaneBadge pv="2.3.1" />
            <span className="shrink-0 font-mono text-mono-sm" title="The package this showcase plays on">
              {p.packageId}
            </span>
            <span className="text-text-3" aria-hidden>
              ·
            </span>
            <span className="truncate">
              {p.fileName} · {split && p.split ? `pages ${p.split.pages.map((x) => x.page).join(" and ")}` : `page ${p.page.page}`}
            </span>
            <span className="ml-auto hidden font-mono text-mono-sm sm:inline">{p.build.embedModel}</span>
          </div>
          <div className="relative min-h-0 flex-1">{split ? <SplitCanvas p={p} t={t} /> : <BuildCanvas p={p} step={step} t={t} />}</div>
        </div>
        <aside className="min-h-0 overflow-y-auto border-t border-border bg-bg-subtle lg:border-l lg:border-t-0">
          <Side p={p} step={step} t={t} />
        </aside>
      </div>
    </ProvenanceScope>
  );
}

/** Page points kept free left of the blocks: role chips and connectors live there, never on the text. */
const GUTTER_PT = 64;

/** The page with its blocks: regions, then each pass's joins, then the boundary. */
function BuildCanvas({ p, step, t }: { p: Sc2Payload; step: number; t: number }) {
  const page: Rect = [0, 0, p.page.width, p.page.height];
  const blockRects = p.blocks.map((b) => b.rect);
  const content = pad(union(blockRects)!, 28);
  const framed: Rect = [content[0] - GUTTER_PT, content[1], content[2], content[3]];
  const focus = step === STEP.regions ? lerpRect(page, framed, ease(seg(t, 0.2, 0.8))) : framed;
  const own = (b: Sc2Payload["blocks"][number]) => b.unitId === p.unit.unitId;
  const at = (s: number, a = 0, b = 1) => (step > s ? 1 : step === s ? seg(t, a, b) : 0);
  const seed = p.seed?.rect;
  const joined = (b: Sc2Payload["blocks"][number]) => (b.phase === "A" ? at(STEP["pass-a"], 0.3, 0.75) : b.phase === "marker" ? at(STEP.marker, 0.45, 0.85) : b.phase === "B" ? at(STEP["pass-b"], 0.45, 0.85) : 0);
  const residual = at(STEP["pass-c"], 0.2, 0.7);
  const final = at(STEP.unit, 0.1, 0.8);
  const sectionUnits = [...new Set(p.blocks.filter((b) => b.phase === "C").map((b) => b.unitId))];
  const candidates = new Set(phaseBCandidates(p));
  const passB = step === STEP["pass-b"];
  const label = p.blocks.find((b) => own(b) && b !== p.seed && /label/.test(b.role));
  return (
    <PageCanvas ariaLabel={`${p.fileName}, page ${p.page.page}: the blocks and the unit they build`} image={p.page.image} pageWidth={p.page.width} pageHeight={p.page.height} focus={focus} align="top" animate={false} padding={16}>
      {p.blocks.map((b) => {
        const isSeed = b === p.seed;
        const j = own(b) ? joined(b) : 0;
        const scored = candidates.has(b);
        const lost = scored && b.phase !== "B";
        const out = lost && step >= STEP["pass-b"] ? at(STEP["pass-b"], 0.45, 0.85) : 0;
        const tone = isSeed && step >= STEP["pass-a"] ? "text" : j > 0 ? "text" : lost && out > 0 ? "faint" : "layout";
        return (
          <g key={b.blockPointer} opacity={step === STEP.regions ? seg(t, 0.05, 0.35) : 1 - 0.45 * out * (1 - residual)}>
            <BoxOutline rect={pad(b.rect, 2)} tone={tone} dashed={!(isSeed && step >= STEP["pass-a"]) && j < 1} width={isSeed && step >= STEP["pass-a"] ? 1.5 : 1} title={`${b.blockPointer} · ${b.role} · phase ${b.phase}`} />
            {step === STEP.regions && <Tag x={b.rect[0] - 6} y={(b.rect[1] + b.rect[3]) / 2} text={roleWord(b.role)} tone="layout" placement="left" opacity={seg(t, 0.45, 0.7)} />}
            {seed && own(b) && !isSeed && b.phase !== "marker" && j > 0 && final < 1 && <Joiner points={gutterRoute(b.rect, seed, blockRects)} k={j} />}
            {passB && scored && <ScoreChip b={b} tau={p.build.tau} opacity={seg(t, 0.35 + [...candidates].indexOf(b) * 0.08, 0.6 + [...candidates].indexOf(b) * 0.08)} />}
          </g>
        );
      })}
      {seed && step === STEP["pass-a"] && <Tag x={seed[2]} y={seed[1] - 4} text="seed · table" placement="above" align="end" opacity={seg(t, 0.1, 0.3)} />}
      {p.link?.markerRect && p.link.footnoteRect && step >= STEP.marker && final < 1 && (
        <>
          <BoxOutline rect={pad(p.link.markerRect, 1.5 + 3 * (step === STEP.marker ? Math.sin(Math.PI * seg(t, 0, 0.3)) : 0))} tone="layout" width={1.5} title={`raised marker ${p.link.marker}`} />
          <PairArc from={p.link.markerRect} to={p.link.footnoteRect} tone="neutral" progress={at(STEP.marker, 0.15, 0.55)} bend={0.3} />
        </>
      )}
      {residual > 0 &&
        sectionUnits.map((u) => {
          const mine = p.blocks.filter((b) => b.unitId === u);
          return (
            <g key={u} opacity={residual * (1 - 0.5 * final)}>
              {mine.map((b) => (
                <BoxOutline key={b.blockPointer} rect={pad(b.rect, 4)} tone="muted" dashed width={1.25} radius={3} fill="color-mix(in srgb, var(--text-2) 6%, transparent)" />
              ))}
              {step === STEP["pass-c"] && <Tag x={mine[0].rect[0]} y={mine[0].rect[1] - 6} text={`section_text · ${u.split(":").at(-1)}`} placement="above" mono tone="muted" opacity={seg(t, 0.5, 0.8)} />}
            </g>
          );
        })}
      {final > 0 && <Boundary rect={p.unit.rect} progress={final} pad={6} width={1.5} />}
      {step === STEP.checks && <Boundary rect={p.unit.rect} pad={6} width={1.5} />}
      {step === STEP.checks && label && seed && <I1Relation from={label.rect} to={seed} t={t} enforced={p.build.invariants?.mode === "enforce"} />}
    </PageCanvas>
  );
}

/** Pass B on the page: each scored paragraph's cosine at its top-right, against τ. */
function ScoreChip({ b, tau, opacity }: { b: Sc2Payload["blocks"][number]; tau: number; opacity: number }) {
  const s = b.score ?? 0;
  const joins = b.phase === "B";
  return <Tag x={b.rect[2]} y={b.rect[1] - 4} text={joins ? `${s.toFixed(3)} ≥ τ ${tau}` : `${s.toFixed(3)} < τ ${tau}`} placement="above" align="end" mono tone={joins ? "text" : "faint"} opacity={opacity} />;
}

/** I1 (a table anchors to its label): the relation the check would test, dotted, and whether this lane enforces it. */
function I1Relation({ from, to, t, enforced }: { from: Rect; to: Rect; t: number; enforced: boolean }) {
  const x = Math.max(from[0], to[0]) + 24;
  return (
    <g opacity={seg(t, 0.3, 0.7)}>
      <line x1={x} y1={from[3]} x2={x} y2={to[1]} stroke="var(--text-3)" strokeWidth={1.25} strokeDasharray="2 3" vectorEffect="non-scaling-stroke" />
      <Tag x={to[2]} y={to[1] - 4} text={enforced ? "I1 · enforced" : "I1 · not enforced in this lane"} placement="above" align="end" tone="faint" />
    </g>
  );
}

/** A 1 px orthogonal join through the gutter (`gutterRoute`), drawn along its length over `k`. */
function Joiner({ points, k }: { points: [number, number][]; k: number }) {
  const lens = points.slice(1).map((q, i) => Math.hypot(q[0] - points[i][0], q[1] - points[i][1]));
  const total = lens.reduce((a, b) => a + b, 0);
  let left = total * easeCubicOut(k);
  const drawn: [number, number][] = [points[0]];
  for (let i = 0; i < lens.length && left > 0; i++) {
    const f = Math.min(1, left / (lens[i] || 1));
    const [a, b] = [points[i], points[i + 1]];
    drawn.push([a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f]);
    left -= lens[i];
  }
  const end = points.at(-1)!;
  return (
    <g>
      <polyline points={drawn.map((q) => q.join(",")).join(" ")} fill="none" stroke="var(--text)" strokeWidth={1} strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <Screen x={points[0][0]} y={points[0][1]}>
        <circle r={2.5} fill="var(--text)" />
      </Screen>
      {k >= 1 && (
        <Screen x={end[0]} y={end[1]}>
          <path d="M-5 -3.5 L0 0 L-5 3.5" fill="none" stroke="var(--text)" strokeWidth={1.25} strokeLinecap="round" />
        </Screen>
      )}
    </g>
  );
}

/** The split table: both pages side by side; one boundary per unit (and a connector only when merged). */
function SplitCanvas({ p, t }: { p: Sc2Payload; t: number }) {
  if (!p.split) return <p className="p-6 text-sm text-text-2">No split table in this document.</p>;
  const sp = spread(p.split.pages.map((x) => ({ width: x.width, height: x.height, image: x.image })));
  const rects = p.split.halves.map((h, i) => onSheet(h.rect, sp.sheets[i]));
  const u = union(rects)!;
  const focus: Rect = [0, Math.max(0, u[1] - 120), sp.width, u[3] + 60];
  const merged = p.split.mergedTables > 0;
  return (
    <PageCanvas ariaLabel={`${p.fileName}, pages ${p.split.pages.map((x) => x.page).join(" and ")}`} sheets={sp.sheets} pageWidth={sp.width} pageHeight={sp.height} focus={lerpRect([0, 0, sp.width, sp.height], focus, ease(seg(t, 0, 0.45)))} widthFit={1} animate={false} padding={16}>
      {rects.map((r, i) => (
        <g key={i}>
          <Boundary rect={r} progress={seg(t, 0.35 + i * 0.15, 0.8 + i * 0.1)} pad={5} width={merged ? 1.5 : 1} />
          <Tag x={r[0] - 5} y={r[3] + 5} text={`unit ${p.split!.halves[i].unitId.split(":").at(-1)} · p${p.split!.halves[i].page}`} placement="below" mono opacity={seg(t, 0.7, 0.95)} />
        </g>
      ))}
      {merged && <PairArc from={rects[0]} to={rects[1]} tone="neutral" progress={seg(t, 0.6, 0.95)} />}
    </PageCanvas>
  );
}

// --------------------------------------------------------------------------------------------- side panel

function Side({ p, step, t }: { p: Sc2Payload; step: number; t: number }) {
  if (step === STEP.regions) return <RegionsPanel p={p} />;
  if (step === STEP["pass-b"]) return <AllocationPanel p={p} t={t} />;
  if (step === STEP["page-break"]) return <SplitPanel p={p} />;
  if (step === STEP.checks) return <ChecksPanel p={p} />;
  return <MembersPanel p={p} step={step} />;
}

function RegionsPanel({ p }: { p: Sc2Payload }) {
  const roles = [...new Set(p.blocks.map((b) => b.role))];
  return (
    <PanelSection title={`Regions on page ${p.page.page}`} provenance={p.provenance} meta="Docling layout blocks, with the role the builder reads.">
      <ul className="flex flex-col divide-y divide-border">
        {roles.map((r) => (
          <li key={r} className="flex h-8 items-center gap-2 text-sm">
            <span className="size-2.5 rounded-[2px] border border-dashed border-layout" aria-hidden />
            <span className="flex-1 text-text">{roleWord(r)}</span>
            <Num value={p.blocks.filter((b) => b.role === r).length} kind="int" size="xs" bare />
          </li>
        ))}
      </ul>
    </PanelSection>
  );
}

const PHASE_LABEL: Record<string, string> = { A: "pass A", marker: "marker", B: "pass B", C: "pass C" };

/** The unit as it grows: each member with the pass that placed it. */
function MembersPanel({ p, step }: { p: Sc2Payload; step: number }) {
  const shownPhases = new Set(["A", ...(step >= STEP.marker ? ["marker"] : []), ...(step >= STEP["pass-b"] ? ["B"] : [])]);
  const members = p.blocks.filter((b) => b.unitId === p.unit.unitId && shownPhases.has(b.phase));
  return (
    <>
      <PanelSection title="The unit" provenance={p.provenance} meta={p.unit.label}>
        <ul className="flex flex-col divide-y divide-border">
          {members.map((b) => (
            <li key={b.blockPointer} className="flex h-8 items-center gap-2 text-sm">
              <span className="flex-1 text-text">{roleWord(b.role)}</span>
              <span className="font-mono text-mono-sm text-text-3">{b.blockPointer}</span>
              <Badge tone={b.phase === "marker" ? "layout" : "neutral"}>{PHASE_LABEL[b.phase]}</Badge>
            </li>
          ))}
        </ul>
      </PanelSection>
      {step === STEP.marker && p.link && (
        <PanelSection title="Marker link" provenance="derived">
          <dl className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            <dt className="text-text-2">Marker</dt>
            <dd className="font-mono text-mono-sm text-text">{p.link.marker}</dd>
            <dt className="text-text-2">Field</dt>
            <dd className="truncate text-text">{p.link.fieldLabel ?? p.link.fieldId}</dd>
            <dt className="text-text-2">Evidence</dt>
            <dd className="text-text">{p.link.evidence}</dd>
          </dl>
        </PanelSection>
      )}
      {step >= STEP["pass-c"] && (
        <PanelSection title="Pass C · section text" meta="What no seed took is grouped as section text, so nothing is dropped.">
          <dl className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 text-sm">
            <dt className="text-text-2">Blocks to section text</dt>
            <dd>
              <Num value={p.blocks.filter((b) => b.phase === "C").length} kind="int" bare />
            </dd>
            <dt className="text-text-2">Section text units</dt>
            <dd>
              <Num value={new Set(p.blocks.filter((b) => b.phase === "C").map((b) => b.unitId)).size} kind="int" bare />
            </dd>
            <dt className="text-text-2">Left without a unit</dt>
            <dd>
              <Num value={p.blocks.filter((b) => !b.unitId).length} kind="int" bare />
            </dd>
          </dl>
        </PanelSection>
      )}
      {step >= STEP.unit && (
        <PanelSection title="Metadata">
          <dl className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
            <dt className="text-text-2">Breadcrumb</dt>
            <dd className="text-text">{p.unit.breadcrumb.join(" › ")}</dd>
            <dt className="text-text-2">Page</dt>
            <dd>
              <Num value={p.page.page} kind="int" />
            </dd>
            <dt className="text-text-2">Box (pt)</dt>
            <dd className="font-mono text-mono-sm text-text">{p.unit.rect.map((n) => n.toFixed(1)).join(", ")}</dd>
            <dt className="text-text-2">Fields</dt>
            <dd>
              <Num value={p.unit.fields.length} kind="int" />
            </dd>
          </dl>
        </PanelSection>
      )}
    </>
  );
}

/** Pass B: each scored paragraph, its cosine against tau, and where it went. */
function AllocationPanel({ p, t }: { p: Sc2Payload; t: number }) {
  const rows = phaseBCandidates(p);
  const tau = p.build.tau;
  return (
    <PanelSection title="Pass B · cosine vs τ" provenance={p.provenance} meta={`${p.build.embedModel} · τ ${tau}`}>
      {/* the τ tick's label, on the bars' own axis */}
      <div className="mb-1 flex items-center gap-2" aria-hidden>
        <div className="relative h-4 flex-1">
          <span className="absolute -translate-x-1/2 font-mono text-mono-sm text-text-2" style={{ left: `${tau * 100}%` }}>
            τ {tau}
          </span>
        </div>
        <span className="min-w-[40px]" />
      </div>
      <ul className="flex flex-col gap-2.5">
        {rows.map((b, i) => {
          const s = b.score ?? 0;
          const grow = easeCubicOut(seg(t, 0.05 + i * 0.08, 0.45 + i * 0.08));
          const decided = seg(t, 0.5, 0.8);
          const joins = b.phase === "B";
          return (
            <li key={b.blockPointer} className="flex flex-col gap-1">
              <div className="flex items-center gap-2 text-xs">
                <span className="font-mono text-mono-sm text-text-2">{b.blockPointer}</span>
                <span className="text-text-2">{roleWord(b.role)}</span>
                <span className={cn("ml-auto", joins ? "text-text" : "text-text-3")} style={{ opacity: decided }}>
                  {joins ? "joins the unit" : "stays out · section text"}
                </span>
              </div>
              <div className="flex items-center gap-2">
                <div className="relative h-2 flex-1 rounded-full bg-surface-3" role="img" aria-label={`cosine ${s} against tau ${tau}`}>
                  <span className={cn("absolute inset-y-0 left-0 rounded-full", joins ? "bg-text" : "bg-text-3")} style={{ width: `${Math.max(0, Math.min(1, s)) * 100 * grow}%` }} />
                  <span className="absolute -inset-y-1 w-px bg-text-2" style={{ left: `${tau * 100}%` }} aria-hidden />
                </div>
                <Num value={s} kind="ratio" size="xs" bare className="min-w-[40px] justify-end" />
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-3 text-xs text-text-2">
        Each score is also on the page, at its paragraph's top-right. A block below τ was scored against this unit and lost; pass C keeps it.
      </p>
    </PanelSection>
  );
}

function SplitPanel({ p }: { p: Sc2Payload }) {
  if (!p.split) return <PanelSection title="Across a page break">No split table found in this document.</PanelSection>;
  return (
    <PanelSection title="Across a page break" provenance={p.provenance}>
      <ul className="flex flex-col divide-y divide-border">
        {p.split.halves.map((h) => (
          <li key={h.unitId} className="flex h-8 items-center gap-2 text-sm">
            <span className="size-2 shrink-0 rounded-full bg-concept" aria-hidden />
            <span className="min-w-0 flex-1 truncate text-text">{h.label}</span>
            <span className="text-xs text-text-2">p{h.page}</span>
          </li>
        ))}
      </ul>
      <p className="mt-2 flex items-center gap-2 text-sm text-text-2">
        mergedTables <Num value={p.split.mergedTables} kind="int" />
      </p>
      <p className="mt-2 text-xs text-text-2">Cross-page merge is our builder's extension; it draws only when the unit record says it merged.</p>
    </PanelSection>
  );
}

function ChecksPanel({ p }: { p: Sc2Payload }) {
  const inv = p.build.invariants;
  return (
    <PanelSection title="Checks I1 and I2" provenance={p.provenance} meta="I1 anchors a chart or table to a heading or label; I2 checks a table and its chart agree.">
      <dl className="grid grid-cols-[96px_minmax(0,1fr)] gap-x-3 gap-y-1.5 text-sm">
        <dt className="text-text-2">Mode</dt>
        <dd>
          <Badge tone="neutral">{inv?.mode ?? "no record"}</Badge>
        </dd>
        <dt className="text-text-2">Violations</dt>
        <dd>
          <Num value={inv?.violations.length ?? 0} kind="int" />
        </dd>
        {invariantRows(inv?.note).map(([k, v]) => (
          <Fragment key={k}>
            <dt className="font-mono text-mono-sm text-text-2">{k}</dt>
            <dd className="text-text">{v}</dd>
          </Fragment>
        ))}
      </dl>
      <p className="mt-3 flex items-center gap-1.5 text-xs text-text-3">
        <ScopeTag /> from the build report
      </p>
    </PanelSection>
  );
}

export const sc2EvidenceUnits: Storyboard<Sc2Payload> = {
  id: "sc-2",
  code: "SC-2",
  title: showcaseMeta("sc-2")?.title ?? "",
  claim: showcaseMeta("sc-2")?.claim ?? "",
  load: loadSc2,
  steps,
  render: (frame) => <Sc2Stage {...frame} />,
  receiptsPayload: (p) => ({ docId: p.docId, unit: p.unit, build: p.build, blocks: p.blocks, link: p.link, split: p.split }),
};

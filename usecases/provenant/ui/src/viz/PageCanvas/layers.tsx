/**
 * PageCanvas layers. Geometry is in PDF points; anything that must keep a constant screen size (badges, tags,
 * stroke widths) is drawn through `usePageCamera().px` or inside <Screen>, which switches to CSS px.
 * Every layer is a pure function of its props, so showcases can drive them with (payload, step, progress).
 */
import { type ReactNode, useMemo } from "react";
import type { Rect } from "../geom";
import { clamp01, pad as padRect } from "../geom";
import { textWidth } from "../measure";
import { usePageCamera } from "./PageCanvas";

export type Tone = "text" | "muted" | "faint" | "border" | "boundary" | "loss" | "cited" | "layout" | "concept" | "agent" | "chunk-1" | "chunk-2" | "chunk-3" | "chunk-4";
export const toneVar = (t: Tone) => (t === "text" ? "var(--text)" : t === "muted" ? "var(--text-2)" : t === "faint" ? "var(--text-3)" : t === "border" ? "var(--border-strong)" : `var(--${t})`);

/** Children are drawn in CSS px, anchored at page point (x, y). */
export function Screen({ x, y, children, opacity = 1 }: { x: number; y: number; children: ReactNode; opacity?: number }) {
  const { view } = usePageCamera();
  return (
    <g transform={`translate(${x} ${y}) scale(${1 / view.k})`} opacity={opacity}>
      {children}
    </g>
  );
}

// --------------------------------------------------------------------------------------------- boxes

/**
 * A thin outline. Colour roles on the paper: fields are neutral `muted` solid, Docling members are
 * `border` dashed; green (`cited`) only for the one cited target, and always with a ✓ next to it.
 */
export function BoxOutline({ rect, tone = "muted", dashed, width = 1, fill, opacity = 1, radius = 0, title }: { rect: Rect; tone?: Tone; dashed?: boolean; width?: number; fill?: string; opacity?: number; radius?: number; title?: string }) {
  const { px } = usePageCamera();
  return (
    <rect
      x={rect[0]}
      y={rect[1]}
      width={rect[2] - rect[0]}
      height={rect[3] - rect[1]}
      rx={radius}
      fill={fill ?? "none"}
      stroke={toneVar(tone)}
      strokeWidth={px(width)}
      strokeDasharray={dashed ? `${px(4)} ${px(3)}` : undefined}
      opacity={opacity}
    >
      {title && <title>{title}</title>}
    </rect>
  );
}

/**
 * The evidence boundary (orange): a rounded rect `pad` pt outside the box with a soft dark halo for contrast on
 * the white page. It draws (stroke-dashoffset) over `progress` 0..0.8, then the tint fades in. `width` is the
 * stroke in CSS px (1 for a unit among many, 1.5 for the one the story is about); `tint` scales the fill.
 */
export function Boundary({ rect, progress = 1, pad = 4, label, width = 1.5, tint: tintScale = 1 }: { rect: Rect; progress?: number; pad?: number; label?: string; width?: number; tint?: number }) {
  const { px } = usePageCamera();
  const r = padRect(rect, pad);
  const draw = clamp01(progress / 0.8);
  const tint = clamp01((progress - 0.7) / 0.3) * tintScale;
  const common = { x: r[0], y: r[1], width: r[2] - r[0], height: r[3] - r[1], rx: 3, pathLength: 100, strokeDasharray: "100 100", strokeDashoffset: 100 * (1 - draw), fill: "none" } as const;
  return (
    <g>
      {tint > 0 && <rect {...common} fill="var(--boundary-tint)" stroke="none" opacity={tint} strokeDasharray={undefined} />}
      <rect {...common} stroke="rgb(0 0 0 / 0.22)" strokeWidth={px(width + 2)} />
      <rect {...common} stroke="var(--boundary)" strokeWidth={px(width)} strokeLinejoin="round" />
      {label && tint > 0 && <Tag x={r[0]} y={r[1]} text={label} tone="boundary" placement="above" opacity={tint} />}
    </g>
  );
}

// --------------------------------------------------------------------------------------------- tags

/**
 * A constant-size label card. `placement` is relative to the anchor point. The card is clamped inside the
 * visible canvas (4 px inset), so a label anchored near an edge never runs off it.
 */
export function Tag({ x, y, text, tone = "text", placement = "above", align = "start", mono, opacity = 1, filled, dashed }: { x: number; y: number; text: string; tone?: Tone; placement?: "above" | "below" | "left" | "right" | "inside"; align?: "start" | "center" | "end"; mono?: boolean; opacity?: number; filled?: boolean; dashed?: boolean }) {
  const { view, size } = usePageCamera();
  const fs = 12;
  const w = Math.ceil(textWidth(text, fs, 500, mono)) + 12;
  const h = 20;
  const off = { above: [0, -h - 3], below: [0, 3], left: [-w - 4, -h / 2], right: [4, -h / 2], inside: [8, 8] }[placement];
  if (align !== "start" && (placement === "above" || placement === "below")) off[0] -= align === "end" ? w : w / 2;
  if (size.w > 0) {
    const sx = (x - view.x) * view.k + off[0];
    const sy = (y - view.y) * view.k + off[1];
    off[0] += Math.min(Math.max(sx, 4), Math.max(4, size.w - w - 4)) - sx;
    off[1] += Math.min(Math.max(sy, 4), Math.max(4, size.h - h - 4)) - sy;
  }
  const color = toneVar(tone);
  return (
    <Screen x={x} y={y} opacity={opacity}>
      <g transform={`translate(${off[0]} ${off[1]})`}>
        <rect width={w} height={h} rx={4} fill={filled ? color : "var(--surface)"} stroke={color} strokeWidth={1} strokeDasharray={dashed ? "3 2" : undefined} />
        <text x={6} y={14} fontSize={fs} fontWeight={500} fill={filled ? "var(--inverse)" : "var(--text)"} style={{ fontFamily: mono ? "var(--font-mono)" : "var(--font-sans)" }}>
          {text}
        </text>
      </g>
    </Screen>
  );
}

/** ✓ / × marker (meaning never by colour alone). */
/** ✓ cited, × loss (only for a confirmed failure), or a neutral dot: "a logged answer used this number". */
/** `dx`: a screen-px offset from the anchor (e.g. beside a figure rather than over its digits). */
export function Mark({ x, y, kind, opacity = 1, scale = 1, dx = 0 }: { x: number; y: number; kind: "cited" | "loss" | "used"; opacity?: number; scale?: number; dx?: number }) {
  const c = kind === "cited" ? "var(--cited)" : kind === "loss" ? "var(--loss)" : "var(--text-2)";
  return (
    <Screen x={x} y={y} opacity={opacity}>
      <g transform={`translate(${dx} 0) scale(${scale})`}>
        <circle r={8} fill="var(--surface)" stroke={c} strokeWidth={1.5} />
        {kind === "cited" ? (
          <path d="M-3.5 0.2 L-1 2.8 L3.8 -2.6" fill="none" stroke={c} strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" />
        ) : kind === "used" ? (
          <circle r={2.5} fill={c} />
        ) : (
          <path d="M-3 -3 L3 3 M3 -3 L-3 3" fill="none" stroke={c} strokeWidth={1.8} strokeLinecap="round" />
        )}
      </g>
    </Screen>
  );
}

// --------------------------------------------------------------------------------------------- chunks

/**
 * Chunk n is told apart by pattern first, hue second (colour-blind safe): #1 indigo 45° hatch, #2 slate dots,
 * #3 light indigo 135° hatch, #4 light slate solid. Neighbouring chunks never share a pattern.
 */
const CHUNK_STYLES: { tone: Tone; pattern: "hatch" | "dots" | "backhatch" | "solid" }[] = [
  { tone: "chunk-1", pattern: "hatch" },
  { tone: "chunk-3", pattern: "dots" },
  { tone: "chunk-2", pattern: "backhatch" },
  { tone: "chunk-4", pattern: "solid" },
];
export const chunkTone = (i: number): Tone => CHUNK_STYLES[i % CHUNK_STYLES.length].tone;
export const chunkPattern = (i: number) => CHUNK_STYLES[i % CHUNK_STYLES.length].pattern;

export function ChunkPatterns() {
  const { px, uid } = usePageCamera();
  const s = px(6);
  return (
    <defs>
      {CHUNK_STYLES.map(({ tone, pattern }, i) => (
        <pattern key={tone} id={`${uid}-chunk-${i}`} patternUnits="userSpaceOnUse" width={s} height={s} patternTransform={pattern === "hatch" ? "rotate(45)" : pattern === "backhatch" ? "rotate(-45)" : undefined}>
          <rect width={s} height={s} fill={toneVar(tone)} opacity={pattern === "solid" ? 0.16 : 0.08} />
          {(pattern === "hatch" || pattern === "backhatch") && <line x1={0} y1={0} x2={0} y2={s} stroke={toneVar(tone)} strokeWidth={px(1.25)} opacity={0.6} />}
          {pattern === "dots" && <circle cx={s / 2} cy={s / 2} r={px(1.1)} fill={toneVar(tone)} opacity={0.75} />}
        </pattern>
      ))}
    </defs>
  );
}

export interface ChunkSlice {
  chunkId: string;
  /** tag text, e.g. "c-01 · 179 tokens" or "what-if #2 · 76 tokens" */
  label?: string;
  rects: Rect[];
}

/** Naive chunk "slices": where each chunk's characters sit on the page (dashed, patterned, labelled). */
export function ChunkSlices({ chunks, opacity = 1, emphasis, labels = true }: { chunks: ChunkSlice[]; opacity?: number; emphasis?: string[]; labels?: boolean }) {
  const { px, uid } = usePageCamera();
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity}>
      <ChunkPatterns />
      {chunks.map((c, i) => {
        const dim = emphasis && !emphasis.includes(c.chunkId);
        const first = c.rects[0];
        return (
          <g key={c.chunkId} opacity={dim ? 0.35 : 1}>
            {c.rects.map((r, j) => (
              <rect key={j} x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} fill={`url(#${uid}-chunk-${i % 4})`} stroke={toneVar(chunkTone(i))} strokeWidth={px(0.75)} strokeDasharray={`${px(3)} ${px(2)}`} />
            ))}
            {labels && first && <Tag x={first[0]} y={first[1]} text={c.label ?? c.chunkId} tone={chunkTone(i)} placement="above" mono filled />}
          </g>
        );
      })}
    </g>
  );
}

/**
 * Chunks at chunk granularity: one tinted slice per page segment (the union of the chunk's characters on that page,
 * `--chunk-n` at 10 % with a single 1 px outline), labelled once. Where a chunk continues on the next sheet, a dashed
 * bracket over the gutter joins its segments, so a chunk spanning a page break reads as one thing. For a contrast
 * frame (SC-3) where the claim is "one chunk", not "which characters": the X-ray's per-line `ChunkSlices` stays the
 * reading view.
 */
export function ChunkRegions({ chunks, opacity = 1, bracketY }: { chunks: { chunkId: string; label?: string; segments: Rect[] }[]; opacity?: number; bracketY?: number }) {
  const { px } = usePageCamera();
  if (opacity <= 0) return null;
  return (
    <g opacity={opacity}>
      {chunks.map((c, i) => {
        const color = toneVar(chunkTone(i));
        const segs = c.segments;
        const y = bracketY ?? Math.min(...segs.map((r) => r[1])) + px(24);
        return (
          <g key={c.chunkId} data-chunk={c.chunkId}>
            {segs.map((r, j) => (
              <rect key={j} x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} rx={px(3)} fill={color} fillOpacity={0.1} stroke={color} strokeWidth={px(1)} />
            ))}
            {/* one chunk across a page break: a dashed bracket over the gutter between its segments, labelled once */}
            {segs.slice(1).map((r, j) => {
              const a = segs[j];
              const t = px(5);
              const d = `M${a[2]} ${y - t} V${y + t} M${a[2]} ${y} H${r[0]} M${r[0]} ${y - t} V${y + t}`;
              return <path key={j} d={d} fill="none" stroke={color} strokeWidth={px(1.5)} strokeDasharray={`${px(4)} ${px(3)}`} />;
            })}
            {segs.length > 1 ? (
              <Tag x={(segs[0][2] + segs[1][0]) / 2} y={y - px(8)} text={c.label ?? c.chunkId} tone={chunkTone(i)} placement="above" align="center" mono filled />
            ) : (
              segs[0] && <Tag x={segs[0][0]} y={segs[0][1]} text={c.label ?? c.chunkId} tone={chunkTone(i)} placement="above" mono filled />
            )}
          </g>
        );
      })}
    </g>
  );
}

// --------------------------------------------------------------------------------------------- reading order

export interface PathStop {
  ordinal: number;
  kind: "label" | "value" | "other";
  rect: Rect;
}

/** Where a stop's ordinal badge sits: labels just left of the line, values further left with a leader, so a
 *  badge never reads as a digit of the amount it points at. */
const BADGE_GAP = { label: 12, value: 26, other: 10 } as const;

/** How many stops a run has reached at `progress` (index-paced: each line gets the same time). */
export function reachedStops(n: number, progress: number): number {
  return n === 0 ? 0 : 1 + Math.floor(clamp01(progress) * (n - 1) + 1e-6);
}

/** Control point of the gentle arc between two stops: bowed to the left of travel, so a hop along one row arcs
 *  over the text it would otherwise strike through (a value run reads 37 → 38 across the amount of 37). */
function bow(a: [number, number], b: [number, number], k = 0.18): [number, number] {
  const [dx, dy] = [b[0] - a[0], b[1] - a[1]];
  return [(a[0] + b[0]) / 2 + dy * k, (a[1] + b[1]) / 2 - dx * k];
}

/**
 * One run of the text layer's reading order: a path through each line in stream order, drawn up to `progress`
 * (index-paced), with an arrowhead at each segment's midpoint and ordinal badges as the path reaches them.
 * `variant` sets the weight: `label` thin and muted with hollow badges, `value` heavier with filled badges,
 * `all` (explorer) the neutral middle. `lead` is a stop the run starts from without a badge (the previous run's
 * last stop), so two runs read as one stream with a pause between them.
 */
export function ReadingPath({ stops, progress = 1, opacity = 1, emphasis, variant = "all", lead }: { stops: PathStop[]; progress?: number; opacity?: number; emphasis?: number[]; variant?: "label" | "value" | "all"; lead?: PathStop }) {
  const { px } = usePageCamera();
  const all = useMemo(() => (lead ? [lead, ...stops] : stops), [lead, stops]);
  const pts = useMemo<[number, number][]>(() => all.map((s) => [s.rect[0] - px(BADGE_GAP[s.kind]), (s.rect[1] + s.rect[3]) / 2]), [all, px]);
  if (opacity <= 0 || !pts.length) return null;
  const n = pts.length;
  const exact = clamp01(progress) * (n - 1);
  const whole = Math.floor(exact + 1e-6);
  const frac = exact - whole;
  // each hop is a quadratic arc; the hop being drawn is cut at `frac` (de Casteljau), so the tip moves smoothly
  const hops: { a: [number, number]; c: [number, number]; b: [number, number] }[] = [];
  for (let i = 0; i < Math.min(whole, n - 1); i++) hops.push({ a: pts[i], c: bow(pts[i], pts[i + 1]), b: pts[i + 1] });
  if (whole < n - 1 && frac > 0) {
    const [a, b] = [pts[whole], pts[whole + 1]];
    const c = bow(a, b);
    const c1: [number, number] = [a[0] + (c[0] - a[0]) * frac, a[1] + (c[1] - a[1]) * frac];
    const c2: [number, number] = [c[0] + (b[0] - c[0]) * frac, c[1] + (b[1] - c[1]) * frac];
    hops.push({ a, c: c1, b: [c1[0] + (c2[0] - c1[0]) * frac, c1[1] + (c2[1] - c1[1]) * frac] });
  }
  const style = {
    label: { stroke: "var(--text-2)", width: 1, alpha: 0.7 },
    value: { stroke: "var(--text)", width: 1.75, alpha: 0.9 },
    all: { stroke: "var(--text-2)", width: 1.25, alpha: 0.75 },
  }[variant];
  const d = hops.map((h, i) => `${i ? "" : `M${h.a[0]} ${h.a[1]} `}Q${h.c[0]} ${h.c[1]} ${h.b[0]} ${h.b[1]}`).join(" ");
  return (
    <g opacity={opacity}>
      {hops.length > 0 && <path d={d} fill="none" stroke={style.stroke} strokeOpacity={style.alpha} strokeWidth={px(style.width)} strokeLinejoin="round" strokeLinecap="round" />}
      {hops.map(({ a, c, b }, i) => {
        const len = Math.hypot(b[0] - a[0], b[1] - a[1]);
        if (len < px(36)) return null; // too short to carry an arrow without crowding the badges
        // the arc's midpoint and its tangent there (parallel to a → b for a quadratic)
        const mid: [number, number] = [0.25 * a[0] + 0.5 * c[0] + 0.25 * b[0], 0.25 * a[1] + 0.5 * c[1] + 0.25 * b[1]];
        const angle = (Math.atan2(b[1] - a[1], b[0] - a[0]) * 180) / Math.PI;
        return (
          <Screen key={i} x={mid[0]} y={mid[1]}>
            <path d="M-3.5 -3 L2.5 0 L-3.5 3" transform={`rotate(${angle})`} fill="none" stroke={style.stroke} strokeOpacity={style.alpha} strokeWidth={variant === "value" ? 1.75 : 1.25} strokeLinecap="round" strokeLinejoin="round" />
          </Screen>
        );
      })}
      {all.map((s, i) => {
        if (i > whole || (lead && i === 0)) return null;
        const [x, y] = pts[i];
        const dim = emphasis && !emphasis.includes(s.ordinal);
        if (s.kind === "other") return <Screen key={s.ordinal} x={x} y={y} opacity={dim ? 0.3 : 0.6}><circle r={2} fill="var(--text-2)" /></Screen>;
        const value = s.kind === "value";
        return (
          <Screen key={s.ordinal} x={x} y={y} opacity={dim ? 0.3 : 1}>
            {value && <line x1={9} x2={BADGE_GAP.value - 3} y1={0} y2={0} stroke="var(--text)" strokeWidth={1} />}
            <circle r={9} fill={value ? "var(--text)" : "var(--surface)"} stroke={value ? "var(--text)" : "var(--text-2)"} strokeWidth={1.25} />
            <text textAnchor="middle" y={4.2} fontSize={12} fontWeight={500} letterSpacing={-0.6} fill={value ? "var(--inverse)" : "var(--text-2)"} style={{ fontFamily: "var(--font-mono)" }}>
              {s.ordinal}
            </text>
          </Screen>
        );
      })}
    </g>
  );
}

/** A chunk boundary: a 2 px vertical cut at page point (x, y0..y1), pulsing once over `pulse` 0..1. */
export function CutLine({ x, y0, y1, pulse = 0, opacity = 1, label }: { x: number; y0: number; y1: number; pulse?: number; opacity?: number; label?: string }) {
  const { px } = usePageCamera();
  if (opacity <= 0) return null;
  const swell = Math.sin(Math.PI * clamp01(pulse));
  const ext = px(5);
  return (
    <g opacity={opacity}>
      <line x1={x} x2={x} y1={y0 - ext} y2={y1 + ext} stroke="var(--surface)" strokeWidth={px(5 + 4 * swell)} strokeLinecap="round" />
      <line x1={x} x2={x} y1={y0 - ext} y2={y1 + ext} stroke="var(--text)" strokeWidth={px(2 + 1.5 * swell)} strokeLinecap="round" />
      {label && <Tag x={x} y={y1 + ext} text={label} placement="below" align="end" mono />}
    </g>
  );
}

// --------------------------------------------------------------------------------------------- pairs

/** The point where the ray from the centre of `r` towards `p` leaves `r`, pushed `gap` further out. */
function edgePoint(r: Rect, p: [number, number], gap: number): [number, number] {
  const cx = (r[0] + r[2]) / 2;
  const cy = (r[1] + r[3]) / 2;
  const dx = p[0] - cx;
  const dy = p[1] - cy;
  const hw = (r[2] - r[0]) / 2;
  const hh = (r[3] - r[1]) / 2;
  const s = Math.min(dx ? hw / Math.abs(dx) : Infinity, dy ? hh / Math.abs(dy) : Infinity);
  const len = Math.hypot(dx, dy) || 1;
  return [cx + dx * s + (dx / len) * gap, cy + dy * s + (dy / len) * gap];
}

/**
 * A pairing arc from a label to a value. `cited`: solid green + ✓ (the value printed inside the field box).
 * `hypothesis`: dashed neutral, no mark (a derived position-only reading). `loss`: dashed pink + ×,
 * only once a logged answer shows the value was used. Anchors sit on the facing edges of the two boxes; the
 * curve is drawn up to `progress` (de Casteljau split) and the mark appears when the arc lands.
 */
export function PairArc({ from, to, tone, progress = 1, bend = 0.22, opacity = 1 }: { from: Rect; to: Rect; tone: "loss" | "cited" | "hypothesis" | "neutral"; progress?: number; bend?: number; opacity?: number }) {
  const { px } = usePageCamera();
  const fc: [number, number] = [(from[0] + from[2]) / 2, (from[1] + from[3]) / 2];
  const tc: [number, number] = [(to[0] + to[2]) / 2, (to[1] + to[3]) / 2];
  const a = edgePoint(from, tc, px(3));
  const b = edgePoint(to, fc, px(12));
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const c: [number, number] = [(a[0] + b[0]) / 2 + dy * bend, (a[1] + b[1]) / 2 - dx * bend];
  const t = clamp01(progress);
  if (t <= 0) return null;
  const p01: [number, number] = [a[0] + (c[0] - a[0]) * t, a[1] + (c[1] - a[1]) * t];
  const p12: [number, number] = [c[0] + (b[0] - c[0]) * t, c[1] + (b[1] - c[1]) * t];
  const end: [number, number] = [p01[0] + (p12[0] - p01[0]) * t, p01[1] + (p12[1] - p01[1]) * t];
  const color = tone === "cited" ? "var(--cited)" : tone === "loss" ? "var(--loss)" : "var(--text-2)";
  const d = `M${a[0]} ${a[1]} Q${p01[0]} ${p01[1]} ${end[0]} ${end[1]}`;
  return (
    <g opacity={opacity}>
      <path d={d} fill="none" stroke="rgb(255 255 255 / 0.9)" strokeWidth={px(4)} strokeLinecap="round" />
      <path d={d} fill="none" stroke={color} strokeWidth={px(tone === "hypothesis" ? 1.5 : 1.75)} strokeLinecap="round" strokeDasharray={tone === "cited" || tone === "neutral" ? undefined : `${px(5)} ${px(4)}`} />
      <Screen x={a[0]} y={a[1]}>
        <circle r={2.5} fill={color} />
      </Screen>
      {t >= 1 && (tone === "cited" || tone === "loss") && <Mark x={b[0]} y={b[1]} kind={tone} scale={0.9} />}
      {t >= 1 && tone === "hypothesis" && (
        <Screen x={b[0]} y={b[1]}>
          <circle r={3.5} fill="var(--surface)" stroke={color} strokeWidth={1.5} />
        </Screen>
      )}
    </g>
  );
}

// --------------------------------------------------------------------------------------------- emphasis

/** Dims everything outside `hole` (focus emphasis). */
export function Dim({ hole, page, opacity = 0.5 }: { hole: Rect; page: Rect; opacity?: number }) {
  if (opacity <= 0) return null;
  const [x0, y0, x1, y1] = page;
  const d = `M${x0} ${y0}H${x1}V${y1}H${x0}Z M${hole[0]} ${hole[1]}V${hole[3]}H${hole[2]}V${hole[1]}Z`;
  return <path d={d} fill="var(--bg)" fillRule="evenodd" opacity={opacity} />;
}

/** Highlight wash behind a text line (e.g. the Box 1 label). */
export function Highlight({ rect, tone = "text", opacity = 1 }: { rect: Rect; tone?: Tone; opacity?: number }) {
  const { px } = usePageCamera();
  const r = padRect(rect, px(2));
  return <rect x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} rx={px(2)} fill={toneVar(tone)} opacity={0.16 * opacity} stroke={toneVar(tone)} strokeOpacity={0.7 * opacity} strokeWidth={px(1)} />;
}

/** Rects that add nothing: duplicates, rects inside another one, and slivers narrower than 4 px on screen. */
export function distinctRegions(regions: Rect[], minPx: number): Rect[] {
  const tol = 1;
  const inside = (a: Rect, b: Rect) => a[0] >= b[0] - tol && a[1] >= b[1] - tol && a[2] <= b[2] + tol && a[3] <= b[3] + tol;
  const big = regions.filter((r) => r[2] - r[0] >= minPx && r[3] - r[1] >= minPx);
  return big.filter((r, i) => !big.some((o, j) => j !== i && inside(r, o) && (!inside(o, r) || j < i)));
}

/**
 * Held regions: the Gold render already fills them black; this draws them as a deliberate redaction (a neutral
 * 45° hatch over the slab, a 1 px dashed orange outline) with one "held · PII" label inside the top-left corner.
 */
export function HeldRegions({ regions, label = "held · PII", variant = "hatch" }: { regions: Rect[]; label?: string; variant?: "hatch" | "withheld" }) {
  const { px, uid } = usePageCamera();
  const shown = distinctRegions(regions, px(4));
  if (!shown.length) return null;
  const s = px(6);
  if (variant === "withheld")
    // SC-6's Gold page: withheld at a glance in both themes (a solid wash, a heavier dashed boundary)
    return (
      <g>
        {shown.map((r, i) => (
          <g key={i}>
            <rect x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} fill="rgb(0 0 0 / 0.18)" stroke="var(--boundary)" strokeWidth={px(1.5)} strokeDasharray={`${px(4)} ${px(3)}`} />
            <Tag x={r[0]} y={r[1]} text={label} tone="boundary" placement={r[3] - r[1] >= px(36) ? "inside" : "above"} />
          </g>
        ))}
      </g>
    );
  return (
    <g>
      <defs>
        <pattern id={`${uid}-held`} patternUnits="userSpaceOnUse" width={s} height={s} patternTransform="rotate(45)">
          <rect width={s} height={s} fill="var(--surface-3)" />
          <line x1={0} y1={0} x2={0} y2={s} stroke="var(--text-3)" strokeWidth={px(1)} />
        </pattern>
      </defs>
      {shown.map((r, i) => {
        const tall = r[3] - r[1] >= px(36);
        return (
          <g key={i}>
            <rect x={r[0]} y={r[1]} width={r[2] - r[0]} height={r[3] - r[1]} fill={`url(#${uid}-held)`} stroke="var(--boundary)" strokeWidth={px(1)} strokeDasharray={`${px(4)} ${px(3)}`} />
            <Tag x={r[0]} y={r[1]} text={label} tone="boundary" placement={tall ? "inside" : "above"} />
          </g>
        );
      })}
    </g>
  );
}

// --------------------------------------------------------------------------------------------- coordinates

/**
 * Measurement ticks at a region's corners, with the corner coordinates in PDF points (SC-9 "the pixels"). Solid
 * neutral strokes (dashes mean "no region / hypothesis" in this system): a 16 px tick outside each edge, in line
 * with it, so the eye reads the box's extent without guides across the page.
 */
export function Crosshair({ rect, opacity = 1, digits = 1 }: { rect: Rect; opacity?: number; digits?: number }) {
  const { px } = usePageCamera();
  if (opacity <= 0) return null;
  const [x0, y0, x1, y1] = rect;
  const t = px(16);
  const g = px(3);
  const line = { stroke: "var(--text-2)", strokeWidth: px(1) } as const;
  const f = (n: number) => n.toFixed(digits);
  return (
    <g opacity={opacity} aria-hidden>
      <g opacity={0.5}>
        {/* vertical edges extended up and down, horizontal edges extended left and right */}
        <line x1={x0} y1={y0 - g - t} x2={x0} y2={y0 - g} {...line} />
        <line x1={x1} y1={y0 - g - t} x2={x1} y2={y0 - g} {...line} />
        <line x1={x0} y1={y1 + g} x2={x0} y2={y1 + g + t} {...line} />
        <line x1={x1} y1={y1 + g} x2={x1} y2={y1 + g + t} {...line} />
        <line x1={x0 - g - t} y1={y0} x2={x0 - g} y2={y0} {...line} />
        <line x1={x0 - g - t} y1={y1} x2={x0 - g} y2={y1} {...line} />
        <line x1={x1 + g} y1={y0} x2={x1 + g + t} y2={y0} {...line} />
        <line x1={x1 + g} y1={y1} x2={x1 + g + t} y2={y1} {...line} />
      </g>
      <Tag x={x0} y={y0 - g - t} text={`(${f(x0)}, ${f(y0)})`} placement="above" align="end" mono tone="muted" />
      <Tag x={x1} y={y1 + g + t} text={`(${f(x1)}, ${f(y1)})`} placement="below" mono tone="muted" />
    </g>
  );
}

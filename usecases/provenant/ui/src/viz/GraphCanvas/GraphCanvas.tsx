/**
 * GraphCanvas: a small RDF graph as SVG on a deterministic dagre layout.
 *
 * - Nodes by kind: class (`--concept` left bar, CURIE), individual (`--surface-2`, its class below), literal (pill,
 *   mono), unit / field (a small `--boundary` corner: opens Trace), region (a page-crop thumbnail with a 1 px
 *   boundary outline).
 * - Edges: 1 px `--border-strong` with the predicate on a `--surface` chip, painted after every path so lines run
 *   under labels; edges with the same predicate into the same node share one chip (`fin:concept ×2`).
 * - The hop path: `hop.order` edges light up in `--agent`, each drawn (stroke-dashoffset) over its share of
 *   `hop.progress`; a node the drawn path reaches gets a 1 px `--agent` ring.
 *
 * Pure: the same (graph, hop, reveal) always draws the same frame. Text stays 12 px unless the figure is wider
 * than its box, where the whole SVG scales down. A visually hidden table lists every triple (the text alternative).
 * With `onOpen`, unit and field nodes are buttons, so the SVG is a labelled group (an `img` would hide them from
 * assistive tech); a static graph stays an `img`. Marker ids are per instance (`useId`).
 */
import { line, curveBasis } from "d3-shape";
import type { GraphNeighbourhood, GraphNode } from "@contracts/api";
import { type ReactNode, useId, useMemo } from "react";
import { cn } from "../../design";
import { splitCurie } from "../../lib/iris";
import { clamp01, seg } from "../geom";
import { type GraphLayout, type LaidNode, THUMB, edgeKey, layoutGraph } from "./layout";

export interface RegionThumb {
  href: string;
  /** the page's size in points, and the crop to show (PDF points, top-left) */
  pageWidth: number;
  pageHeight: number;
  crop: [number, number, number, number];
}

export interface GraphCanvasProps {
  graph: Pick<GraphNeighbourhood, "nodes" | "edges">;
  /** edges to light in order (edge keys `${s}|${p}|${o}`), drawn over `progress` 0..1 */
  hop?: { order: string[]; progress: number };
  /** 0..1: nodes and edges fade in (an entering graph) */
  reveal?: number;
  /** nodes outside this set dim (by IRI) */
  emphasis?: Set<string>;
  /** page crops for region nodes */
  regionThumb?: (n: GraphNode) => RegionThumb | undefined;
  /** unit and field nodes become buttons (open Trace) */
  onOpen?: (n: GraphNode) => void;
  ariaLabel: string;
  className?: string;
  /** layered direction: left to right (default) or top to bottom (wide labels) */
  rankdir?: "LR" | "TB";
  /** extra SVG drawn in the layout's coordinates (callouts) */
  children?: (layout: GraphLayout) => ReactNode;
}

const curve = line<[number, number]>().curve(curveBasis);
/** The smallest on-screen scale of the layout: 12 px labels render at ≥ 11 px. */
const MIN_SCALE = 11 / 12;

export function GraphCanvas({ graph, hop, reveal = 1, emphasis, regionThumb, onOpen, ariaLabel, className, rankdir = "LR", children }: GraphCanvasProps) {
  const layout = useMemo(() => layoutGraph(graph, { rankdir }), [graph, rankdir]);
  const order = hop?.order ?? [];
  const drawn = new Map<string, number>(order.map((k, i) => [k, hop ? seg(hop.progress, i / order.length, (i + 1) / order.length) : 0]));
  const reached = new Set<string>();
  for (const e of layout.edges) {
    const t = drawn.get(e.key) ?? 0;
    if (t > 0) reached.add(e.edge.s);
    if (t >= 1) reached.add(e.edge.o);
  }
  const fade = clamp01(reveal);
  // de-emphasis is a colour (text-3, ≥ 4.5:1 in both themes), never opacity: dimmed text stays legible
  const muted = (iri: string) => Boolean(emphasis && !emphasis.has(iri));
  const uid = useId().replace(/[^\w-]/g, "");
  const arrow = `gc-arrow-${uid}`;
  const arrowHop = `gc-arrow-hop-${uid}`;
  const interactive = Boolean(onOpen) && layout.nodes.some((n) => n.node.kind === "unit" || n.node.kind === "field");

  return (
    <figure className={cn("relative flex min-h-0 items-center overflow-auto", className)} style={{ justifyContent: "safe center" }}>
      <svg
        role={interactive ? "group" : "img"}
        aria-label={ariaLabel}
        width={layout.width}
        height={layout.height}
        viewBox={`0 0 ${layout.width} ${layout.height}`}
        className="block h-auto max-h-full max-w-full shrink-0"
        // never scale below MIN_SCALE: 12 px labels stay ≥ 11 px on screen (the figure scrolls instead)
        style={{ opacity: fade, minWidth: Math.round(layout.width * MIN_SCALE) }}
      >
        <defs>
          <marker id={arrow} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 L8 4 L0 8 z" fill="var(--border-strong)" />
          </marker>
          <marker id={arrowHop} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
            <path d="M0 0 L8 4 L0 8 z" fill="var(--agent)" />
          </marker>
        </defs>
        {layout.edges.map((e) => {
          const t = drawn.get(e.key) ?? 0;
          const d = curve(e.points) ?? "";
          return (
            <g key={e.key} data-edge={e.edge.label}>
              <path d={d} fill="none" stroke="var(--border-strong)" strokeWidth={1} markerEnd={`url(#${arrow})`} />
              {t > 0 && (
                <path d={d} fill="none" stroke="var(--agent)" strokeWidth={1.5} pathLength={100} strokeDasharray="100 100" strokeDashoffset={100 * (1 - t)} markerEnd={t >= 1 ? `url(#${arrowHop})` : undefined} data-hop="drawn" />
              )}
            </g>
          );
        })}
        {/* labels after every path: a line never runs over a predicate */}
        {layout.edges.map((e) =>
          e.label.text ? (
            <g key={`label-${e.key}`} transform={`translate(${e.label.x - e.label.w / 2} ${e.label.y - 10})`} data-edge-label={e.label.text}>
              <title>{e.edge.label}</title>
              <rect width={e.label.w} height={20} rx={10} fill="var(--surface)" stroke={(drawn.get(e.key) ?? 0) >= 1 ? "var(--agent)" : "var(--border)"} strokeWidth={1} />
              <text x={6} y={14} fontSize={12} fill={muted(e.edge.s) || muted(e.edge.o) ? "var(--text-3)" : "var(--text-2)"} style={{ fontFamily: "var(--font-mono)" }}>
                {e.label.text}
              </text>
            </g>
          ) : null,
        )}
        {layout.nodes.map((n) => (
          <Node key={n.node.iri} n={n} ring={reached.has(n.node.iri)} muted={muted(n.node.iri)} thumb={n.node.kind === "region" ? regionThumb?.(n.node) : undefined} onOpen={onOpen && (n.node.kind === "unit" || n.node.kind === "field") ? () => onOpen(n.node) : undefined} />
        ))}
        {children?.(layout)}
      </svg>
      <table className="sr-only">
        <caption>{ariaLabel}</caption>
        <tbody>
          {graph.edges.map((e) => (
            <tr key={edgeKey(e)}>
              <td>{graph.nodes.find((x) => x.iri === e.s)?.label ?? e.s}</td>
              <td>{e.label}</td>
              <td>{graph.nodes.find((x) => x.iri === e.o)?.label ?? e.o}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}

function Node({ n, ring, muted, thumb, onOpen }: { n: LaidNode; ring: boolean; muted: boolean; thumb?: RegionThumb; onOpen?: () => void }) {
  const ink = muted ? "var(--text-3)" : "var(--text)";
  const { kind } = n.node;
  const x = n.x - n.w / 2;
  const y = n.y - n.h / 2;
  const pill = kind === "literal";
  const fill = kind === "individual" ? "var(--surface-2)" : kind === "literal" ? "var(--bg)" : "var(--surface)";
  const [prefix, local] = kind === "class" ? splitCurie(n.title) : ["", n.title];
  const textX = x + (kind === "class" ? 14 : 10) + (kind === "region" ? THUMB.w + 8 : 0);
  const body = (
    <g data-node={kind} data-muted={muted || undefined}>
      {ring && <rect x={x - 3} y={y - 3} width={n.w + 6} height={n.h + 6} rx={pill ? (n.h + 6) / 2 : 8} fill="none" stroke="var(--agent)" strokeWidth={1} />}
      <rect x={x} y={y} width={n.w} height={n.h} rx={pill ? n.h / 2 : kind === "unit" || kind === "field" ? 3 : 6} fill={fill} stroke={kind === "region" ? "var(--boundary)" : "var(--border-strong)"} strokeWidth={1} />
      {kind === "class" && <rect x={x} y={y} width={3} height={n.h} rx={1.5} fill="var(--concept)" />}
      {(kind === "unit" || kind === "field") && <path d={`M${x + n.w - 9} ${y + 3} H${x + n.w - 3} V${y + 9}`} fill="none" stroke="var(--boundary)" strokeWidth={1.5} />}
      {thumb && <Thumb t={thumb} x={x + 6} y={y + 6} />}
      <text x={textX} y={n.subtitle || n.detail ? y + 18 : y + n.h / 2 + 4} fontSize={12} fontWeight={500} fill={ink} style={{ fontFamily: pill || kind === "class" ? "var(--font-mono)" : "var(--font-sans)" }}>
        {prefix && <tspan fill="var(--text-2)" fontWeight={400}>{prefix}:</tspan>}
        {local}
      </text>
      {n.detail && (
        <text x={textX} y={y + 34} fontSize={12} fill={ink} style={{ fontFamily: "var(--font-sans)" }}>
          {n.detail}
        </text>
      )}
      {n.subtitle && (
        <text x={textX} y={y + (n.detail ? 50 : 34)} fontSize={12} fill="var(--text-2)" style={{ fontFamily: "var(--font-mono)" }}>
          {n.subtitle}
        </text>
      )}
    </g>
  );
  if (!onOpen) return body;
  return (
    <g
      role="button"
      tabIndex={0}
      aria-label={`Open ${n.title}${n.detail ? ` (${n.detail})` : ""} in Trace`}
      className="gc-node cursor-pointer outline-none"
      onClick={onOpen}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <rect className="gc-focus" x={n.x - n.w / 2 - 4} y={n.y - n.h / 2 - 4} width={n.w + 8} height={n.h + 8} rx={8} fill="none" stroke="transparent" strokeWidth={2} />
      {body}
    </g>
  );
}

/** A page crop: the region with a 1 px boundary outline (the thumbnail is the region, so the outline is its frame). */
function Thumb({ t, x, y }: { t: RegionThumb; x: number; y: number }) {
  const [cx0, cy0, cx1, cy1] = t.crop;
  return (
    <svg x={x} y={y} width={THUMB.w} height={THUMB.h} viewBox={`${cx0} ${cy0} ${cx1 - cx0} ${cy1 - cy0}`} preserveAspectRatio="xMidYMid meet">
      <rect x={cx0} y={cy0} width={cx1 - cx0} height={cy1 - cy0} fill="#fff" />
      <image href={t.href} x={0} y={0} width={t.pageWidth} height={t.pageHeight} preserveAspectRatio="none" />
    </svg>
  );
}

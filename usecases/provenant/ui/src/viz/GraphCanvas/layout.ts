/**
 * Deterministic layered layout for GraphCanvas: dagre, left to right. The same graph always lays out
 * the same way (insertion order is the payload's order), so screenshots and scrubbed frames are stable. Sizes are
 * in CSS px: labels are measured at 12 px, the type scale's floor.
 */
import dagre from "@dagrejs/dagre";
import type { GraphEdge, GraphNode } from "@contracts/api";
import { curie } from "../../lib/iris";
import { textWidth } from "../measure";

export interface LaidNode {
  node: GraphNode;
  /** centre */
  x: number;
  y: number;
  w: number;
  h: number;
  /** the node's own name (`fin:CouponRate`, `field f-box1`, `6`) */
  title: string;
  /** the document a unit or field belongs to, when two nodes would otherwise read the same (`w2-2025-maya-fictus`) */
  detail?: string;
  /** the last line: the class the node was matched as (individuals, units, fields) */
  subtitle?: string;
}

export interface LaidEdge {
  edge: GraphEdge;
  /** `${s}|${p}|${o}` */
  key: string;
  points: [number, number][];
  /** `text` is empty on an edge whose label another edge carries (same predicate into the same node: `×n`) */
  label: { x: number; y: number; w: number; text: string };
}

export interface GraphLayout {
  nodes: LaidNode[];
  edges: LaidEdge[];
  width: number;
  height: number;
}

const FS = 12;
const PAD_X = 10;
const LINE = 16;
/** region nodes draw a page-crop thumbnail beside their label */
export const THUMB = { w: 56, h: 36 } as const;

export const edgeKey = (e: Pick<GraphEdge, "s" | "p" | "o">) => `${e.s}|${e.p}|${e.o}`;

function sizeOf(n: GraphNode): Pick<LaidNode, "w" | "h" | "title" | "detail" | "subtitle"> {
  // the API qualifies a colliding unit/field label with its document ("field f-box1 · w2-2025-maya-fictus")
  const [title, detail] = n.kind === "unit" || n.kind === "field" ? (n.label.split(" · ") as [string, string?]) : [n.label];
  const subtitle = n.kind === "literal" || n.kind === "class" ? undefined : n.types.map(curie).join(", ") || undefined;
  const mono = n.kind === "literal" || n.kind === "class";
  const tw = Math.max(textWidth(title, FS, 500, mono), detail ? textWidth(detail, FS, 400) : 0, subtitle ? textWidth(subtitle, FS, 400, true) : 0);
  if (n.kind === "region") return { title, detail, subtitle, w: Math.ceil(tw) + PAD_X * 2 + THUMB.w + 8, h: THUMB.h + 12 };
  const lines = 1 + (detail ? 1 : 0) + (subtitle ? 1 : 0);
  return { title, detail, subtitle, w: Math.ceil(tw) + PAD_X * 2 + (n.kind === "class" ? 4 : 0), h: LINE * lines + 10 };
}

/** Longest edge label drawn as is; a longer one (a gloss such as "SPARQL template that computes this quantity") is
 *  drawn as its predicate's name (`computedBy`), the gloss kept for the tooltip. */
export const MAX_EDGE_LABEL = 24;

/** The text an edge draws: its label, or its predicate's local name when the label is a long gloss. */
export function edgeText(e: Pick<GraphEdge, "p" | "label">): string {
  if (e.label.length <= MAX_EDGE_LABEL) return e.label;
  return e.p.split(/[#/:]/).filter(Boolean).at(-1) ?? e.label;
}

/** Edges with the same predicate into the same node share one label (`fin:concept ×2`), drawn on the first. */
function sharedLabels(edges: GraphEdge[]): Map<GraphEdge, string> {
  const groups = new Map<string, GraphEdge[]>();
  for (const e of edges) {
    const k = `${e.p}|${e.o}`;
    groups.set(k, [...(groups.get(k) ?? []), e]);
  }
  const out = new Map<GraphEdge, string>();
  for (const g of groups.values()) g.forEach((e, i) => out.set(e, i === 0 ? (g.length > 1 ? `${edgeText(e)} ×${g.length}` : edgeText(e)) : ""));
  return out;
}

export function layoutGraph(g: { nodes: GraphNode[]; edges: GraphEdge[] }, opts: { rankdir?: "LR" | "TB"; nodesep?: number; ranksep?: number } = {}): GraphLayout {
  const d = new dagre.graphlib.Graph({ multigraph: true });
  d.setGraph({ rankdir: opts.rankdir ?? "LR", nodesep: opts.nodesep ?? 12, ranksep: opts.ranksep ?? 56, edgesep: 10, marginx: 8, marginy: 8 });
  d.setDefaultEdgeLabel(() => ({}));
  const sized = new Map(g.nodes.map((n) => [n.iri, sizeOf(n)]));
  for (const n of g.nodes) {
    const s = sized.get(n.iri)!;
    d.setNode(n.iri, { width: s.w, height: s.h });
  }
  const edges = g.edges.filter((e) => sized.has(e.s) && sized.has(e.o));
  const labels = sharedLabels(edges);
  for (const e of edges) {
    const text = labels.get(e) ?? edgeText(e);
    const lw = text ? Math.ceil(textWidth(text, FS, 400, true)) + 12 : 0;
    d.setEdge(e.s, e.o, { width: lw, height: text ? 20 : 0, labelpos: "c" }, edgeKey(e));
  }
  dagre.layout(d);
  const nodes: LaidNode[] = g.nodes.map((n) => {
    const p = d.node(n.iri);
    return { node: n, x: p.x, y: p.y, ...sized.get(n.iri)! };
  });
  const laidEdges: LaidEdge[] = edges.map((e) => {
    const p = d.edge({ v: e.s, w: e.o, name: edgeKey(e) }) as { points: { x: number; y: number }[]; x: number; y: number; width: number };
    return { edge: e, key: edgeKey(e), points: p.points.map((q) => [q.x, q.y]), label: { x: p.x, y: p.y, w: p.width, text: labels.get(e) ?? edgeText(e) } };
  });
  const gl = d.graph();
  return { nodes, edges: laidEdges, width: Math.ceil(gl.width ?? 0), height: Math.ceil(gl.height ?? 0) };
}

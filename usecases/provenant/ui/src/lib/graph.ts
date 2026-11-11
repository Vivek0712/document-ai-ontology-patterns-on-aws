/**
 * Graph display helpers (no layout code, so screens can use them without loading dagre).
 */
import type { GraphNeighbourhood } from "@contracts/api";

/** Literal labels longer than this (a definition, a comment) would force the whole graph to scale below 12 px. */
export const LONG_LITERAL = 48;

/**
 * The graph without long literal nodes and their edges, and how many it left out. Callers say so on screen and show
 * the text elsewhere (the term page, the triple list), so nothing is hidden silently.
 */
export function withoutLongLiterals<G extends Pick<GraphNeighbourhood, "nodes" | "edges">>(g: G, max = LONG_LITERAL): { graph: G; hidden: number } {
  const long = new Set(g.nodes.filter((n) => n.kind === "literal" && n.label.length > max).map((n) => n.iri));
  return { graph: { ...g, nodes: g.nodes.filter((n) => !long.has(n.iri)), edges: g.edges.filter((e) => !long.has(e.o) && !long.has(e.s)) }, hidden: long.size };
}

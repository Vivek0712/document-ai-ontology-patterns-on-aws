/**
 * W-11 Graph: the TBox and the package's ABox around any IRI (`GET /graph/neighbourhood`, template
 * `neighbourhood_v1`, depth 1 or 2), or the path a logged `graph_hop` call bound (`?run=&call=`). Left: the query;
 * centre: GraphCanvas (deterministic dagre layout); bottom: the read-only SPARQL that ran. The naive lane binds no
 * ontology and says so. URL: /knowledge/graph?focus=fin:W2WagesBox1&depth=1|2&pv=2.3.1|1.0&run=&call=
 */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useApi, useMe } from "../../api/context";
import { RpApiError } from "../../api/types";
import { Page, PageHeader, PlayExplainer } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import { Badge, Button, Card, CardHeader, CodeBlock, EmptyState, LaneBadge, Num, ProvenanceScope, ProvenanceTag, SegmentedControl, Skeleton } from "../../design";
import { useSearchParam } from "../../hooks/useSearchParam";
import { withoutLongLiterals } from "../../lib/graph";
import { curie } from "../../lib/iris";
import { GraphCanvas, edgeKey } from "../../viz/GraphCanvas";

const DEFAULT_FOCUS = "fin:W2WagesBox1";
/** Past this many nodes the canvas shows the first ones and says how many it left out. */
const MAX_NODES = 150;

export default function GraphScreen() {
  const api = useApi();
  const { sub } = useMe();
  const { packageId } = useWorkspace();
  const [focus, setFocus] = useSearchParam("focus", DEFAULT_FOCUS);
  const [depth, setDepth] = useSearchParam("depth", "1");
  const [pv, setPv] = useSearchParam("pv", "2.3.1");
  const [runId] = useSearchParam("run");
  const [callId] = useSearchParam("call");
  const [draft, setDraft] = useState(focus);
  const fin = useQuery({ queryKey: ["ontology-classes", "fin"], queryFn: () => api.listOntologyClasses("fin"), staleTime: Infinity });
  const replay = Boolean(runId && callId);
  const g = useQuery({
    queryKey: ["graph", sub, packageId, focus, depth, pv, runId, callId],
    queryFn: () => (replay ? api.getCallPath(runId, callId) : api.getGraphNeighbourhood(focus, packageId, depth === "2" ? 2 : 1, pv)),
    retry: false,
  });
  const nodes = g.data?.nodes ?? [];
  const capped = g.data && nodes.length > MAX_NODES ? { ...g.data, nodes: nodes.slice(0, MAX_NODES), edges: g.data.edges.filter((e) => nodes.slice(0, MAX_NODES).some((n) => n.iri === e.s) && nodes.slice(0, MAX_NODES).some((n) => n.iri === e.o)) } : g.data;
  // long literals (definitions) would shrink every label below 12 px: they are listed under the canvas instead
  const legible = capped && withoutLongLiterals(capped);
  const shown = legible?.graph;
  const longLiterals = capped?.nodes.filter((n) => n.kind === "literal" && !shown?.nodes.includes(n)) ?? [];
  return (
    <Page wide className="max-w-[1600px]">
      <PageHeader
        crumbs={[{ label: "Knowledge" }, { label: "Graph" }]}
        title="Graph"
        description="The ontology and the facts written from page regions, around one IRI. Read-only: every edge is a triple in Amazon Neptune."
        actions={<PlayExplainer showcaseId="sc-5" />}
      />
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[260px_minmax(0,1fr)]">
        <Card className="flex flex-col gap-4 p-4 lg:self-start">
          <form
            className="flex flex-col gap-1.5"
            onSubmit={(e) => {
              e.preventDefault();
              setFocus(draft.trim() || DEFAULT_FOCUS);
            }}
          >
            <label htmlFor="focus" className="text-caps uppercase text-text-2">
              Focus IRI
            </label>
            <input id="focus" list="fin-terms" value={draft} onChange={(e) => setDraft(e.target.value)} className="h-8 rounded-sm border border-border-strong bg-surface px-2 font-mono text-mono-sm text-text" />
            <datalist id="fin-terms">
              {(fin.data?.items ?? []).map((t) => (
                <option key={t.iri} value={t.curie} />
              ))}
            </datalist>
            <Button size="sm" variant="secondary" type="submit" className="self-start">
              Show
            </Button>
          </form>
          <div className="flex flex-col gap-1.5">
            <span className="text-caps uppercase text-text-2">Depth</span>
            <SegmentedControl aria-label="Depth" size="sm" value={depth} onValueChange={setDepth} items={[{ value: "1", label: "1 hop" }, { value: "2", label: "2 hops" }]} />
          </div>
          <div className="flex flex-col gap-1.5">
            <span className="text-caps uppercase text-text-2">Graphs</span>
            <SegmentedControl
              aria-label="Lane"
              size="sm"
              value={pv}
              onValueChange={setPv}
              items={[
                { value: "2.3.1", label: "TBox + ABox 2.3.1" },
                { value: "1.0", label: "1.0" },
              ]}
            />
            <span className="text-xs text-text-2">
              ABox of <span className="font-mono">{packageId}</span>, the lanes your token can read.
            </span>
          </div>
          {replay && (
            <p className="rounded-sm border border-border px-2.5 py-2 text-xs text-text-2">
              Showing the path a logged graph_hop bound: run <span className="font-mono">{runId}</span>
            </p>
          )}
        </Card>
        <div className="flex min-w-0 flex-col gap-3">
          <Card className="flex flex-col">
            <CardHeader
              title={<span className="font-mono text-mono-md">{replay ? `${runId} · ${callId}` : curie(focus)}</span>}
              meta={
                g.data && (
                  <span className="flex items-center gap-2">
                    <ProvenanceScope provenance={api.mode === "http" ? "measured" : "local-run"} source="the neighbourhood's nodes and edges">
                      <Num value={nodes.length} kind="int" bare /> nodes · <Num value={g.data.edges.length} kind="int" bare /> edges
                    </ProvenanceScope>
                    {(g.data.truncated || nodes.length > MAX_NODES) && <Badge tone="neutral">truncated to {MAX_NODES}</Badge>}
                  </span>
                )
              }
              actions={<ProvenanceTag provenance={api.mode === "http" ? "measured" : "local-run"} source={g.data?.source === "logged-rows" ? "the call's logged rows" : "neighbourhood_v1 over the built graph (pyoxigraph locally, Amazon Neptune on AWS)"} />}
            />
            <div className="flex min-h-[240px] flex-1 flex-col p-3" data-testid="graph-canvas">
              {g.error ? (
                <EmptyState title={g.error instanceof RpApiError && g.error.status === 404 ? "No neighbourhood for this IRI here" : "The graph could not load"}>
                  {g.error instanceof Error ? g.error.message : String(g.error)}
                </EmptyState>
              ) : !shown ? (
                <Skeleton className="h-full rounded-md" />
              ) : pv === "1.0" && !replay ? (
                <EmptyState title="1.0 has no ontology binding">
                  <span className="flex items-center gap-1.5">
                    <LaneBadge pv="1.0" /> writes no graph: its chunks carry no concepts, so there is nothing to traverse.
                  </span>
                </EmptyState>
              ) : shown.nodes.length === 0 ? (
                <EmptyState title="Nothing around this IRI" />
              ) : (
                <GraphCanvas
                  graph={shown}
                  hop={replay ? { order: shown.edges.map(edgeKey), progress: 1 } : undefined}
                  emphasis={new Set([shown.focus, ...shown.edges.filter((e) => e.s === shown.focus || e.o === shown.focus).flatMap((e) => [e.s, e.o])])}
                  ariaLabel={`Graph around ${curie(focus)}`}
                />
              )}
            </div>
          </Card>
          {longLiterals.length > 0 && (
            <Card>
              <CardHeader title="Long literals" meta="left out of the drawing so labels stay legible" />
              <ul className="flex flex-col gap-1 px-4 py-3 text-sm">
                {longLiterals.map((n) => {
                  const e = capped?.edges.find((x) => x.o === n.iri);
                  return (
                    <li key={n.iri} className="grid grid-cols-[160px_minmax(0,1fr)] gap-3">
                      <span className="font-mono text-mono-sm text-text-2">{e?.label ?? ""}</span>
                      <span className="text-text">{n.label}</span>
                    </li>
                  );
                })}
              </ul>
            </Card>
          )}
          {g.data?.sparql && <CodeBlock title={`SPARQL · ${g.data.template} (read-only)`} code={g.data.sparql} maxHeight={260} />}
        </div>
      </div>
    </Page>
  );
}

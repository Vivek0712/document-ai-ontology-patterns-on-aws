/**
 * W-09 Ontology explorer: the document, structure and domain ontologies as loaded into the graph.
 * Left: modules with their terms; right: one term (labels with their triples, definition, hierarchy, properties,
 * fields that use it per package) and, below it, its neighbourhood in the workspace package's graph (depth 1). A second tab
 * shows the "schema first" view: the designed TBox modules and the ABox each package and lane populated.
 * Editing a triple is not offered. URL: /knowledge/ontology?iri=fin:W2WagesBox1&tab=explorer|schema
 */
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { OntologyTerm, OntologyTermList } from "@contracts/api";
import { useApi, useMe } from "../../api/context";
import { RpApiError } from "../../api/types";
import { Page, PageHeader, PlayExplainer } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import { Badge, Card, CardHeader, EmptyState, ErrorState, LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, Skeleton, Tabs, TabsContent, TabsList, TabsTrigger, cn } from "../../design";
import { useSearchParam } from "../../hooks/useSearchParam";
import { withoutLongLiterals } from "../../lib/graph";
import { GraphCanvas } from "../../viz/GraphCanvas";

const DEFAULT_TERM = "fin:QualifyingIncome";
const KIND_LABEL: Record<OntologyTerm["kind"], string> = {
  class: "class",
  concept: "concept",
  object_property: "object property",
  datatype_property: "datatype property",
  annotation_property: "annotation property",
  individual: "individual",
};

export default function OntologyScreen() {
  const api = useApi();
  const [iri, setIri] = useSearchParam("iri", DEFAULT_TERM);
  const [tab, setTab] = useSearchParam("tab", "explorer");
  const classes = useQuery({ queryKey: ["ontology-classes"], queryFn: () => api.listOntologyClasses(), staleTime: Infinity });
  return (
    <Page wide className="max-w-[1600px]">
      <PageHeader
        crumbs={[{ label: "Knowledge" }, { label: "Ontology" }]}
        title="Ontology explorer"
        description="Document types, structure roles and domain concepts as loaded into the graph. Each label shows the triple that states it."
        meta={classes.data && <Badge tone="neutral">version {classes.data.ontologyVersion}</Badge>}
        actions={<PlayExplainer showcaseId="sc-3" />}
      />
      {classes.error ? (
        <ErrorState title="The ontology could not load" message={String(classes.error instanceof Error ? classes.error.message : classes.error)} code={classes.error instanceof RpApiError ? classes.error.code : undefined} />
      ) : !classes.data ? (
        <Skeleton className="h-[560px] rounded-md" />
      ) : (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="explorer">Explorer</TabsTrigger>
            <TabsTrigger value="schema">Schema first</TabsTrigger>
          </TabsList>
          <TabsContent value="explorer">
            <div className="grid grid-cols-1 gap-3 lg:grid-cols-[260px_minmax(0,1fr)]">
              <ModuleTree list={classes.data} selected={iri} onSelect={setIri} />
              <div className="flex min-w-0 flex-col gap-3">
                <TermPage iri={iri} />
                <Neighbourhood iri={iri} />
              </div>
            </div>
          </TabsContent>
          <TabsContent value="schema">
            <SchemaFirst list={classes.data} />
          </TabsContent>
        </Tabs>
      )}
    </Page>
  );
}

/** Modules with term counts; the selected term's module is open. A filter narrows every module at once. */
function ModuleTree({ list, selected, onSelect }: { list: OntologyTermList; selected: string; onSelect: (iri: string) => void }) {
  const [filter, setFilter] = useState("");
  const selectedModule = list.items.find((t) => t.curie === selected || t.iri === selected)?.module;
  const [open, setOpen] = useState<string | undefined>(selectedModule ?? "fin");
  const f = filter.trim().toLowerCase();
  const byModule = useMemo(() => {
    const m = new Map<string, OntologyTerm[]>();
    for (const t of list.items) {
      if (f && !(t.curie.toLowerCase().includes(f) || t.label?.toLowerCase().includes(f) || t.labels.some((l) => l.value.toLowerCase().includes(f)))) continue;
      m.set(t.module, [...(m.get(t.module) ?? []), t]);
    }
    for (const v of m.values()) v.sort((a, b) => a.curie.localeCompare(b.curie));
    return m;
  }, [list.items, f]);
  return (
    <Card className="flex max-h-[calc(100vh-220px)] min-h-[360px] flex-col overflow-hidden">
      <label className="flex items-center gap-2 border-b border-border px-3 py-2">
        <Search className="size-3.5 text-text-3" aria-hidden />
        <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter by label or CURIE" className="h-6 min-w-0 flex-1 bg-transparent text-sm text-text placeholder:text-text-3 focus:outline-none" aria-label="Filter terms" />
      </label>
      <ul className="min-h-0 flex-1 overflow-y-auto py-1" aria-label="Ontology modules">
        {list.modules.map((m) => {
          const terms = byModule.get(m.module) ?? [];
          const isOpen = open === m.module || Boolean(f);
          return (
            <li key={m.module}>
              <button type="button" onClick={() => setOpen(isOpen && !f ? undefined : m.module)} className="flex w-full items-center gap-1.5 px-3 py-1 text-left text-sm text-text hover:bg-surface-2" aria-expanded={isOpen}>
                <ChevronRight className={cn("size-3.5 text-text-3 transition-transform duration-fast", isOpen && "rotate-90")} aria-hidden />
                <span className="font-mono text-mono-sm">{m.module}:</span>
                <span className="ml-auto text-xs text-text-2">{terms.length}</span>
              </button>
              {isOpen && (
                <ul>
                  {terms.slice(0, 400).map((t) => (
                    <li key={t.iri}>
                      <button
                        type="button"
                        onClick={() => onSelect(t.curie)}
                        className={cn("flex w-full items-center gap-2 py-0.5 pl-8 pr-3 text-left text-xs hover:bg-surface-2", t.curie === selected ? "bg-surface-2 text-text" : "text-text-2")}
                        aria-current={t.curie === selected ? "true" : undefined}
                      >
                        <span className={cn("size-1.5 shrink-0 rounded-full", t.kind === "class" || t.kind === "concept" ? "bg-concept" : "bg-text-3")} aria-hidden />
                        <span className="truncate font-mono text-mono-sm">{t.curie.split(":").slice(1).join(":")}</span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

const termLink = (c: string) => `/knowledge/ontology?iri=${encodeURIComponent(c)}`;

function Curies({ items }: { items: string[] }) {
  if (!items.length) return <span className="text-text-3">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((c) => (
        <Link key={c} to={termLink(c)} className="rounded-xs border border-border px-1.5 font-mono text-mono-sm text-text hover:bg-surface-2">
          {c}
        </Link>
      ))}
    </span>
  );
}

function TermPage({ iri }: { iri: string }) {
  const api = useApi();
  const { sub } = useMe();
  const term = useQuery({ queryKey: ["ontology-term", sub, iri], queryFn: () => api.getOntologyTerm(iri) });
  if (term.error) return <ErrorState title={`No term ${iri}`} message={term.error instanceof Error ? term.error.message : String(term.error)} />;
  if (!term.data) return <Skeleton className="h-[480px] rounded-md" />;
  const t = term.data;
  const props = Object.entries(t.properties);
  return (
    <ProvenanceScope provenance={api.mode === "http" ? "measured" : "local-run"} source="GET /ontology/terms (the built TBox, tbox.json)">
      <Card data-testid="term-page">
        <CardHeader
          title={
            <span className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-mono-md">{t.curie}</span>
              <Badge tone="concept" dot>
                {KIND_LABEL[t.kind]}
              </Badge>
            </span>
          }
          meta={<span className="font-mono">{t.graph}</span>}
        />
        <div className="flex flex-col gap-4 p-4">
          {t.label && <p className="text-md text-text">{t.label}</p>}
          {t.definition && <p className="max-w-3xl text-sm text-text-2">{t.definition}</p>}
          <section>
            <h3 className="mb-1.5 text-caps uppercase text-text-2">Labels</h3>
            <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
              {t.labels.map((l) => (
                <li key={`${l.scheme}-${l.value}`} className="grid grid-cols-[minmax(0,1fr)_96px] items-center gap-3 px-3 py-1.5 sm:grid-cols-[220px_96px_minmax(0,1fr)]">
                  <span className="text-sm text-text">{l.value}</span>
                  <Badge tone="neutral" className="justify-self-start">{l.scheme}</Badge>
                  <span className="col-span-2 truncate font-mono text-mono-sm text-text-2 sm:col-span-1" title={l.triple}>
                    {l.triple}
                  </span>
                </li>
              ))}
              {!t.labels.length && <li className="px-3 py-1.5 text-sm text-text-3">No labels.</li>}
            </ul>
          </section>
          <dl className="grid grid-cols-[120px_minmax(0,1fr)] gap-x-3 gap-y-2 text-sm">
            <dt className="text-text-2">subClassOf</dt>
            <dd>
              <Curies items={t.subClassOf} />
            </dd>
            <dt className="text-text-2">broader</dt>
            <dd>
              <Curies items={t.broader} />
            </dd>
            {(t.domain.length > 0 || t.range.length > 0) && (
              <>
                <dt className="text-text-2">domain · range</dt>
                <dd className="flex flex-wrap items-center gap-2">
                  <Curies items={t.domain} /> <span className="text-text-3">→</span> <Curies items={t.range} />
                </dd>
              </>
            )}
            {props.map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="truncate font-mono text-mono-sm text-text-2">{k}</dt>
                <dd className="font-mono text-mono-sm text-text">{v.join(", ")}</dd>
              </div>
            ))}
          </dl>
          <section>
            <h3 className="mb-1.5 flex items-center gap-2 text-caps uppercase text-text-2">
              Used by <ScopeTag />
            </h3>
            {t.usedBy && Object.keys(t.usedBy).length > 0 ? (
              <ul className="flex flex-wrap gap-2">
                {Object.entries(t.usedBy).map(([pkg, n]) => (
                  <li key={pkg} className="flex items-baseline gap-1.5 rounded-sm border border-border px-2.5 py-1 text-xs text-text-2">
                    <span className="font-mono">{pkg}</span> <Num value={n} kind="int" size="xs" /> fields you can read
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-text-2">No field in a package you can read carries this term.</p>
            )}
          </section>
        </div>
      </Card>
    </ProvenanceScope>
  );
}

/** The term's neighbourhood in the workspace package's evidence-units graph (depth 1). */
function Neighbourhood({ iri }: { iri: string }) {
  const api = useApi();
  const { sub } = useMe();
  const { packageId } = useWorkspace();
  const g = useQuery({ queryKey: ["neighbourhood", sub, packageId, iri, 1], queryFn: () => api.getGraphNeighbourhood(iri, packageId, 1), retry: false });
  return (
    <Card className="flex min-h-[420px] flex-col">
      <CardHeader
        title="Neighbourhood"
        meta={
          <span className="flex items-center gap-1.5">
            <LaneBadge pv="2.3.1" /> depth 1
          </span>
        }
        actions={
          <Link to={`/knowledge/graph?focus=${encodeURIComponent(iri)}`} className="text-xs text-text-2 hover:text-text">
            Open in Graph
          </Link>
        }
      />
      <div className="h-[380px] p-2">
        {g.error ? (
          <EmptyState title="No neighbourhood for this term here">
            {g.error instanceof RpApiError && g.error.status === 404 ? `${packageId} has no exported neighbourhood for ${iri}.` : g.error instanceof Error ? g.error.message : String(g.error)}
          </EmptyState>
        ) : !g.data ? (
          <Skeleton className="h-full rounded-md" />
        ) : g.data.nodes.length === 0 ? (
          <EmptyState title="Nothing around this term in this lane" />
        ) : (
          <LegibleGraph graph={g.data} label={`Neighbourhood of ${iri}`} />
        )}
      </div>
    </Card>
  );
}

/** A neighbourhood at a readable size: long literals (the definition, shown on the term page) are left out, and counted. */
function LegibleGraph({ graph, label }: { graph: Parameters<typeof GraphCanvas>[0]["graph"]; label: string }) {
  const { graph: g, hidden } = withoutLongLiterals(graph);
  return (
    <div className="flex h-full flex-col">
      <div className="min-h-0 flex-1">
        <GraphCanvas graph={g} ariaLabel={label} rankdir="LR" />
      </div>
      {hidden > 0 && <p className="px-2 pt-1 text-xs text-text-2">{hidden} long literal{hidden === 1 ? "" : "s"} left out of the drawing (shown on the term page).</p>}
    </div>
  );
}

/** Slide 24: the designed TBox modules, and the ABox each package and lane populated from page regions. */
function SchemaFirst({ list }: { list: OntologyTermList }) {
  const api = useApi();
  const { sub } = useMe();
  const status = useQuery({
    queryKey: ["schema-first", sub],
    queryFn: async () => {
      const { items } = await api.listPackages();
      return Promise.all(items.map(async (p) => ({ pkg: p, status: await api.packageStatus(p.packageId) })));
    },
  });
  const provenance = api.mode === "http" ? "measured" : "local-run";
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <Card>
        <CardHeader title="Designed: TBox modules" meta={<span className="font-mono">{list.source}</span>} actions={<ProvenanceTag provenance="derived" source="counted from tbox.json (authored in Git, built to N-Quads)" />} />
        <table className="w-full text-sm">
          <thead className="bg-bg-subtle text-left text-caps uppercase text-text-2">
            <tr>
              <th className="px-4 py-1.5 font-medium">Module</th>
              <th className="px-4 py-1.5 font-medium">Graph</th>
              <th className="px-4 py-1.5 text-right font-medium">Classes</th>
              <th className="px-4 py-1.5 text-right font-medium">Concepts</th>
              <th className="px-4 py-1.5 text-right font-medium">Properties</th>
            </tr>
          </thead>
          <tbody>
            <ProvenanceScope provenance="derived" source="counted from tbox.json">
              {list.modules.map((m) => (
                <tr key={m.module} className="border-t border-border">
                  <td className="px-4 py-1.5 font-mono text-mono-sm text-text">{m.module}:</td>
                  <td className="px-4 py-1.5 font-mono text-mono-sm text-text-2">{m.graph}</td>
                  <td className="px-4 py-1.5 text-right">
                    <Num value={m.classes} kind="int" />
                  </td>
                  <td className="px-4 py-1.5 text-right">
                    <Num value={m.concepts} kind="int" />
                  </td>
                  <td className="px-4 py-1.5 text-right">
                    <Num value={m.properties} kind="int" />
                  </td>
                </tr>
              ))}
            </ProvenanceScope>
          </tbody>
        </table>
      </Card>
      <Card>
        <CardHeader title="Populated: ABox per package and lane" actions={<ProvenanceTag provenance={provenance} source="GET /packages/{id}/status counters" />} />
        {!status.data ? (
          <Skeleton className="m-4 h-40" />
        ) : (
          <ProvenanceScope provenance={provenance}>
            <table className="w-full text-sm">
              <thead className="bg-bg-subtle text-left text-caps uppercase text-text-2">
                <tr>
                  <th className="px-4 py-1.5 font-medium">Package</th>
                  <th className="px-4 py-1.5 font-medium">Lane</th>
                  <th className="px-4 py-1.5 text-right font-medium">Units</th>
                  <th className="px-4 py-1.5 text-right font-medium">Triples</th>
                </tr>
              </thead>
              <tbody>
                {status.data.flatMap(({ pkg, status: st }) =>
                  Object.entries(st.counters).map(([pv, c]) => (
                    <tr key={`${pkg.packageId}-${pv}`} className="border-t border-border">
                      <td className="px-4 py-1.5 text-text">{pkg.name}</td>
                      <td className="px-4 py-1.5">
                        <LaneBadge pv={pv} />
                      </td>
                      <td className="px-4 py-1.5 text-right">
                        <Num value={c.units} kind="int" />
                      </td>
                      <td className="px-4 py-1.5 text-right">{pv === "1.0" ? <span className="text-xs text-text-2">no binding</span> : <Num value={c.triples} kind="int" />}</td>
                    </tr>
                  )),
                )}
              </tbody>
            </table>
          </ProvenanceScope>
        )}
        <p className="border-t border-border px-4 py-2 text-xs text-text-2">
          Designed schema, populated from page regions. No LLM-extracted graph is built here: graph_write writes each fact from a typed field by a deterministic rule (ingest_lib/rdf.py). <ProvenanceTag provenance="illustrative" source="schematic note: no values" />
        </p>
      </Card>
    </div>
  );
}

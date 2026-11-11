/**
 * W-10 Governed meaning. Each tab shows what governs the definition in this build (Git, `make ontology-check`, the
 * built TBox, the lane config). With a COA snapshot on the record (`coaMode` snapshot), COA's objects sit beside
 * them: the uploaded ontology ("uploaded, not steward-reviewed"), the metric card, the independent recomputation
 * with its resolver trace, the provenance hashes, measured latency and the token rule, every value tagged
 * "COA snapshot · captured <date>". With `coaMode` off each COA place says COA is off. Only COA's real status
 * enums appear, in the legend. URL: /knowledge/governed?tab=ontology|approval|metric|binding|discovery
 */
import { useQuery } from "@tanstack/react-query";
import { Check, ExternalLink, X } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import type { CoaSnapshot, GovernedDefinitionView, OntologyTermList, PipelineDetail } from "@contracts/api";
import { useApi, useMe } from "../../api/context";
import { type OurTotal, bindingLines, coaOf, curie, governanceLabel, promptDefinition } from "../../api/governance";
import { loadOurTotal } from "../../showcase/stories/sc8-payload";
import { useWorkspace } from "../../app/workspace";
import { CoaHashes, CoaLatency, CoaMetricCard, CoaScope, CoaTag, CoaTokenRule, ConceptAnchor, CrossCheckRow } from "./coa";
import { RpApiError } from "../../api/types";
import { Page, PageHeader, PlayExplainer } from "../../app/Page";
import { Badge, Button, Card, CardHeader, DataList, ErrorState, LaneBadge, Num, ProvenanceScope, ScopeTag, Skeleton, Tabs, TabsContent, TabsList, TabsTrigger, Tooltip, cn } from "../../design";
import { useSearchParam } from "../../hooks/useSearchParam";

/** COA's real status enums: the legend lists them in full; nothing else is ever rendered as a COA state. */
const COA_ENUMS = {
  "Induction proposal": ["inducing", "pending", "updated", "accepting", "embeddings_sync", "accepted", "rejected", "cancelled", "failed", "accept_failed"],
  "Table review": ["PENDING_REVIEW", "APPROVED", "REJECTED"],
  "Ontology ingest": ["pending", "running", "embeddings_sync", "completed", "failed"],
};
const LABEL_TEMPLATE = "concept_by_label_v1";
const sha12 = (s?: string) => (s ? s.slice(0, 12) : "—");

export default function GovernedScreen() {
  const api = useApi();
  const { sub } = useMe();
  const { packageId } = useWorkspace();
  const [tab, setTab] = useSearchParam("tab", "ontology");
  const q = useQuery({
    queryKey: ["governed", sub, packageId],
    queryFn: async () => {
      const [definition, units, naive, fin] = await Promise.all([api.getGovernedDefinition("qualifying_income"), api.getPipeline("2.3.1"), api.getPipeline("1.0"), api.listOntologyClasses("fin")]);
      const coa = coaOf(definition);
      // our template's total, which COA's recomputation is compared with (no comparison when it cannot load)
      const ours = coa ? await loadOurTotal(api, packageId, definition.definition.computedBy).catch(() => undefined) : undefined;
      return { definition, units, naive, fin, coa, ours };
    },
  });
  const d = q.data?.definition;
  const coa = q.data?.coa;
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Knowledge" }, { label: "Governed meaning" }]}
        title="Governed meaning"
        description="What “total income” means, who governs it and how the pipeline and the agent use it."
        actions={<PlayExplainer showcaseId="sc-8" />}
      />
      {q.error ? (
        <ErrorState title="The governed definition could not load" message={q.error instanceof Error ? q.error.message : String(q.error)} code={q.error instanceof RpApiError ? q.error.code : undefined} />
      ) : !q.data || !d ? (
        <Skeleton className="h-[480px] rounded-md" />
      ) : (
        <ProvenanceScope provenance={api.mode === "http" ? "measured" : "local-run"} source={`GET /governance/definitions/${d.definition.name}`}>
          <div className="flex flex-col gap-4" data-testid="governed">
            <section className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-bg-subtle px-4 py-2 text-xs text-text-2">
              {coa ? (
                <>
                  <span className="text-text">COA {coa.coaVersion}</span>
                  <span>namespace {coa.namespace}</span>
                  <CoaTag s={coa} />
                </>
              ) : (
                <span className="text-text">COA off</span>
              )}
              <span className="text-text">{governanceLabel(d)}</span>
              <span className="ml-auto flex items-center gap-2">
                <ScopeTag />
                <Tooltip content={coa ? "This build replays a recorded COA session; there is no console to open." : "COA is off in this build."}>
                  <span tabIndex={0}>
                    <Button size="sm" variant="ghost" disabled>
                      Open in COA console <ExternalLink />
                    </Button>
                  </span>
                </Tooltip>
              </span>
            </section>
            <Tabs value={tab} onValueChange={setTab}>
              <TabsList>
                {["ontology", "approval", "metric", "binding", "discovery"].map((t) => (
                  <TabsTrigger key={t} value={t} className="capitalize">
                    {t}
                  </TabsTrigger>
                ))}
              </TabsList>
              <TabsContent value="ontology">
                <OntologyTab data={q.data} />
              </TabsContent>
              <TabsContent value="approval">
                <ApprovalTab data={q.data} />
              </TabsContent>
              <TabsContent value="metric">
                <MetricTab data={q.data} />
              </TabsContent>
              <TabsContent value="binding">
                <BindingTab data={q.data} />
              </TabsContent>
              <TabsContent value="discovery">
                <DiscoveryTab data={q.data} />
              </TabsContent>
            </Tabs>
          </div>
        </ProvenanceScope>
      )}
    </Page>
  );
}

interface Data {
  definition: GovernedDefinitionView;
  units: PipelineDetail;
  naive: PipelineDetail;
  fin: OntologyTermList;
  coa?: CoaSnapshot;
  ours?: OurTotal;
}

/** A COA object this build does not show: COA is off, or the recorded session holds no such object. */
function Absent({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <section className="rounded-md border border-dashed border-border-strong bg-surface/60 px-4 py-3" data-coa-slot>
      <p className="flex items-center gap-2 text-sm text-text">
        {title} <Badge tone="neutral">{note}</Badge>
      </p>
      <div className="mt-1 text-xs text-text-2">{children}</div>
    </section>
  );
}

const COA_OFF = "COA is off in this build: the Git record governs the definition.";

function OntologyTab({ data }: { data: Data }) {
  const d = data.definition;
  const fin = data.fin.items.filter((t) => t.kind === "class" || t.kind === "concept");
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <div className="flex flex-col gap-3">
        <Card>
          <CardHeader title="Ontology inventory" meta="as authored in Git" />
          <table className="w-full text-sm">
            <thead className="bg-bg-subtle text-left text-caps uppercase text-text-2">
              <tr>
                <th className="px-4 py-1.5 font-medium">Ontology</th>
                <th className="px-4 py-1.5 font-medium">State</th>
                <th className="px-4 py-1.5 font-medium">Version</th>
                <th className="px-4 py-1.5 font-medium">Commit</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-border">
                <td className="px-4 py-2 font-mono text-mono-sm text-text">{d.ontology.ontologyId}</td>
                <td className="px-4 py-2 text-text-2">authored in Git</td>
                <td className="px-4 py-2 font-mono text-mono-sm">{d.ontology.version}</td>
                <td className="px-4 py-2 font-mono text-mono-sm text-text-2">{d.git ? d.git.commit.slice(0, 12) : "no commit yet"}</td>
              </tr>
              {data.coa && (
                <tr className="border-t border-border" data-coa-row>
                  <td className="px-4 py-2 font-mono text-mono-sm text-text">{data.coa.ontology.ontologyId}</td>
                  <td className="px-4 py-2 text-text-2">in COA · {data.coa.ontology.label}</td>
                  <td className="px-4 py-2 font-mono text-mono-sm">{data.coa.ontology.ontologyType}</td>
                  <td className="px-4 py-2">
                    <CoaTag s={data.coa} />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <div className="border-t border-border p-4">
            <DataList
              rows={[
                ["Source", <span className="font-mono text-mono-sm">{d.ontology.source}</span>],
                ["sha256", <span className="font-mono text-mono-sm">{sha12(d.ontology.sourceSha256)}</span>],
                ["N-Quads", <span className="font-mono text-mono-sm">{d.ontology.nquads}</span>],
              ]}
            />
          </div>
        </Card>
        {data.coa ? (
          <Absent title="Induced ontology (steward-accepted grounding)" note="none recorded">
            The recorded session uploaded fin.ttl; it ran no induction proposal, so COA holds no steward-accepted grounding.
          </Absent>
        ) : (
          <Absent title="COA" note="off">
            {COA_OFF}
          </Absent>
        )}
      </div>
      <Card>
        <CardHeader title="fin: classes and concepts" meta="our graph" actions={<Link to="/knowledge/ontology?iri=fin:QualifyingIncome" className="text-xs text-text-2 hover:text-text">Ontology explorer</Link>} />
        <ul className="grid max-h-[420px] grid-cols-1 overflow-y-auto px-4 py-2 sm:grid-cols-2">
          {fin.map((t) => (
            <li key={t.iri} className="truncate py-0.5">
              <Link to={`/knowledge/ontology?iri=${encodeURIComponent(t.curie)}`} className="font-mono text-mono-sm text-text hover:underline">
                {t.curie}
              </Link>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function ApprovalTab({ data }: { data: Data }) {
  const d = data.definition;
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <div className="flex flex-col gap-3">
        <Card>
          <CardHeader title="Git review (this build)" />
          <div className="flex flex-col gap-1 p-4 text-sm">
            <p className="text-text">{d.review ? `Reviewed by ${d.review.reviewer}` : "No reviewed merge yet."}</p>
            <p className="text-text-2">{d.git ? `Last commit ${d.git.commit.slice(0, 12)} by ${d.git.author}` : "The repository has no commits yet, so the record carries none."}</p>
          </div>
        </Card>
        {data.coa ? (
          <Absent title="Steward review in COA" note="none">
            COA ingests uploaded Turtle ({data.coa.ontology.ontologyType}) without a steward step. Its stewards review induction proposals; the recorded session has none.
          </Absent>
        ) : (
          <Absent title="Steward review in COA" note="COA off">
            {COA_OFF}
          </Absent>
        )}
        <Card>
          <CardHeader title="Legend · COA status enums" meta="only these values ever render" />
          <dl className="flex flex-col gap-2 p-4 text-xs">
            {Object.entries(COA_ENUMS).map(([k, v]) => (
              <div key={k} className="flex flex-wrap items-baseline gap-1.5">
                <dt className="w-32 text-text-2">{k}</dt>
                {v.map((x) => (
                  <dd key={x} className="rounded-xs border border-border px-1.5 font-mono text-mono-sm text-text-2">
                    {x}
                  </dd>
                ))}
              </div>
            ))}
          </dl>
        </Card>
      </div>
      <Card>
        <CardHeader
          title={d.check.command}
          meta={
            <span>
              <Num value={d.check.passed} kind="int" bare /> / <Num value={d.check.total} kind="int" bare /> pass · inputs {sha12(d.check.inputsSha256)}
            </span>
          }
        />
        <ul className="grid grid-cols-1 gap-x-4 px-4 py-2 sm:grid-cols-2">
          {d.check.checks.map((c) => (
            <li key={c.name} className="flex items-center gap-1.5 py-0.5 text-xs">
              {c.ok ? <Check className="size-3 shrink-0 text-cited" aria-label="pass" /> : <X className="size-3 shrink-0 text-loss" aria-label="fail" />}
              <span className={cn("truncate", c.ok ? "text-text-2" : "text-text")} title={c.name}>
                {c.name}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function MetricTab({ data }: { data: Data }) {
  const d = data.definition;
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <Card>
        <CardHeader title={d.definition.label ?? d.definition.name} meta={<span className="font-mono">{curie(d.definition.iri)}</span>} />
        <div className="flex flex-col gap-3 p-4">
          <blockquote className="border-l-2 border-border-strong pl-3 text-sm text-text">{d.definition.description}</blockquote>
          <DataList
            rows={[
              ["subClassOf", <span className="font-mono text-mono-sm">{d.definition.subClassOf.map(curie).join(", ")}</span>],
              ["Operand", <span className="font-mono text-mono-sm">{d.definition.operandConcepts.map(curie).join(", ")}</span>],
              ["Computed by", <span className="font-mono text-mono-sm">{d.template?.name ?? d.definition.computedBy}</span>],
              ["Template", <span className="font-mono text-mono-sm">{d.template?.path}</span>],
              ["Template sha256", <span className="font-mono text-mono-sm">{sha12(d.template?.sha256)}</span>],
            ]}
          />
        </div>
      </Card>
      {data.coa ? (
        <div className="flex flex-col gap-3">
          <CrossCheckRow s={data.coa} ours={data.ours} />
          <CoaMetricCard s={data.coa} />
          <CoaLatency s={data.coa} />
        </div>
      ) : (
        <Absent title="COA metric" note="COA off">
          {COA_OFF} No independent recomputation is shown.
        </Absent>
      )}
    </div>
  );
}

function BindingTab({ data }: { data: Data }) {
  const sentence = promptDefinition(data.units.promptText, curie(data.definition.definition.iri));
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {[data.units, data.naive].map((lane) => (
          <Card key={lane.item.pipelineVersion}>
            <CardHeader title={<span className="flex items-center gap-2"><LaneBadge pv={lane.item.pipelineVersion} /> {lane.item.configKey}</span>} meta={<span className="font-mono">{sha12(lane.item.configSha)}</span>} />
            <ul className="flex flex-col gap-1 p-4 font-mono text-mono-sm">
              {bindingLines(lane.yaml).map((r) => (
                <li key={r.key} className={cn("rounded-xs px-2 py-1 [overflow-wrap:anywhere]", r.binds ? "bg-surface-2 text-text" : "text-text-2")}>
                  preprocessing.{r.line}
                </li>
              ))}
            </ul>
          </Card>
        ))}
      </div>
      {sentence.length > 0 && (
        <Card>
          <CardHeader title="Prompt · the definition both lanes are given" meta={<span className="font-mono">{sha12(data.units.item.promptSha)}</span>} />
          <div className="px-4 py-3 text-sm">
            {sentence.map((l, i) => (
              <p key={l} className={i === 0 ? "text-text-2" : "text-text"}>
                {l}
              </p>
            ))}
          </div>
        </Card>
      )}
      {data.coa ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          <CoaHashes s={data.coa} />
          <Absent title="Governance block in the lane config" note="none">
            The lane config has no preprocessing.ontology.governance block; the evidence-units lane binds the ontology version above, and the hashes tie COA's copy to it.
          </Absent>
        </div>
      ) : (
        <Absent title="Governance block and COA hashes" note="COA off">
          The lane config has no preprocessing.ontology.governance block. {COA_OFF}
        </Absent>
      )}
    </div>
  );
}

function DiscoveryTab({ data }: { data: Data }) {
  const checks = data.definition.check.checks.filter((c) => c.name.startsWith(LABEL_TEMPLATE));
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <Card>
        <CardHeader title={`${LABEL_TEMPLATE} · label → concept`} meta={data.definition.check.command} />
        <ul className="flex flex-col gap-1 p-4">
          {checks.map((c) => (
            <li key={c.name} className="flex items-center gap-2 text-sm">
              {c.ok ? <Check className="size-3.5 text-cited" aria-label="pass" /> : <X className="size-3.5 text-loss" aria-label="fail" />}
              <span className="font-mono text-mono-sm text-text">{c.name.slice(LABEL_TEMPLATE.length).trim()}</span>
            </li>
          ))}
        </ul>
      </Card>
      <div className="flex flex-col gap-3">
        {data.coa && (
          <CoaScope s={data.coa}>
            <Card>
              <CardHeader title="COA finds the metric by name" meta={<span className="flex items-center gap-2"><ConceptAnchor s={data.coa} /></span>} actions={<ScopeTag />} />
              <ul className="flex flex-wrap gap-1.5 p-4">
                {data.coa.metric.synonyms.map((x) => (
                  <li key={x} className="rounded-xs border border-border px-2 py-0.5 text-sm text-text">
                    {x}
                  </li>
                ))}
              </ul>
            </Card>
          </CoaScope>
        )}
        {data.coa && <CoaTokenRule s={data.coa} />}
        <Absent title="Agent runs that asked COA" note="none">
          The agent has no COA tool in this build. A discovery run would be labelled and never part of a before/after comparison.{data.coa ? "" : ` ${COA_OFF}`}
        </Absent>
      </div>
    </div>
  );
}

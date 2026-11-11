/**
 * SC-8 · Governed meaning. "Total income" is one definition, `fin:QualifyingIncome`: authored in Git and checked by
 * `make ontology-check`, loaded into our graph, bound to the evidence-units lane at preprocessing and computed by our
 * SPARQL template with field lineage. With a COA snapshot on the record, COA's objects sit beside ours: the uploaded
 * ontology (labelled "uploaded, not steward-reviewed"), the governed metric card, its independent recomputation with
 * the resolver trace, the provenance hashes, measured latency and the token rule, each tagged with the capture date.
 * Without one (coaMode off) the same places say COA is off. Nothing here says COA reviewed or approved anything.
 */
import { easeCubicOut } from "d3-ease";
import { Check, GitCommitHorizontal, X } from "lucide-react";
import type { ReactNode } from "react";
import { governanceLabel } from "../../api/governance";
import { DataList, LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, Tooltip, cn } from "../../design";
import { withoutLongLiterals } from "../../lib/graph";
import { curie } from "../../lib/iris";
import { seg } from "../../viz/geom";
import { GraphCanvas } from "../../viz/GraphCanvas";
import { showcaseMeta } from "../catalog";
import type { Frame, StoryStep, Storyboard } from "../engine/types";
import { CoaHashes, CoaLatency, CoaMetricCard, CoaScope, CoaTag, CoaTokenRule, ConceptAnchor, CrossCheckRow } from "../../features/knowledge/coa";
import { coaTagLabel, crossCheckLabel, crossCheckState, definedByText, traceChips } from "../../api/governance";
import { NotInBuild } from "./parts";
import { LABEL_TEMPLATE, type Sc8Payload, bindingLines, discoveryCaption, executorCaption, labelChecks, loadSc8, ontologyCaption, promptDefinition, reviewCaption } from "./sc8-payload";
import { SC8_STEPS } from "./steps";

const STEP = Object.fromEntries(SC8_STEPS.map((s, i) => [s.id, i])) as Record<(typeof SC8_STEPS)[number]["id"], number>;
const sha12 = (s?: string) => (s ? s.slice(0, 12) : "—");

const defReceipt = (p: Sc8Payload) => ({ label: "Governed definition record", value: `${p.definition.source} · ${p.definition.recordedBy}`, source: `GET /governance/definitions/${p.definition.definition.name}`, provenance: p.provenance });
/** A receipt for a value read from the recorded COA session. */
const coaReceipt = (p: Sc8Payload, label: string, value: string | number | boolean | undefined, path: string) => ({ label, value, path: `$.definition.coa.snapshot.${path}`, source: p.coa ? `${coaTagLabel(p.coa)} · runs/local/coa` : undefined, provenance: "snapshot" as const });
const COA_OFF = "COA is off in this build: the Git record governs the definition.";

const steps: StoryStep<Sc8Payload>[] = [
  {
    ...SC8_STEPS[0],
    durationMs: 2600,
    caption: ontologyCaption,
    services: ["Amazon Neptune"],
    receipts: (p) => [
      defReceipt(p),
      { label: "Ontology source", value: `${p.definition.ontology.ontologyId} ${p.definition.ontology.version} · sha256 ${p.definition.ontology.sourceSha256}`, source: p.definition.ontology.source, provenance: p.provenance },
      { label: "Neighbourhood", value: p.graph ? `${p.graph.nodes.length} nodes · ${p.graph.edges.length} edges · ${p.graph.template}` : p.graphError, source: `GET /graph/neighbourhood?iri=${curie(p.definition.definition.iri)}&packageId=${p.packageId}`, provenance: p.provenance },
      p.coa ? coaReceipt(p, "COA ontology", `${p.coa.ontology.ontologyId} · ${p.coa.ontology.ontologyType} · ${p.coa.ontology.label}`, "ontology") : { label: "COA", value: p.definition.coa.note, provenance: p.provenance },
    ],
  },
  {
    ...SC8_STEPS[1],
    durationMs: 2600,
    caption: reviewCaption,
    services: ["Git", "make ontology-check"],
    receipts: (p) => [
      { label: "Commit", value: p.definition.git ? `${p.definition.git.commit} · ${p.definition.git.author}` : "no commit yet", provenance: p.provenance },
      { label: "Review", value: p.definition.review ? `${p.definition.review.reviewer} · ${p.definition.review.approvedAt ?? ""}` : "no reviewed merge yet", provenance: p.provenance },
      { label: p.definition.check.command, value: `${p.definition.check.passed}/${p.definition.check.total} · inputs sha256 ${p.definition.check.inputsSha256}`, path: "$.definition.check", provenance: p.provenance },
      ...(p.coa ? [coaReceipt(p, "COA steward review", p.coa.ontology.stewardReviewed, "ontology.stewardReviewed")] : []),
    ],
  },
  {
    ...SC8_STEPS[2],
    durationMs: 2400,
    caption: "The definition: the sum of W-2 Box 1 per borrower for the tax year.",
    services: ["Amazon Neptune"],
    receipts: (p) => [
      defReceipt(p),
      { label: "rdfs:comment", value: p.definition.definition.description, path: "$.definition.definition.description", provenance: p.provenance },
      ...(p.coa
        ? [
            coaReceipt(p, "COA metric SQL", `${p.coa.metric.dialect} · ${p.coa.metric.sql}`, "metric.sql"),
            coaReceipt(p, "COA definedBy · effectiveFrom (set by COA)", `${definedByText(p.coa)} · ${p.coa.metric.effectiveFrom}`, "metric"),
          ]
        : []),
    ],
  },
  {
    ...SC8_STEPS[3],
    durationMs: 2200,
    caption: "The definition is bound when documents are prepared.",
    services: ["Amazon S3 · config"],
    receipts: (p) => [
      { label: "Evidence units config", value: p.lanes.units.item.configSha, source: p.lanes.units.item.configKey, provenance: p.provenance },
      { label: "Naive config", value: p.lanes.naive.item.configSha, source: p.lanes.naive.item.configKey, provenance: p.provenance },
      { label: "Prompt", value: p.lanes.units.item.promptSha, provenance: p.provenance },
      ...(p.coa?.hashes ?? []).map((h, i) => coaReceipt(p, h.name, `sha256 ${h.sha256}${h.detail ? ` · ${h.detail}` : ""}`, `hashes[${i}]`)),
    ],
  },
  {
    ...SC8_STEPS[4],
    durationMs: 2600,
    caption: executorCaption,
    services: ["AWS Lambda · graph_hop", "Amazon Neptune"],
    receipts: (p) => [
      { label: "Template", value: `${p.definition.template?.name} · sha256 ${p.definition.template?.sha256}`, source: p.definition.template?.path, provenance: p.provenance },
      { label: "Gate run's hop", value: p.hop ? `${p.hop.runId} · ${p.hop.callId}` : "none", source: p.hop ? `runs/${p.hop.runId}.json · toolCalls` : undefined, provenance: p.provenance },
      ...(p.hop?.rows ?? []).map((r, i) => ({ label: `row ${i + 1}`, value: JSON.stringify(r), path: `$.hop.rows[${i}]`, provenance: p.provenance })),
      ...(p.coa
        ? [
            coaReceipt(p, crossCheckLabel(p.coa), `${p.coa.crossCheck.value} · ${crossCheckState(p.coa, p.ours)}`, "crossCheck.value"),
            coaReceipt(p, "COA resolver trace", traceChips(p.coa).join(" → "), "crossCheck.trace"),
            { label: "Latency (measured)", value: p.coa.latency.calls.map((c) => `${c.call} ${c.medianMs} ms`).join(" · "), path: "$.definition.coa.snapshot.latency", source: p.coa.latency.basis, provenance: "measured" as const },
          ]
        : []),
    ],
  },
  {
    ...SC8_STEPS[5],
    durationMs: 2200,
    caption: discoveryCaption,
    services: ["AgentCore Gateway", "AWS Lambda · graph_hop"],
    receipts: (p) => [
      ...p.definition.check.checks.filter((c) => c.name.startsWith(LABEL_TEMPLATE)).map((c) => ({ label: c.name, value: c.ok, source: p.definition.check.command, provenance: p.provenance })),
      { label: "Templates the gate run called", value: p.runTemplates.join(", ") || "none", provenance: p.provenance },
      ...(p.coa ? [coaReceipt(p, "COA synonyms", p.coa.metric.synonyms.join(" · "), "metric.synonyms"), coaReceipt(p, "COA token rule", p.coa.access?.rule, "access")] : []),
    ],
  },
];

// --------------------------------------------------------------------------------------------- frame

function Sc8Stage({ payload: p, step, progress: t }: Frame<Sc8Payload>) {
  const d = p.definition.definition;
  return (
    <ProvenanceScope provenance={p.provenance} source={`GET /governance/definitions/${d.name}`}>
      <div className="grid h-full min-h-0 grid-rows-[auto_minmax(0,1fr)] lg:grid-cols-[340px_minmax(0,1fr)] lg:grid-rows-1">
        {/* the definition column is a fixed 340 px on every step, so the main column never shifts while scrubbing */}
        <aside className="min-h-0 overflow-y-auto border-b border-border bg-bg-subtle lg:border-b-0 lg:border-r">
          <DefinitionCard p={p} />
        </aside>
        <div className="min-h-0 overflow-y-auto" data-testid="sc8-main">
          <div className="flex min-h-full max-w-[1120px] flex-col justify-center px-4 py-4 sm:px-5">
            {step === STEP.ontology && <Ontology p={p} t={t} />}
            {step === STEP.review && <Review p={p} t={t} />}
            {step === STEP.definition && <Definition p={p} t={t} />}
            {step === STEP.bound && <Bound p={p} t={t} />}
            {step === STEP.executor && <Executor p={p} t={t} />}
            {step === STEP.discovery && <Discovery p={p} t={t} />}
          </div>
        </div>
      </div>
    </ProvenanceScope>
  );
}

/** The single card the fallback variant keeps on screen: what governs "total income" in this build. */
function DefinitionCard({ p }: { p: Sc8Payload }) {
  const d = p.definition;
  return (
    <section className="flex flex-col gap-3 p-4">
      <h2 className="flex items-center gap-2 text-caps uppercase text-text-2">
        <span className="flex-1">Governed definition</span>
        <ScopeTag />
      </h2>
      <p className="text-md text-text">{d.definition.label}</p>
      <p className="font-mono text-mono-sm text-text-2">{curie(d.definition.iri)}</p>
      <DataList
        rows={[
          ["Name", <span className="font-mono text-mono-sm">{d.definition.name}</span>],
          ["Source", <span className="font-mono text-mono-sm">{d.ontology.source}</span>],
          ["Version", <span className="font-mono text-mono-sm">{d.ontology.version}</span>],
          ["sha256", <Tooltip content={d.ontology.sourceSha256}><span className="font-mono text-mono-sm" tabIndex={0}>{sha12(d.ontology.sourceSha256)}</span></Tooltip>],
          ["Computed by", <span className="font-mono text-mono-sm">{d.definition.computedBy}</span>],
          ["Governance", <span>{governanceLabel(d)}</span>],
        ]}
      />
      <CoaSlot p={p} />
    </section>
  );
}

/** COA's side of the card: the recorded metric (snapshot) or, in the Git fallback, that COA is off. */
function CoaSlot({ p }: { p: Sc8Payload }) {
  const c = p.definition.coa;
  if (p.coa) return <CoaMetricCard s={p.coa} compact />;
  return (
    <div className="rounded-sm border border-dashed border-border-strong px-3 py-2 text-xs text-text-2" data-coa={c.state}>
      <span className="text-text">COA off</span>
      <span className="block">{COA_OFF}</span>
    </div>
  );
}

/** `wrap`: the meta (a long provenance tag) moves under the title instead of truncating it. */
function Panel({ title, meta, children, className, style, wrap }: { title: string; meta?: ReactNode; children: ReactNode; className?: string; style?: React.CSSProperties; wrap?: boolean }) {
  return (
    <section className={cn("rounded-md border border-border bg-surface", className)} style={style}>
      <h3 className={cn("flex items-center gap-2 border-b border-border px-4 py-2 text-caps uppercase text-text-2", wrap && "flex-wrap gap-y-1")}>
        <span className={wrap ? "mr-auto" : "min-w-0 flex-1 truncate"}>{title}</span>
        {meta}
      </h3>
      {children}
    </section>
  );
}

/** Step 1: the concept in our graph, and the ontology it comes from. */
function Ontology({ p, t }: { p: Sc8Payload; t: number }) {
  const d = p.definition;
  return (
    <div className="flex flex-col gap-3">
      <Panel title="Neighbourhood in our graph" meta={<span className="font-mono normal-case tracking-normal">{curie(d.definition.iri)} · {p.packageId} · {p.graph?.template}</span>}>
        <div className="h-[360px] p-3" data-testid="graph-canvas">
          {p.graph ? (
            <GraphCanvas graph={withoutLongLiterals(p.graph).graph} reveal={easeCubicOut(seg(t, 0, 0.7))} emphasis={new Set([d.definition.iri, ...d.definition.operandConcepts, ...d.definition.subClassOf])} ariaLabel={`Graph neighbourhood of ${curie(d.definition.iri)}`} />
          ) : (
            <p className="text-sm text-text-2">{p.graphError ?? "No neighbourhood."}</p>
          )}
        </div>
      </Panel>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2" style={{ opacity: 0.3 + 0.7 * seg(t, 0.5, 0.9) }}>
        <Panel title="Ontology inventory">
          <div className="p-4">
            <DataList
              rows={[
                ["Ontology", <span className="font-mono text-mono-sm">{d.ontology.ontologyId}</span>],
                ["N-Quads", <span className="font-mono text-mono-sm">{d.ontology.nquads}</span>],
                ["Graph", <span className="font-mono text-mono-sm">{d.definition.graph}</span>],
              ]}
            />
          </div>
        </Panel>
        {p.coa ? (
          <CoaScope s={p.coa}>
            <Panel wrap title="Uploaded to COA" meta={<CoaTag s={p.coa} />}>
              <div className="p-4">
                <DataList
                  rows={[
                    ["Ontology", <span className="font-mono text-mono-sm">{p.coa.ontology.ontologyId}</span>],
                    ["Concept", <ConceptAnchor s={p.coa} />],
                    ["Classes · axioms", <span><Num value={p.coa.ontology.classCount} kind="int" bare /> · <Num value={p.coa.ontology.axiomCount} kind="int" bare /></span>],
                    ["Same file as Git", <span>{p.coa.ontology.matchesGitSource ? "yes · sha256 equal" : "no"}</span>],
                  ]}
                />
              </div>
            </Panel>
          </CoaScope>
        ) : (
          <NotInBuild title="COA" note="off in this build">
            {COA_OFF}
          </NotInBuild>
        )}
      </div>
    </div>
  );
}

/** Step 2: the Git record and the ontology check, check by check. */
function Review({ p, t }: { p: Sc8Payload; t: number }) {
  const d = p.definition;
  const checks = d.check.checks;
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.2fr)]">
      <div className="flex flex-col gap-3">
        <Panel title="Git">
          <ul className="flex flex-col gap-2 p-4 text-sm">
            <li className="flex items-start gap-2">
              <GitCommitHorizontal className="mt-0.5 size-4 shrink-0 text-text-2" aria-hidden />
              {d.git ? (
                <span>
                  <span className="font-mono text-mono-sm text-text">{d.git.commit.slice(0, 12)}</span> · {d.git.author}
                </span>
              ) : (
                <span className="text-text-2">No commit yet: the repository has no commits, so the record carries none.</span>
              )}
            </li>
            <li className="text-text-2">{d.review ? `Reviewed by ${d.review.reviewer}` : "No reviewed merge yet. The review record fills in when one lands."}</li>
          </ul>
        </Panel>
        {p.coa ? (
          <Panel wrap title="Steward review in COA" meta={<CoaTag s={p.coa} />}>
            <p className="px-4 py-3 text-sm text-text-2">
              None. COA ingests uploaded Turtle ({p.coa.ontology.ontologyType}) without a steward step; its stewards review induction proposals, and none was recorded. Git is where this definition is reviewed.
            </p>
          </Panel>
        ) : (
          <NotInBuild title="Steward review in COA" note="COA off">
            COA's stewards review induction proposals, not uploaded Turtle. {COA_OFF}
          </NotInBuild>
        )}
      </div>
      <Panel title="Ontology check" meta={<span className="normal-case tracking-normal"><span className="font-mono">{d.check.command}</span> · <Num value={d.check.passed} kind="int" size="xs" bare /> / <Num value={d.check.total} kind="int" size="xs" bare /></span>}>
        <ol className="grid grid-cols-1 gap-x-4 px-4 py-3 sm:grid-cols-2">
          {checks.map((c, i) => {
            const on = seg(t, (i / checks.length) * 0.85, (i / checks.length) * 0.85 + 0.1);
            return (
              <li key={c.name} className="flex items-center gap-1.5 py-0.5 text-xs" style={{ opacity: 0.25 + 0.75 * on }}>
                {on >= 1 ? c.ok ? <Check className="size-3 shrink-0 text-cited" aria-label="pass" /> : <X className="size-3 shrink-0 text-loss" aria-label="fail" /> : <span className="size-3 shrink-0" />}
                <span className={cn("truncate", c.ok ? "text-text-2" : "text-text")} title={c.name}>
                  {c.name}
                </span>
              </li>
            );
          })}
        </ol>
      </Panel>
    </div>
  );
}

/** Step 3: the definition as the TBox states it (rdfs:comment verbatim). */
function Definition({ p, t }: { p: Sc8Payload; t: number }) {
  const d = p.definition.definition;
  return (
    <div className="flex max-w-[760px] flex-col gap-3">
      <Panel title="Definition" meta={<span className="font-mono normal-case tracking-normal">{curie(d.iri)} · rdfs:comment</span>}>
        <blockquote className="px-5 py-4 text-md text-text" style={{ opacity: 0.2 + 0.8 * easeCubicOut(seg(t, 0, 0.6)) }}>
          {d.description}
        </blockquote>
      </Panel>
      <Panel title="As triples" style={{ opacity: 0.2 + 0.8 * seg(t, 0.4, 0.9) }}>
        <ul className="flex flex-col gap-1 px-4 py-3 font-mono text-mono-sm text-text">
          {d.subClassOf.map((c) => (
            <li key={c}>
              {curie(d.iri)} <span className="text-text-2">rdfs:subClassOf</span> {curie(c)}
            </li>
          ))}
          {d.operandConcepts.map((c) => (
            <li key={c}>
              {curie(d.iri)} <span className="text-text-2">fin:operandConcept</span> {curie(c)}
            </li>
          ))}
          {d.computedBy && (
            <li>
              {curie(d.iri)} <span className="text-text-2">rp:computedBy</span> "{d.computedBy}"
            </li>
          )}
          {d.altLabels.map((l) => (
            <li key={l}>
              {curie(d.iri)} <span className="text-text-2">skos:altLabel</span> "{l}"
            </li>
          ))}
        </ul>
      </Panel>
      {p.coa && (
        <div style={{ opacity: 0.2 + 0.8 * seg(t, 0.6, 1) }}>
          <CoaMetricCard s={p.coa} />
        </div>
      )}
    </div>
  );
}

/** Step 4: where each lane binds (or does not bind) the ontology at preprocessing, and the prompt's definition. */
function Bound({ p, t }: { p: Sc8Payload; t: number }) {
  const units = p.lanes.units;
  const sentence = promptDefinition(units.promptText, curie(p.definition.definition.iri));
  return (
    <div className="flex flex-col gap-3">
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        {[units, p.lanes.naive].map((lane, li) => {
          const rows = bindingLines(lane.yaml);
          const binds = rows.some((r) => r.key === "ontology" && r.binds);
          return (
            <Panel
              key={lane.item.pipelineVersion}
              title={lane.item.configKey}
              meta={<span className="font-mono normal-case tracking-normal">{sha12(lane.item.configSha)}</span>}
              style={{ opacity: 0.25 + 0.75 * easeCubicOut(seg(t, li * 0.25, 0.45 + li * 0.25)) }}
            >
              <div className="flex flex-col gap-2 p-4">
                <span className="flex items-center gap-2 text-sm text-text">
                  <LaneBadge pv={lane.item.pipelineVersion} />
                  {binds ? "binds the ontology at preprocessing" : "binds no ontology"}
                </span>
                <ul className="flex flex-col gap-1 font-mono text-mono-sm">
                  {rows.map((r) => (
                    <li key={r.key} className={cn("rounded-xs px-2 py-1 [overflow-wrap:anywhere]", r.binds ? "bg-surface-2 text-text" : "text-text-2")}>
                      preprocessing.{r.line}
                    </li>
                  ))}
                </ul>
              </div>
            </Panel>
          );
        })}
      </div>
      {sentence.length > 0 && (
        <Panel title="Prompt · the same text in both lanes" meta={<span className="font-mono normal-case tracking-normal">{sha12(units.item.promptSha)}</span>} style={{ opacity: 0.2 + 0.8 * seg(t, 0.6, 1) }}>
          <div className="flex flex-col gap-1 px-4 py-3 text-sm">
            <p className="text-text-2">{sentence[0]}</p>
            {sentence.slice(1).map((l) => (
              <p key={l} className="text-text">
                {l}
              </p>
            ))}
          </div>
        </Panel>
      )}
      {p.coa && (
        <div style={{ opacity: 0.2 + 0.8 * seg(t, 0.7, 1) }}>
          <CoaHashes s={p.coa} />
        </div>
      )}
      <p className="text-xs text-text-2">The lane config has no governance block; this build governs the definition in Git and binds the ontology version above.</p>
    </div>
  );
}

/** Step 5: the template's rows, each operand with its field IRI, and the total; the COA cross-check slot. */
function Executor({ p, t }: { p: Sc8Payload; t: number }) {
  const h = p.hop;
  if (!h) return <p className="text-sm text-text-2">The gate run made no hop with {p.definition.definition.computedBy}.</p>;
  return (
    <div className="flex flex-col gap-3">
      <Panel title="Template rows" meta={<span className="flex items-center gap-2 font-mono normal-case tracking-normal">{h.template} · run {h.runId} <ScopeTag /></span>}>
        <ol className="divide-y divide-border">
          {h.operands.map((o, i) => {
            const row = h.rows.find((r) => r.field?.endsWith(`/${o.fieldId}`) && r.unit?.endsWith(o.unitId));
            const on = easeCubicOut(seg(t, i * 0.25, i * 0.25 + 0.35));
            return (
              <li key={`${o.unitId}|${o.fieldId}`} className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5" style={{ opacity: 0.2 + 0.8 * on }}>
                <span className="min-w-0">
                  <span className="text-sm text-text">{o.label}</span>
                  {row?.taxYear && <span className="ml-2 text-xs text-text-2">TY{row.taxYear}</span>}
                  <span className="block truncate font-mono text-mono-sm text-text-2" title={row?.field}>
                    {row?.field ?? `${o.unitId}/${o.fieldId}`}
                  </span>
                </span>
                <Num value={o.value} kind="money" />
              </li>
            );
          })}
          {h.total && (
            <li className="flex items-center justify-end gap-3 px-4 py-3" style={{ opacity: 0.2 + 0.8 * seg(t, 0.65, 1) }}>
              <span className="text-xs text-text-2">total</span>
              <Num value={h.total} kind="money" size="lg" />
            </li>
          )}
        </ol>
      </Panel>
      {p.coa ? (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]" style={{ opacity: 0.2 + 0.8 * seg(t, 0.7, 1) }}>
          <CrossCheckRow s={p.coa} ours={p.ours} reveal={seg(t, 0.7, 1)} />
          <CoaLatency s={p.coa} />
        </div>
      ) : (
        <NotInBuild title="Independent recomputation in COA" note="COA off">
          {COA_OFF} No cross-check row is shown.
        </NotInBuild>
      )}
    </div>
  );
}

/** Step 6: how the label finds the definition: the build's checks and the templates the gate run used. */
function Discovery({ p, t }: { p: Sc8Payload; t: number }) {
  const checks = labelChecks(p);
  const target = p.definition.definition.iri;
  return (
    <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
      <Panel title="Label to concept" meta={<span className="flex items-center gap-2 font-mono normal-case tracking-normal">{LABEL_TEMPLATE} <ProvenanceTag provenance="derived" source={p.definition.check.command} /></span>}>
        <ul className="flex flex-col gap-1.5 px-4 py-3">
          {checks.map((c, i) => {
            const hit = c.concepts.includes(target);
            return (
              <li
                key={c.label}
                className={cn("grid grid-cols-[14px_minmax(0,1fr)_auto] items-center gap-2 rounded-xs px-1.5 py-0.5 font-mono text-mono-sm", hit && "bg-concept/10 ring-1 ring-concept/50")}
                style={{ opacity: 0.2 + 0.8 * seg(t, i * 0.15, i * 0.15 + 0.3) }}
                data-resolves={hit ? "definition" : undefined}
              >
                {c.ok ? <Check className="size-3.5 text-cited" aria-label="pass" /> : <X className="size-3.5 text-loss" aria-label="fail" />}
                <span className={cn("truncate", hit ? "text-text" : "text-text-2")} title={c.label}>
                  {c.label}
                </span>
                <span className={cn("whitespace-nowrap", hit ? "text-text" : "text-text-2")}>
                  → {c.concepts.length ? c.concepts.map(curie).join(", ") : "no concept"}
                </span>
              </li>
            );
          })}
        </ul>
      </Panel>
      <div className="flex flex-col gap-3">
        <Panel title="Templates the gate run called">
          <ul className="flex flex-wrap gap-1.5 px-4 py-3">
            {p.runTemplates.map((x, i) => (
              <li key={`${x}-${i}`} className="rounded-xs border border-border px-2 py-0.5 font-mono text-mono-sm text-text">
                {x}
              </li>
            ))}
          </ul>
        </Panel>
        {p.coa ? (
          <>
            <CoaScope s={p.coa}>
              <Panel wrap title="COA finds the metric by name" meta={<CoaTag s={p.coa} />}>
                <ul className="flex flex-wrap gap-1.5 px-4 py-3">
                  {p.coa.metric.synonyms.map((x) => (
                    <li key={x} className="rounded-xs border border-border px-2 py-0.5 text-sm text-text">
                      {x}
                    </li>
                  ))}
                </ul>
              </Panel>
            </CoaScope>
            <CoaTokenRule s={p.coa} />
          </>
        ) : (
          <NotInBuild title="Discovery through COA" note="COA off">
            {COA_OFF}
          </NotInBuild>
        )}
        <p className="text-xs text-text-2">The agent has no COA tool in this build; a discovery run would be labelled and never part of a before/after comparison.</p>
      </div>
    </div>
  );
}

export const sc8GovernedMeaning: Storyboard<Sc8Payload> = {
  id: "sc-8",
  code: "SC-8",
  title: showcaseMeta("sc-8")?.title ?? "",
  claim: showcaseMeta("sc-8")?.claim ?? "",
  load: loadSc8,
  steps,
  render: (frame) => <Sc8Stage {...frame} />,
  receiptsPayload: (p) => ({ definition: p.definition, hop: p.hop, runTemplates: p.runTemplates, graph: p.graph && { nodes: p.graph.nodes.length, edges: p.graph.edges.length, template: p.graph.template } }),
};

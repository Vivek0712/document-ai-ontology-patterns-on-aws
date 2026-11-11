/**
 * SC-8 payload: "total income" is one governed definition, `fin:QualifyingIncome`, authored and checked in Git,
 * loaded into our graph, bound to the evidence-units lane at preprocessing and computed by our SPARQL template with
 * field-level lineage.
 *
 * Reads: `GET /governance/definitions/qualifying_income` (the record `make ontology-check` writes), its
 * neighbourhood in the graph (`GET /graph/neighbourhood`), both lanes' pipeline configs and prompt, and the gate
 * run's `graph_hop` call (rows, total, operands). The record's `coa` slot decides the variant: `snapshot` adds a
 * recorded COA session (metric card, independent recomputation, hashes, measured latency, token rule), each value
 * tagged "COA snapshot · captured <date>"; `off` is the Git-only fallback, and its COA slots say COA is off.
 */
import type { CoaSnapshot, GovernedDefinitionView, GraphNeighbourhood, PipelineDetail } from "@contracts/api";
import { dayOf } from "../../api/runs";
import type { RpApi } from "../../api/types";
import type { Provenance } from "../../design";
import { curie } from "../../lib/iris";
import { type OurTotal, coaOf, crossCheckState, governanceLabel } from "../../api/governance";
export { bindingLines, promptDefinition } from "../../api/governance";
import type { Loaded, StoryContext } from "../engine/types";
import { packageOfQuestion } from "./compare";
import { gateQuestion, headlineRuns } from "./lending";

export interface Sc8Hop {
  runId: string;
  callId: string;
  template: string;
  rows: Record<string, string>[];
  columns: string[];
  total?: string;
  operands: { unitId: string; fieldId: string; value: string; label: string }[];
  graphs: string[];
}

export interface Sc8Payload {
  definition: GovernedDefinitionView;
  graph?: GraphNeighbourhood;
  graphError?: string;
  lanes: { units: PipelineDetail; naive: PipelineDetail };
  hop?: Sc8Hop;
  /** templates the gate run's graph_hop calls used (discovery evidence) */
  runTemplates: string[];
  packageId: string;
  provenance: Provenance;
  /** the recorded COA session when the record carries one (coaMode snapshot); undefined in the Git fallback */
  coa?: CoaSnapshot;
  /** our template's total for the gate question's package, which COA's recomputation is compared with */
  ours?: OurTotal;
}

/** The governed name SC-8 follows (the definition the lending prompt and the hop template both name). */
export const DEFINITION = "qualifying_income";
/** The template that resolves a printed label to a concept. */
export const LABEL_TEMPLATE = "concept_by_label_v1";

export async function loadSc8({ api, packageId }: StoryContext): Promise<Loaded<Sc8Payload>> {
  const qid = gateQuestion("sc-8").questionId;
  const home = await packageOfQuestion(api, qid, packageId);
  if (!home) throw new Error(`No logged run of ${qid} in a package you can read.`);
  const definition = await api.getGovernedDefinition(DEFINITION);
  const [graph, units, naive, runs] = await Promise.all([
    api.getGraphNeighbourhood(curie(definition.definition.iri), home.packageId, 1).catch((e: unknown) => e as Error),
    api.getPipeline("2.3.1"),
    api.getPipeline("1.0"),
    headlineRuns(api, home.packageId, qid),
  ]);
  const run = runs.runs["2.3.1"];
  const hops = run.toolCalls.filter((c) => c.tool === "graph_hop");
  const sum = hops.find((c) => c.input.template === definition.definition.computedBy);
  const out = sum?.output as Record<string, unknown> | undefined;
  const local = api.mode !== "http";
  const coa = coaOf(definition);
  return {
    source: {
      mode: local ? "local-run" : "replay",
      label: governanceLabel(definition),
      detail: `${definition.ontology.source} · ${definition.check.command} ${definition.check.passed}/${definition.check.total} · executor from run ${run.runId}`,
      recordedAt: dayOf(definition.recordedAt),
      runId: run.runId,
    },
    payload: {
      definition,
      graph: graph instanceof Error ? undefined : graph,
      graphError: graph instanceof Error ? graph.message : undefined,
      lanes: { units, naive },
      hop:
        sum && out
          ? {
              runId: run.runId,
              callId: sum.callId,
              template: String(out.template ?? sum.input.template),
              rows: (out.rows as Record<string, string>[]) ?? [],
              columns: (out.columns as string[]) ?? [],
              total: out.total as string | undefined,
              operands: (out.operands as Sc8Hop["operands"]) ?? [],
              graphs: (out.graphs as string[]) ?? [],
            }
          : undefined,
      runTemplates: hops.map((c) => String(c.input.template)),
      packageId: home.packageId,
      provenance: local ? "local-run" : "measured",
      coa,
      ours: out?.total ? { packageId: home.packageId, total: String(out.total), runId: run.runId } : undefined,
    },
  };
}

/**
 * Our total for the governed definition (the gate run's template result in the package that holds the gate
 * question), for screens that show COA's recomputation without loading SC-8 (W-10). Undefined when there is none.
 */
export async function loadOurTotal(api: RpApi, packageId: string, computedBy?: string): Promise<OurTotal | undefined> {
  const qid = gateQuestion("sc-8").questionId;
  const home = await packageOfQuestion(api, qid, packageId);
  if (!home) return undefined;
  const run = (await headlineRuns(api, home.packageId, qid)).runs["2.3.1"];
  const hop = run.toolCalls.find((c) => c.tool === "graph_hop" && (!computedBy || c.input.template === computedBy));
  const total = (hop?.output as Record<string, unknown> | undefined)?.total;
  return total ? { packageId: home.packageId, total: String(total), runId: run.runId } : undefined;
}

// ------------------------------------------------------------------------------------------- captions

export function reviewCaption(p: Sc8Payload): string {
  const c = p.definition.check;
  if (p.definition.review) return `Reviewed in Git; ${c.passed} of ${c.total} ontology checks pass.`;
  return `Checked in Git: ${c.passed} of ${c.total} ontology checks pass. No reviewed merge yet.`;
}

export function executorCaption(p: Sc8Payload): string {
  if (!p.hop) return "The gate run made no hop with the defining template.";
  if (p.coa && crossCheckState(p.coa, p.ours) === "match") return "Our template computes it; COA's governed metric recomputes the same total.";
  if (p.coa && crossCheckState(p.coa, p.ours) === "differs") return "Our template computes it; COA's recorded recomputation differs.";
  return "Our template computes it, with the field each operand came from.";
}

export function ontologyCaption(p: Sc8Payload): string {
  return p.coa ? "Authored in Git, loaded into our graph and uploaded to COA." : "Authored in Git and loaded into our graph.";
}

/** The ontology build's label checks: each label, the concept(s) it resolved to (the check's detail), pass or fail. */
export function labelChecks(p: Pick<Sc8Payload, "definition">): { label: string; concepts: string[]; ok: boolean }[] {
  return p.definition.check.checks
    .filter((c) => c.name.startsWith(LABEL_TEMPLATE))
    .map((c) => ({
      label: c.name.slice(LABEL_TEMPLATE.length).trim().replace(/^'(.*)'$/, "$1"),
      concepts: (c.detail ?? "").split(",").map((x) => x.trim()).filter((x) => x.startsWith("urn:") || x.includes(":")).filter((x) => x !== "no concept"),
      ok: c.ok,
    }));
}

export function discoveryCaption(p: Sc8Payload): string {
  if (p.runTemplates.includes(LABEL_TEMPLATE)) return "The agent found the definition by its label.";
  const label = labelChecks(p).find((c) => c.concepts.includes(p.definition.definition.iri));
  return label && label.label.length < 44 ? `'${label.label}' resolves to ${curie(p.definition.definition.iri)}.` : "The ontology build resolves the label to the definition.";
}

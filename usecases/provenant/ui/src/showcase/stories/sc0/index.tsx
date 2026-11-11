/**
 * SC-0 · Story tour (the headline). One lending package, one question, two pipelines that differ only
 * in preprocessing: upload, watch both lanes run, ask the naive lane, see why its text layer reads the way it does,
 * ask the evidence-units lane, draw the boundary, follow a figure to the uploaded bytes, and the governed
 * definition behind "total income".
 *
 * The before and after answers are the logged runs' own, with N of N; captions that state an
 * outcome are functions of the payload (./captions.ts). Every frame is `render({ payload, step, progress })`
 *. Steps are addressed by their id in SC0_STEPS, never by position.
 *
 *   load.ts      the payload            captions.ts   every caption that states a fact
 *   frames/      board.tsx (no page) and canvas.tsx (page frames)
 */
import { coaOf, coaTagLabel, crossCheckLabel, crossCheckState, traceChips } from "../../../api/governance";
import { ProvenanceScope, cn } from "../../../design";
import { showcaseMeta } from "../../catalog";
import type { Frame, StoryStep, Storyboard } from "../../engine/types";
import type { Lane } from "../lending";
import { SC0_STEPS } from "../steps";
import { WHY_CAPTION, beforeCaption, definitionCaption, matchLine, questionCaption, watchCaption } from "./captions";
import { AskColumns, DefinitionCard, QuestionCard, UploadList, WatchStrip } from "./frames/board";
import { CanvasStep, FollowStep } from "./frames/canvas";
import { type Sc0Payload, followedDoc, loadSc0, ourTotal, rec } from "./load";

export type { Sc0Payload } from "./load";
export { beforeCaption, matchLine, watchCaption, definitionCaption } from "./captions";

type StepId = (typeof SC0_STEPS)[number]["id"];
const step = (id: StepId) => SC0_STEPS.find((s) => s.id === id)!;

const steps: StoryStep<Sc0Payload>[] = [
  {
    ...step("question"),
    durationMs: 2000,
    caption: questionCaption,
    services: ["Amazon Cognito", "Amazon API Gateway"],
    receipts: (p) => [
      { label: "Package", value: p.packageId, path: "$.packageId", provenance: p.provenance },
      { label: "Golden question", value: `${p.runs.qid} · ${p.question}`, source: "eval/golden.jsonl", provenance: "generator" },
      { label: "Documents", value: p.documents.map((d) => d.fileName).join(", "), path: "$.documents", provenance: "generator" },
    ],
  },
  {
    ...step("upload"),
    durationMs: 3000,
    caption: "Presigned upload straight to Amazon S3. Each file gets a sha256.",
    services: ["Amazon S3", "AWS Lambda · register", "Amazon DynamoDB"],
    receipts: (p) => p.documents.map((d, i) => ({ label: d.fileName, value: `sha256 ${d.sha256} · version ${d.sourceVersionId}`, source: d.bronzeKey, path: `$.documents[${i}].sha256`, provenance: p.provenance })),
  },
  {
    ...step("watch"),
    durationMs: 4000,
    caption: watchCaption,
    services: ["Amazon EventBridge", "AWS Step Functions", "AWS Fargate", "Amazon Comprehend"],
    receipts: (p) => [
      ...(["1.0", "2.3.1"] as const).map((pv) => ({ label: `${pv} counters · units, held, indexed, triples`, value: ["units", "held", "indexed", "triples"].map((k) => p.counters[pv]?.[k as keyof (typeof p.counters)[string]]).join(" · "), path: `$.counters['${pv}']`, provenance: p.provenance })),
      { label: "Stage records (durations measured)", value: p.lanes.length * 2, path: "$.lanes[*].lanes[*].stages", source: "rp-stages", provenance: p.provenance },
    ],
  },
  {
    ...step("before"),
    durationMs: 3500,
    caption: beforeCaption,
    services: ["Amazon Bedrock AgentCore", "Amazon Bedrock", "Amazon OpenSearch Serverless"],
    receipts: (p) => laneReceipts(p, "1.0"),
  },
  {
    ...step("why"),
    durationMs: 4000,
    caption: WHY_CAPTION,
    services: ["AWS Lambda · rp-xray", "Amazon S3"],
    receipts: (p) => [
      { label: "X-ray of the naive lane's page", value: `${p.xray.doc.fileName} p${p.xray.page} · ${p.xray.control.lines.length} lines`, source: p.xray.control.sources.bronze.key, provenance: "derived" },
      { label: "Numbers the logged answer used", value: (p.xray.control.answerNumbers ?? []).map((n) => `${n.value} → lines ${n.matchedLines.join(",") || "none"}`).join("; ") || "none", path: "$.xray.control.answerNumbers", provenance: p.provenance },
      { label: "Logged answer run", value: p.xray.control.sources.answerRun?.runId, provenance: p.provenance },
      { label: "Position-only value for Box 1", value: p.xray.box1.positionalValue?.text, provenance: "hypothesis" },
      { label: "Verdict", value: matchLine(p), provenance: "derived" },
    ],
  },
  {
    ...step("after"),
    durationMs: 4000,
    caption: "Same agent, same model. Evidence units.",
    services: ["Amazon Bedrock AgentCore", "Amazon Bedrock", "Amazon Neptune"],
    receipts: (p) => [...laneReceipts(p, "2.3.1"), { label: "Config diff", value: p.diff.summary, path: "$.diff.summary", source: "GET /pipelines/diff?a=1.0&b=2.3.1", provenance: "derived" }],
  },
  {
    ...step("boundary"),
    durationMs: 2500,
    caption: "Each value stays with its printed label in one unit.",
    services: ["AWS Fargate · docling-serve", "AWS Lambda · eu-builder"],
    receipts: (p) => [
      { label: "Evidence unit", value: p.xray.unit.unitId, source: p.xray.control.sources.evidence?.key, provenance: p.provenance },
      { label: "Box 1 inside its field box", value: p.xray.box1.geometricValue?.text, provenance: p.provenance },
    ],
  },
  {
    ...step("follow"),
    durationMs: 5000,
    caption: "Every figure opens the region it came from.",
    services: ["AWS Lambda · rp-get-region", "Amazon S3", "Amazon DynamoDB"],
    receipts: (p) => [
      { label: "Followed claim", value: `${p.trace.claim.text} · ${p.trace.unit.unitId} · ${p.trace.field?.fieldId}`, path: "$.trace.claim", provenance: p.provenance },
      { label: "Bronze sha256 (lineage)", value: p.trace.source.sha256, source: p.trace.source.bronzeKey, provenance: p.provenance },
      { label: "sha256 shown at upload (DocumentItem)", value: followedDoc(p)?.sha256, path: "$.documents[*].sha256", provenance: p.provenance },
      { label: "Same object (compared in the browser)", value: p.trace.source.sha256 === followedDoc(p)?.sha256, provenance: "derived" },
      { label: "Lane · configSha", value: `${p.trace.pipeline.pipelineVersion} · ${p.trace.pipeline.configSha}`, provenance: p.provenance },
    ],
  },
  {
    ...step("definition"),
    durationMs: 3000,
    caption: definitionCaption,
    services: ["Amazon Neptune"],
    receipts: (p) =>
      p.definition
        ? [
            { label: "Definition (rdfs:comment)", value: p.definition.definition.description, source: p.definition.ontology.nquads, provenance: "generator" },
            { label: "Turtle source sha256", value: p.definition.ontology.sourceSha256, source: p.definition.ontology.source, provenance: "derived" },
            { label: "Template", value: `${p.definition.template?.name} · ${p.definition.template?.sha256}`, source: p.definition.template?.path, provenance: "derived" },
            { label: "Commit", value: p.definition.git?.commit ?? "not committed yet", provenance: "derived" },
            { label: p.definition.check.command, value: `${p.definition.check.passed} of ${p.definition.check.total} checks passed`, provenance: p.provenance },
            ...coaReceipts(p),
          ]
        : [{ label: "Governed definition", value: p.definitionError ?? "unavailable", provenance: "derived" }],
  },
];

/** The recorded COA session's cross-check, when the record carries one (coaMode snapshot). */
function coaReceipts(p: Sc0Payload) {
  const coa = coaOf(p.definition);
  if (!coa) return [];
  const source = `${coaTagLabel(coa)} · runs/local/coa`;
  return [
    { label: crossCheckLabel(coa), value: `${coa.crossCheck.value} · ${crossCheckState(coa, ourTotal(p))}`, path: "$.definition.coa.snapshot.crossCheck", source, provenance: "snapshot" as const },
    { label: "COA resolver trace", value: traceChips(coa).join(" → "), path: "$.definition.coa.snapshot.crossCheck.trace", source, provenance: "snapshot" as const },
    { label: "COA ontology", value: `${coa.ontology.ontologyId} · ${coa.ontology.label}`, path: "$.definition.coa.snapshot.ontology", source, provenance: "snapshot" as const },
  ];
}

function laneReceipts(p: Sc0Payload, pv: Lane) {
  const r = rec(p, pv);
  const s = p.runs.stats[pv];
  return [
    { label: `Representative run · ${pv}`, value: `${r.runId} · total ${r.answer?.total ?? "none"}`, source: r.s3Key, provenance: p.provenance },
    { label: "Golden check", value: r.check ? `${r.check.qid} · expected ${r.check.expected} · ${r.check.match ? "match" : "no match"}` : "none", path: `$.runs.runs['${pv}'].check`, provenance: p.provenance },
    { label: `Logged runs on this config (scored by ${s.scoredBy.join(", ") || "nobody"})`, value: `${s.correct} of ${s.scored} correct · ${s.cited} of ${s.scored} cite a region · ${Object.entries(s.totals).map(([k, v]) => `${k} ×${v}`).join(", ")}`, path: `$.runs.stats['${pv}']`, source: `configSha ${s.configSha.slice(0, 12)}`, provenance: "derived" as const },
    { label: "Model · promptSha", value: `${r.modelId} · ${r.promptSha.slice(0, 12)}`, provenance: p.provenance },
  ];
}

// --------------------------------------------------------------------------------------------- stage

const BEATS = ["Upload", "Watch", "Ask", "Verify"] as const;
const BEAT_OF: Record<StepId, number> = { question: -1, upload: 0, watch: 1, before: 2, why: 2, after: 2, boundary: 3, follow: 3, definition: 3 };

function Sc0Stage({ payload: p, step: index, progress: t }: Frame<Sc0Payload>) {
  const id = SC0_STEPS[index].id;
  return (
    <ProvenanceScope provenance={p.provenance} source={p.runs.runs["2.3.1"].s3Key}>
      <div className="flex h-full min-h-0 flex-col">
        <Beats at={BEAT_OF[id]} />
        <div className="relative min-h-0 flex-1">
          {id === "why" || id === "boundary" ? (
            <CanvasStep p={p} mode={id} t={t} />
          ) : id === "follow" ? (
            <FollowStep p={p} t={t} />
          ) : (
            <BoardStep p={p} id={id} t={t} />
          )}
        </div>
      </div>
    </ProvenanceScope>
  );
}

/** Four beat markers: where the story is. */
function Beats({ at }: { at: number }) {
  return (
    <ol className="flex h-9 shrink-0 items-center gap-1 border-b border-border bg-bg-subtle px-3 text-xs" aria-label="Story beats">
      {BEATS.map((b, i) => (
        <li key={b} className="flex items-center gap-1" aria-current={i === at ? "step" : undefined}>
          {i > 0 && <span className="mx-1 h-px w-5 bg-border-strong" aria-hidden />}
          <span className={cn("inline-flex items-center gap-1.5 rounded-xs px-1.5 py-0.5", i === at ? "bg-surface-2 text-text" : i < at ? "text-text-2" : "text-text-3")}>
            <span className="tnum font-mono">{i + 1}</span>
            {b}
          </span>
        </li>
      ))}
    </ol>
  );
}

/** Board steps share one top line under the beat bar (no vertical centring: the content never jumps). */
function BoardStep({ p, id, t }: { p: Sc0Payload; id: StepId; t: number }) {
  const short = id === "question" || id === "upload" || id === "definition";
  return (
    <div className="h-full overflow-y-auto px-5 pb-5">
      <div className={cn("mx-auto w-full max-w-[1040px]", short ? "pt-12" : "pt-6")}>
        {id === "question" && <QuestionCard p={p} t={t} />}
        {id === "upload" && <UploadList p={p} t={t} />}
        {id === "watch" && <WatchStrip p={p} t={t} />}
        {(id === "before" || id === "after") && <AskColumns p={p} after={id === "after"} t={t} />}
        {id === "definition" && <DefinitionCard p={p} t={t} />}
      </div>
    </div>
  );
}

export const sc0StoryTour: Storyboard<Sc0Payload> = {
  id: "sc-0",
  code: "SC-0",
  title: "Story tour",
  claim: showcaseMeta("sc-0")?.claim ?? "",
  load: loadSc0,
  steps,
  render: (frame) => <Sc0Stage {...frame} />,
  receiptsPayload: (p) => ({
    question: p.question,
    documents: p.documents,
    counters: p.counters,
    stats: p.runs.stats,
    runs: { "1.0": p.runs.runs["1.0"].answer, "2.3.1": p.runs.runs["2.3.1"].answer },
    xray: { answerNumbers: p.xray.control.answerNumbers, hypothesis: p.xray.hypothesis },
    trace: p.trace,
    definition: p.definition,
    diff: { summary: p.diff.summary, changedBlocks: p.diff.changedBlocks, identicalBlocks: p.diff.identicalBlocks },
  }),
};

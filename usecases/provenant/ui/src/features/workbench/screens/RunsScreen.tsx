/**
 * W-15 Runs: every ask run of the package (run records), the N-of-N per golden question and lane on the lanes'
 * current configs, and one run as a full record: answer, claims, tool calls with measured ms, retrieved units,
 * usage and every sha. Numbers render through <Num>; the run list's provenance is the run store's, the N-of-N
 * table's is "derived" (counted here from the eval harness's scores, see api/runs.ts).
 *
 * A golden question no run answered shows why when the data says so: the unit that holds its answer was held
 * by the PII gate (links to W-12), or the lane built no unit for the document.
 */
import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type { RunSummary } from "@contracts/api";
import { useApi, useMe } from "../../../api/context";
import { type LaneRunStats, MOCK_MODEL_ID, type RunScore, allRuns, goldRegionOf, goldenQids, laneRunStats, scoreOf } from "../../../api/runs";
import { RpApiError } from "../../../api/types";
import { Page, PageHeader } from "../../../app/Page";
import {
  Badge,
  Button,
  CitationChip,
  DataList,
  EmptyState,
  ErrorState,
  JsonView,
  LaneBadge,
  Num,
  type Provenance,
  ProvenanceScope,
  ScopeTag,
  Skeleton,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
  cn,
} from "../../../design";
import { AnswerChips, AnswerFigure, RetrievedList, ToolTimeline, callsOfRun } from "../../ask/parts";
import { shortSub } from "../../../lib/format";

const when = (iso: string) => new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit" });

export function RunsScreen() {
  const { packageId = "" } = useParams();
  const api = useApi();
  const { sub } = useMe();
  const nav = useNavigate();
  const q = useQuery({
    queryKey: ["runs-all", sub, packageId],
    queryFn: async () => {
      const [runs, pipelines, scores, unrecorded, pkg] = await Promise.all([allRuns(api, packageId), api.listPipelines(), api.getRunScores(packageId), api.getAskErrors(packageId), api.getPackage(packageId)]);
      return { runs, pipelines, scores, unrecorded, owner: pkg.package.ownerSub };
    },
  });
  const provenance: Provenance = api.mode === "http" ? "measured" : "local-run";

  const groups = useMemo(() => {
    if (!q.data) return [];
    const { runs, pipelines, scores, unrecorded, owner } = q.data;
    return goldenQids(runs, scores).map((qid) => {
      const lanes = pipelines.items
        .map((p) => laneRunStats(runs, qid, p.pipelineVersion, p.configSha, scores, { callerSub: owner }))
        .filter((s) => s.n > 0)
        // asks that errored before writing a record are counted apart (local run index), never silently dropped
        .map((s) => ({ ...s, errors: s.errors + (unrecorded[`${qid}|${s.pipelineVersion}`] ?? 0) }));
      const gold = lanes.flatMap((s) => s.runs).map((r) => scores[r.runId]?.gold).find(Boolean) ?? undefined;
      return { qid, question: lanes[0]?.runs[0]?.question ?? "", lanes, gold };
    });
  }, [q.data]);
  const runs = useMemo(() => [...(q.data?.runs ?? [])].sort((a, b) => b.createdAt.localeCompare(a.createdAt)), [q.data]);

  return (
    <Page wide>
      <PageHeader
        crumbs={[{ label: "Workbench" }, { label: packageId, to: `/workbench/packages/${encodeURIComponent(packageId)}` }, { label: "Runs" }]}
        title="Runs"
        description="Every ask is a run record: versions, tool calls, retrieved units and the answer. Golden questions are counted per lane on the lane's current config."
      />
      {q.error ? (
        <ErrorState title="Runs could not load" message={q.error instanceof Error ? q.error.message : String(q.error)} code={q.error instanceof RpApiError ? q.error.code : undefined} />
      ) : !q.data ? (
        <Skeleton className="h-[480px] rounded-md" />
      ) : runs.length === 0 ? (
        <EmptyState title="No runs yet">Ask a question to write the first run record.</EmptyState>
      ) : (
        <ProvenanceScope provenance={provenance} source={api.mode === "http" ? "rp-runs · runs/{runId}.json" : "runs/local (local run · Bedrock)"}>
          <ProvenanceScope provenance="derived" source="counted from the eval harness's score of each run (eval/run.py outcome_from_events)">
          <section className="mb-5 rounded-md border border-border bg-surface">
            <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
              <h2 className="text-sm font-medium">Golden questions · N of N</h2>
              <span className="truncate text-xs text-text-2">every scored run, per lane on its current config · representative = most common total, first run with it</span>
              <span className="ml-auto">
                <ScopeTag />
              </span>
            </header>
            <Table>
              <THead>
                <tr>
                  <TH>Question</TH>
                  <TH>Lane</TH>
                  <TH numeric>Runs</TH>
                  <TH numeric>Correct</TH>
                  <TH numeric>Cite a region</TH>
                  <TH>Totals</TH>
                  <TH>Representative</TH>
                </tr>
              </THead>
              <TBody>
                {groups.flatMap((g) =>
                  g.lanes.map((s, i) => (
                    <TR key={`${g.qid}-${s.pipelineVersion}`}>
                      {i === 0 && (
                        <TD rowSpan={g.lanes.length} className="max-w-[360px] align-top">
                          <span className="font-mono text-mono-sm text-text-2">{g.qid}</span> <span className="text-text">{g.question}</span>
                        </TD>
                      )}
                      <TD>
                        <LaneBadge pv={s.pipelineVersion} />
                      </TD>
                      <TD numeric>
                        <span className="inline-flex items-baseline gap-1.5">
                          <Num value={s.n} kind="int" />
                          {s.errors > 0 && <AgentErrors n={s.errors} local={api.mode !== "http"} />}
                        </span>
                      </TD>
                      <TD numeric>{s.scored ? <Ratio a={s.correct} n={s.scored} loss={s.correct === 0} /> : <NotScored />}</TD>
                      <TD numeric>{s.scored ? <Ratio a={s.cited} n={s.scored} /> : <NotScored />}</TD>
                      <TD className="tnum font-mono text-mono-sm text-text-2">
                        <span className="flex flex-wrap items-center gap-2">
                          {totals(s)}
                          {s.scored > 0 && s.correct === 0 && g.gold && <MissReason gold={g.gold} pv={s.pipelineVersion} />}
                        </span>
                      </TD>
                      <TD>
                        {s.representative && (
                          <Link className="font-mono text-mono-sm text-text hover:underline" to={`/runs/${encodeURIComponent(s.representative.runId)}`}>
                            {s.representative.runId}
                          </Link>
                        )}
                      </TD>
                    </TR>
                  )),
                )}
              </TBody>
            </Table>
          </section>
          </ProvenanceScope>

          <section className="rounded-md border border-border bg-surface">
            <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
              <h2 className="text-sm font-medium">All runs</h2>
              <span className="text-xs text-text-2">
                <Num value={runs.length} kind="int" bare /> run records
              </span>
            </header>
            <Table>
              <THead>
                <tr>
                  <TH>When</TH>
                  <TH>Question</TH>
                  <TH>Lane</TH>
                  <TH numeric>Total</TH>
                  <TH>Check</TH>
                  <TH numeric>Cited</TH>
                  <TH>Tools</TH>
                  <TH numeric>Latency</TH>
                  <TH />
                </tr>
              </THead>
              <TBody>
                {runs.map((r) => (
                  <TR key={r.runId} interactive onClick={() => nav(`/runs/${encodeURIComponent(r.runId)}`)}>
                    <TD className="whitespace-nowrap">
                      <Link to={`/runs/${encodeURIComponent(r.runId)}`} className="text-text-2 hover:text-text hover:underline" onClick={(e) => e.stopPropagation()}>
                        {when(r.createdAt)}
                      </Link>
                    </TD>
                    <TD className="max-w-[300px] truncate" title={r.question}>
                      {r.modelId === MOCK_MODEL_ID && (
                        <Badge tone="neutral" className="mr-1.5">
                          mock
                        </Badge>
                      )}
                      {r.question}
                    </TD>
                    <TD>
                      <LaneBadge pv={r.pipelineVersion} />
                    </TD>
                    <TD numeric>{r.total ? <Num value={r.total} kind="money" /> : <span className="text-text-3">—</span>}</TD>
                    <TD>
                      <CheckCell r={r} scores={q.data.scores} />
                    </TD>
                    <TD numeric>
                      <Ratio a={r.resolvableClaims} n={r.claims} />
                    </TD>
                    <TD className="max-w-[180px] truncate font-mono text-mono-sm text-text-2" title={r.toolSequence.join(" → ")}>{r.toolSequence.join(" → ")}</TD>
                    <TD numeric>
                      <Num value={r.latencyMs} kind="ms" />
                    </TD>
                    <TD className="w-6 text-text-3">
                      <ChevronRight className="size-4" aria-hidden />
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </section>
        </ProvenanceScope>
      )}
    </Page>
  );
}

const totals = (s: LaneRunStats) =>
  Object.entries(s.totals)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${k} ×${v}`)
    .join(", ");

function Ratio({ a, n, loss }: { a: number; n: number; loss?: boolean }) {
  return (
    <span className={cn("tnum inline-flex items-baseline gap-1", loss ? "text-loss" : "text-text")}>
      <Num value={a} kind="int" bare />
      <span className="text-text-3">/</span>
      <Num value={n} kind="int" bare />
      {loss && <span aria-label="none correct">×</span>}
    </span>
  );
}

const NotScored = () => <span className="text-xs text-text-3">not scored</span>;

/** Asks that ended in an agent error: not in N, but shown beside it (N asked = N + errors). */
function AgentErrors({ n, local }: { n: number; local: boolean }) {
  return (
    <span className="whitespace-nowrap text-xs text-loss" title={local ? "Agent errors that wrote no run record, counted by the local run index (runs/local/index.json)" : "Runs with status error"}>
      + <Num value={n} kind="int" bare provenance="derived" /> agent {n === 1 ? "error" : "errors"}
    </span>
  );
}

/** Why no run got a golden question right, when the unit views say so (derived; see `goldRegionOf`). */
function MissReason({ gold, pv }: { gold: NonNullable<RunScore["gold"]>; pv: string }) {
  const api = useApi();
  const { sub } = useMe();
  const q = useQuery({ queryKey: ["gold-region", sub, gold.docId, pv], queryFn: async () => (await goldRegionOf(api, gold, pv)) ?? null });
  if (!q.data) return null;
  if (q.data.state === "no-units") return <span className="font-sans text-xs text-text-3">no unit for this document</span>;
  return (
    <Link to={`/governance/pii?lane=${encodeURIComponent(pv)}&unit=${encodeURIComponent(q.data.unitId)}`} className="rounded-xs hover:bg-surface-2" title={`${q.data.unitId}: the unit holding the answer was held by the PII gate`}>
      <CitationChip kind="held" label="held by PII gate" className="font-sans" />
    </Link>
  );
}

function CheckCell({ r, scores }: { r: RunSummary; scores: Record<string, RunScore> }) {
  const s = scoreOf(r, scores);
  if (!s) return <NotScored />;
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap text-sm" title={s.by === "eval" ? "scored by the eval harness" : "the agent's own golden check"}>
      <span className={s.correct ? "text-cited" : "text-loss"} aria-label={s.correct ? "matches the golden set" : "does not match"}>
        {s.correct ? "✓" : "×"}
      </span>
      <span className="font-mono text-mono-sm text-text-2">{s.qid}</span>
    </span>
  );
}

export function RunScreen() {
  const { runId = "" } = useParams();
  const api = useApi();
  const { sub } = useMe();
  const q = useQuery({ queryKey: ["run", sub, runId], queryFn: () => api.getRun(runId) });
  const provenance: Provenance = api.mode === "http" ? "measured" : "local-run";
  if (q.error)
    return (
      <Page>
        <ErrorState title="This run could not load" message={q.error instanceof Error ? q.error.message : String(q.error)} code={q.error instanceof RpApiError ? q.error.code : undefined} />
      </Page>
    );
  if (!q.data)
    return (
      <Page>
        <Skeleton className="h-[480px] rounded-md" />
      </Page>
    );
  const r = q.data;
  return (
    <Page wide>
      <PageHeader
        crumbs={[{ label: "Workbench" }, { label: "Runs", to: `/workbench/packages/${encodeURIComponent(r.packageId)}/runs` }, { label: r.runId }]}
        title={r.question}
        meta={<LaneBadge pv={r.pipelineVersion} />}
        description={`${when(r.createdAt)} · ${r.status}${r.check ? ` · golden ${r.check.qid}` : ""}`}
        actions={
          <Button size="sm" variant="secondary" asChild>
            <Link to={`/workbench/packages/${encodeURIComponent(r.packageId)}/ask?runs=${encodeURIComponent(r.runId)}`}>Open in Ask</Link>
          </Button>
        }
      />
      <ProvenanceScope provenance={provenance} source={r.s3Key}>
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_380px]">
          <div className="flex min-w-0 flex-col gap-4">
            <section className="flex flex-col gap-3 rounded-md border border-border bg-surface p-4">
              <div className="flex items-center gap-2">
                <h2 className="text-caps uppercase text-text-2">Answer</h2>
                <span className="ml-auto">
                  <ScopeTag />
                </span>
              </div>
              {r.answer ? (
                <>
                  <AnswerFigure answer={r.answer} check={r.check} size="lg" />
                  <p className="text-sm text-text">{r.answer.answer}</p>
                  <AnswerChips answer={r.answer} runId={r.runId} />
                  {r.answer.caveats.length > 0 && (
                    <ul className="list-disc pl-4 text-xs text-text-2">
                      {r.answer.caveats.map((c, i) => (
                        <li key={i}>{c}</li>
                      ))}
                    </ul>
                  )}
                </>
              ) : (
                <p className="text-sm text-text-2">{r.error ?? "no answer"}</p>
              )}
            </section>
            <section className="flex flex-col gap-3 rounded-md border border-border bg-surface p-4">
              <h2 className="text-caps uppercase text-text-2">Tool calls</h2>
              <ToolTimeline calls={callsOfRun(r)} latencyMs={r.latencyMs} tools={r.toolsEnabled} />
              <ol className="flex flex-col divide-y divide-border rounded-sm border border-border">
                {r.toolCalls.map((c) => (
                  <li key={c.callId}>
                    <details>
                      <summary className="flex cursor-pointer items-center gap-2 px-3 py-2 text-sm">
                        <span className="font-mono text-mono-sm text-text">{c.tool}</span>
                        <Badge tone={c.status === "ok" ? "neutral" : "loss"}>{c.status}</Badge>
                        <span className="text-xs text-text-2">{c.callId}</span>
                        <span className="ml-auto">
                          <Num value={c.ms} kind="ms" />
                        </span>
                      </summary>
                      <div className="grid grid-cols-1 gap-2 px-3 pb-3 md:grid-cols-3">
                        <div>
                          <p className="mb-1 text-xs text-text-2">Input (model)</p>
                          <JsonView value={c.input} maxHeight={240} />
                        </div>
                        <div>
                          <p className="mb-1 text-xs text-text-2">Injected by the gateway</p>
                          <JsonView value={c.injected} maxHeight={240} />
                        </div>
                        <div>
                          <p className="mb-1 text-xs text-text-2">Output</p>
                          <JsonView value={c.output} maxHeight={240} collapseAfter={1} />
                        </div>
                      </div>
                    </details>
                  </li>
                ))}
              </ol>
            </section>
            <section className="rounded-md border border-border bg-surface p-4">
              <h2 className="mb-1 text-caps uppercase text-text-2">Retrieved · {r.retrieved.length}</h2>
              <RetrievedList items={r.retrieved} />
            </section>
          </div>
          <aside className="flex flex-col gap-4">
            <section className="rounded-md border border-border bg-surface p-4">
              <h2 className="mb-3 text-caps uppercase text-text-2">Versions</h2>
              <DataList
                rows={[
                  ["Model", <span className="font-mono text-mono-sm">{r.modelId}</span>],
                  ["configSha", <Sha v={r.configSha} />],
                  ...Object.entries(r.blockShas).map(([k, v]) => [<span className="pl-2">{k}</span>, <Sha v={v} />] as [JSX.Element, JSX.Element]),
                  ["promptSha", <Sha v={r.promptSha} />],
                  ["corpusHash", <Sha v={r.corpusHash} />],
                  ["Caller", <span className="font-mono text-mono-sm" title={r.callerSub}>{shortSub(r.callerSub)} · {r.groups.join(", ")}</span>],
                  ["Memory", <Num value={r.memoryRecordsInjected} kind="int" unit="records injected" />],
                ]}
              />
            </section>
            <section className="rounded-md border border-border bg-surface p-4">
              <h2 className="mb-3 text-caps uppercase text-text-2">Usage</h2>
              <DataList
                rows={[
                  ["Latency", <Num value={r.latencyMs} kind="ms" />],
                  ...Object.entries(r.usage).map(([k, v]) => [k, <Num value={v} kind="int" />] as [string, JSX.Element]),
                  ["Record", <span className="break-all font-mono text-mono-sm">{r.s3Key}</span>],
                ]}
              />
            </section>
          </aside>
        </div>
      </ProvenanceScope>
    </Page>
  );
}

function Sha({ v }: { v: string }) {
  return (
    <span className="font-mono text-mono-sm" title={v}>
      {v.slice(0, 12)}
    </span>
  );
}

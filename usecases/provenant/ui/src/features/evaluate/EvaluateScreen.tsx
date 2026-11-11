/**
 * W-16 Evaluate: the golden set and how each lane did on it.
 * - Golden set: every question with its type, expected answer (generator), document, page and gold region.
 * - Results: per question and lane, one dot per logged run (top-1 hit ✓ or miss ×); region hits for evidence units.
 *   Rates by type only in a published build (RP_PUBLIC_METRICS=on); otherwise "Not published".
 * - Runs: the stored eval runs with their golden sha, corpus hashes and config shas.
 * The caption is permanent: "Measured on our constructed corpus. Your documents will differ."
 * URL: /evaluate?tab=golden|results|runs&eval=<id>&lane=1.0|2.3.1&q=<qid>
 */
import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import type { EvalOutcome, EvalRunView } from "@contracts/api";
import { useApi, useMe } from "../../api/context";
import { type GoldenQuestion, goldPageKey, goldenSet } from "../../api/golden";
import { evalRunOnAws } from "../../api/runs";
import { RpApiError } from "../../api/types";
import { Page, PageHeader, PlayExplainer } from "../../app/Page";
import { Badge, Card, CardHeader, EmptyState, ErrorState, LaneBadge, Num, ProvenanceScope, ProvenanceTag, ScopeTag, SegmentedControl, Sheet, Skeleton, TBody, TD, TH, THead, TR, Table, Tabs, TabsContent, TabsList, TabsTrigger, Tooltip, cn } from "../../design";
import { useSearchParam } from "../../hooks/useSearchParam";
import { pad, toRect } from "../../viz/geom";
import { BoxOutline, PageCanvas } from "../../viz/PageCanvas";

const LANES = ["1.0", "2.3.1"] as const;

export default function EvaluateScreen() {
  const api = useApi();
  const { sub } = useMe();
  const [tab, setTab] = useSearchParam("tab", "results");
  const [evalParam, setEval] = useSearchParam("eval");
  const runs = useQuery({ queryKey: ["eval-runs", sub], queryFn: () => api.listEvalRuns() });
  const latest = [...(runs.data?.items ?? [])].sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
  const evalId = evalParam || latest?.evalRunId || "";
  const run = useQuery({ queryKey: ["eval-run", sub, evalId], queryFn: () => api.getEvalRun(evalId), enabled: Boolean(evalId) });
  const golden = useQuery({ queryKey: ["golden", sub], queryFn: () => goldenSet(api), staleTime: Infinity });
  const caption = run.data?.caption ?? "Measured on our constructed corpus. Your documents will differ.";
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Evaluate" }]}
        title="Evaluate"
        description="Golden questions with known pages and regions, asked of both pipelines. Recall@1: the first unit retrieved is the one that holds the answer."
        meta={runs.data && <Badge tone="neutral" title="Rates are withheld until the publication flag RP_PUBLIC_METRICS is on.">{runs.data.published ? "rates published" : "rates not published"}</Badge>}
        actions={<PlayExplainer showcaseId="sc-12" />}
      />
      <p className="mb-4 rounded-md border border-border bg-bg-subtle px-4 py-2 text-sm text-text" data-testid="eval-caption">
        {caption}
      </p>
      {runs.error ? (
        <ErrorState title="Eval results could not load" message={runs.error instanceof Error ? runs.error.message : String(runs.error)} code={runs.error instanceof RpApiError ? runs.error.code : undefined} />
      ) : (
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value="golden">Golden set</TabsTrigger>
            <TabsTrigger value="results">Results</TabsTrigger>
            <TabsTrigger value="runs">Runs</TabsTrigger>
          </TabsList>
          <TabsContent value="golden">{golden.error ? <ErrorState title="The golden set could not load" message={golden.error instanceof Error ? golden.error.message : String(golden.error)} /> : !golden.data ? <Skeleton className="h-64 rounded-md" /> : <GoldenTab items={golden.data.items} source={golden.data} outcomes={run.data?.outcomes ?? []} />}</TabsContent>
          <TabsContent value="results">{run.error ? <ErrorState title="This eval run could not load" message={run.error instanceof Error ? run.error.message : String(run.error)} code={run.error instanceof RpApiError ? run.error.code : undefined} /> : !run.data ? <Skeleton className="h-64 rounded-md" /> : <ResultsTab run={run.data} golden={golden.data?.items ?? []} />}</TabsContent>
          <TabsContent value="runs">
            <RunsTab items={runs.data?.items ?? []} selected={evalId} onSelect={(id) => { setEval(id); setTab("results"); }} />
          </TabsContent>
        </Tabs>
      )}
    </Page>
  );
}

function GoldenTab({ items, source, outcomes }: { items: GoldenQuestion[]; source: { key: string; versionId?: string }; outcomes: EvalOutcome[] }) {
  const [qid, setQid] = useSearchParam("q");
  const selected = items.find((g) => g.qid === qid);
  const logged = new Set(outcomes.map((o) => o.qid));
  return (
    <ProvenanceScope provenance="generator" source={`${source.key}${source.versionId ? ` · ${source.versionId}` : ""}`}>
      <Card data-testid="golden-set">
        <CardHeader title={`${items.length} questions`} meta={<span className="font-mono">{source.key}</span>} actions={<ScopeTag />} />
        <div className="max-h-[calc(100vh-320px)] overflow-y-auto">
          <Table>
            <THead className="sticky top-0 z-[1]">
              <tr>
                <TH>Question</TH>
                <TH>Type</TH>
                <TH numeric>Answer</TH>
                <TH>Gold region</TH>
                <TH>Logged</TH>
              </tr>
            </THead>
            <TBody>
              {items.map((g) => (
                <TR key={g.qid} interactive onClick={() => setQid(g.qid)}>
                  <TD>
                    <button type="button" className="text-left text-text hover:underline" onClick={() => setQid(g.qid)}>
                      {g.question}
                    </button>
                    <span className="block font-mono text-mono-sm text-text-2">{g.qid}</span>
                  </TD>
                  <TD>
                    <Badge tone="neutral">{g.type}</Badge>
                  </TD>
                  <TD numeric className="font-mono text-mono-sm text-text">
                    {g.answer}
                  </TD>
                  <TD className="text-xs text-text-2">
                    {g.fileName} · p{g.page}
                  </TD>
                  <TD className="text-xs text-text-2">{logged.has(g.qid) ? "yes" : "—"}</TD>
                </TR>
              ))}
            </TBody>
          </Table>
        </div>
      </Card>
      <Sheet open={Boolean(selected)} onOpenChange={(o) => !o && setQid(undefined)} title={selected?.question ?? ""} description={selected && `${selected.qid} · ${selected.type} · ${selected.packageId}`} width={520}>
        {selected && <GoldRegion q={selected} />}
      </Sheet>
    </ProvenanceScope>
  );
}

/** The gold region on the evidence-units lane's masked Gold page (the access check is the API's). */
function GoldRegion({ q }: { q: GoldenQuestion }) {
  const api = useApi();
  const { sub } = useMe();
  const page = useQuery({
    queryKey: ["gold-page", sub, q.packageId, q.docId, q.page],
    queryFn: async () => {
      const [art, doc] = await Promise.all([api.getArtifact(goldPageKey(q)), api.getDocument(q.docId)]);
      // the page's size in points, as register measured it
      const reg = doc.lanes["2.3.1"]?.stages.find((s) => s.stageId === "register")?.output as { pages?: { page: number; width: number; height: number }[] } | undefined;
      return { url: art.url, size: reg?.pages?.find((x) => x.page === q.page) };
    },
    retry: false,
  });
  const box = q.goldBbox ? toRect(q.goldBbox) : undefined;
  return (
    <div className="flex flex-col gap-3 p-4">
      <p className="text-sm text-text-2">
        Expected <span className="font-mono text-text">{q.answer}</span> <ProvenanceTag provenance="generator" />
      </p>
      <div className="h-[420px] overflow-hidden rounded-md border border-border">
        {page.error ? (
          <EmptyState title="No Gold page for this question">{page.error instanceof Error ? page.error.message : String(page.error)}</EmptyState>
        ) : !page.data?.url || !page.data.size ? (
          <Skeleton className="h-full" />
        ) : (
          <PageCanvas ariaLabel={`${q.fileName}, page ${q.page}: the gold region`} image={{ src: page.data.url, dpi: 150 }} pageWidth={page.data.size.width} pageHeight={page.data.size.height} focus={box ? pad(box, 60) : undefined} padding={12}>
            {box && <BoxOutline rect={pad(box, 2)} tone="text" width={1.5} />}
          </PageCanvas>
        )}
      </div>
      <p className="text-xs text-text-2">
        {q.fileName} · p{q.page} · the box the golden set says holds the answer (PDF points).
      </p>
    </div>
  );
}

function ResultsTab({ run, golden }: { run: EvalRunView; golden: GoldenQuestion[] }) {
  const [lane, setLane] = useSearchParam("lane", "both");
  const lanes = lane === "both" ? LANES : LANES.filter((l) => l === lane);
  const qids = [...new Set(run.outcomes.map((o) => o.qid))].sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const multiUnit = Array.isArray(run.meta.multiUnit) ? (run.meta.multiUnit as string[]) : [];
  const question = (qid: string) => golden.find((g) => g.qid === qid)?.question;
  const provenance = evalRunOnAws(run.mode) ? "measured" : "local-run";
  return (
    <ProvenanceScope provenance={provenance} source={`GET /eval/runs/${run.evalRunId}`}>
      <div className="flex flex-col gap-3" data-testid="eval-results">
        <div className="flex flex-wrap items-center gap-3 text-xs text-text-2">
          <SegmentedControl aria-label="Lane" size="sm" value={lane} onValueChange={setLane} items={[{ value: "both", label: "Both lanes" }, { value: "1.0", label: "Naive · 1.0" }, { value: "2.3.1", label: "Evidence units · 2.3.1" }]} />
          <span className="font-mono">{run.evalRunId}</span>
          <span>{run.mode}</span>
          <span>
            <Num value={qids.length} kind="int" size="xs" bare /> of <Num value={Object.values(run.goldenTypes).reduce((s, n) => s + n, 0)} kind="int" size="xs" provenance="generator" bare /> questions have logged runs
          </span>
          <Tooltip content={run.modeNote}>
            <span className="ml-auto" tabIndex={0}>
              <ScopeTag />
            </span>
          </Tooltip>
        </div>
        <Card>
          <Table>
            <THead>
              <tr>
                <TH>Question</TH>
                <TH>Type</TH>
                {lanes.map((pv) => (
                  <TH key={pv}>
                    <span className="flex items-center gap-1.5">
                      <LaneBadge pv={pv} /> top-1
                    </span>
                  </TH>
                ))}
                {lanes.includes("2.3.1") && <TH>Region hit</TH>}
              </tr>
            </THead>
            <TBody>
              {qids.map((qid) => {
                const mine = run.outcomes.filter((o) => o.qid === qid);
                const multi = multiUnit.includes(qid);
                return (
                  <TR key={qid}>
                    <TD>
                      <span className="text-text">{question(qid) ?? qid}</span>
                      <span className="block font-mono text-mono-sm text-text-2">{qid}</span>
                    </TD>
                    <TD>
                      <Badge tone="neutral">{mine[0]?.type}</Badge>
                    </TD>
                    {lanes.map((pv) => (
                      <TD key={pv}>
                        {multi && mine.some((o) => o.pipelineVersion === pv) ? (
                          <Tooltip content="No single unit prints this answer (a total of several fields), so top-1 cannot hold it.">
                            <span className="whitespace-nowrap text-xs text-text-2" tabIndex={0}>
                              needs 2+ units · n/a
                            </span>
                          </Tooltip>
                        ) : (
                          <OutcomeDots outcomes={mine.filter((o) => o.pipelineVersion === pv)} pick={(o) => o.recallAt1} />
                        )}
                      </TD>
                    ))}
                    {lanes.includes("2.3.1") && (
                      <TD>
                        <OutcomeDots outcomes={mine.filter((o) => o.pipelineVersion === "2.3.1")} pick={(o) => Boolean(o.regionHitAt1)} />
                      </TD>
                    )}
                  </TR>
                );
              })}
            </TBody>
          </Table>
        </Card>
        {run.lanes ? <RatesByType run={run} /> : <Unpublished withheld={run.withheld} />}
      </div>
    </ProvenanceScope>
  );
}

/** Past this many runs a row shows the first ones and a "+n" count, so every row keeps one line. */
const MAX_DOTS = 6;

function OutcomeDots({ outcomes, pick }: { outcomes: EvalOutcome[]; pick: (o: EvalOutcome) => boolean }) {
  if (!outcomes.length) return <span className="text-xs text-text-3">not run</span>;
  const shown = outcomes.slice(0, MAX_DOTS);
  const hits = outcomes.filter(pick).length;
  return (
    <span className="flex flex-nowrap items-center gap-0.5">
      {shown.map((o) => {
        const ok = pick(o);
        return (
          <Tooltip key={`${o.runId}-${o.repeat}`} content={<span className="font-mono">{o.runId} · {ok ? "hit" : "miss"}{o.top1UnitId ? ` · top-1 ${o.top1UnitId}` : ""}</span>}>
            <span role="img" aria-label={ok ? "hit" : "miss"} className={cn("flex size-3.5 items-center justify-center rounded-full border [&_svg]:size-2.5", ok ? "border-cited text-cited" : "border-dashed border-loss text-loss")}>
              {ok ? <Check strokeWidth={3} /> : <X strokeWidth={3} />}
            </span>
          </Tooltip>
        );
      })}
      {outcomes.length > MAX_DOTS && (
        <Tooltip content={`${hits} of ${outcomes.length} runs hit`}>
          <span className="ml-1 font-mono text-mono-sm text-text-2" tabIndex={0}>
            +<Num value={outcomes.length - MAX_DOTS} kind="int" size="xs" bare />
          </span>
        </Tooltip>
      )}
    </span>
  );
}

function Unpublished({ withheld }: { withheld: string[] }) {
  return (
    <section className="rounded-md border border-dashed border-border-strong px-4 py-3 text-sm">
      <p className="text-text">
        Rates by type{" "}
        <span className="text-xs text-text-2" title={`Withheld until RP_PUBLIC_METRICS is on: ${withheld.join("; ") || "rates"}`}>
          Not published
        </span>
      </p>
      <p className="mt-1 text-xs text-text-2">Rates are not published in this build. The per-question outcomes above are what it shows: top-1 on single-unit questions, region hit on every question.</p>
    </section>
  );
}

function RatesByType({ run }: { run: EvalRunView }) {
  const lanes = run.lanes!;
  return (
    <Card>
      <CardHeader title="Recall@1 by type" meta={run.caption} />
      <Table>
        <THead>
          <tr>
            <TH>Type</TH>
            {LANES.map((pv) => (
              <TH key={pv} numeric>
                {pv} · n
              </TH>
            ))}
            <TH numeric>2.3.1 region hit</TH>
          </tr>
        </THead>
        <TBody>
          {Object.keys(run.goldenTypes).map((type) => (
            <TR key={type}>
              <TD>{type}</TD>
              {LANES.map((pv) => {
                const m = lanes[pv]?.byType[type];
                return (
                  <TD key={pv} numeric>
                    {m ? (
                      <span className="inline-flex items-baseline gap-1.5">
                        <Num value={m.recallAt1.rate ?? 0} kind="pct" /> <span className="text-xs text-text-2">n {m.recallAt1.n}</span>
                      </span>
                    ) : (
                      "—"
                    )}
                  </TD>
                );
              })}
              <TD numeric>{lanes["2.3.1"]?.byType[type] ? <Num value={lanes["2.3.1"].byType[type].regionHitAt1.rate ?? 0} kind="pct" /> : "—"}</TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

function RunsTab({ items, selected, onSelect }: { items: { evalRunId: string; createdAt: string; mode: string; modeNote: string; questions: number; outcomes: number; goldenSha: string; corpusHash?: string; pipelineVersions: string[] }[]; selected: string; onSelect: (id: string) => void }) {
  if (!items.length) return <EmptyState title="No eval run is stored yet.">Run make eval-logged, or eval/run.py on AWS.</EmptyState>;
  return (
    <Card>
      <Table>
        <THead>
          <tr>
            <TH>Eval run</TH>
            <TH>Mode</TH>
            <TH numeric>Questions</TH>
            <TH numeric>Outcomes</TH>
            <TH>Golden sha</TH>
            <TH>Lanes</TH>
          </tr>
        </THead>
        <TBody>
          {items.map((r) => (
            <TR key={r.evalRunId} interactive onClick={() => onSelect(r.evalRunId)} className={cn(r.evalRunId === selected && "bg-surface-2")}>
              <TD>
                <button type="button" className="font-mono text-mono-sm text-text hover:underline" onClick={() => onSelect(r.evalRunId)}>
                  {r.evalRunId}
                </button>
                <span className="block text-xs text-text-2">{r.createdAt.replace("T", " ").slice(0, 16)} UTC</span>
              </TD>
              <TD>
                <Tooltip content={r.modeNote}>
                  <span tabIndex={0}>
                    <Badge tone="neutral">{r.mode}</Badge>
                  </span>
                </Tooltip>
              </TD>
              <TD numeric>
                <Num value={r.questions} kind="int" provenance="local-run" bare />
              </TD>
              <TD numeric>
                <Num value={r.outcomes} kind="int" provenance="local-run" bare />
              </TD>
              <TD className="font-mono text-mono-sm text-text-2">{r.goldenSha.slice(0, 12)}</TD>
              <TD>
                <span className="flex gap-1">
                  {r.pipelineVersions.map((pv) => (
                    <LaneBadge key={pv} pv={pv} />
                  ))}
                </span>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

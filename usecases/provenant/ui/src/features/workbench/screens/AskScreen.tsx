/**
 * W-07 Ask: one question, one or both lanes side by side (the top bar's lane control), each with its answer,
 * citations, retrieved units, the tool trace with measured ms, and the run footer.
 *
 * - `?q=<qid>` picks a golden question; `?runs=<runId>,<runId>` reopens logged runs without asking again.
 * - Mock mode replays the logged local run of a golden question (Bedrock, recorded); any other question gets a
 *   keyword stand-in with no model, and says so.
 */
import { useQuery } from "@tanstack/react-query";
import { MessageSquareText } from "lucide-react";
import { type FormEvent, type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { AnswerPayload, AskEvent, Check, RetrievedUnit, RunFooter, RunRecord, RunStartEvent } from "@contracts/api";
import { useApi, useMe } from "../../../api/context";
import { type LaneRunStats, MOCK_MODEL_ID, allRuns, laneRunStats, normaliseQuestion, questionIndex } from "../../../api/runs";
import { RpApiError } from "../../../api/types";
import { Page, PageHeader } from "../../../app/Page";
import { useWorkspace } from "../../../app/workspace";
import {
  Badge,
  Button,
  DataList,
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
  EmptyState,
  JsonView,
  LaneBadge,
  type Provenance,
  ProvenanceScope,
  ScopeTag,
  Skeleton,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Tooltip,
} from "../../../design";
import { AnswerChips, AnswerFigure, RetrievedList, StabilityLine, type TraceCall, ToolTimeline, callsOfStream } from "../../ask/parts";

const DEFAULT_Q = "What is the total income across this package?";

export interface AskState {
  runStart?: RunStartEvent;
  calls: TraceCall[];
  text: string;
  answer?: AnswerPayload;
  check?: Check;
  run?: RunFooter;
  /** a reopened run record carries its retrieved units directly */
  retrieved?: RetrievedUnit[];
  error?: { code: string; message: string };
  done: boolean;
}

export const emptyAsk = (): AskState => ({ calls: [], text: "", done: false });

/** Pure reducer over the SSE events (also used by tests). */
export function reduceAsk(s: AskState, e: AskEvent): AskState {
  switch (e.type) {
    case "run_start":
      return { ...emptyAsk(), runStart: e };
    case "tool_start":
      return { ...s, calls: [...s.calls, { start: e }] };
    case "tool_end":
      return { ...s, calls: s.calls.map((c) => (c.start.callId === e.callId ? { ...c, end: e } : c)) };
    case "text":
      return { ...s, text: s.text + e.delta };
    case "answer": {
      const { type: _t, ...payload } = e;
      void _t;
      return { ...s, answer: payload };
    }
    case "check":
      return { ...s, check: { qid: e.qid, expected: e.expected, actual: e.actual, match: e.match } };
    case "run": {
      const { type: _t, ...footer } = e;
      void _t;
      return { ...s, run: footer, done: true };
    }
    case "error":
      return { ...s, error: { code: e.code, message: e.message }, done: true };
  }
}

/** Retrieved units = union of search_units results, in order of first appearance. */
export function retrievedOf(s: AskState): RetrievedUnit[] {
  if (s.retrieved) return s.retrieved;
  const seen = new Set<string>();
  const out: RetrievedUnit[] = [];
  for (const c of s.calls) for (const r of c.end?.retrieved ?? []) if (!seen.has(r.unitId)) (seen.add(r.unitId), out.push(r));
  return out;
}

/** A logged run record as the same state a live stream ends in. */
export function stateOfRun(r: RunRecord): AskState {
  const t0 = Date.parse(r.createdAt);
  return {
    runStart: { type: "run_start", runId: r.runId, packageId: r.packageId, pipelineVersion: r.pipelineVersion, question: r.question, modelId: r.modelId, configSha: r.configSha, promptSha: r.promptSha, tools: r.toolsEnabled, t: 0 },
    calls: r.toolCalls.map((c) => {
      const t = (Date.parse(c.startedAt) - t0) / 1000;
      const out = c.output as Record<string, unknown>;
      return {
        start: { type: "tool_start" as const, callId: c.callId, tool: c.tool, input: c.input, t },
        end: { type: "tool_end" as const, callId: c.callId, tool: c.tool, ms: c.ms, status: c.status, summary: typeof out.summary === "string" ? out.summary : "", units: [], scores: [], retrieved: [], rows: Array.isArray(out.rows) ? (out.rows as Record<string, unknown>[]) : [], total: typeof out.total === "string" ? out.total : undefined, template: typeof out.template === "string" ? out.template : undefined, t: t + c.ms / 1000 },
      };
    }),
    text: r.answer?.answer ?? "",
    answer: r.answer,
    check: r.check,
    run: { runId: r.runId, packageId: r.packageId, pipelineVersion: r.pipelineVersion, configSha: r.configSha, blockShas: r.blockShas, promptSha: r.promptSha, modelId: r.modelId, corpusHash: r.corpusHash, memoryRecordsInjected: r.memoryRecordsInjected, latencyMs: r.latencyMs, usage: r.usage, startedAt: r.createdAt, runtimeSessionId: r.runtimeSessionId },
    retrieved: r.retrieved,
    done: true,
  };
}

export function AskScreen() {
  const { packageId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const api = useApi();
  const { sub } = useMe();
  const { lane: mode } = useWorkspace();
  const [question, setQuestion] = useState(DEFAULT_Q);
  const [states, setStates] = useState<Record<string, AskState>>({});
  const aborts = useRef<AbortController[]>([]);
  const pvs = useMemo(() => (mode === "both" ? ["1.0", "2.3.1"] : [mode]), [mode]);
  const provenance: Provenance = api.mode === "http" ? "measured" : "local-run";

  const pkg = useQuery({ queryKey: ["package", sub, packageId], queryFn: () => api.getPackage(packageId) });
  const status = useQuery({ queryKey: ["status", sub, packageId], queryFn: () => api.packageStatus(packageId), refetchInterval: (q) => (q.state.data && Object.values(q.state.data.lanes).every((l) => l.ready) ? false : 2000) });
  const history = useQuery({
    queryKey: ["runs-all", sub, packageId],
    queryFn: async () => {
      const [runs, pipelines, scores] = await Promise.all([allRuns(api, packageId), api.listPipelines(), api.getRunScores(packageId)]);
      return { runs, pipelines, scores };
    },
  });

  /**
   * Questions with logged runs. A scored run (eval harness, or the agent's own golden check) carries its qid,
   * and those get N-of-N per lane on the current configs; the others are listed by their text.
   */
  const golden = useMemo(() => {
    if (!history.data) return [];
    const { runs, scores } = history.data;
    const qids = questionIndex(runs, scores);
    const byText = new Map<string, { qid?: string; question: string }>();
    for (const r of runs) {
      if (r.modelId === MOCK_MODEL_ID) continue;
      const k = normaliseQuestion(r.question);
      if (!byText.has(k)) byText.set(k, { qid: qids.get(k), question: r.question });
    }
    return [...byText.values()]
      .sort((a, b) => (a.qid ?? "~").localeCompare(b.qid ?? "~", undefined, { numeric: true }) || a.question.localeCompare(b.question))
      .map((g) => ({
        ...g,
        stats: g.qid ? (Object.fromEntries(history.data!.pipelines.items.map((p) => [p.pipelineVersion, laneRunStats(runs, g.qid!, p.pipelineVersion, p.configSha, scores, { callerSub: pkg.data?.package.ownerSub })])) as Record<string, LaneRunStats>) : undefined,
      }));
  }, [history.data, pkg.data]);
  const goldenOf = (q: string) => golden.find((g) => normaliseQuestion(g.question) === normaliseQuestion(q));

  const lanes = status.data?.lanes ?? {};
  const ready = pvs.every((pv) => lanes[pv]?.ready);
  const busy = Object.values(states).some((s) => !s.done);

  const ask = useCallback(
    async (q: string, e?: FormEvent) => {
      e?.preventDefault();
      aborts.current.forEach((a) => a.abort());
      aborts.current = [];
      setStates(Object.fromEntries(pvs.map((pv) => [pv, emptyAsk()])));
      await Promise.all(
        pvs.map(async (pv) => {
          const ac = new AbortController();
          aborts.current.push(ac);
          try {
            await api.ask({ question: q, packageId, pipelineVersion: pv }, { signal: ac.signal, onEvent: (ev) => setStates((m) => ({ ...m, [pv]: reduceAsk(m[pv] ?? emptyAsk(), ev) })) });
            setStates((m) => ({ ...m, [pv]: { ...(m[pv] ?? emptyAsk()), done: true } }));
          } catch (err) {
            if (ac.signal.aborted) return;
            const msg = err instanceof RpApiError && err.status === 403 ? `You don't have access to package ${packageId}.` : err instanceof Error ? err.message : String(err);
            setStates((m) => ({ ...m, [pv]: { ...(m[pv] ?? emptyAsk()), error: { code: err instanceof RpApiError ? err.code : "error", message: msg }, done: true } }));
          }
        }),
      );
    },
    [api, packageId, pvs],
  );
  useEffect(() => () => aborts.current.forEach((a) => a.abort()), []);

  // ?runs=a,b reopens logged runs; ?q=<qid> picks (and asks) a golden question once the lanes are ready
  const runsParam = params.get("runs");
  const qParam = params.get("q");
  useEffect(() => {
    if (!runsParam) return;
    void Promise.all(runsParam.split(",").map((id) => api.getRun(id))).then((rs) => {
      setStates(Object.fromEntries(rs.map((r) => [r.pipelineVersion, stateOfRun(r)])));
      if (rs[0]) setQuestion(rs[0].question);
    });
  }, [api, runsParam]);
  const asked = useRef(false);
  useEffect(() => {
    if (!qParam || runsParam || asked.current || !ready) return;
    const g = golden.find((x) => x.qid === qParam);
    if (!g) return;
    asked.current = true;
    setQuestion(g.question);
    void ask(g.question);
  }, [qParam, runsParam, golden, ready, ask]);

  const shownLanes = runsParam ? Object.keys(states).sort() : pvs;
  const allDone = shownLanes.length > 0 && shownLanes.every((pv) => states[pv]?.done);
  const current = goldenOf(question);

  return (
    <Page wide>
      <PageHeader
        crumbs={[{ label: "Workbench" }, { label: pkg.data?.package.name ?? packageId, to: `/workbench/packages/${encodeURIComponent(packageId)}` }, { label: "Ask" }]}
        title="Ask"
        description="Same agent, prompt, model, retriever and K in both lanes; only preprocessing differs."
        actions={
          <span className="flex items-center gap-1.5 text-xs text-text-2">
            {shownLanes.map((pv) => (
              <LaneBadge key={pv} pv={pv} />
            ))}
            <span>· lanes from the top bar</span>
          </span>
        }
      />
      <form
        className="mb-2 flex flex-wrap items-center gap-2"
        onSubmit={(e) => {
          if (runsParam) setParams({}, { replace: true });
          void ask(question, e);
        }}
      >
        <input
          className="h-8 min-w-[280px] flex-1 rounded-sm border border-border-strong bg-surface px-3 text-sm text-text outline-none placeholder:text-text-3 focus-visible:ring-2 focus-visible:ring-focus"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          maxLength={2000}
          aria-label="Question"
        />
        <GoldenPicker golden={golden} onPick={(g) => setQuestion(g.question)} />
        {ready ? (
          <Button type="submit" variant="primary" disabled={busy || !question.trim()} className="min-w-[88px]">
            {busy ? "Asking…" : "Ask"}
          </Button>
        ) : (
          <Tooltip content="Waiting for the lanes to finish indexing.">
            <span tabIndex={0}>
              <Button type="button" variant="secondary" disabled className="min-w-[88px]">
                Ask
              </Button>
            </span>
          </Tooltip>
        )}
      </form>
      <p className="mb-4 flex flex-wrap items-center gap-x-2 text-xs text-text-2">
        {current?.qid ? (
          <>
            golden <span className="font-mono">{current.qid}</span>
          </>
        ) : current ? (
          <span>logged question · the agent's golden check did not run on it</span>
        ) : (
          <span>not a logged question: no ✓ or × check</span>
        )}
        {api.mode !== "http" && <span>· mock: golden questions replay their logged local run (Bedrock); others get a keyword stand-in with no model</span>}
        {runsParam && <span>· showing logged runs {runsParam.split(",").join(", ")}</span>}
      </p>

      {!ready && status.data && !Object.keys(states).length ? (
        <EmptyState
          icon={<MessageSquareText />}
          title="The lanes are still indexing"
          action={
            <Button asChild variant="secondary" size="sm">
              <Link to={`/workbench/packages/${encodeURIComponent(packageId)}`}>Open pipeline board</Link>
            </Button>
          }
        >
          Ask unlocks when every selected lane is ready.
        </EmptyState>
      ) : (
        <ProvenanceScope provenance={provenance} source="run records (GET /runs/{runId}) and the /ask event stream">
          <div className="grid grid-cols-1 gap-3 xl:grid-cols-2" data-testid={allDone ? "ask-done" : undefined}>
            {shownLanes.map((pv) => (
              <LaneColumn key={pv} pv={pv} state={states[pv]} stats={current?.stats?.[pv]} />
            ))}
          </div>
        </ProvenanceScope>
      )}
    </Page>
  );
}

function GoldenPicker({ golden, onPick }: { golden: { qid?: string; question: string }[]; onPick: (g: { question: string }) => void }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button type="button" variant="secondary" disabled={!golden.length}>
          Logged questions
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-[420px]">
        <DropdownMenuLabel>Questions with logged runs</DropdownMenuLabel>
        {golden.map((g) => (
          <DropdownMenuItem key={g.question} onSelect={() => onPick(g)}>
            <span className="min-w-0 flex-1 truncate">{g.question}</span>
            {g.qid && <span className="shrink-0 font-mono text-mono-sm text-text-2">{g.qid}</span>}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function LaneColumn({ pv, state, stats }: { pv: string; state?: AskState; stats?: LaneRunStats }) {
  const nav = useNavigate();
  const [sel, setSel] = useState<string>();
  const header = (right?: ReactNode) => (
    <header className="flex min-h-11 items-center gap-2 border-b border-border px-4 py-2">
      <LaneBadge pv={pv} />
      {state?.run && <span className="truncate font-mono text-mono-sm text-text-2">{state.run.runId}</span>}
      <span className="ml-auto flex items-center gap-2">
        {right}
        <ScopeTag />
      </span>
    </header>
  );
  if (!state)
    return (
      <section className="rounded-md border border-border bg-surface">
        {header()}
        <p className="p-4 text-sm text-text-2">Ask a question to run the agent on the {pv} index.</p>
      </section>
    );
  const retrieved = retrievedOf(state);
  const call = state.calls.find((c) => c.start.callId === sel);
  const mockAnswer = state.run?.modelId === MOCK_MODEL_ID;
  return (
    <section className="flex min-w-0 flex-col rounded-md border border-border bg-surface" data-lane={pv} aria-live="polite">
      {header(!state.done ? <Badge tone="running" dot>streaming</Badge> : mockAnswer ? <Badge tone="neutral">keyword stand-in</Badge> : undefined)}
      <div className="flex flex-col gap-3 p-4">
        {state.error ? (
          <p className="text-sm text-text">
            <span className="text-loss">× </span>
            {state.error.message}
          </p>
        ) : !state.runStart ? (
          <Skeleton className="h-16" />
        ) : (
          <>
            <AnswerFigure answer={state.answer} check={state.check} size="lg" />
            <p className="text-sm text-text">{state.answer?.answer ?? state.text}</p>
            {state.answer && <AnswerChips answer={state.answer} runId={state.run?.runId} />}
            {stats && stats.n > 0 && <StabilityLine stats={stats} />}
            {state.answer && state.answer.caveats.length > 0 && (
              <details className="text-xs text-text-2">
                <summary className="cursor-pointer select-none">Caveats ({state.answer.caveats.length})</summary>
                <ul className="mt-1 list-disc pl-4">
                  {state.answer.caveats.map((c, i) => (
                    <li key={i}>{c}</li>
                  ))}
                </ul>
              </details>
            )}
          </>
        )}
      </div>
      <Tabs defaultValue="trace" className="border-t border-border px-4 pb-4">
        <TabsList>
          <TabsTrigger value="trace">Trace</TabsTrigger>
          <TabsTrigger value="retrieved">Retrieved · {retrieved.length}</TabsTrigger>
          <TabsTrigger value="run">Run</TabsTrigger>
        </TabsList>
        <TabsContent value="trace" className="flex flex-col gap-3">
          <ToolTimeline calls={callsOfStream(state.calls)} latencyMs={state.run?.latencyMs} tools={state.runStart?.tools} onSelect={(id) => setSel(sel === id ? undefined : id)} selected={sel} />
          {call && (
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              <div>
                <p className="mb-1 text-xs text-text-2">Input (model arguments; identity is injected by the gateway)</p>
                <JsonView value={call.start.input} maxHeight={220} />
              </div>
              <div>
                <p className="mb-1 text-xs text-text-2">Result · {call.end?.status ?? "running"}</p>
                <JsonView value={call.end ? { summary: call.end.summary, template: call.end.template, total: call.end.total, rows: call.end.rows, units: call.end.units, error: call.end.error } : "running"} maxHeight={220} />
              </div>
            </div>
          )}
        </TabsContent>
        <TabsContent value="retrieved">
          <RetrievedList items={retrieved} onOpen={(r) => nav(`/workbench/units/${encodeURIComponent(r.unitId)}`)} />
        </TabsContent>
        <TabsContent value="run">
          {state.run ? (
            <DataList
              rows={[
                ["Run", <Link className="font-mono text-mono-sm hover:underline" to={`/runs/${encodeURIComponent(state.run.runId)}`}>{state.run.runId}</Link>],
                ["Model", <span className="font-mono text-mono-sm">{state.run.modelId}</span>],
                ["Config", <span className="font-mono text-mono-sm" title={state.run.configSha}>{state.run.configSha.slice(0, 12)}</span>],
                ["Prompt", <span className="font-mono text-mono-sm" title={state.run.promptSha}>{state.run.promptSha.slice(0, 12)}</span>],
                ["Corpus", <span className="font-mono text-mono-sm" title={state.run.corpusHash}>{state.run.corpusHash.slice(0, 12)}</span>],
                ["Usage", <span className="tnum text-sm">{Object.entries(state.run.usage).map(([k, v]) => `${k} ${v}`).join(" · ")}</span>],
              ]}
            />
          ) : (
            <p className="text-sm text-text-2">The footer arrives with the last event.</p>
          )}
        </TabsContent>
      </Tabs>
    </section>
  );
}

/**
 * W-08 Trace: the composed follow-the-number view (replaces the v1 lineage JSON). Left: the masked Gold page with
 * the unit boundary, the field, its engine blocks and the crosshair; pan and zoom. Right: the chain of hops from the
 * figure to the uploaded bytes and the pipeline and run, and (when the run has one) the naive lane's citation for
 * the same figure. URL: /workbench/trace/:unitId?field=&run=&contrast=1
 */
import { useQuery } from "@tanstack/react-query";
import { Waypoints } from "lucide-react";
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { useApi, useMe } from "../../api/context";
import { allRuns } from "../../api/runs";
import { type Trace, composeContrast, composeTrace } from "../../api/trace";
import { RpApiError } from "../../api/types";
import { Page, PageHeader } from "../../app/Page";
import { Button, EmptyState, ErrorState, LaneBadge, type Provenance, ProvenanceScope, ScopeTag, Skeleton, ToggleChips } from "../../design";
import { FULL_CHAIN, TraceChain } from "./TraceChain";
import { TraceCanvas, traceFrames } from "./TraceCanvas";

type Layer = "unit" | "field" | "blocks" | "crosshair";

export default function TraceScreen() {
  const { unitId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const fieldId = params.get("field") ?? undefined;
  const runId = params.get("run") ?? undefined;
  const contrastOn = params.get("contrast") === "1";
  const api = useApi();
  const { sub } = useMe();
  const [layers, setLayers] = useState<Layer[]>(["unit", "field", "crosshair"]);

  const trace = useQuery({ queryKey: ["trace", sub, unitId, fieldId ?? "", runId ?? ""], queryFn: () => composeTrace(api, { unitId, fieldId, runId }) });
  const definition = useQuery({
    queryKey: ["definition", sub],
    queryFn: () => api.getGovernedDefinition("qualifying_income"),
    enabled: Boolean(trace.data?.operand),
    retry: false,
  });
  const contrast = useQuery({
    queryKey: ["contrast", sub, runId ?? "", unitId, fieldId ?? ""],
    enabled: contrastOn && Boolean(trace.data?.run),
    queryFn: async () => {
      const t = trace.data!;
      const run = await api.getRun(t.run!.runId);
      // the latest naive run of the same question
      const naive = (await allRuns(api, run.packageId)).filter((r) => r.pipelineVersion === "1.0" && r.question === run.question).sort((a, b) => b.createdAt.localeCompare(a.createdAt))[0];
      return naive ? ((await composeContrast(api, naive.runId, t.claim.value, t)) ?? null) : null;
    },
  });

  if (trace.error) {
    const e = trace.error instanceof RpApiError ? trace.error : undefined;
    return (
      <Page>
        <PageHeader crumbs={[{ label: "Workbench" }, { label: "Trace" }]} title="Trace" />
        <ErrorState
          title={e?.status === 403 ? "You don't have access to this region." : "This figure could not be traced"}
          message={e?.status === 403 ? "The region service checks the package ACL for every request." : trace.error instanceof Error ? trace.error.message : String(trace.error)}
          code={e?.code}
          requestId={e?.requestId}
        />
      </Page>
    );
  }
  if (!trace.data)
    return (
      <Page>
        <Skeleton className="h-[640px] rounded-md" />
      </Page>
    );

  const t = trace.data;
  const provenance: Provenance = api.mode === "http" ? "measured" : "local-run";
  const toggleContrast = () => {
    const next = new URLSearchParams(params);
    if (contrastOn) next.delete("contrast");
    else next.set("contrast", "1");
    setParams(next, { replace: true });
  };
  const fr = traceFrames(t);

  return (
    <Page wide className="flex h-full flex-col">
      <PageHeader
        crumbs={[{ label: "Workbench" }, { label: "Trace" }, { label: t.unit.label }]}
        title={t.field ? `${t.field.label} · ${t.field.value}` : t.unit.label}
        meta={<LaneBadge pv={t.pipeline.pipelineVersion} />}
        description={t.run ? `Cited by run ${t.run.runId}. Every hop below is read from the API; nothing is typed.` : "Every hop below is read from the API; nothing is typed."}
        actions={
          <>
            {t.run && (
              <Button size="sm" variant={contrastOn ? "primary" : "secondary"} onClick={toggleContrast}>
                Contrast with 1.0
              </Button>
            )}
            {t.run && (
              <Button size="sm" variant="secondary" asChild>
                <Link to={`/runs/${encodeURIComponent(t.run.runId)}`}>Open run</Link>
              </Button>
            )}
          </>
        }
      />
      <ProvenanceScope provenance={provenance} source={t.sources.map((s) => s.route).join(" · ")}>
        <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_420px]">
          <section className="flex min-h-[560px] min-w-0 flex-col overflow-hidden rounded-md border border-border bg-surface">
            <div className="flex h-10 shrink-0 items-center gap-2 border-b border-border px-3 text-xs text-text-2">
              <span className="truncate">
                {t.source.fileName} · page {t.region.page} · masked Gold render
              </span>
              <ToggleChips
                className="ml-auto"
                aria-label="Layers"
                value={layers}
                onValueChange={(v) => setLayers(v as Layer[])}
                items={[
                  { value: "unit", label: "unit", swatch: <Swatch color="var(--boundary)" /> },
                  { value: "field", label: "field", swatch: <Swatch color="var(--cited)" /> },
                  { value: "blocks", label: "engine blocks", swatch: <Swatch color="var(--layout)" dashed /> },
                  { value: "crosshair", label: "coordinates", swatch: <Swatch color="var(--text-2)" /> },
                ]}
              />
            </div>
            <div className="relative min-h-0 flex-1">
              <TraceCanvas
                trace={t}
                focus={fr.unit}
                animate
                interactive
                layers={{ unit: on(layers, "unit"), field: on(layers, "field"), mark: on(layers, "field"), blocks: on(layers, "blocks"), crosshair: on(layers, "crosshair") }}
              />
            </div>
          </section>
          <aside className="scroll-fade-b min-h-0 overflow-y-auto pb-6">
            <h2 className="mb-2 flex items-center gap-2 text-caps uppercase text-text-2">
              <span className="flex-1">Lineage</span>
              <ScopeTag />
            </h2>
            <TraceChain trace={t} hops={contrastOn ? [...FULL_CHAIN, "contrast"] : FULL_CHAIN} definition={definition.data} contrast={contrast.data ?? undefined} actions />
            {contrastOn && contrast.data === null && (
              <EmptyState icon={<Waypoints />} title="No naive run of this question" className="mt-2">
                Ask the same question on Naive · 1.0 to compare citations.
              </EmptyState>
            )}
            <Sources t={t} />
          </aside>
        </div>
      </ProvenanceScope>
    </Page>
  );
}

function Swatch({ color, dashed }: { color: string; dashed?: boolean }) {
  return <span className="inline-block size-2.5 rounded-[2px] border" style={{ borderColor: color, borderStyle: dashed ? "dashed" : "solid" }} aria-hidden />;
}

const on = (ls: Layer[], l: Layer) => (ls.includes(l) ? 1 : 0);

function Sources({ t }: { t: Trace }) {
  return (
    <details className="mt-3 rounded-md border border-border bg-surface px-3 py-2 text-xs text-text-2">
      <summary className="cursor-pointer select-none text-text-2">Routes and objects ({t.sources.length})</summary>
      <ul className="mt-2 flex flex-col gap-1.5">
        {t.sources.map((s) => (
          <li key={s.label} className="flex flex-col">
            <span className="text-text">
              {s.label} · <span className="font-mono">{s.route}</span>
            </span>
            {s.key && <span className="break-all font-mono text-text-2">{s.key}</span>}
            {(s.versionId || s.sha256) && (
              <span className="font-mono text-text-3">
                {s.versionId && `version ${s.versionId.slice(0, 8)}`} {s.sha256 && `sha256 ${s.sha256.slice(0, 12)}`}
              </span>
            )}
          </li>
        ))}
      </ul>
    </details>
  );
}

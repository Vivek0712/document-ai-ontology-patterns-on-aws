/**
 * W-12 Governance · PII gate: the gate's decision for every unit of the package, per lane. A GateFlow summary,
 * the rule as configured, a table (unit · document · type · decision · entities · max score · indexed · in graph)
 * and a drawer with the masked Gold page and the held outline. No span text anywhere: decisions carry type,
 * score and digit count only. URL: /governance/pii?lane=2.3.1&unit=<unitId>
 */
import { useQuery } from "@tanstack/react-query";
import { Check, Minus, Play, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi, useMe } from "../../api/context";
import { type PiiUnitDecision, type PiiView, composePii } from "../../api/pii";
import { waitForLanes } from "../../api/runs";
import { RpApiError } from "../../api/types";
import { Page, PageHeader } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import {
  Badge,
  Button,
  CitationChip,
  DataList,
  ErrorState,
  LaneBadge,
  Num,
  type Provenance,
  ProvenanceScope,
  ScopeTag,
  SegmentedControl,
  Sheet,
  Skeleton,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Table,
} from "../../design";
import { GateFlow, type GateToken } from "../../viz/GateFlow";
import { type Rect, toRect } from "../../viz/geom";
import { Boundary, HeldRegions, PageCanvas } from "../../viz/PageCanvas";

const LANES = ["2.3.1", "1.0"] as const;

/** Wording for a hit under the hold rule. */
const BELOW_THRESHOLD = "Last-4 only: below the hold rule's identifier length.";

export default function PiiScreen() {
  const api = useApi();
  const { sub } = useMe();
  const { packageId } = useWorkspace();
  const [params, setParams] = useSearchParams();
  const lane = (LANES as readonly string[]).includes(params.get("lane") ?? "") ? (params.get("lane") as (typeof LANES)[number]) : "2.3.1";
  const unitParam = params.get("unit");
  const q = useQuery({
    queryKey: ["pii", sub, packageId, lane],
    queryFn: async () => {
      await waitForLanes(api, packageId);
      const [view, pkg] = await Promise.all([composePii(api, packageId, lane), api.getPackage(packageId)]);
      return { view, pkg };
    },
  });
  const set = (k: string, v?: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
  };
  const provenance: Provenance = api.mode === "http" ? "measured" : "local-run";

  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Governance" }, { label: "PII gate" }]}
        title="PII gate"
        description="Every unit passes through Amazon Comprehend before Gold. A unit with an unmasked identifier is held in Silver: never promoted, indexed or written to the graph."
        actions={
          <>
            <SegmentedControl aria-label="Lane" size="sm" value={lane} onValueChange={(v) => set("lane", v)} items={LANES.map((pv) => ({ value: pv, label: <LaneBadge pv={pv} className="border-0 bg-transparent px-0" /> }))} />
            <Button size="sm" variant="secondary" asChild>
              <Link to="/showcase/sc-6">
                <Play />
                Play explainer
              </Link>
            </Button>
          </>
        }
      />
      {q.error ? (
        <ErrorState title="The gate's decisions could not load" message={q.error instanceof Error ? q.error.message : String(q.error)} code={q.error instanceof RpApiError ? q.error.code : undefined} />
      ) : !q.data ? (
        <Skeleton className="h-[480px] rounded-md" />
      ) : (
        <ProvenanceScope provenance={provenance} source="unit views (PiiDecision + UnitStatus) · pii_gate/decisions.json · promote held_regions.json">
          <Body view={q.data.view} docs={q.data.pkg.documents} selected={unitParam} onSelect={(u) => set("unit", u)} />
        </ProvenanceScope>
      )}
    </Page>
  );
}

function Body({ view, docs, selected, onSelect }: { view: PiiView; docs: { docId: string; fileName: string; sha256?: string }[]; selected: string | null; onSelect: (unitId?: string) => void }) {
  const held = view.units.filter((u) => u.action === "hold");
  const passed = view.units.filter((u) => u.action !== "hold");
  const indexed = passed.filter((u) => u.indexed).length;
  const inGraph = passed.filter((u) => u.inGraph).length;
  const graphLane = passed.some((u) => u.inGraph !== null);
  // held units stay in Silver (their token never leaves it); the passed units are one summary row in Gold
  const tokens: GateToken[] = [
    { id: "passed", label: `${passed.length} units passed`, meta: graphLane ? `${indexed} indexed · ${inGraph} in graph` : `${indexed} indexed`, travel: 3, silver: false, state: "passed" },
    ...held.map((u) => ({ id: u.unitId, label: u.label.split(" · ").slice(-2).join(" · "), meta: u.fileName, state: "held" as const, note: <HitLine u={u} /> })),
  ];
  const sel = view.units.find((u) => u.unitId === selected);
  return (
    <div className="flex flex-col gap-5">
      <section className="rounded-md border border-border bg-surface p-4">
        <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span className="text-caps uppercase text-text-2">Summary</span>
          {(["units", "held", "masked", "passed"] as const).map((k) => (
            <span key={k} className="flex items-baseline gap-1.5 text-text-2">
              <Num value={view.counters[k]} kind="int" />
              {k === "masked" ? "below threshold" : k}
            </span>
          ))}
          <span className="ml-auto flex items-center gap-2 text-xs text-text-2">
            <ScopeTag />
          </span>
        </div>
        {view.errors.length > 0 && (
          <p className="mb-3 flex items-start gap-1.5 rounded-sm border border-border bg-surface-2 px-2.5 py-1.5 text-xs text-text-2" role="status">
            <TriangleAlert className="mt-px size-3.5 shrink-0" aria-hidden />
            <span>Some inputs could not be read, so parts of this view are unknown: {view.errors.join("; ")}.</span>
          </p>
        )}
        <GateFlow
          docs={docs.map((d) => ({ id: d.docId, label: d.fileName, sha: d.sha256?.slice(0, 8) }))}
          tokens={tokens}
          compact
          counts={{
            silver: (
              <span className="inline-flex items-baseline gap-1">
                <Num value={view.counters.units} kind="int" bare unit="units" />
                <span className="text-text-3">·</span>
                <Num value={view.counters.held} kind="int" bare unit="held" />
              </span>
            ),
            gold: <Num value={view.counters.passed} kind="int" bare unit="units" />,
          }}
          gate={<RuleCard view={view} />}
        />
      </section>

      <section className="rounded-md border border-border bg-surface">
        <Table>
          <THead>
            <tr>
              <TH>Unit</TH>
              <TH>Document</TH>
              <TH>Type</TH>
              <TH>Decision</TH>
              <TH>Entities</TH>
              <TH numeric>Max score</TH>
              <TH>Indexed</TH>
              <TH>In graph</TH>
            </tr>
          </THead>
          <TBody>
            {view.units.map((u) => (
              <TR key={u.unitId} interactive onClick={() => onSelect(u.unitId)} data-action={u.action} className={u.unitId === selected ? "bg-surface-2" : undefined}>
                <TD className="max-w-[220px] truncate" title={u.unitId}>
                  <button
                    type="button"
                    className="max-w-full truncate rounded-xs text-left hover:underline focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-text-2"
                    onClick={(e) => {
                      e.stopPropagation();
                      onSelect(u.unitId);
                    }}
                    aria-label={`Open ${u.label}`}
                  >
                    {u.label.split(" · ").slice(-1)[0]} <span className="font-mono text-mono-sm text-text-3">{u.unitId.split(":").pop()}</span>
                  </button>
                </TD>
                <TD className="max-w-[180px] truncate font-mono text-mono-sm text-text-2" title={u.fileName}>{u.fileName}</TD>
                <TD>
                  <Badge tone="concept" dot>{u.unitType}</Badge>
                </TD>
                <TD>
                  <Decision u={u} />
                </TD>
                <TD className="font-mono text-mono-sm">{u.hits.length ? u.hits.map((h) => h.type).join(", ") : <span className="text-text-3">none</span>}</TD>
                <TD numeric>{u.hits.length ? <Num value={Math.max(...u.hits.map((h) => h.score))} kind="ratio" /> : <span className="text-text-3">—</span>}</TD>
                <TD>{yesNo(u.indexed)}</TD>
                <TD>{yesNo(u.inGraph)}</TD>
              </TR>
            ))}
          </TBody>
        </Table>
      </section>
      <UnitDrawer u={sel} view={view} onClose={() => onSelect(undefined)} />
    </div>
  );
}

/** The gate as configured, pinned inside the gate column: rule · action · min score. */
function RuleCard({ view }: { view: PiiView }) {
  const c = view.config;
  if (!c) return <p className="rounded-xs border border-border px-2 py-1.5 text-xs text-text-3">gate config unavailable</p>;
  return (
    <div className="flex flex-col gap-0.5 rounded-xs border border-boundary bg-surface px-2 py-1.5 text-xs">
      <span className="truncate font-mono text-mono-sm text-text">{c.rule}</span>
      <span className="flex items-baseline gap-1 text-text-2">
        {c.action} <span className="text-text-3">·</span> min score <Num value={c.minScore} kind="raw" provenance="design-target" source={`${view.configKey ?? "lane config"} · preprocessing.piiGate.minScore`} bare size="xs" />
      </span>
    </div>
  );
}

function Decision({ u }: { u: PiiUnitDecision }) {
  if (u.action === "hold") return <CitationChip kind="held" label={`held · ${u.rule}`} />;
  if (u.action === "below_threshold")
    return (
      <span className="text-sm text-text-2" title={BELOW_THRESHOLD}>
        below threshold
      </span>
    );
  return <span className="text-sm text-text-2">pass</span>;
}

function HitLine({ u }: { u: PiiUnitDecision }) {
  const h = u.hits.find((x) => !x.masked) ?? u.hits[0];
  if (!h) return null;
  return (
    <>
      <span className="font-mono text-mono-sm">{h.type}</span>
      <Num value={h.score} kind="ratio" bare size="xs" />
      <span>·</span>
      <Num value={h.digits} kind="int" bare size="xs" unit="digits" />
    </>
  );
}

function yesNo(v: boolean | null): ReactNode {
  if (v === null) return <span className="text-xs text-text-3">no graph lane</span>;
  return v ? (
    <span className="inline-flex items-center gap-1 text-text">
      <Check className="size-3.5" aria-hidden />
      yes
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 text-text-2">
      <Minus className="size-3.5" aria-hidden />
      no
    </span>
  );
}

function UnitDrawer({ u, view, onClose }: { u?: PiiUnitDecision; view: PiiView; onClose: () => void }) {
  const page = u && view.goldPages.find((g) => g.docId === u.docId && g.page === (u.pages[0] ?? 1));
  const region = useRegionSize(u);
  return (
    <Sheet open={Boolean(u)} onOpenChange={(o) => !o && onClose()} title={u?.label ?? ""} description={u ? `${u.unitId} · ${u.pipelineVersion}` : undefined} width={480}>
      {u && (
        <div className="flex flex-col gap-4 p-4">
          <DataList
            rows={[
              ["Decision", <Decision u={u} />],
              ["Rule", <span className="font-mono text-mono-sm">{u.rule}</span>],
              ["Tier", <span>{u.tier === "silver" ? "stays in Silver" : "promoted to Gold"}</span>],
              ["Pages", <span className="tnum">{u.pages.join(", ")}</span>],
              ["Indexed", yesNo(u.indexed)],
              ["In graph", yesNo(u.inGraph)],
              ["Decisions object", <span className="break-all font-mono text-mono-sm">{u.decisions.key}</span>],
            ]}
          />
          {u.hits.length > 0 && (
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs text-text-2">
                  <th className="pb-1 font-medium">Entity</th>
                  <th className="pb-1 text-right font-medium">Score</th>
                  <th className="pb-1 text-right font-medium">Digits</th>
                  <th className="pb-1 text-right font-medium">Span</th>
                  <th className="pb-1 font-medium">&nbsp;Masked</th>
                </tr>
              </thead>
              <tbody>
                {u.hits.map((h, i) => (
                  <tr key={i} className="border-t border-border">
                    <td className="py-1 font-mono text-mono-sm">{h.type}</td>
                    <td className="py-1 text-right"><Num value={h.score} kind="ratio" /></td>
                    <td className="py-1 text-right"><Num value={h.digits} kind="int" /></td>
                    <td className="py-1 text-right"><Num value={h.spanLength} kind="int" unit="chars" /></td>
                    <td className="py-1">&nbsp;{h.masked ? "yes" : "no"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {u.action === "below_threshold" && <p className="text-xs text-text-2">{BELOW_THRESHOLD}</p>}
          {page?.heldRegionsError && (
            <p className="flex items-center gap-1.5 text-xs text-text-2" role="status">
              <TriangleAlert className="size-3.5" aria-hidden />
              held regions unavailable: the outlines are not drawn (the render itself is masked)
            </p>
          )}
          {page?.url && region ? (
            <div className="h-[420px] overflow-hidden rounded-md border border-border">
              <PageCanvas ariaLabel={`${u.fileName}, Gold page ${page.page}`} image={{ src: page.url }} pageWidth={region.w} pageHeight={region.h} focus={[0, 0, region.w, region.h] as Rect} animate={false}>
                <HeldRegions regions={page.heldRegions.map(toRect)} />
                {u.action !== "hold" && u.bboxes.filter((b) => b.page === page.page).map((b, i) => <Boundary key={i} rect={toRect(b)} width={1} tint={0.5} />)}
              </PageCanvas>
            </div>
          ) : (
            <p className="text-xs text-text-2">No Gold page image for this unit's page.</p>
          )}
          <p className="text-xs text-text-2">The page is the masked Gold render: held regions are filled black before the image is written. No span text is ever sent to the browser.</p>
        </div>
      )}
    </Sheet>
  );
}

/** Page size from any unit on the same document (the region API knows it; held units have no region). */
function useRegionSize(u?: PiiUnitDecision): { w: number; h: number } | undefined {
  const api = useApi();
  const { sub } = useMe();
  const q = useQuery({
    queryKey: ["page-size", sub, u?.docId ?? ""],
    enabled: Boolean(u),
    queryFn: async () => {
      const list = await api.listUnits(u!.docId, u!.pipelineVersion === "1.0" ? "2.3.1" : u!.pipelineVersion);
      const open = list.items.find((i) => !i.held);
      if (!open) return null;
      const r = await api.getRegion(open.unitId);
      return { w: r.pageWidth, h: r.pageHeight };
    },
  });
  return q.data ?? undefined;
}

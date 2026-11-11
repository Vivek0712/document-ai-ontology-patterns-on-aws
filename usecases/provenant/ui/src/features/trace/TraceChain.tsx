/**
 * The follow-the-number chain (W-08, SC-9, SC-0 step 7): one card per hop, from the cited figure down to the
 * uploaded bytes and the pipeline that produced it. Every value is a field of the composed `Trace`; numbers render
 * through <Num> inside the caller's ProvenanceScope.
 *
 * The chain is a pure function of its props: `hops` picks and orders the cards, `shown` reveals the first n
 * (the last one fades in with `progress`), `focus` marks the card the story is on. W-08 shows every hop.
 */
import { Check, ChevronRight, Copy, X } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import type { LineageStep, StageRef } from "@contracts/api";
import type { GitGovernedDefinition } from "../../api/governance";
import { curie } from "../../api/governance";
import type { Trace, TraceContrast } from "../../api/trace";
import { Badge, CitationChip, IconButton, LaneBadge, Num, Tooltip, cn, toast } from "../../design";
import { clamp01 } from "../../viz/geom";
import { REGION_DIGITS } from "./TraceCanvas";

export type HopId = "claim" | "field" | "unit" | "blocks" | "region" | "page" | "source" | "index" | "graph" | "pipeline" | "run" | "definition" | "contrast";

export const FULL_CHAIN: HopId[] = ["claim", "field", "unit", "blocks", "region", "page", "source", "index", "graph", "pipeline", "run", "definition"];

interface Props {
  trace: Trace;
  hops?: HopId[];
  /** how many hops are visible; the last visible one fades in with `progress` */
  shown?: number;
  progress?: number;
  focus?: HopId;
  definition?: GitGovernedDefinition;
  contrast?: TraceContrast;
  /** a sha256 seen earlier in the story (SC-0 step 1): the source card states the comparison */
  compareSha?: { sha256: string; label: string };
  /** card actions (copy, open): workbench only */
  actions?: boolean;
  /** hops already seen, drawn as one line (title and key value) so the newest card has room */
  compact?: HopId[];
  className?: string;
}

export function TraceChain({ trace, hops = FULL_CHAIN, shown = hops.length, progress = 1, focus, definition, contrast, compareSha, actions, compact = [], className }: Props) {
  const list = hops.filter((h) => available(h, trace, definition, contrast));
  const visible = Math.min(shown, list.length);
  const ref = useRef<HTMLOListElement>(null);
  // a story's focused hop is scrolled into its container (DOM only; the frame itself stays a pure function)
  useEffect(() => {
    const el = focus ? ref.current?.querySelector<HTMLElement>(`[data-hop="${focus}"]`) : undefined;
    if (!el) return;
    const reduced = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    el.scrollIntoView?.({ block: "nearest", behavior: reduced ? "auto" : "smooth" });
  }, [focus, shown]);
  return (
    <ol ref={ref} className={cn("relative flex flex-col gap-1.5", className)} aria-label="Lineage, from the figure to the uploaded file">
      {list.map((h, i) => {
        if (i >= shown) return null;
        const appear = i === shown - 1 ? clamp01(progress / 0.6) : 1;
        return (
          <li key={h} className="relative flex gap-2.5" style={{ opacity: appear, transform: `translateY(${(1 - appear) * 4}px)` }} data-hop={h}>
            <Rail first={i === 0} last={i === visible - 1} active={focus === h} />
            <div className="min-w-0 flex-1 pb-0.5">
              {compact.includes(h) && focus !== h ? (
                <CompactHop id={h} trace={trace} />
              ) : (
                <Hop id={h} trace={trace} definition={definition} contrast={contrast} compareSha={compareSha} focused={focus === h} actions={actions} />
              )}
            </div>
          </li>
        );
      })}
    </ol>
  );
}

/** A hop already seen, as one line: its title and the value it contributes. */
function CompactHop({ id, trace: t }: { id: HopId; trace: Trace }) {
  const b = t.region.bbox;
  const body: Partial<Record<HopId, [string, ReactNode]>> = {
    claim: ["Figure", <Num value={t.claim.value || t.claim.text} kind="money" size="xs" bare />],
    field: ["Field", t.field ? <span className="flex min-w-0 items-baseline gap-1.5"><span className="truncate">{t.field.label}</span><Num value={t.field.value} kind="money" size="xs" bare /></span> : "—"],
    unit: ["Evidence unit", <span className="truncate">{t.unit.label}</span>],
    region: ["Region", <span className="tnum truncate font-mono text-mono-sm" title={`page ${t.region.page}, PDF points`}>({pt(b.x0)}, {pt(b.y0)})–({pt(b.x1)}, {pt(b.y1)})</span>],
    source: ["Uploaded file", <span className="font-mono text-mono-sm">{sha12(t.source.sha256)}</span>],
  };
  const [title, value] = body[id] ?? [id, null];
  return (
    <div className="flex h-7 min-w-0 items-center gap-2 rounded-md border border-border bg-surface px-3 text-sm text-text">
      <span className="shrink-0 text-xs font-medium text-text-2">{title}</span>
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5 truncate">{value}</span>
    </div>
  );
}

function available(h: HopId, t: Trace, def?: GitGovernedDefinition, contrast?: TraceContrast): boolean {
  switch (h) {
    case "field":
      return Boolean(t.field);
    case "index":
      return Boolean(t.index);
    case "graph":
      return Boolean(t.graph);
    case "run":
      return Boolean(t.run);
    case "definition":
      return Boolean(def);
    case "contrast":
      return Boolean(contrast);
    default:
      return true;
  }
}

/** The vertical rail: a dot per hop, joined by a 1 px line. */
function Rail({ first, last, active }: { first: boolean; last: boolean; active: boolean }) {
  return (
    <div className="relative flex w-3 shrink-0 justify-center" aria-hidden>
      {!first && <span className="absolute top-0 h-3 w-px bg-border-strong" />}
      {!last && <span className="absolute bottom-[-6px] top-3 w-px bg-border-strong" />}
      <span className={cn("relative mt-[9px] size-[7px] rounded-full border", active ? "border-text bg-text" : "border-border-strong bg-surface")} />
    </div>
  );
}

const sha12 = (s?: string) => (s ? s.slice(0, 12) : "—");
/** PDF points at the canvas ticks' precision. */
const pt = (n: number) => n.toFixed(REGION_DIGITS);
const stepOf = (t: Trace, kind: LineageStep["kind"]) => t.steps.find((s) => s.kind === kind);

function Mono({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span className="break-all font-mono text-mono-sm text-text" title={title}>
      {children}
    </span>
  );
}

function Hop({ id, trace: t, definition, contrast, compareSha, focused, actions }: { id: HopId; trace: Trace; definition?: GitGovernedDefinition; contrast?: TraceContrast; compareSha?: Props["compareSha"]; focused: boolean; actions?: boolean }) {
  switch (id) {
    case "claim":
      return (
        <Card title="Figure in the answer" focused={focused} meta={<LaneBadge pv={t.claim.lane} />}>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <Num value={t.claim.value || t.claim.text} kind="money" size="md" mark={t.claim.resolvable ? "cited" : "loss"} />
            {t.operand && (
              <span className="text-xs text-text-2">
                one of {t.operand.operands.length} operands of <Num value={t.operand.total} kind="money" /> · <span className="font-mono">{t.operand.template}</span>
              </span>
            )}
          </div>
        </Card>
      );
    case "field":
      return (
        <Card title="Field" focused={focused} stage={stepOf(t, "field")?.stage}>
          <Rows
            rows={[
              ["Label", <span className="text-sm">{t.field!.label}</span>],
              ["Value", <Num value={t.field!.value} kind="money" />],
              ["Concept", <Badge tone="concept" dot className="font-mono">{t.field!.concept ?? "—"}</Badge>],
              ["Period", <Mono>{t.field!.period ?? "—"}</Mono>],
              ["Confidence", t.field!.confidence == null ? <span className="text-text-2" title={t.field!.source}>not scored</span> : <Num value={t.field!.confidence} kind="ratio" />],
            ]}
          />
        </Card>
      );
    case "unit":
      return (
        <Card title="Evidence unit" focused={focused} stage={stepOf(t, "unit")?.stage} copy={actions ? t.unit.unitId : undefined}>
          <Rows
            rows={[
              ["Unit", <span className="text-sm">{t.unit.label}</span>],
              ["Id", <Mono>{t.unit.unitId.split(":").slice(-2).join(":")}</Mono>],
              ["Type", <Badge tone="concept" dot>{t.unit.unitType}</Badge>],
              ...(t.unit.memberCount !== undefined ? [["Members", <Num value={t.unit.memberCount} kind="int" unit="blocks" />] as [string, ReactNode]] : []),
            ]}
          />
        </Card>
      );
    case "blocks":
      return (
        <Card title="Engine blocks" focused={focused} stage={stepOf(t, "blocks")?.stage}>
          <p className="mb-1.5 truncate text-xs text-text-2" title={t.blocks.engineVersion ?? t.blocks.engine}>
            {t.blocks.engineVersion ?? t.blocks.engine}
          </p>
          <ul className="flex flex-col gap-1">
            {t.blocks.items.map((b) => (
              <li key={b.pointer} className="flex items-center gap-2 text-sm">
                <Mono>{b.pointer}</Mono>
                <Badge tone="layout">{b.engineLabel ?? "block"}</Badge>
                {b.text && <span className="tnum ml-auto truncate font-mono text-mono-sm text-text-2">{b.text}</span>}
              </li>
            ))}
          </ul>
        </Card>
      );
    case "region": {
      const b = t.region.bbox;
      return (
        <Card title="Page region" focused={focused} stage={stepOf(t, "region")?.stage}>
          <Rows
            rows={[
              ["Page", <Num value={t.region.page} kind="int" />],
              [
                "Box",
                <span className="tnum font-mono text-mono-sm">
                  (<Num value={pt(b.x0)} />, <Num value={pt(b.y0)} />) – (<Num value={pt(b.x1)} />, <Num value={pt(b.y1)} />) pt
                </span>,
              ],
              ["Frame", <span className="text-text-2">PDF points, top-left origin</span>],
            ]}
          />
        </Card>
      );
    }
    case "page": {
      const img = stepOf(t, "page_image")?.artifacts.find((a) => a.kind === "page_image");
      return (
        <Card title="Gold page image" focused={focused} stage={stepOf(t, "page_image")?.stage}>
          <Rows
            rows={[
              ["Render", <Num value={t.region.image.dpi} kind="int" unit="dpi" />],
              ["Held regions", <Num value={t.region.heldRegions.length} kind="int" unit="filled black" />],
              ["Object", <Mono title={img?.key}>{img?.key.split("/").slice(-3).join("/")}</Mono>],
            ]}
          />
        </Card>
      );
    }
    case "source":
      return (
        <Card title="Uploaded file · Bronze" focused={focused} stage={stepOf(t, "source")?.stage} copy={actions ? t.source.sha256 : undefined}>
          <Rows
            rows={[
              ["File", <Mono>{t.source.fileName}</Mono>],
              ["sha256", <Mono title={t.source.sha256}>{sha12(t.source.sha256)}</Mono>],
              ["Version", <Mono title={t.source.versionId}>{t.source.versionId?.slice(0, 8) ?? "—"}</Mono>],
              ...(t.source.sizeBytes !== undefined ? [["Size", <Num value={t.source.sizeBytes} kind="int" unit="bytes" />] as [string, ReactNode]] : []),
            ]}
          />
          <Verdict ok={t.source.matchesRegister}>{t.source.matchesRegister ? "matches the sha256 register wrote" : "does not match the registered sha256"}</Verdict>
          {compareSha && (
            <Verdict ok={compareSha.sha256 === t.source.sha256}>
              {compareSha.sha256 === t.source.sha256 ? `same object as ${compareSha.label}` : `differs from ${compareSha.label}`}
            </Verdict>
          )}
        </Card>
      );
    case "index":
      return (
        <Card title="Index" focused={focused} stage={stepOf(t, "index")?.stage}>
          <Rows rows={[["Index", <Mono>{t.index!.index}</Mono>], ["_id", <Mono>{t.index!.aossId}</Mono>]]} />
        </Card>
      );
    case "graph":
      return (
        <Card title="Graph" focused={focused} stage={stepOf(t, "graph")?.stage}>
          <Rows rows={[["Graph", <Mono>{t.graph!.namedGraph}</Mono>], ["Fact", <Badge tone="concept" dot className="font-mono">{t.graph!.factConcept ?? "—"}</Badge>]]} />
        </Card>
      );
    case "pipeline":
      return (
        <Card title="Pipeline" focused={focused} meta={<LaneBadge pv={t.pipeline.pipelineVersion} />}>
          <Rows
            rows={[
              ["configSha", <Mono title={t.pipeline.configSha}>{sha12(t.pipeline.configSha)}</Mono>],
              ["preprocessing", <Mono title={t.pipeline.preprocessingSha}>{sha12(t.pipeline.preprocessingSha)}</Mono>],
              ["codeSha", <Mono>{t.pipeline.codeSha ?? "—"}</Mono>],
              ["Ontology", <Mono>{t.pipeline.ontologyVersion ?? "none"}</Mono>],
              ["Governance", t.pipeline.governance ? <Mono>{t.pipeline.governance.source} · {t.pipeline.governance.ontologyId}</Mono> : <span className="text-text-2">not bound in the lane config yet</span>],
            ]}
          />
        </Card>
      );
    case "run":
      return (
        <Card title="Run" focused={focused} copy={actions ? t.run!.runId : undefined}>
          <Rows
            rows={[
              ["Run", <Mono>{t.run!.runId}</Mono>],
              ["Model", <Mono>{t.run!.modelId}</Mono>],
              ["promptSha", <Mono title={t.run!.promptSha}>{sha12(t.run!.promptSha)}</Mono>],
              ["corpusHash", <Mono title={t.run!.corpusHash}>{sha12(t.run!.corpusHash)}</Mono>],
            ]}
          />
        </Card>
      );
    case "definition":
      return (
        <Card title="Governed definition" focused={focused} meta={<Badge tone="neutral">Git · {definition!.ontology.version}</Badge>}>
          <p className="text-sm text-text">{definition!.definition.description}</p>
          <p className="mt-1.5 font-mono text-mono-sm text-text-2">
            {curie(definition!.definition.iri)} · computed by {definition!.definition.computedBy}
          </p>
        </Card>
      );
    case "contrast":
      return <ContrastCard contrast={contrast!} focused={focused} />;
  }
}

/** The naive citation for the same figure: chunk → text layer → file, and no field, region or page. */
export function ContrastCard({ contrast, focused }: { contrast: TraceContrast; focused?: boolean }) {
  return (
    <Card title="The naive citation" focused={focused} meta={<LaneBadge pv="1.0" />} dashed>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <CitationChip kind="noregion" label={contrast.claim.chunkLabel ?? contrast.claim.label ?? "chunk"} title={contrast.claim.reason} />
        <Num value={contrast.claim.value ?? contrast.claim.text} kind="money" />
      </div>
      <ol className="flex flex-wrap items-center gap-1 text-xs text-text-2">
        {contrast.stops.map((s, i) => (
          <li key={s.kind} className="flex items-center gap-1">
            {i > 0 && <ChevronRight className="size-3 text-text-3" aria-hidden />}
            <span className="rounded-xs border border-border px-1.5 py-0.5 text-text">{s.title}</span>
          </li>
        ))}
      </ol>
      {contrast.missing.length > 0 && (
        <p className="mt-2 flex items-center gap-1.5 text-xs text-text-2">
          <X className="size-3 text-loss" aria-hidden />
          no {contrast.missing.map((k) => k.replace("_", " ")).join(", ")}
        </p>
      )}
    </Card>
  );
}

function Verdict({ ok, children }: { ok: boolean; children: ReactNode }) {
  return (
    <p className="mt-2 flex items-center gap-1.5 text-xs text-text" data-verdict={ok ? "ok" : "fail"}>
      {ok ? <Check className="size-3.5 text-cited" aria-label="matches" /> : <X className="size-3.5 text-loss" aria-label="does not match" />}
      {children}
    </p>
  );
}

function Rows({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="grid grid-cols-[92px_minmax(0,1fr)] gap-x-3 gap-y-1 text-sm">
      {rows.map(([k, v], i) => (
        <div key={i} className="contents">
          <dt className="truncate text-xs leading-5 text-text-2">{k}</dt>
          <dd className="min-w-0 text-text">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function StageChip({ stage }: { stage: StageRef }) {
  return (
    <Tooltip content={`${stage.stageId} · ${stage.state} · attempt ${stage.attempt}${stage.executionArn ? ` · ${stage.executionArn.split(":").pop()}` : ""}`}>
      <span className="inline-flex shrink-0 items-center gap-1 whitespace-nowrap font-mono text-mono-sm text-text-2" tabIndex={0}>
        {stage.stageId}
        {stage.durationMs !== undefined && (
          <>
            {" · "}
            <Num value={stage.durationMs} kind="ms" size="xs" className="flex-nowrap" />
          </>
        )}
      </span>
    </Tooltip>
  );
}

function Card({ title, meta, stage, focused, dashed, copy, children }: { title: string; meta?: ReactNode; stage?: StageRef; focused?: boolean; dashed?: boolean; copy?: string; children: ReactNode }) {
  return (
    <section className={cn("rounded-md border bg-surface px-3 py-2.5 transition-colors duration-base", dashed && "border-dashed", focused ? "border-text-2" : "border-border")}>
      <header className="mb-1.5 flex min-h-5 items-center gap-2">
        <h3 className="min-w-0 flex-1 truncate text-xs font-medium text-text-2">{title}</h3>
        {stage && <StageChip stage={stage} />}
        {meta}
        {copy && (
          <IconButton
            size="sm"
            label="Copy"
            icon={<Copy />}
            onClick={() => void navigator.clipboard?.writeText(copy).then(() => toast.success("Copied"))}
          />
        )}
      </header>
      {children}
    </section>
  );
}

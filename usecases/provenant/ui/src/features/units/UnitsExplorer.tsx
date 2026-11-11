/**
 * W-06 Units explorer: one page, both lanes. 2.3.1 evidence units (boundary, members, fields) and, as a toggleable
 * overlay, the X-ray of the 1.0 lane (chunk slices, reading order, positional pairs, with a labelled what-if size).
 * URL is the state: ?pkg=&doc=&page=&layers=units,fields,chunks&size=300&focus=<unitId|chunkId>
 *
 * Colour roles on the paper: orange is the unit boundary and the held frame only; fields are neutral outlines,
 * members dashed; green appears only with a ✓ on a printed value; chunks are opt-in.
 */
import type { DocumentItem } from "@contracts/api";
import { FileText, ScanSearch } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { usePackage, useXray } from "../../api/queries";
import { type XrayPage, xrayUnusable } from "../../api/xray";
import { Page, PageHeader } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import {
  Badge,
  Button,
  EmptyState,
  ErrorState,
  LaneBadge,
  Num,
  ProvenanceTag,
  SegmentedControl,
  Skeleton,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  ToggleChips,
  cn,
} from "../../design";
import { type Rect, pad, toRect, union } from "../../viz/geom";
import { Boundary, BoxOutline, ChunkSlices, HeldRegions, PageCanvas, PairArc, ReadingPath, chunkPattern, chunkTone, toneVar } from "../../viz/PageCanvas";
import { sliceOf, stopsOf } from "../../viz/xray";

type Layer = "units" | "members" | "fields" | "chunks" | "path" | "pairs" | "held";
const ALL_LAYERS: Layer[] = ["units", "members", "fields", "chunks", "path", "pairs", "held"];
const XRAY_LAYERS: Layer[] = ["chunks", "path", "pairs"];

/** Chunks are opt-in except on the naive lane alone: with every layer on, nothing on the page reads. */
function defaultLayers(lane: string): Layer[] {
  if (lane === "1.0") return ["chunks", "path", "held"];
  return ["units", "fields", "held"];
}

const DOC_TYPE_LABEL: Record<string, string> = { "doc:W2": "W-2", "doc:Payslip": "Payslip", "doc:BankStatement": "Bank statement" };

/** "W-2 · 2025 Maya Fictus" from the doc type and the file name (the distinguishing part first, nothing invented). */
export function docDisplayName(d: Pick<DocumentItem, "fileName" | "docType">): { primary: string; file: string } {
  const type = d.docType ? (DOC_TYPE_LABEL[d.docType] ?? d.docType.replace(/^doc:/, "").replace(/([a-z])([A-Z])/g, "$1 $2")) : "Unclassified";
  const typeWords = new Set(type.toLowerCase().split(/[\s-]+/).concat(type.toLowerCase().replace(/[^a-z0-9]/g, "")));
  const stem = d.fileName.replace(/\.[a-z0-9]+$/i, "").split(/[_\s]+/).filter((w) => !typeWords.has(w.toLowerCase()));
  const rest = stem.map((w) => (/^[a-z]/.test(w) ? w[0].toUpperCase() + w.slice(1) : w)).join(" ");
  return { primary: rest ? `${type} · ${rest}` : type, file: d.fileName };
}

/** Middle ellipsis: keeps the distinguishing end of a file name visible. */
export function middleEllipsis(s: string, max = 26): string {
  if (s.length <= max) return s;
  const head = Math.ceil((max - 1) / 2);
  return `${s.slice(0, head)}…${s.slice(s.length - (max - 1 - head))}`;
}

export default function UnitsExplorer() {
  const [params, setParams] = useSearchParams();
  const { packageId, lane } = useWorkspace();
  const pkgId = params.get("pkg") ?? packageId;
  const pkg = usePackage(pkgId);
  const docs = pkg.data?.documents ?? [];
  const docId = params.get("doc") ?? docs[0]?.docId;
  const page = Number(params.get("page") ?? 1) || 1;
  const sizeParam = params.get("size");
  const layers = useMemo<Layer[]>(() => {
    const raw = params.get("layers");
    return raw === null ? defaultLayers(lane) : (raw.split(",").filter((l) => (ALL_LAYERS as string[]).includes(l)) as Layer[]);
  }, [params, lane]);
  const focusId = params.get("focus") ?? undefined;
  const xray = useXray(docId, page, sizeParam ? Number(sizeParam) : undefined);

  const set = (patch: Record<string, string | undefined>) => {
    const next = new URLSearchParams(params);
    for (const [k, v] of Object.entries(patch)) (v === undefined ? next.delete(k) : next.set(k, v));
    setParams(next, { replace: true });
  };
  const doc = docs.find((d) => d.docId === docId);
  const x = xray.data;
  const xrayOn = layers.some((l) => XRAY_LAYERS.includes(l));
  const unusable = x ? xrayUnusable(x) : null;
  const control = x?.control.sizeTokens;

  return (
    <Page wide className="flex h-full flex-col pb-4">
      <PageHeader
        crumbs={[{ label: "Workbench", to: "/workbench/packages" }, { label: pkg.data?.package.name ?? pkgId, to: `/workbench/packages/${encodeURIComponent(pkgId)}` }, { label: "Units explorer" }]}
        title={doc ? docDisplayName(doc).primary : "Units explorer"}
        meta={doc?.docType && <Badge tone="concept" dot>{doc.docType}</Badge>}
        description="2.3.1 evidence units on the masked page, with the 1.0 lane's X-ray as an overlay: where the naive chunks' characters sit, and the order the text layer reads them."
      />
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[216px_minmax(0,1fr)_300px]">
        {/* documents */}
        <nav aria-label="Documents" className="flex min-h-0 flex-col gap-0.5 overflow-y-auto rounded-md border border-border bg-surface p-1.5">
          <div className="px-2 pb-1 pt-1.5 text-caps uppercase text-text-2">Documents</div>
          {pkg.isLoading && [0, 1, 2].map((i) => <Skeleton key={i} className="mx-2 my-1 h-8" />)}
          {docs.map((d) => {
            const name = docDisplayName(d);
            return (
              <button
                key={d.docId}
                type="button"
                title={d.fileName}
                onClick={() => set({ doc: d.docId, page: undefined, focus: undefined })}
                className={cn("flex items-start gap-2 rounded-sm px-2 py-1.5 text-left", d.docId === docId ? "bg-surface-2 text-text" : "text-text-2 hover:bg-surface-2/60 hover:text-text")}
              >
                <FileText className="mt-0.5 size-3.5 shrink-0 text-text-3" aria-hidden />
                <span className="min-w-0 flex-1">
                  <span className="flex items-baseline gap-2">
                    <span className="line-clamp-2 min-w-0 flex-1 text-sm">{name.primary}</span>
                    <span className="shrink-0 text-xs text-text-3">{d.pageCount ?? "?"}p</span>
                  </span>
                  <span className="block truncate font-mono text-mono-sm text-text-3">{middleEllipsis(name.file, 22)}</span>
                </span>
              </button>
            );
          })}
        </nav>

        {/* canvas */}
        <div className="flex min-h-[480px] min-w-0 flex-col overflow-hidden rounded-md border border-border">
          <div className="flex flex-col gap-1.5 border-b border-border bg-bg-subtle px-2.5 py-2">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <ToggleChips<Layer>
                className="min-w-0"
                aria-label="Layers"
                value={layers}
                onValueChange={(v) => set({ layers: v.join(",") })}
                items={[
                  { value: "units", label: "Units", swatch: <LayerSwatch layer="units" />, title: "2.3.1 evidence-unit boundaries" },
                  { value: "members", label: "Members", swatch: <LayerSwatch layer="members" />, title: "Docling blocks inside each unit" },
                  { value: "fields", label: "Fields", swatch: <LayerSwatch layer="fields" />, title: "Typed fields (label, value, concept)" },
                  { value: "chunks", label: "Chunks", swatch: <LayerSwatch layer="chunks" />, title: "X-ray: where each 1.0 chunk's characters sit" },
                  { value: "path", label: "Order", swatch: <LayerSwatch layer="path" />, title: "X-ray: the text layer's reading order" },
                  { value: "pairs", label: "Pairs", swatch: <LayerSwatch layer="pairs" />, title: "X-ray: position-only pairing (derived hypothesis, dashed) vs the printed value (✓)" },
                  { value: "held", label: "Held", swatch: <LayerSwatch layer="held" />, title: "Regions held by the PII gate" },
                ]}
              />
              <div className="ml-auto flex items-center gap-2">
                {x && x.pageCount > 1 && (
                  <SegmentedControl
                    aria-label="Page"
                    size="sm"
                    value={String(page)}
                    onValueChange={(v) => set({ page: v, focus: undefined })}
                    items={Array.from({ length: x.pageCount }, (_, i) => ({ value: String(i + 1), label: `p${i + 1}` }))}
                  />
                )}
                {x && (
                  <>
                    <span className="text-xs text-text-2">Chunk size</span>
                    <SegmentedControl
                      aria-label="Chunk size (what-if)"
                      size="sm"
                      value={String(x.sizeTokens)}
                      onValueChange={(v) => set({ size: Number(v) === control ? undefined : v })}
                      items={x.whatIfSizes.map((s) => ({
                        value: String(s),
                        label: String(s),
                        disabled: !xrayOn || Boolean(unusable),
                        title: s === control ? `${s} tokens · the v1.0 control` : `${s} tokens · what-if re-split with the real v1.0 splitter`,
                      }))}
                    />
                  </>
                )}
              </div>
            </div>
            <LayerLegend layers={layers} whatIf={Boolean(x?.whatIf && xrayOn)} sizes={x ? [x.sizeTokens, x.control.sizeTokens] : undefined} />
          </div>
          <div className="relative min-h-0 flex-1">
            {xray.error ? (
              <div className="p-6">
                <ErrorState title="X-ray unavailable for this document" message={xray.error.message} />
              </div>
            ) : x ? (
              <ExplorerCanvas x={x} layers={unusable ? layers.filter((l) => !XRAY_LAYERS.includes(l)) : layers} focusId={focusId} />
            ) : (
              <Skeleton className="absolute inset-4 rounded-md" />
            )}
            {x?.whatIf && xrayOn && <div className="pointer-events-none absolute inset-2 rounded-md border border-dashed border-text-2" />}
          </div>
        </div>

        {/* details */}
        <aside className="flex min-h-0 flex-col overflow-hidden rounded-md border border-border bg-surface">
          {x ? <Details x={x} docId={docId} focusId={focusId} onFocus={(id) => set({ focus: id === focusId ? undefined : id })} /> : <Skeleton className="m-4 h-40" />}
        </aside>
      </div>
    </Page>
  );
}

/** A 12 px sample of each layer, drawn with the same stroke, dash and fill as on the paper. */
function LayerSwatch({ layer }: { layer: Layer }) {
  const r = { x: 1.5, y: 1.5, width: 9, height: 9, rx: 1.5 };
  return (
    <svg aria-hidden width={12} height={12} viewBox="0 0 12 12" className="shrink-0">
      {layer === "units" && <rect {...r} fill="var(--boundary-tint)" stroke="var(--boundary)" strokeWidth={1.25} />}
      {layer === "members" && <rect {...r} fill="none" stroke="var(--text-3)" strokeWidth={1} strokeDasharray="2 1.5" />}
      {layer === "fields" && <rect {...r} fill="none" stroke="var(--text-2)" strokeWidth={1} />}
      {layer === "chunks" && (
        <>
          <rect {...r} fill="color-mix(in srgb, var(--chunk-1) 12%, transparent)" stroke="var(--chunk-1)" strokeWidth={1} strokeDasharray="2 1.5" />
          <path d="M3 9 L9 3 M1.5 6 L6 1.5 M6 10.5 L10.5 6" stroke="var(--chunk-1)" strokeWidth={0.8} />
        </>
      )}
      {layer === "path" && (
        <>
          <path d="M2 9 L10 3" stroke="var(--text-2)" strokeWidth={1} />
          <circle cx={3} cy={8.2} r={2} fill="var(--surface)" stroke="var(--text-2)" />
          <circle cx={9} cy={3.8} r={2} fill="var(--text)" />
        </>
      )}
      {layer === "pairs" && <path d="M1.5 10 Q6 1 10.5 10" fill="none" stroke="var(--text-2)" strokeWidth={1.25} strokeDasharray="2.5 2" />}
      {layer === "held" && (
        <>
          <rect {...r} fill="var(--surface-3)" stroke="var(--boundary)" strokeWidth={1} strokeDasharray="2 1.5" />
          <path d="M3 9 L9 3 M1.5 6 L6 1.5 M6 10.5 L10.5 6" stroke="var(--text-3)" strokeWidth={0.8} />
        </>
      )}
    </svg>
  );
}

/** One quiet line under the chips: what the marks mean for the layers that are on, and the what-if state. */
function LayerLegend({ layers, whatIf, sizes }: { layers: Layer[]; whatIf: boolean; sizes?: [number, number] }) {
  const notes: ReactNode[] = [];
  if (layers.includes("fields")) notes.push("fields: neutral outlines");
  if (layers.includes("pairs")) notes.push("✓ printed value in its field · dashed arc: position-only (derived hypothesis)");
  if (layers.includes("held")) notes.push("hatched: held by the PII gate, never shipped");
  if (!notes.length && !whatIf) return null;
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-text-3">
      {whatIf && sizes && (
        <span className="inline-flex items-center gap-1 text-text-2">
          <ProvenanceTag provenance="what-if" className="-ml-1" />
          {sizes[0]}-token re-split · the control stays {sizes[1]}
        </span>
      )}
      {notes.map((n, i) => (
        <span key={i}>{n}</span>
      ))}
    </p>
  );
}

function focusRect(x: XrayPage, id: string | undefined): Rect | undefined {
  if (!id) {
    const keyLines = x.lines.filter((l) => l.kind !== "other" && l.bbox).map((l) => toRect(l.bbox!));
    const content = union([...keyLines, ...x.units.flatMap((u) => u.bboxes.map(toRect))]) ?? union(x.lines.filter((l) => l.bbox).map((l) => toRect(l.bbox!)));
    return content ? pad(content, 16) : undefined;
  }
  const u = x.units.find((u) => u.unitId === id);
  if (u) return pad(union(u.bboxes.map(toRect))!, 12);
  const c = x.chunks.find((c) => c.chunkId === id);
  if (c) return pad(union(c.rects.map(toRect))!, 12);
  return undefined;
}

const area = (r: Rect) => (r[2] - r[0]) * (r[3] - r[1]);
const overlaps = (a: Rect, b: Rect) => a[0] < b[2] && b[0] < a[2] && a[1] < b[3] && b[1] < a[3];

function ExplorerCanvas({ x, layers, focusId }: { x: XrayPage; layers: Layer[]; focusId?: string }) {
  const on = (l: Layer) => layers.includes(l);
  const [hover, setHover] = useState<string>();
  // a unit that overlaps a smaller one draws at half weight, unless it is the hovered or the selected one
  const units = useMemo(() => {
    const boxes = x.units.flatMap((u) => u.bboxes.map((b, i) => ({ unitId: u.unitId, key: `${u.unitId}-${i}`, rect: toRect(b) })));
    return boxes
      .map((b) => ({ ...b, covers: boxes.some((o) => o.unitId !== b.unitId && overlaps(o.rect, b.rect) && area(o.rect) < area(b.rect)) }))
      .sort((a, b) => area(b.rect) - area(a.rect));
  }, [x]);
  const unitFocus = focusId && x.units.some((v) => v.unitId === focusId) ? focusId : undefined;
  const strong = hover ?? unitFocus;
  return (
    <PageCanvas
      ariaLabel={`${x.sources.fileName ?? x.docId}, page ${x.page}`}
      image={{ src: x.image.url, src2x: x.image.zoomUrl, dpi: x.image.dpi }}
      pageWidth={x.pageWidth}
      pageHeight={x.pageHeight}
      focus={focusRect(x, focusId)}
      interactive
      padding={20}
    >
      {on("chunks") && <ChunkSlices chunks={x.chunks.map(sliceOf)} emphasis={focusId && x.chunks.some((c) => c.chunkId === focusId) ? [focusId] : undefined} />}
      {on("path") && <ReadingPath stops={stopsOf(x)} opacity={0.9} />}
      {on("members") && x.units.flatMap((u) => u.members.map((m) => <BoxOutline key={`${u.unitId}-${m.memberId}`} rect={toRect(m.bbox)} tone="border" dashed opacity={0.9} title={`${m.memberId} · ${m.role}`} />))}
      {on("units") &&
        units.map((b) => {
          const weight = strong ? (b.unitId === strong ? 1 : 0.35) : b.covers ? 0.5 : 1;
          return (
            <g key={b.key} opacity={weight} onPointerEnter={() => setHover(b.unitId)} onPointerLeave={() => setHover((h) => (h === b.unitId ? undefined : h))}>
              <rect x={b.rect[0]} y={b.rect[1]} width={b.rect[2] - b.rect[0]} height={b.rect[3] - b.rect[1]} fill="transparent" />
              <Boundary rect={b.rect} width={b.unitId === strong ? 1.5 : 1} tint={b.covers && b.unitId !== strong ? 0.4 : 0.8} />
            </g>
          );
        })}
      {on("fields") && x.units.flatMap((u) => u.fields.map((f) => <BoxOutline key={`${u.unitId}-${f.fieldId}`} rect={toRect(f.bbox)} tone="muted" opacity={0.85} title={`${f.label} = ${f.value}`} />))}
      {on("pairs") &&
        x.pairs.map((p) => {
          const label = p.label.bbox && toRect(p.label.bbox);
          const pos = p.positionalValue?.bbox && toRect(p.positionalValue.bbox);
          const geo = p.geometricValue?.bbox && toRect(p.geometricValue.bbox);
          // the positional hypothesis: × only when a logged answer used the position-only value
          const used = x.answerNumbers?.some((a) => a.isPositionalValueOf === p.fieldId);
          if (!label) return null;
          return (
            <g key={p.fieldId}>
              {pos && !p.positionalSameLine && <PairArc from={label} to={pos} tone={used ? "loss" : "hypothesis"} opacity={0.85} />}
              {geo && <PairArc from={label} to={geo} tone="cited" bend={-0.35} opacity={0.85} />}
            </g>
          );
        })}
      {on("held") && <HeldRegions regions={x.heldRegions.map(toRect)} />}
    </PageCanvas>
  );
}

function Details({ x, docId, focusId, onFocus }: { x: XrayPage; docId?: string; focusId?: string; onFocus(id: string): void }) {
  const unusable = xrayUnusable(x);
  return (
    <Tabs defaultValue="units" className="flex min-h-0 flex-1 flex-col">
      <div className="flex items-center border-b border-border pr-1.5">
        <TabsList className="min-w-0 flex-1 border-b-0 px-3">
          <TabsTrigger value="units" title="2.3.1 evidence units on this page">
            Units <span className="text-text-2">{x.units.length}</span>
          </TabsTrigger>
          <TabsTrigger value="chunks" title="X-ray of the naive 1.0 lane">
            X-ray <span className="text-text-2">{x.chunks.length}</span>
          </TabsTrigger>
        </TabsList>
        {docId && (
          <Button asChild size="sm" variant="ghost" className="shrink-0">
            <Link to={`/workbench/docs/${encodeURIComponent(docId)}/units`}>Unit lists</Link>
          </Button>
        )}
      </div>
      <TabsContent value="units" className="min-h-0 flex-1 overflow-y-auto p-2 pt-2">
        <div className="mb-1.5 px-1">
          <LaneBadge pv="2.3.1" />
        </div>
        {x.units.length === 0 ? (
          <EmptyState title="No evidence units on this page" icon={<ScanSearch />} />
        ) : (
          x.units.map((u) => (
            <ListButton key={u.unitId} active={u.unitId === focusId} onClick={() => onFocus(u.unitId)}>
              <div className="flex items-start gap-2">
                <span className="mt-1.5 size-2 shrink-0 rounded-[2px] border border-boundary" aria-hidden />
                <span className="line-clamp-2 min-w-0 flex-1 text-sm text-text">{u.label}</span>
              </div>
              <div className="mt-0.5 flex flex-wrap items-center gap-x-2 pl-4 text-xs text-text-2">
                <span className="font-mono">{u.unitId.split(":").pop()}</span>
                <span>{u.unitType}</span>
                <span>{u.members.length} members</span>
                <span>{u.fields.length} fields</span>
              </div>
              {u.unitId === focusId && u.fields.length > 0 && (
                <ul className="mt-2 flex flex-col gap-1 pl-4">
                  {u.fields.map((f) => (
                    <li key={f.fieldId} className="flex items-baseline gap-2 text-xs">
                      <span className="min-w-0 flex-1 truncate text-text-2" title={f.label}>{f.label}</span>
                      <span className="tnum font-mono text-mono-sm text-text">{f.value}</span>
                    </li>
                  ))}
                </ul>
              )}
            </ListButton>
          ))
        )}
        <Footnote>
          <ProvenanceTag provenance={x.sources.mode === "live" ? "measured" : "local-run"} source={x.sources.evidence?.key} className="-ml-1" /> Gold units; held units never leave Silver.
        </Footnote>
      </TabsContent>
      <TabsContent value="chunks" className="min-h-0 flex-1 overflow-y-auto p-2 pt-2">
        <div className="mb-1.5 px-1">
          <LaneBadge pv="1.0" />
        </div>
        <div className="mb-2 grid grid-cols-2 gap-2 px-1">
          <Stat label="Chunk size">
            <Num value={x.sizeTokens} kind="int" unit="tok" provenance={x.whatIf ? "what-if" : "design-target"} />
          </Stat>
          <Stat label="Char alignment">
            {x.textLayer && x.coverage !== null ? <Num value={x.coverage} kind="ratio" provenance={x.sources.mode === "live" ? "measured" : "local-run"} /> : <span className="text-sm text-text-2">no text layer</span>}
          </Stat>
        </div>
        {!x.textLayer ? (
          <EmptyState title="No text layer" icon={<ScanSearch />}>
            A scanned page has no characters for the 1.0 lane to chunk: 0 chunks, nothing to align. The 2.3.1 lane reads it with OCR.
          </EmptyState>
        ) : (
          x.chunks.map((c, i) => (
            <ListButton key={c.chunkId} active={c.chunkId === focusId} onClick={() => onFocus(c.chunkId)}>
              <div className="flex items-center gap-2">
                <span className={cn("size-2 shrink-0 rounded-[2px] border", chunkPattern(i) === "solid" ? "border-solid" : "border-dashed")} style={{ borderColor: toneVar(chunkTone(i)) }} aria-hidden />
                <span className="text-sm text-text">{x.whatIf ? c.label : c.chunkId}</span>
                <span className="ml-auto text-xs text-text-2">
                  {c.tokens} tokens{c.coverage !== null && ` · ${(c.coverage * 100).toFixed(1)}% aligned`}
                </span>
              </div>
            </ListButton>
          ))
        )}
        {unusable && x.textLayer && (
          <Footnote>
            <span className="text-text-2">X-ray layers disabled for this page: {unusable}</span>
          </Footnote>
        )}
        {x.heldTokensDropped > 0 && (
          <Footnote>
            <span className="text-text-2">{x.heldTokensDropped} tokens of held regions or held chunks were dropped before this payload left the API.</span>
          </Footnote>
        )}
        <Footnote>
          <ProvenanceTag provenance="derived" className="-ml-1" /> Inspection only: chunks stay unresolvable for citations.
        </Footnote>
      </TabsContent>
    </Tabs>
  );
}

function ListButton({ active, onClick, children }: { active: boolean; onClick(): void; children: ReactNode }) {
  return (
    <button type="button" onClick={onClick} className={cn("mb-0.5 block w-full rounded-sm px-2 py-1.5 text-left", active ? "bg-surface-2" : "hover:bg-surface-2/60")}>
      {children}
    </button>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="rounded-sm border border-border px-2 py-1.5">
      <div className="text-xs text-text-2">{label}</div>
      {children}
    </div>
  );
}

function Footnote({ children }: { children: ReactNode }) {
  return <p className="mt-2 flex flex-wrap items-center gap-1.5 px-1 text-xs text-text-2">{children}</p>;
}

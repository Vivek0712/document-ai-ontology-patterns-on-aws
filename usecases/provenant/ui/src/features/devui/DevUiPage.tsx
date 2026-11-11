/** /dev/ui: every design-system component in both themes, side by side (dev builds only). */
import { Bell, Copy, Play, Plus, Settings } from "lucide-react";
import { type ReactNode, useState } from "react";
import { usePackage, useXray } from "../../api/queries";
import { Page, PageHeader } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import {
  Badge,
  Button,
  Card,
  CardBody,
  CardHeader,
  CitationChip,
  Chip,
  CodeBlock,
  DataList,
  EmptyState,
  ErrorState,
  IconButton,
  JsonView,
  Kbd,
  LaneBadge,
  Num,
  PROVENANCE,
  type Provenance,
  SegmentedControl,
  Sheet,
  Skeleton,
  Table,
  TBody,
  TD,
  TH,
  THead,
  TR,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  ToggleChips,
  toast,
} from "../../design";
import { pad, toRect, union } from "../../viz/geom";
import { sliceOf, stopsOf } from "../../viz/xray";
import { Boundary, ChunkSlices, Highlight, PageCanvas, PairArc, ReadingPath, Tag } from "../../viz/PageCanvas";

const COLORS = ["bg", "bg-subtle", "surface", "surface-2", "surface-3", "canvas", "border", "border-strong", "text", "text-2", "text-3", "focus", "boundary", "loss", "cited", "agent", "concept", "layout", "running", "chunk-1", "chunk-2", "chunk-3", "chunk-4", "tier-bronze", "tier-silver", "tier-gold"];
const TYPE: [string, string, string][] = [
  ["caps", "text-caps uppercase", "11/16 · section headers, tags"],
  ["xs", "text-xs", "12/16 · secondary text, meta"],
  ["sm", "text-sm", "13/20 · base: body, tables, buttons"],
  ["md", "text-md font-medium", "14/20 · card titles, captions"],
  ["lg", "text-lg font-semibold", "16/24 · page titles"],
  ["xl", "text-xl font-semibold", "20/28 · showcase titles"],
  ["hero", "text-hero", "20/28 600 · every answer total (Num lg); the only display size in showcases"],
  ["figure", "text-figure", "28/36 · dev reference only"],
  ["mono-sm", "font-mono text-mono-sm", "12/18 · ids, shas, JSON"],
  ["mono-md", "font-mono text-mono-md", "13/20 · SPARQL, config"],
];

export default function DevUiPage() {
  return (
    <Page wide>
      <PageHeader crumbs={[{ label: "Dev" }]} title="Design system" description="Tokens and components in both themes. Colour roles: orange is the boundary motif only; pink is failure with ×; green is success with ✓." />
      <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <ThemePane theme="dark" />
        <ThemePane theme="light" />
      </div>
      <section className="mt-6">
        <h2 className="mb-2 text-caps uppercase text-text-2">PageCanvas · real X-ray fixture</h2>
        <CanvasDemo />
      </section>
    </Page>
  );
}

function ThemePane({ theme }: { theme: "dark" | "light" }) {
  return (
    <div data-theme={theme} className="flex flex-col gap-5 rounded-lg border border-border bg-bg p-5 text-sm text-text">
      <div className="flex items-center gap-2">
        <Badge tone="strong">{theme}</Badge>
        <span className="text-xs text-text-2">data-theme="{theme}"</span>
      </div>
      <Group title="Colour roles">
        <div className="grid grid-cols-4 gap-1.5 sm:grid-cols-6">
          {COLORS.map((c) => (
            <div key={c} className="flex flex-col gap-1">
              <div className="h-7 rounded-xs border border-border" style={{ background: `var(--${c})` }} />
              <span className="truncate font-mono text-2xs text-text-2">{c}</span>
            </div>
          ))}
        </div>
      </Group>
      <Group title="Type scale">
        <div className="flex flex-col gap-1.5">
          {TYPE.map(([name, cls, note]) => (
            <div key={name} className="flex items-baseline gap-3">
              <span className="w-14 shrink-0 font-mono text-2xs text-text-2">{name}</span>
              <span className={cls}>{name === "figure" ? "169,650.00" : "Every figure, proved to a region"}</span>
              <span className="ml-auto hidden text-2xs text-text-2 md:inline">{note}</span>
            </div>
          ))}
        </div>
      </Group>
      <Group title="Buttons">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="primary" icon={<Play />}>Primary</Button>
          <Button>Secondary</Button>
          <Button variant="ghost">Ghost</Button>
          <Button variant="danger">× Danger</Button>
          <Button size="sm" icon={<Plus />}>Small</Button>
          <Button loading>Loading</Button>
          <Button disabled>Disabled</Button>
          <IconButton label="Settings" icon={<Settings />} />
          <IconButton label="Copy" icon={<Copy />} variant="secondary" size="sm" />
          <IconButton label="Notifications" icon={<Bell />} active />
        </div>
      </Group>
      <Group title="Badges · chips">
        <div className="flex flex-wrap items-center gap-1.5">
          <Badge>neutral</Badge>
          <Badge tone="strong">strong</Badge>
          <Badge tone="layout" dot>layout</Badge>
          <Badge tone="concept" dot>fin:W2WagesBox1</Badge>
          <Badge tone="agent" dot>graph_hop</Badge>
          <Badge tone="running" dot>running 00:48</Badge>
          <Badge tone="cited" dot>✓ ready</Badge>
          <Badge tone="loss">× failed</Badge>
          <Badge tone="boundary">held: PII</Badge>
          <LaneBadge pv="1.0" />
          <LaneBadge pv="2.3.1" />
          <Chip>AWS Lambda</Chip>
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <CitationChip kind="cited" label="W-2 · Maya Fictus · Box 1" page={1} to="/workbench/units" />
          <CitationChip kind="noregion" label="chunk 827d37:c-01" />
          <CitationChip kind="held" label="held: PII" />
          <CitationChip kind="operand" label="Arjun · Box 1" value="71,250.00" />
        </div>
      </Group>
      <Group title="Num · provenance">
        <div className="flex flex-col gap-1.5">
          {(Object.keys(PROVENANCE) as Provenance[]).map((p, i) => (
            <Num key={p} value={[169650, 98400, 179, 0.9989, 128, 412][i % 6]} kind={i % 6 === 3 ? "ratio" : i % 6 === 2 || i % 6 === 4 ? "int" : i % 6 === 5 ? "ms" : "money"} provenance={p} source="dev/ui sample · illustrative" mark={i === 0 ? "cited" : i === 1 ? "loss" : undefined} />
          ))}
          <Num value="169650.00" kind="money" provenance="illustrative" size="figure" />
        </div>
      </Group>
      <Group title="Tabs · segmented · toggles">
        <Controls />
      </Group>
      <Group title="Card · data list · table">
        <Card>
          <CardHeader title="W-2 · Maya Fictus · Wages (Boxes 1–6)" meta="eu-02 · field_group · 18 members" actions={<IconButton size="sm" label="Copy id" icon={<Copy />} />} />
          <CardBody>
            <DataList rows={[["Concept", <Badge tone="concept" dot>fin:W2WagesBox1</Badge>], ["Value", <span className="tnum">98,400.00</span>], ["Page", "1"]]} />
          </CardBody>
        </Card>
        <Table className="mt-3 rounded-md border border-border">
          <THead>
            <tr>
              <TH>Unit</TH>
              <TH>Type</TH>
              <TH numeric>Tokens</TH>
            </tr>
          </THead>
          <TBody>
            <TR interactive>
              <TD className="font-mono text-mono-sm">c-01</TD>
              <TD>chunk</TD>
              <TD numeric>179</TD>
            </TR>
            <TR interactive>
              <TD className="font-mono text-mono-sm">eu-02</TD>
              <TD>field_group</TD>
              <TD numeric>63</TD>
            </TR>
          </TBody>
        </Table>
      </Group>
      <Group title="Code · JSON">
        <CodeBlock title="qi_sum_w2_box1_v1.rq" code={"PREFIX fin: <urn:rp:ont:fin:>\nSELECT ?borrower ?wagesBox1 WHERE {\n  fin:QualifyingIncome fin:operandConcept ?operand .\n  FILTER (?taxYear = 2025)\n}"} highlight={[3]} maxHeight={140} />
        <JsonView className="mt-2" value={{ chunkId: "c-01", tokens: 179, coverage: 0.9989, rects: [[39.2, 40.5, 120.1, 52.0]] }} highlight={["$.coverage"]} maxHeight={160} />
      </Group>
      <Group title="Keys · states">
        <div className="flex items-center gap-1">
          <Kbd>⌘</Kbd>
          <Kbd>K</Kbd>
          <span className="mx-2 text-text-2">·</span>
          <Kbd>Space</Kbd>
          <Kbd>←</Kbd>
          <Kbd>→</Kbd>
        </div>
        <div className="mt-3 flex flex-col gap-2">
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-4 w-1/2" />
        </div>
        <EmptyState className="mt-3" title="No packages yet." action={<Button size="sm" variant="primary">New package</Button>}>
          A package is a set of PDFs ingested by both pipeline versions.
        </EmptyState>
        <div className="mt-3">
          <ErrorState title="Could not resolve the region" code="forbidden" requestId="5f1c2a" message="You don't have access to this region." />
        </div>
      </Group>
      <Group title="Toast · drawer">
        <Overlays />
      </Group>
    </div>
  );
}

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-2 text-caps uppercase text-text-2">{title}</h3>
      {children}
    </section>
  );
}

function Controls() {
  const [lane, setLane] = useState("both");
  const [layers, setLayers] = useState(["units", "chunks"]);
  return (
    <div className="flex flex-col gap-3">
      <Tabs defaultValue="sources">
        <TabsList>
          <TabsTrigger value="sources">Sources</TabsTrigger>
          <TabsTrigger value="json">JSON</TabsTrigger>
          <TabsTrigger value="prov">Provenance</TabsTrigger>
        </TabsList>
        <TabsContent value="sources" className="text-xs text-text-2">silver/pkg-lending-01/…/units.jsonl</TabsContent>
        <TabsContent value="json" className="text-xs text-text-2">{"{ … }"}</TabsContent>
        <TabsContent value="prov" className="text-xs text-text-2">local run</TabsContent>
      </Tabs>
      <SegmentedControl aria-label="Lanes" value={lane} onValueChange={setLane} items={[{ value: "1.0", label: "1.0" }, { value: "2.3.1", label: "2.3.1" }, { value: "both", label: "Both" }]} />
      <ToggleChips aria-label="Layers" value={layers} onValueChange={setLayers} items={[{ value: "units", label: "Units" }, { value: "chunks", label: "Chunks" }, { value: "path", label: "Reading order" }]} />
    </div>
  );
}

function Overlays() {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-wrap gap-2">
      <Button size="sm" onClick={() => toast.success("Link copied", { description: "/showcase/sc-1?step=3" })}>Success toast</Button>
      <Button size="sm" onClick={() => toast.error("Region forbidden", { description: "403 · request 5f1c2a" })}>Error toast</Button>
      <Button size="sm" onClick={() => setOpen(true)}>Open drawer</Button>
      <Sheet open={open} onOpenChange={setOpen} title="Receipts" description="SC-1 · step 3 · Chunk slices">
        <div className="p-4 text-sm text-text-2">Drawer body.</div>
      </Sheet>
    </div>
  );
}

/** PageCanvas with every layer type, on the first W-2 of the current package (real fixture data). */
function CanvasDemo() {
  const { packageId } = useWorkspace();
  const pkg = usePackage(packageId);
  const docId = pkg.data?.documents.find((d) => d.docType === "doc:W2")?.docId;
  const x = useXray(docId, 1, 128).data;
  if (!x) return <Skeleton className="h-[420px]" />;
  const pair = x.pairs.find((p) => p.positionalValue && p.geometricValue && !p.positionalSameLine);
  const label = pair?.label.bbox && toRect(pair.label.bbox);
  const pos = pair?.positionalValue?.bbox && toRect(pair.positionalValue.bbox);
  const geo = pair?.geometricValue?.bbox && toRect(pair.geometricValue.bbox);
  const unit = x.units.find((u) => u.fields.some((f) => f.fieldId === pair?.fieldId));
  return (
    <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
      {(["dark", "light"] as const).map((t) => (
        <div key={t} data-theme={t} className="h-[420px] overflow-hidden rounded-lg border border-border">
          <PageCanvas ariaLabel="demo" image={{ src: x.image.url, src2x: x.image.zoomUrl }} pageWidth={x.pageWidth} pageHeight={x.pageHeight} focus={unit ? pad(union(unit.bboxes.map(toRect))!, 16) : undefined} interactive>
            <ChunkSlices chunks={x.chunks.map(sliceOf)} opacity={0.9} />
            <ReadingPath stops={stopsOf(x)} opacity={0.35} />
            {unit && <Boundary rect={toRect(unit.bboxes[0])} />}
            {label && <Highlight rect={label} />}
            {label && pos && <PairArc from={label} to={pos} tone="hypothesis" />}
            {label && geo && <PairArc from={label} to={geo} tone="cited" bend={-0.35} />}
            {geo && <Tag x={geo[0]} y={geo[3]} text="what-if · 128 tokens" placement="below" dashed />}
          </PageCanvas>
        </div>
      ))}
    </div>
  );
}

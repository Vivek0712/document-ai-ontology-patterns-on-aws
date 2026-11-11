/**
 * Receipts drawer: the exact artifacts and payload fields each step reads.
 * "This step" lists the receipts, "All steps" the full story, "JSON" the payload with the step's paths highlighted.
 */
import { Badge, JsonView, ProvenanceTag, Sheet, Tabs, TabsContent, TabsList, TabsTrigger } from "../../design";
import type { Receipt, Storyboard } from "./types";

interface Props<P> {
  open: boolean;
  onOpenChange(o: boolean): void;
  story: Storyboard<P>;
  payload: P;
  step: number;
}

export function Receipts<P>({ open, onOpenChange, story, payload, step }: Props<P>) {
  const mine = story.steps[step]?.receipts?.(payload) ?? [];
  return (
    <Sheet open={open} onOpenChange={onOpenChange} modal={false} title="Receipts" description={`${story.code} · step ${step + 1} · ${story.steps[step]?.title}`} width={440}>
      <Tabs defaultValue="step" className="px-4 pb-4">
        <TabsList>
          <TabsTrigger value="step">This step</TabsTrigger>
          <TabsTrigger value="all">All steps</TabsTrigger>
          <TabsTrigger value="json">JSON</TabsTrigger>
        </TabsList>
        <TabsContent value="step">
          <ReceiptList items={mine} />
        </TabsContent>
        <TabsContent value="all" className="flex flex-col gap-4">
          {story.steps.map((s, i) => (
            <section key={s.id}>
              <div className="mb-1.5 flex items-center gap-2 text-xs text-text-2">
                <Badge tone={i === step ? "strong" : "neutral"}>{i + 1}</Badge>
                {s.title}
              </div>
              <ReceiptList items={s.receipts?.(payload) ?? []} />
            </section>
          ))}
        </TabsContent>
        <TabsContent value="json">
          <JsonView value={story.receiptsPayload ? story.receiptsPayload(payload) : payload} highlight={mine.flatMap((r) => (r.path ? [r.path] : []))} collapseAfter={2} maxHeight={640} />
        </TabsContent>
      </Tabs>
    </Sheet>
  );
}

function ReceiptList({ items }: { items: Receipt[] }) {
  if (!items.length) return <div className="text-sm text-text-2">This step reads nothing new.</div>;
  return (
    <ul className="flex flex-col divide-y divide-border rounded-sm border border-border">
      {items.map((r, i) => (
        <li key={i} className="flex flex-col gap-1 px-3 py-2">
          <div className="flex items-center gap-2">
            <span className="min-w-0 flex-1 text-sm text-text">{r.label}</span>
            {r.provenance && <ProvenanceTag provenance={r.provenance} />}
          </div>
          {r.value !== undefined && r.value !== null && <div className="tnum break-words font-mono text-mono-sm text-text">{String(r.value)}</div>}
          {r.path && <div className="break-all font-mono text-mono-sm text-text-2">{r.path}</div>}
          {r.source && <div className="break-all font-mono text-mono-sm text-text-2">{r.source}</div>}
        </li>
      ))}
    </ul>
  );
}

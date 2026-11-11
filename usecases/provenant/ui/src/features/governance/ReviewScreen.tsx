/**
 * W-14 Governance · Review queue: fields the evidence-units builder extracted below
 * `units.reviewMinConfidence` wait for a human. The drawer shows the page region and the extracted value, and
 * records ACCEPT, CORRECT (with a value) or REJECT with a note (`POST /review/{pkg}/items/{key}/decision`). CORRECT
 * never overwrites the extracted value: it records an override, kept in the item's history.
 * URL: /governance/review?item=<reviewKey>
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Pencil, X } from "lucide-react";
import { useState } from "react";
import type { ReviewDecisionRequest, ReviewItem } from "@contracts/api";
import { useApi, useMe } from "../../api/context";
import { RpApiError } from "../../api/types";
import { Page, PageHeader } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import { Badge, Button, Card, DataList, EmptyState, ErrorState, LaneBadge, Num, ProvenanceScope, ScopeTag, Sheet, Skeleton, TBody, TD, TH, THead, TR, Table, toast } from "../../design";
import { useSearchParam } from "../../hooks/useSearchParam";
import { pad, toRect } from "../../viz/geom";
import { Boundary, BoxOutline, PageCanvas } from "../../viz/PageCanvas";

const STATE_TONE: Record<ReviewItem["state"], "neutral" | "cited" | "loss" | "strong"> = { OPEN: "strong", ACCEPTED: "cited", CORRECTED: "neutral", REJECTED: "loss" };

/** The dev stand-in for Textract (`LOCAL-DOCLING-OCR`) reports no confidences: its 0.0 is a placeholder, never shown as measured. */
const isStandIn = (r: ReviewItem) => Boolean(r.engineModelVersion?.startsWith("LOCAL-"));

function Confidence({ item }: { item: ReviewItem }) {
  if (isStandIn(item))
    return (
      <span className="text-xs text-text-2" title={`${item.engineModelVersion}: the local OCR stand-in has no confidences, so every field it reads is below the threshold`}>
        none · local OCR stand-in
      </span>
    );
  return (
    <span className="inline-flex items-baseline gap-1">
      <Num value={item.confidence} kind="ratio" />
      <span className="text-xs text-text-2">
        / <Num value={item.threshold} kind="raw" size="xs" bare />
      </span>
    </span>
  );
}

export default function ReviewScreen() {
  const api = useApi();
  const { sub } = useMe();
  const { packageId } = useWorkspace();
  const [item, setItem] = useSearchParam("item");
  const q = useQuery({ queryKey: ["review", sub, packageId], queryFn: () => api.listReview(packageId) });
  const selected = q.data?.items.find((i) => i.reviewKey === item);
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Governance" }, { label: "Review queue" }]}
        title="Review queue"
        description="Fields extracted below the review threshold go to a person before anyone relies on them."
      />
      {q.error ? (
        <ErrorState title="The review queue could not load" message={q.error instanceof Error ? q.error.message : String(q.error)} code={q.error instanceof RpApiError ? q.error.code : undefined} />
      ) : !q.data ? (
        <Skeleton className="h-48 rounded-md" />
      ) : q.data.items.length === 0 ? (
        <EmptyState title="No fields below the review threshold in this package." />
      ) : (
        <ProvenanceScope provenance={api.mode === "http" ? "measured" : "local-run"} source={`GET /review?packageId=${packageId}`}>
          <Card data-testid="review-queue">
            <Table>
              <THead>
                <tr>
                  <TH>Document</TH>
                  <TH>Field</TH>
                  <TH numeric>Value</TH>
                  <TH numeric>
                    <span className="inline-flex items-center gap-1">
                      Confidence <ScopeTag />
                    </span>
                  </TH>
                  <TH>Engine</TH>
                  <TH>Reason</TH>
                  <TH>State</TH>
                </tr>
              </THead>
              <TBody>
                {q.data.items.map((r) => (
                  <TR key={r.reviewKey} interactive onClick={() => setItem(r.reviewKey)}>
                    <TD>
                      <button type="button" className="text-left text-text hover:underline" onClick={() => setItem(r.reviewKey)}>
                        {r.docId}
                      </button>
                      <span className="mt-0.5 flex items-center gap-1.5">
                        <LaneBadge pv={r.pipelineVersion} />
                      </span>
                    </TD>
                    <TD>
                      <span className="text-text">{r.label}</span>
                      <span className="block font-mono text-mono-sm text-text-2">{r.fieldId}</span>
                    </TD>
                    <TD numeric className="font-mono text-mono-sm">
                      {r.value}
                    </TD>
                    <TD numeric>
                      <Confidence item={r} />
                    </TD>
                    <TD className="font-mono text-mono-sm text-text-2">{r.engine}</TD>
                    <TD className="text-text-2">{isStandIn(r) ? "no confidence reported" : r.reason}</TD>
                    <TD>
                      <Badge tone={STATE_TONE[r.state]}>{r.state.toLowerCase()}</Badge>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </Card>
        </ProvenanceScope>
      )}
      <Sheet open={Boolean(selected)} onOpenChange={(o) => !o && setItem(undefined)} title={selected ? `${selected.label} · ${selected.value}` : "Review item"} description={selected?.docId} width={480}>
        {selected && <ReviewDrawer item={selected} />}
      </Sheet>
    </Page>
  );
}

function ReviewDrawer({ item }: { item: ReviewItem }) {
  const api = useApi();
  const { sub } = useMe();
  const qc = useQueryClient();
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const region = useQuery({ queryKey: ["region", sub, item.unitId, item.fieldId], queryFn: () => api.getRegion(item.unitId, item.fieldId), retry: false });
  const decide = useMutation({
    mutationFn: (body: ReviewDecisionRequest) => api.decideReview(item.packageId, item.reviewKey, body),
    onSuccess: (next) => {
      qc.setQueryData(["review", sub, item.packageId], (old: { items: ReviewItem[] } | undefined) => old && { items: old.items.map((i) => (i.reviewKey === next.reviewKey ? next : i)) });
      setValue("");
      setNote("");
      toast.success(`Recorded ${next.decision?.action.toLowerCase()}`);
    },
    onError: (e) => {
      toast.error(e instanceof Error ? e.message : String(e));
      // 409: someone decided since this view loaded; show their decision before anything else is sent
      if (e instanceof RpApiError && e.status === 409) void qc.invalidateQueries({ queryKey: ["review", sub, item.packageId] });
    },
  });
  const r = region.data;
  const field = r?.fieldBbox ? toRect(r.fieldBbox) : item.bbox ? toRect(item.bbox) : undefined;
  const unit = r ? toRect(r.unitBboxes.find((b) => b.page === r.page) ?? r.bbox) : undefined;
  // expectedDecisionAt: the decision this view shows (none = null), so a stale view answers 409 instead of overwriting
  const send = (action: ReviewDecisionRequest["action"]) =>
    decide.mutate({ action, expectedDecisionAt: item.decision?.at ?? null, ...(action === "CORRECT" ? { value: value.trim() } : {}), ...(note.trim() ? { note: note.trim() } : {}) });
  return (
    <ProvenanceScope provenance={api.mode === "http" ? "measured" : "local-run"} source={`GET /review · ${item.reviewKey}`}>
      <div className="flex flex-col gap-4 p-4">
        <div className="h-[260px] overflow-hidden rounded-md border border-border">
          {r && field ? (
            <PageCanvas ariaLabel={`${r.fileName}, page ${r.page}: the field under review`} image={r.url ? { src: r.url, dpi: 150 } : undefined} pageWidth={r.pageWidth} pageHeight={r.pageHeight} focus={pad(field, 48)} padding={12}>
              {unit && <Boundary rect={unit} width={1} />}
              <BoxOutline rect={pad(field, 2)} tone="text" width={1.5} />
            </PageCanvas>
          ) : region.error ? (
            <EmptyState title="No page region">{region.error instanceof Error ? region.error.message : String(region.error)}</EmptyState>
          ) : (
            <Skeleton className="h-full" />
          )}
        </div>
        <DataList
          rows={[
            ["Extracted", <span className="font-mono text-mono-md text-text">{item.value}</span>],
            ["Confidence", <Confidence item={item} />],
            ["Engine", <span className="font-mono text-mono-sm">{item.engine}{item.engineModelVersion ? ` · ${item.engineModelVersion}` : ""}</span>],
            ["Unit", <span className="font-mono text-mono-sm [overflow-wrap:anywhere]">{item.unitId}</span>],
            ["State", <Badge tone={STATE_TONE[item.state]}>{item.state.toLowerCase()}</Badge>],
          ]}
        />
        <section className="flex flex-col gap-2 rounded-md border border-border p-3">
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Corrected value (CORRECT only; the extracted value is kept)
            <input value={value} onChange={(e) => setValue(e.target.value)} className="h-8 rounded-sm border border-border-strong bg-surface px-2 font-mono text-mono-sm text-text" placeholder={item.value} />
          </label>
          <label className="flex flex-col gap-1 text-xs text-text-2">
            Note
            <input value={note} onChange={(e) => setNote(e.target.value)} className="h-8 rounded-sm border border-border-strong bg-surface px-2 text-sm text-text" />
          </label>
          <div className="flex flex-wrap gap-2">
            <Button size="sm" variant="secondary" onClick={() => send("ACCEPT")} loading={decide.isPending && decide.variables?.action === "ACCEPT"}>
              <Check /> Accept
            </Button>
            <Button size="sm" variant="secondary" onClick={() => send("CORRECT")} disabled={!value.trim()} loading={decide.isPending && decide.variables?.action === "CORRECT"}>
              <Pencil /> Correct
            </Button>
            <Button size="sm" variant="danger" onClick={() => send("REJECT")} loading={decide.isPending && decide.variables?.action === "REJECT"}>
              <X /> Reject
            </Button>
          </div>
        </section>
        <section>
          <h3 className="mb-1.5 text-caps uppercase text-text-2">History</h3>
          <ol className="flex flex-col gap-1.5" data-testid="review-history">
            {[...(item.history ?? []), ...(item.decision ? [item.decision] : [])].reverse().map((d, i) => (
              <li key={`${d.at}-${i}`} className="rounded-sm border border-border px-3 py-1.5 text-xs text-text-2">
                <span className="font-medium text-text">{d.action.toLowerCase()}</span> by <span className="font-mono">{d.by}</span> · {d.at.replace("T", " ").slice(0, 19)}
                {d.value && (
                  <span>
                    {" "}
                    · override <span className="font-mono text-text">{d.value}</span>
                  </span>
                )}
                {d.note && <span className="block">{d.note}</span>}
              </li>
            ))}
            {!item.decision && !item.history?.length && <li className="text-xs text-text-3">No decision yet.</li>}
          </ol>
        </section>
      </div>
    </ProvenanceScope>
  );
}

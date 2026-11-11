/**
 * W-17 Cost: usage per document type, stage, lane or service, the line-item ledger, the model
 * tier per call type, and the panels for billed actuals and budgets. Usage is measured (stage records, run usage)
 * or derived with its basis; amounts are `estimate` (usage × list price, `config/prices.yaml`, with its date) and
 * appear only in a published build. Grouping is done here over the ledger lines the API returned, so every grouping
 * shows the same lines. URL: /cost?groupBy=docType|stage|lane|service&lane=1.0|2.3.1
 */
import { useQuery } from "@tanstack/react-query";
import type { CostLine, CostView } from "@contracts/api";
import { useApi, useMe } from "../../api/context";
import { RpApiError } from "../../api/types";
import { Page, PageHeader, PlayExplainer } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import { Badge, Card, CardHeader, ErrorState, LaneBadge, Num, type NumKind, ProvenanceScope, ProvenanceTag, ScopeTag, SegmentedControl, Skeleton, TBody, TD, TH, THead, TR, Table, Tooltip } from "../../design";
import { useSearchParam } from "../../hooks/useSearchParam";

type GroupBy = "docType" | "stage" | "lane" | "service";
const UNIT_LABEL: Record<string, string> = { page: "pages", unit_100_chars: "Comprehend units", token_in: "tokens in", token_out: "tokens out", vcpu_s: "vCPU-s", gb_s: "GB-s", request: "requests", lambda_gb_s: "Lambda GB-s", ms: "ms" };
const keyOf: Record<GroupBy, (l: CostLine) => string> = {
  docType: (l) => l.docType ?? (l.stageId === "ask" ? "asks" : "—"),
  stage: (l) => l.stageId,
  lane: (l) => l.pipelineVersion,
  service: (l) => l.service,
};

export default function CostScreen() {
  const api = useApi();
  const { sub } = useMe();
  const { packageId } = useWorkspace();
  const [groupBy, setGroupBy] = useSearchParam("groupBy", "docType");
  const [lane, setLane] = useSearchParam("lane", "all");
  const q = useQuery({ queryKey: ["cost", sub, packageId], queryFn: () => api.getCost(packageId, "docType") });
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Cost" }]}
        title="Cost and model tiering"
        description="What each document, stage and answer used, and the model tier per call. Amounts are list-price estimates, shown only when metrics are published."
        meta={q.data && <Badge tone="neutral" title="Amounts are withheld until the publication flag RP_PUBLIC_METRICS is on.">{q.data.published ? "amounts published" : "amounts not published"}</Badge>}
        actions={<PlayExplainer showcaseId="sc-13" />}
      />
      {q.error ? (
        <ErrorState title="Cost could not load" message={q.error instanceof Error ? q.error.message : String(q.error)} code={q.error instanceof RpApiError ? q.error.code : undefined} />
      ) : !q.data ? (
        <Skeleton className="h-[520px] rounded-md" />
      ) : (
        <ProvenanceScope provenance={api.mode === "http" ? "measured" : "local-run"} source={`GET /cost?packageId=${packageId}`}>
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3 text-xs text-text-2">
              <SegmentedControl<string>
                aria-label="Group by"
                size="sm"
                value={groupBy}
                onValueChange={setGroupBy}
                items={[
                  { value: "docType", label: "Document type" },
                  { value: "stage", label: "Stage" },
                  { value: "lane", label: "Lane" },
                  { value: "service", label: "Service" },
                ]}
              />
              <SegmentedControl<string> aria-label="Lane" size="sm" value={lane} onValueChange={setLane} items={[{ value: "all", label: "Both lanes" }, { value: "1.0", label: "1.0" }, { value: "2.3.1", label: "2.3.1" }]} />
              <span>
                prices <span className="font-mono">{q.data.prices.source}</span> · as of {q.data.prices.asOf}
              </span>
            </div>
            <Groups view={q.data} groupBy={groupBy as GroupBy} lane={lane} />
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">
              <Tiering view={q.data} />
              <div className="flex flex-col gap-4">
                <Card>
                  <CardHeader title="Billed actuals" meta="AWS Cost Explorer · daily, by service" />
                  <p className="px-4 py-3 text-sm text-text-2">Not in this build. Estimates only; actuals need the deployed stack and lag about a day.</p>
                </Card>
                <Card>
                  <CardHeader title="Budgets" meta="AWS Budgets alerts" />
                  <p className="px-4 py-3 text-sm text-text-2">GET /cost/budgets is not built yet; it lists each budget's name, limit, thresholds and state from budgets:DescribeBudgets.</p>
                </Card>
              </div>
            </div>
            <Ledger view={q.data} lane={lane} />
            {q.data.notes.length > 0 && (
              <ul className="flex flex-col gap-1 px-1 text-xs text-text-2">
                {q.data.notes.map((n) => (
                  <li key={n}>{n}</li>
                ))}
              </ul>
            )}
          </div>
        </ProvenanceScope>
      )}
    </Page>
  );
}

function Groups({ view, groupBy, lane }: { view: CostView; groupBy: GroupBy; lane: string }) {
  const lines = view.lines.filter((l) => lane === "all" || l.pipelineVersion === lane);
  const units = [...new Set(lines.map((l) => l.usage.unit))];
  const groups = new Map<string, CostLine[]>();
  for (const l of lines) groups.set(keyOf[groupBy](l), [...(groups.get(keyOf[groupBy](l)) ?? []), l]);
  const derived = units.filter((u) => lines.some((l) => l.usage.unit === u && !l.usage.measured));
  // one number format per column: integers when every value is whole, else two decimals
  const kindOf = (u: string): NumKind => (lines.filter((l) => l.usage.unit === u).every((l) => Number.isInteger(l.usage.quantity)) ? "int" : "dec2");
  return (
    <Card data-testid="cost-groups">
      <CardHeader title={`Usage by ${groupBy === "docType" ? "document type" : groupBy}`} meta={<span><Num value={lines.length} kind="int" bare /> ledger lines</span>} actions={<ScopeTag />} />
      {derived.length > 0 && (
        <p className="flex flex-wrap items-center gap-1.5 border-b border-border px-4 py-1.5 text-xs text-text-2">
          <ProvenanceTag provenance="derived" source={lines.find((l) => !l.usage.measured && l.usage.basis)?.usage.basis} />
          {derived.map((u) => UNIT_LABEL[u] ?? u).join(", ")}: derived from measured inputs (basis on hover in the ledger); the rest is logged usage.
        </p>
      )}
      <div className="overflow-x-auto">
        <Table>
          <THead>
            <tr>
              <TH>{groupBy === "docType" ? "Document type" : groupBy}</TH>
              <TH numeric>Lines</TH>
              {units.map((u) => (
                <TH key={u} numeric className="whitespace-nowrap">
                  {UNIT_LABEL[u] ?? u}
                </TH>
              ))}
              {view.published && (
                <TH numeric>
                  <span className="inline-flex items-center gap-1">
                    Estimate · USD <ProvenanceTag provenance="estimate" source={`${view.prices.source} · as of ${view.prices.asOf}`} />
                  </span>
                </TH>
              )}
            </tr>
          </THead>
          <TBody>
            {[...groups.entries()]
              .sort((a, b) => a[0].localeCompare(b[0]))
              .map(([k, ls]) => (
                <TR key={k}>
                  <TD className="font-mono text-mono-sm text-text">{k}</TD>
                  <TD numeric>
                    <Num value={ls.length} kind="int" bare />
                  </TD>
                  {units.map((u) => {
                    const sum = ls.filter((l) => l.usage.unit === u).reduce((s, l) => s + l.usage.quantity, 0);
                    return (
                      <TD key={u} numeric>
                        {sum ? <Num value={sum} kind={kindOf(u)} bare /> : <span className="text-text-3">—</span>}
                      </TD>
                    );
                  })}
                  {view.published && (
                    <TD numeric>
                      <GroupEstimate lines={ls} />
                    </TD>
                  )}
                </TR>
              ))}
          </TBody>
        </Table>
      </div>
    </Card>
  );
}

/** A group's estimate over its priced lines; with unpriced lines it reads "≥ x · n unpriced", never a silent $0. */
function GroupEstimate({ lines }: { lines: CostLine[] }) {
  const priced = lines.filter((l) => l.estimateUsd !== undefined && l.estimateUsd !== null);
  const unpriced = lines.length - priced.length;
  const sum = priced.reduce((s, l) => s + (l.estimateUsd ?? 0), 0);
  return (
    <span className="inline-flex items-baseline gap-1" title={unpriced ? `${unpriced} lines have no list price in config/prices.yaml` : undefined}>
      {unpriced > 0 && <span className="text-text-2">≥</span>}
      <Num value={sum.toFixed(4)} kind="raw" provenance="estimate" bare />
      {unpriced > 0 && (
        <span className="text-xs text-text-2">
          · <Num value={unpriced} kind="int" bare /> unpriced
        </span>
      )}
    </span>
  );
}

function Ledger({ view, lane }: { view: CostView; lane: string }) {
  const lines = view.lines.filter((l) => lane === "all" || l.pipelineVersion === lane);
  return (
    <Card data-testid="cost-ledger">
      <CardHeader title="Ledger" meta={`${lines.length} lines · one row per usage line`} />
      <div className="max-h-[520px] overflow-auto">
        <Table>
          <THead className="sticky top-0 z-[1]">
            <tr>
              <TH>Document or run</TH>
              <TH>Lane</TH>
              <TH>Stage</TH>
              <TH>Service</TH>
              <TH numeric>
                <span className="inline-flex items-center gap-1">
                  Usage <ScopeTag />
                </span>
              </TH>
              {view.published && (
                <TH numeric>
                  <span className="inline-flex items-center gap-1">
                    Estimate <ProvenanceTag provenance="estimate" source={view.prices.source} />
                  </span>
                </TH>
              )}
            </tr>
          </THead>
          <TBody>
            {lines.map((l, i) => (
              <TR key={`${l.docId ?? l.runId}-${l.stageId}-${l.service}-${l.usage.unit}-${i}`}>
                <TD className="max-w-[320px] truncate font-mono text-mono-sm text-text-2" title={l.docId ?? l.runId}>
                  {l.docId ?? l.runId}
                </TD>
                <TD>
                  <LaneBadge pv={l.pipelineVersion} />
                </TD>
                <TD className="font-mono text-mono-sm">{l.stageId}</TD>
                <TD className="max-w-[300px] truncate font-mono text-mono-sm text-text-2" title={l.priceRef ?? l.service}>
                  {l.service}
                </TD>
                <TD numeric>
                  <Tooltip content={l.usage.basis ?? "logged by the stage or the model call"}>
                    <span className="inline-flex items-baseline gap-1" tabIndex={0}>
                      <Num value={l.usage.quantity} kind={Number.isInteger(l.usage.quantity) ? "int" : "dec2"} provenance={l.usage.measured ? undefined : "derived"} bare={l.usage.measured} />
                      <span className="text-xs text-text-2">{UNIT_LABEL[l.usage.unit] ?? l.usage.unit}</span>
                    </span>
                  </Tooltip>
                </TD>
                {view.published && (
                  <TD numeric>
                    <Num value={l.estimateUsd} kind="raw" provenance="estimate" bare />
                  </TD>
                )}
              </TR>
            ))}
          </TBody>
        </Table>
      </div>
    </Card>
  );
}

function Tiering({ view }: { view: CostView }) {
  return (
    <Card>
      <CardHeader title="Model tiering" meta="tier per call type is configuration (config/models.yaml)" />
      <Table>
        <THead>
          <tr>
            <TH>Call type</TH>
            <TH>Model</TH>
            <TH numeric>Calls</TH>
            <TH numeric>Tokens in · out</TH>
            {view.published && <TH numeric>Estimate</TH>}
          </tr>
        </THead>
        <TBody>
          {view.tiering.map((r) => (
            <TR key={`${r.callType}-${r.modelId}`}>
              <TD>
                <span className="text-text">{r.callType}</span>
                <span className="block font-mono text-mono-sm text-text-2">{r.modelKey}</span>
              </TD>
              <TD className="font-mono text-mono-sm text-text-2">{r.modelId}</TD>
              <TD numeric>
                <Num value={r.calls} kind="int" bare />
              </TD>
              <TD numeric>
                <Tooltip content={r.source}>
                  <span tabIndex={0}>
                    <Num value={r.tokensIn} kind="int" bare /> · <Num value={r.tokensOut} kind="int" bare />
                  </span>
                </Tooltip>
              </TD>
              {view.published && (
                <TD numeric>
                  <Num value={r.estimateUsd} kind="raw" provenance="estimate" bare />
                </TD>
              )}
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

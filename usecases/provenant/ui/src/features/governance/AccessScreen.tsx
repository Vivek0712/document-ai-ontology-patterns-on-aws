/**
 * W-13 Governance · Access and security.
 * - Access: the signed-in identities and their token claims, the identity path a request takes, and the access log
 *   of one run (`GET /access/decisions?runId=`): per tool call, what the gateway injected and what it decided.
 *   "Try as B" sends the same request with User B's session; the answer is the API's, never simulated.
 * - Encryption and transport: every row is read by a describe call at request time (`GET /governance/security`).
 *   That route is not built yet, so each row says "not verified" (never a default value).
 * URL: /governance/access?tab=access|security&run=<runId>
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, Lock, UserRound } from "lucide-react";
import { useState } from "react";
import type { AccessDecision, Me } from "@contracts/api";
import { useApi, useMe, useSession } from "../../api/context";
import { allRuns } from "../../api/runs";
import { RpApiError } from "../../api/types";
import { Page, PageHeader, PlayExplainer } from "../../app/Page";
import { useWorkspace } from "../../app/workspace";
import { shortSub, subLabel } from "../../lib/format";
import { Badge, Button, Card, CardHeader, DecisionBadge, EmptyState, ErrorState, LaneBadge, Num, ProvenanceScope, ProvenanceTag, Skeleton, TBody, TD, TH, THead, TR, Table, Tabs, TabsContent, TabsList, TabsTrigger, Tooltip } from "../../design";
import { useSearchParam } from "../../hooks/useSearchParam";

export default function AccessScreen() {
  const [tab, setTab] = useSearchParam("tab", "access");
  return (
    <Page>
      <PageHeader
        crumbs={[{ label: "Governance" }, { label: "Access and security" }]}
        title="Access and security"
        description="Who asked, what the gateway injected into each tool call, what it allowed; how data is encrypted and transported."
        actions={<PlayExplainer showcaseId="sc-7" />}
      />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="access">Access</TabsTrigger>
          <TabsTrigger value="security">Encryption and transport</TabsTrigger>
        </TabsList>
        <TabsContent value="access">
          <AccessTab />
        </TabsContent>
        <TabsContent value="security">
          <SecurityTab />
        </TabsContent>
      </Tabs>
    </Page>
  );
}

function AccessTab() {
  const api = useApi();
  const me = useMe();
  const { apiAs } = useSession();
  const { packageId } = useWorkspace();
  const [runParam, setRun] = useSearchParam("run");
  const runs = useQuery({ queryKey: ["runs-all", me.sub, packageId], queryFn: () => allRuns(api, packageId) });
  const sorted = [...(runs.data ?? [])].filter((r) => r.status === "ok").sort((a, b) => b.createdAt.localeCompare(a.createdAt));
  const runId = runParam || sorted[0]?.runId || "";
  const decisions = useQuery({ queryKey: ["access", me.sub, runId], queryFn: () => api.getAccessDecisions(runId), enabled: Boolean(runId), retry: false });
  const peers = useQuery({
    queryKey: ["identities", me.sub],
    queryFn: async () => {
      const [a, b] = await Promise.all([apiAs("a"), apiAs("b")]);
      return { a: await a?.me(), b: await b?.me() };
    },
  });
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_minmax(0,1.3fr)]">
        <IdentityCard letter="A" me={peers.data?.a} current={me} />
        <IdentityCard letter="B" me={peers.data?.b} current={me} />
        <IdentityPath />
      </div>
      <Card>
        <CardHeader
          title="Access log"
          meta={decisions.data?.items[0]?.source === "run-record" ? "rebuilt from the run record: this run has no interceptor log rows" : decisions.data ? "written by the interceptor" : undefined}
          actions={
            <span className="flex items-center gap-2">
              <label htmlFor="run" className="text-xs text-text-2">
                Run
              </label>
              <select id="run" value={runId} onChange={(e) => setRun(e.target.value)} className="h-7 max-w-[420px] rounded-sm border border-border-strong bg-surface px-2 font-mono text-mono-sm text-text">
                {sorted.map((r) => (
                  <option key={r.runId} value={r.runId}>
                    {r.runId} · {r.pipelineVersion} · {r.modelId.split(".").at(-1)} · {r.question.slice(0, 40)}
                  </option>
                ))}
              </select>
              <ProvenanceTag provenance={api.mode === "http" ? "measured" : "local-run"} source={`GET /access/decisions?runId=${runId}`} />
            </span>
          }
        />
        {decisions.error ? (
          <div className="p-4">
            <ErrorState title="No access decisions for this run" message={decisions.error instanceof Error ? decisions.error.message : String(decisions.error)} code={decisions.error instanceof RpApiError ? decisions.error.code : undefined} />
          </div>
        ) : !decisions.data ? (
          <Skeleton className="m-4 h-32" />
        ) : (
          <DecisionTable rows={decisions.data.items} known={[peers.data?.a?.sub, peers.data?.b?.sub, me.sub]} />
        )}
        {runId && <TryAsB runId={runId} />}
      </Card>
    </div>
  );
}

function IdentityCard({ letter, me, current }: { letter: "A" | "B"; me?: Me; current: Me }) {
  return (
    <Card>
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <span className="flex size-5 items-center justify-center rounded-full bg-surface-3 text-xs font-semibold text-text">{letter}</span>
            {me?.email ?? `User ${letter}`}
          </span>
        }
        meta={me?.sub === current.sub ? "this session" : me ? "second session" : undefined}
      />
      {me ? (
        <dl className="grid grid-cols-[88px_minmax(0,1fr)] gap-x-3 gap-y-1 p-4 text-sm">
          <dt className="text-text-2">sub</dt>
          <dd className="font-mono text-mono-sm text-text" title={me.sub}>
            {shortSub(me.sub)}
          </dd>
          <dt className="text-text-2">groups</dt>
          <dd className="flex flex-wrap gap-1">
            {me.groups.map((g) => (
              <Badge key={g} tone="neutral">
                {g}
              </Badge>
            ))}
          </dd>
          <dt className="text-text-2">principals</dt>
          <dd className="font-mono text-mono-sm text-text-2 [overflow-wrap:anywhere]">{me.principals.join(", ")}</dd>
          <dt className="text-text-2">token</dt>
          <dd className="text-xs text-text-2">Cognito access token, in memory only</dd>
        </dl>
      ) : (
        <p className="flex items-center gap-2 p-4 text-sm text-text-2">
          <UserRound className="size-4" aria-hidden /> Not signed in in this browser.
        </p>
      )}
    </Card>
  );
}

/** The path a request's identity takes. */
function IdentityPath() {
  const hops = ["Cognito JWT at Amazon API Gateway", "AgentCore Runtime", "AgentCore Gateway · inbound authorizer", "REQUEST interceptor"];
  return (
    <Card>
      <CardHeader title="Identity path" actions={<ProvenanceTag provenance="illustrative" source="schematic: the deployed path, no values" />} />
      <div className="flex flex-col gap-3 p-4">
        <ol className="flex flex-wrap items-center gap-1.5">
          {hops.map((h, i) => (
            <li key={h} className="flex items-center gap-1.5">
              {i > 0 && <ArrowRight className="size-3.5 text-text-3" aria-hidden />}
              <span className="rounded-sm border border-border-strong px-2 py-1 text-xs text-text">{h}</span>
            </li>
          ))}
        </ol>
        <p className="text-xs text-text-2">
          The interceptor injects callerSub, groups, pipelineVersion, packageId and runId into every tool call and strips any the model sent. Whether the Gateway's inbound authorizer counts as AgentCore Identity's inbound auth is to verify on the deployed stack. There is no outbound credential provider: every tool is a Lambda in the same account.
        </p>
      </div>
    </Card>
  );
}

function DecisionTable({ rows, known }: { rows: AccessDecision[]; known: (string | undefined)[] }) {
  if (!rows.length) return <EmptyState title="This run made no tool call." />;
  return (
    <div className="overflow-x-auto" data-testid="access-decisions">
      <Table>
        <THead>
          <tr>
            <TH>Time</TH>
            <TH>Tool</TH>
            <TH>Caller</TH>
            <TH>Injected</TH>
            <TH>Model sent</TH>
            <TH>Point</TH>
            <TH>Decision</TH>
          </tr>
        </THead>
        <TBody>
          {rows.map((a) => (
            <TR key={a.callId}>
              <TD className="whitespace-nowrap font-mono text-mono-sm text-text-2">{a.at.slice(11, 23)}</TD>
              <TD>
                <span className="font-mono text-mono-sm text-text">{a.tool}</span>
                <span className="block font-mono text-mono-sm text-text-2">{a.target}</span>
              </TD>
              <TD>
                <span className="font-mono text-mono-sm" title={`${a.callerSub6}: the first 6 characters (the access log never holds a full sub)`}>
                  {subLabel(a.callerSub6, known)}
                </span>
                <span className="block text-xs text-text-2">{a.groups.join(", ")}</span>
              </TD>
              <TD className="whitespace-nowrap">
                <span className="flex items-center gap-1.5">
                  <LaneBadge pv={a.injected.pipelineVersion} />
                  <span className="font-mono text-mono-sm text-text-2">{a.injected.packageId}</span>
                </span>
              </TD>
              <TD className="font-mono text-mono-sm text-text-2">
                {a.modelArgs.join(", ")}
                {a.strippedModelKeys.length > 0 && <span className="block text-text">stripped {a.strippedModelKeys.join(", ")}</span>}
              </TD>
              <TD className="font-mono text-mono-sm text-text-2">{a.injectionPoint}</TD>
              <TD>
                <DecisionBadge d={a} />
                {a.reason && <span className="block text-xs text-text-2">{a.reason}</span>}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  );
}

/** Replays the selected request with User B's session; shows whatever the API answered. */
function TryAsB({ runId }: { runId: string }) {
  const { apiAs } = useSession();
  const [state, setState] = useState<{ status: "idle" | "sending" } | { status: "done"; code: number; message: string; rows?: number } | { status: "no-session" }>({ status: "idle" });
  const send = async () => {
    setState({ status: "sending" });
    const b = await apiAs("b");
    if (!b) return setState({ status: "no-session" });
    try {
      const page = await b.getAccessDecisions(runId);
      setState({ status: "done", code: 200, message: "rows returned to User B", rows: page.items.length });
    } catch (e) {
      setState({ status: "done", code: e instanceof RpApiError ? e.status : 0, message: e instanceof Error ? e.message : String(e) });
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-3 border-t border-border px-4 py-3">
      <Tooltip content="Sends GET /access/decisions for this run with User B's token.">
        <Button size="sm" variant="secondary" onClick={() => void send()} loading={state.status === "sending"}>
          Try as B
        </Button>
      </Tooltip>
      {state.status === "done" && (
        <span className="flex items-center gap-2 text-sm" data-testid="try-as-b">
          {state.code === 403 && <Lock className="size-3.5 text-loss" aria-hidden />}
          <span className="font-mono text-mono-sm text-text">{state.code}</span>
          <span className="text-text">
            {state.rows !== undefined && (
              <ProvenanceScope provenance="measured" source={`GET /access/decisions?runId=${runId} as User B`}>
                <Num value={state.rows} kind="int" bare />{" "}
              </ProvenanceScope>
            )}
            {state.message}
          </span>
          <span className="text-xs text-text-2">answered by the API</span>
        </span>
      )}
      {state.status === "no-session" && <span className="text-sm text-text-2">No User B session in this browser.</span>}
    </div>
  );
}

const SECURITY_ROWS: { what: string; read: string }[] = [
  { what: "Customer managed key alias/rp-data · key rotation", read: "kms:DescribeKey, kms:GetKeyRotationStatus" },
  { what: "Data bucket default encryption", read: "s3:GetBucketEncryption" },
  { what: "Data bucket TLS-only policy (aws:SecureTransport)", read: "s3:GetBucketPolicy" },
  { what: "DynamoDB table encryption", read: "dynamodb:DescribeTable" },
  { what: "OpenSearch Serverless encryption policy", read: "aoss:GetSecurityPolicy" },
  { what: "Neptune storage encryption", read: "rds:DescribeDBClusters" },
  { what: "CloudFront TLS policy", read: "cloudfront:GetDistributionConfig" },
  { what: "API Gateway TLS policy", read: "apigateway:GetDomainName" },
];

function SecurityTab() {
  return (
    <Card data-testid="security">
      <CardHeader title="Encryption and transport" meta="read by describe calls at request time" />
      <Table>
        <THead>
          <tr>
            <TH>Control</TH>
            <TH>Read by</TH>
            <TH>State</TH>
          </tr>
        </THead>
        <TBody>
          {SECURITY_ROWS.map((r) => (
            <TR key={r.what}>
              <TD className="text-text">{r.what}</TD>
              <TD className="font-mono text-mono-sm text-text-2">{r.read}</TD>
              <TD>
                <Badge tone="neutral" className="border-dashed">
                  not verified
                </Badge>
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
      <p className="border-t border-border px-4 py-2 text-xs text-text-2">GET /governance/security is not built yet, so no row is verified. A row shows a value only when a describe call returned it; it never shows a default.</p>
    </Card>
  );
}

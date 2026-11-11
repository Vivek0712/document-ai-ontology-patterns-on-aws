/**
 * SC-7 payload: the package owner (User A) and another analyst (User B) ask the same question.
 * `GET /showcases/sc-7/payload` composes both users' logged runs per lane (what `search_units` returned, the filter it
 * ran, the identity the gateway injected) and the access-log rows of every tool call. The region check is a real
 * request sent with each identity's own session (`peer`), so the 403 comes from the API, never from client logic
 *; without a second session the step says so instead of simulating it.
 */
import type { AccessDecision, Me, RegionResult, ShowcasePayload, SourceRef } from "@contracts/api";
import { allRuns, dayOf, qidOf, questionIndex } from "../../api/runs";
import { type RpApi, RpApiError } from "../../api/types";
import type { Provenance } from "../../design";
import type { Loaded, StoryContext } from "../engine/types";
import { packageOfQuestion } from "./compare";
import { LANES, type Lane, gateQuestion } from "./lending";

export interface Sc7Search {
  callId: string;
  status: "ok" | "error" | "denied";
  units: number;
  index: string | null;
  filter: Record<string, unknown> | null;
  error: string | null;
}

export interface Sc7Run {
  runId: string;
  createdAt: string;
  modelId: string;
  status: string;
  total: string | null;
  answer: string | null;
  check: { match: boolean; expected: string } | null;
  claims: number;
  resolvableClaims: number;
  search: Sc7Search[];
  toolCalls: number;
  injected: { callerSub: string; groups: string[]; pipelineVersion: string; packageId: string; runId: string; via: string };
}

export interface Sc7User {
  role: "owner" | "other";
  callerSub6: string;
  groups: string[];
  runs: Partial<Record<Lane, Sc7Run>>;
  n: Partial<Record<Lane, number>>;
}

export interface Sc7Data {
  question: string;
  packageId: string;
  users: Sc7User[];
  access: Record<string, AccessDecision[]>;
  regionProbe: { unitId: string; fieldId?: string };
}

/** One request sent with one identity's session: what the API answered. */
export type Probe =
  | { state: "ok"; status: 200; region?: RegionResult }
  | { state: "refused"; status: number; code: string; message: string; requestId?: string }
  | { state: "no-session" };

export interface Sc7Payload {
  data: Sc7Data;
  /** the identities as their sessions report them (`GET /me`); B absent without a second session */
  me: Partial<Record<"a" | "b", Me>>;
  probes: { regionA: Probe; regionB: Probe; packageB: Probe };
  packageName: string;
  /** who the package admits (`Package.allowedPrincipals`, read as the owner) */
  packagePrincipals: string[];
  qid: string;
  payloadSha: string;
  sources: SourceRef[];
  provenance: Provenance;
}

async function probe<T>(fn: (() => Promise<T>) | undefined, ok: (v: T) => Probe): Promise<Probe> {
  if (!fn) return { state: "no-session" };
  try {
    return ok(await fn());
  } catch (e) {
    if (e instanceof RpApiError) return { state: "refused", status: e.status, code: e.code, message: e.message, requestId: e.requestId };
    throw e;
  }
}

export async function loadSc7({ api, packageId, peer }: StoryContext): Promise<Loaded<Sc7Payload>> {
  const qid = gateQuestion("sc-7").questionId;
  const home = await packageOfQuestion(api, qid, packageId);
  if (!home) throw new Error(`No logged run of ${qid} in a package you can read.`);
  const [runs, scores] = await Promise.all([allRuns(api, home.packageId), api.getRunScores(home.packageId)]);
  const byQ = questionIndex(runs, scores);
  const question = runs.find((r) => qidOf(r, scores, byQ) === qid)?.question;
  if (!question) throw new Error(`No logged run of ${qid} in ${home.packageId}.`);
  const res = (await api.getShowcasePayload<Sc7Data>("sc-7", { packageId: home.packageId, question })) as ShowcasePayload<Sc7Data>;
  const d = res.data;
  const b: RpApi | undefined = await peer?.("b");
  const { unitId, fieldId } = d.regionProbe;
  const [pkg, meA, meB, regionA, regionB, packageB] = await Promise.all([
    api.getPackage(home.packageId),
    api.me(),
    b?.me(),
    probe(() => api.getRegion(unitId, fieldId), (region) => ({ state: "ok", status: 200, region })),
    probe(b && (() => b.getRegion(unitId, fieldId)), () => ({ state: "ok", status: 200 })),
    probe(b && (() => b.getPackage(home.packageId)), () => ({ state: "ok", status: 200 })),
  ]);
  const owner = d.users.find((u) => u.role === "owner");
  const recorded = owner?.runs["2.3.1"]?.createdAt;
  const local = res.mode !== "live";
  return {
    source: {
      mode: local ? "local-run" : "replay",
      label: `Replay of ${res.runIds.length} logged ${local ? "local " : ""}runs · two identities`,
      detail: local
        ? "Logged runs: the v1 agent on Amazon Bedrock from a dev machine, each identity injected by the local gateway stand-in (not AWS); the region and package checks are live requests to the mock API"
        : "Logged runs on AWS; the region and package checks are live requests with each identity's token",
      recordedAt: dayOf(recorded),
      runId: owner?.runs["2.3.1"]?.runId,
    },
    payload: {
      data: d,
      me: { a: meA, ...(meB ? { b: meB } : {}) },
      probes: { regionA, regionB, packageB },
      packageName: home.name,
      packagePrincipals: pkg.package.allowedPrincipals,
      qid,
      payloadSha: res.payloadSha,
      sources: res.sources,
      provenance: local ? "local-run" : "measured",
    },
  };
}

export const userOf = (p: Sc7Payload, role: Sc7User["role"]) => p.data.users.find((u) => u.role === role);
export const USERS: { role: Sc7User["role"]; letter: "A" | "B"; key: "a" | "b" }[] = [
  { role: "owner", letter: "A", key: "a" },
  { role: "other", letter: "B", key: "b" },
];

/** The lane SC-7 follows through steps 3–5 (the one whose answer cites regions). */
export const FOCUS_LANE: Lane = LANES[1];

// ------------------------------------------------------------------------------------------- captions

/** Step 3: where the identity was injected, from the access rows. */
export function injectionCaption(p: Sc7Payload): string {
  const points = new Set(Object.values(p.data.access).flat().map((r) => r.injectionPoint));
  if (points.has("agent_hook")) return "An agent hook injects who is asking. The model cannot widen it.";
  if (points.has("local-gateway") && !points.has("interceptor")) return "The local gateway injects who is asking. The model cannot widen it.";
  return "The gateway injects who is asking. The model cannot widen it.";
}

/** Step 4: chosen from User B's search calls (spec variants, plus the package refusal the tool logged). */
export function scopedCaption(p: Sc7Payload): string {
  const calls = Object.values(userOf(p, "other")?.runs ?? {}).flatMap((r) => r?.search ?? []);
  const units = calls.reduce((s, c) => s + c.units, 0);
  if (units > 0) return `User B retrieves ${units} units; see the access log.`;
  if (calls.length && calls.every((c) => c.status === "denied")) return "The search tool refuses User B: no access to this package.";
  return "User B's filter matches nothing in this package.";
}

/** Step 5: from the live probe. */
export function regionCaption(p: Sc7Payload): string {
  const b = p.probes.regionB;
  if (b.state === "refused" && b.status === 403) return "The region service checks access again.";
  if (b.state === "no-session") return "Sign in as User B to send the region request with B's token.";
  return "User B opened the region: see the receipts.";
}

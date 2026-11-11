/**
 * Time projection for the mock API. Fixtures hold the FINAL records of every lane plus the full event
 * log (QUEUED/STARTED/HEARTBEAT/SUCCEEDED/FAILED/SKIPPED, as rp-doc-status stores them). Projecting at a
 * virtual time `v` replays the log, so the board shows PENDING -> QUEUED -> RUNNING (heartbeats) ->
 * SUCCEEDED exactly as DynamoDB would at that moment.
 */
import type {
  Counters,
  DocStatusView,
  DocumentItem,
  LaneRecord,
  LaneRollup,
  LaneView,
  StageCell,
  StageEvent,
  StageRecord,
} from "@contracts/api";
import type { WorldLane } from "./world";

export const ms = (iso: string | undefined): number => (iso ? Date.parse(iso) : Number.NaN);
export const iso = (t: number): string => new Date(t).toISOString();

const TIME_KEY = /(^at$|At$)/;

/** Deep-copy `value`, moving every ISO timestamp field (`at`, `*At`) by `shiftMs`. */
export function shiftTimes<T>(value: T, shiftMs: number): T {
  if (shiftMs === 0) return structuredClone(value);
  const walk = (v: unknown, key?: string): unknown => {
    if (Array.isArray(v)) return v.map((x) => walk(x));
    if (v && typeof v === "object") {
      const out: Record<string, unknown> = {};
      for (const [k, x] of Object.entries(v)) out[k] = walk(x, k);
      return out;
    }
    if (typeof v === "string" && key && TIME_KEY.test(key) && !Number.isNaN(Date.parse(v))) {
      return iso(Date.parse(v) + shiftMs);
    }
    return v;
  };
  return walk(value) as T;
}

/** Project one stage record at virtual time `v` from its final record and the lane's event log. */
export function projectStage(final: StageRecord, events: StageEvent[], v: number): StageRecord {
  const mine = events.filter((e) => e.stageId === final.stageId && ms(e.at) <= v);
  const last = mine[mine.length - 1];
  const base: StageRecord = {
    ...final,
    state: "PENDING",
    attempt: 0,
    heartbeatCount: 0,
    inputs: final.inputs,
    artifacts: [],
    metrics: {},
    queuedAt: undefined,
    startedAt: undefined,
    lastHeartbeatAt: undefined,
    endedAt: undefined,
    durationMs: undefined,
    progress: undefined,
    message: undefined,
    headline: undefined,
    output: undefined,
    error: undefined,
    skipReason: undefined,
  };
  if (!last) return base;
  const lastStart = [...mine].reverse().find((e) => e.event === "STARTED");
  const queued = mine.find((e) => e.event === "QUEUED");
  switch (last.event) {
    case "SKIPPED":
      return { ...base, state: "SKIPPED", skipReason: final.skipReason ?? last.message, updatedAt: last.at };
    case "QUEUED":
      return { ...base, state: "QUEUED", queuedAt: last.at, message: last.message, updatedAt: last.at };
    case "STARTED":
    case "HEARTBEAT": {
      const hbs = mine.filter((e) => e.event === "HEARTBEAT" && lastStart && ms(e.at) >= ms(lastStart.at));
      const lastHb = hbs[hbs.length - 1];
      return {
        ...base,
        state: "RUNNING",
        attempt: last.attempt,
        queuedAt: queued?.at,
        startedAt: lastStart?.at,
        heartbeatCount: hbs.length,
        lastHeartbeatAt: lastHb?.at,
        progress: lastHb?.progress ?? (final.progress ? { ...final.progress, done: 0 } : undefined),
        message: last.message,
        updatedAt: last.at,
      };
    }
    case "FAILED": {
      if (final.state === "FAILED" && ms(final.endedAt) <= v) return final;
      // transient failure before a Step Functions Retry
      return {
        ...base,
        state: "FAILED",
        attempt: last.attempt,
        queuedAt: queued?.at,
        startedAt: lastStart?.at,
        endedAt: last.at,
        durationMs: last.durationMs,
        error: last.error,
        message: last.message,
        updatedAt: last.at,
      };
    }
    case "SUCCEEDED":
      return final;
  }
}

export function projectLane(lane: WorldLane, v: number): { lane: LaneRecord; stages: StageRecord[]; events: StageEvent[] } {
  const events = lane.events.filter((e) => ms(e.at) <= v);
  const stages = lane.stages.map((s) => projectStage(s, lane.events, v));
  const final = lane.lane;
  let record: LaneRecord;
  if (!events.length) {
    record = { docId: final.docId, packageId: final.packageId, pipelineVersion: final.pipelineVersion, state: "PENDING", updatedAt: final.startedAt ?? final.updatedAt, configSha: final.configSha };
  } else if (ms(final.endedAt) <= v) {
    record = final;
  } else {
    const lastEv = events[events.length - 1];
    record = {
      docId: final.docId,
      packageId: final.packageId,
      pipelineVersion: final.pipelineVersion,
      state: "RUNNING",
      currentStage: lastEv.stageId,
      executionArn: final.executionArn,
      startedAt: final.startedAt,
      configSha: final.configSha,
      configVersionId: final.configVersionId,
      promptSha: final.promptSha,
      ontologyVersion: final.ontologyVersion,
      updatedAt: lastEv.at,
    };
  }
  return { lane: record, stages, events };
}

export function toCell(r: StageRecord): StageCell {
  return {
    stageId: r.stageId,
    group: r.group,
    order: r.order,
    title: r.title,
    state: r.state,
    engine: r.engine,
    headline: r.headline,
    attempt: r.attempt,
    queuedAt: r.queuedAt,
    startedAt: r.startedAt,
    endedAt: r.endedAt,
    durationMs: r.durationMs,
    lastHeartbeatAt: r.lastHeartbeatAt,
    heartbeatCount: r.heartbeatCount,
    progress: r.progress,
    metrics: r.metrics,
    skipReason: r.skipReason,
    error: r.error,
  };
}

export function laneView(p: { lane: LaneRecord; stages: StageRecord[] }): LaneView {
  return {
    state: p.lane.state,
    currentStage: p.lane.currentStage,
    executionArn: p.lane.executionArn,
    startedAt: p.lane.startedAt,
    endedAt: p.lane.endedAt,
    durationMs: p.lane.durationMs,
    configSha: p.lane.configSha,
    stages: p.stages.map(toCell),
  };
}

export function docStatusView(doc: DocumentItem, lanes: Record<string, LaneView>): DocStatusView {
  return {
    docId: doc.docId,
    fileName: doc.fileName,
    sizeBytes: doc.sizeBytes,
    pageCount: doc.pageCount,
    docType: doc.docType,
    uploadState: doc.uploadState,
    bronzeKey: doc.bronzeKey,
    inBronzeAt: doc.inBronzeAt,
    lanes,
  };
}

/** Package rollup per pipeline version (what the rp-doc-status stream writes onto rp-packages). */
export function rollup(
  docs: DocStatusView[],
  pv: string,
): { lane: LaneRollup; counters: Counters } {
  const counts = { total: 0, pending: 0, running: 0, succeeded: 0, failed: 0, skipped: 0 };
  const counters: Counters = { units: 0, held: 0, indexed: 0, triples: 0 };
  let expected = 0;
  for (const d of docs) {
    if (d.uploadState !== "IN_BRONZE" && d.uploadState !== "QUARANTINED") continue;
    const lane = d.lanes[pv];
    if (!lane) continue;
    counts.total += 1;
    const key = lane.state.toLowerCase() as keyof typeof counts;
    counts[key] += 1;
    for (const c of lane.stages) {
      if (c.state !== "SUCCEEDED") continue;
      if (c.stageId === "units") counters.units += c.metrics.units ?? 0;
      if (c.stageId === "pii_gate") {
        counters.held += c.metrics.held ?? 0;
        expected += c.metrics.passed ?? 0;
      }
      if (c.stageId === "index") counters.indexed += c.metrics.indexed ?? 0;
      if (c.stageId === "graph") counters.triples += c.metrics.triples ?? 0;
    }
  }
  let state: LaneRollup["state"];
  if (counts.total === 0) state = "EMPTY";
  else if (counts.running > 0) state = "RUNNING";
  else if (counts.pending > 0) state = "PENDING";
  else if (counts.failed === counts.total) state = "FAILED";
  else if (counts.failed > 0) state = "PARTIAL";
  else state = "SUCCEEDED";
  const ready = (state === "SUCCEEDED" || state === "PARTIAL") && counters.indexed > 0;
  return { lane: { state, ready, indexed: counters.indexed, expected, docs: counts }, counters };
}

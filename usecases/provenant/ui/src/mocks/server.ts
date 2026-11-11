/**
 * Mock API (VITE_API_MODE=mock | replay). Dev only: serves world.json (generated from the Python contracts)
 * through the same RpApi interface as HttpApi, with live pipeline timing, ACL checks, uploads that start
 * new lanes, reprocess, and a replayed /ask stream. Never used by the production build.
 */
import type {
  AccessDecisionPage,
  AskEvent,
  AskRequest,
  BuildReplay,
  CoaDownRequest,
  CoaEnvironmentView,
  CoaKeepAliveRequest,
  CoaSettingsRequest,
  CoaUpRequest,
  CostView,
  DocumentDetail,
  DocumentItem,
  EvalRunList,
  EvalRunView,
  FilterInspection,
  GraphNeighbourhood,
  LaneView,
  GovernedDefinitionView,
  Me,
  OntologyTerm,
  OntologyTermList,
  Package,
  PackageCreate,
  PackageStatus,
  RetrievedUnit,
  ReviewDecisionRequest,
  ReviewItem,
  RunRecord,
  RunSummary,
  ShowcaseCatalog,
  ShowcasePayload,
  StageId,
  StageOverlay,
  Unit,
  UploadUrlResponse,
} from "@contracts/api";
import { STAGE_ORDER } from "@contracts/api";
import { type ApiErrorCode, type AskHandlers, type CoaSimulatedOutcome, type RpApi, RpApiError, type UploadHandle, type UploadProgress } from "../api/types";
import { coaSimulation } from "./coaEnv";
import { docStatusView, iso, laneView, ms, projectLane, rollup, shiftTimes } from "./clock";
import { overlayFromArtifacts } from "../lib/overlay";
import { type KnowledgeExport, loadKnowledge, loadShard, mergeShard, type World, type WorldLane } from "./world";
import { MOCK_MODEL_ID, type RunScore } from "../api/runs";
import type { XrayPage } from "../api/xray";
import { loadXrayIndex } from "./xrayFixtures";

const PVS = ["1.0", "2.3.1"];
/** The publication flag as the mock reads it (`VITE_PUBLIC_METRICS=on`). The published views exist only in
 *  an export made with `make mock-world PUBLIC_METRICS=on`; without them the mock serves the unpublished view. */
const PUBLIC_METRICS = (import.meta.env?.VITE_PUBLIC_METRICS ?? "off") === "on";
const metricsView = <T,>(views: Partial<Record<"on" | "off", T>> | undefined): T | undefined => (PUBLIC_METRICS ? views?.on ?? views?.off : views?.off);
const LENDING = "pkg-lending-01";
const MOCK_MODEL = MOCK_MODEL_ID;

const sleep = (n: number) => new Promise((r) => setTimeout(r, n));
const latency = () => sleep(40 + Math.random() * 120);
const hex = (n: number) =>
  Array.from(crypto.getRandomValues(new Uint8Array(Math.ceil(n / 2))), (b) => b.toString(16).padStart(2, "0"))
    .join("")
    .slice(0, n);

function slug(s: string, max = 40): string {
  return s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, max).replace(/-$/, "") || "x";
}

async function docIdFor(packageId: string, fileName: string): Promise<string> {
  const stem = fileName.includes(".") ? fileName.slice(0, fileName.lastIndexOf(".")) : fileName;
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(`${packageId}/${fileName}`));
  const h = Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 6);
  return `${slug(stem)}-${h}`;
}

/** evidence_core.models.golden.normalise_question: the key of `askReplays`. */
function pyNormaliseQuestion(q: string): string {
  return q.trim().toLowerCase().replace(/\?+$/, "").trim().replace(/\s+/g, " ");
}

function normaliseQuestion(q: string): string {
  return q.toLowerCase().replace(/[^a-z0-9 ]+/g, " ").replace(/\s+/g, " ").trim();
}

interface DocEntry {
  item: DocumentItem; // raw (unshifted) times
  shift: number;
  /** object URL of the uploaded file (mock bronze GET for the owner) */
  blobUrl?: string;
  /** template docId this doc was cloned from (page renders are named after it) */
  template?: string;
}

interface PkgEntry {
  pkg: Package;
  docIds: string[];
}

export class MockApi implements RpApi {
  readonly mode: "mock" | "replay";
  private readonly packages = new Map<string, PkgEntry>();
  private readonly docs = new Map<string, DocEntry>();
  private readonly laneShift = new Map<string, number>();
  /** runs asked in this session (keyword stand-in); logged runs are fetched from /mock/runs/ on demand */
  private readonly runs: RunRecord[] = [];
  private readonly logged = new Map<string, Promise<RunRecord>>();
  private readonly runSummaries: RunSummary[] = [];
  private readonly base: number;
  private readonly user: Me;
  /** per-package shards already merged (`world.shards`), and the loads in flight */
  private readonly shardLoads = new Map<string, Promise<void>>();
  private knowledge?: Promise<KnowledgeExport>;
  /** review decisions made in this session (the mock's stand-in for the table write) */
  private readonly decisions = new Map<string, ReviewItem>();

  /**
   * `ingestStartedAt` (mock mode): when the lending package's recorded ingest starts replaying (epoch ms). The
   * default is now, so the board shows it run; the session provider passes a remembered time so a reload does
   * not restart it, or an hour ago (`?ingest=done`) for a finished package.
   */
  constructor(
    private readonly world: World,
    userKey: "a" | "b",
    mode: "mock" | "replay" = "mock",
    ingestStartedAt: number = Date.now(),
  ) {
    this.mode = mode;
    this.user = world.users[userKey];
    this.base = ms(world.baseTime);
    const now = Date.now();
    for (const wp of world.packages) {
      // the lending package replays its recorded ingest from `ingestStartedAt`; in replay mode, and for any
      // other package, the recorded ingest ran an hour ago (finished).
      const start = wp.package.packageId === LENDING && mode === "mock" ? Math.min(now, ingestStartedAt + 2000) - 2000 : now - 60 * 60_000;
      const shift = start - (wp.baseTime ? ms(wp.baseTime) : this.base);
      this.packages.set(wp.package.packageId, { pkg: wp.package, docIds: wp.documents.map((d) => d.docId) });
      for (const d of wp.documents) {
        this.docs.set(d.docId, { item: d, shift });
        for (const pv of PVS) this.laneShift.set(`${d.docId}|${pv}`, shift);
      }
    }
    // logged runs keep the time they were recorded: a replay banner quotes that date
    for (const s of world.runSummaries) this.runSummaries.push(structuredClone(s));
  }

  /** Dev control on the mock banner: finish every running lane now. */
  fastForward(): void {
    for (const [k, v] of this.laneShift) this.laneShift.set(k, v - 15 * 60_000);
    for (const d of this.docs.values()) d.shift -= 15 * 60_000;
  }

  // ---------------------------------------------------------------------------------------- access
  private principals(): string[] {
    return this.user.principals;
  }

  private pkgOr404(packageId: string): PkgEntry {
    const p = this.packages.get(packageId);
    if (!p) throw new RpApiError(404, "not_found", `package ${packageId} not found`);
    if (!p.pkg.allowedPrincipals.some((x) => this.principals().includes(x)))
      throw new RpApiError(403, "forbidden", `You don't have access to package ${packageId}.`);
    return p;
  }

  private docOr404(docId: string): DocEntry {
    const d = this.docs.get(docId);
    if (!d) throw new RpApiError(404, "not_found", `document ${docId} not found`);
    this.pkgOr404(d.item.packageId);
    return d;
  }

  // ---------------------------------------------------------------------------------------- lazy shards
  /** Fetch and merge a package's shard the first time a route needs it (no-op for an unsharded world). */
  private ensure(packageId: string | undefined): Promise<void> {
    const path = packageId ? this.world.shards?.[packageId] : undefined;
    if (!packageId || !path) return Promise.resolve();
    let load = this.shardLoads.get(packageId);
    if (!load) {
      load = loadShard(path).then((shard) => mergeShard(this.world, shard));
      load.catch(() => this.shardLoads.delete(packageId)); // a failed fetch is retried on the next request
      this.shardLoads.set(packageId, load);
    }
    return load;
  }

  /** The package a document (or a cloned upload's template) belongs to. */
  private pkgOfDoc(docId: string): string | undefined {
    const d = this.docs.get(docId);
    return d ? this.docs.get(d.template ?? docId)?.item.packageId ?? d.item.packageId : undefined;
  }

  private pkgOfRun(runId: string): string | undefined {
    return this.runSummaries.find((r) => r.runId === runId)?.packageId;
  }

  /** An S3 key's package: a package id segment, else the package of the run record it names. */
  private pkgOfKey(key: string): string | undefined {
    return key.split("/").find((seg) => this.packages.has(seg)) ?? this.pkgOfRun(key.replace(/^runs\//, "").replace(/\.json$/, ""));
  }

  private isOwner(p: PkgEntry): boolean {
    return p.pkg.ownerSub === this.user.sub;
  }

  // ---------------------------------------------------------------------------------------- projection
  private projectedDoc(d: DocEntry): DocumentItem {
    const item = shiftTimes(d.item, d.shift);
    if (item.uploadState === "IN_BRONZE" && item.inBronzeAt && ms(item.inBronzeAt) > Date.now()) {
      return { ...item, uploadState: "AWAITING_UPLOAD", inBronzeAt: undefined };
    }
    return item;
  }

  private lane(docId: string, pv: string) {
    const wl: WorldLane | undefined = this.world.lanes[docId]?.[pv];
    if (!wl) return undefined;
    const shift = this.laneShift.get(`${docId}|${pv}`) ?? 0;
    const p = projectLane(wl, Date.now() - shift);
    return shiftTimes(p, shift);
  }

  private stageDone(docId: string, pv: string, stageId: StageId): boolean {
    return this.lane(docId, pv)?.stages.find((s) => s.stageId === stageId)?.state === "SUCCEEDED";
  }

  private status(p: PkgEntry): PackageStatus {
    const docs = p.docIds.map((id) => {
      const d = this.docs.get(id)!;
      const item = this.projectedDoc(d);
      const lanes: Record<string, LaneView> = {};
      if (item.uploadState !== "AWAITING_UPLOAD") {
        for (const pv of PVS) {
          const l = this.lane(id, pv);
          if (l) lanes[pv] = laneView(l);
        }
      }
      return docStatusView(item, lanes);
    });
    const lanes: PackageStatus["lanes"] = {};
    const counters: PackageStatus["counters"] = {};
    for (const pv of p.pkg.pipelineVersions) {
      const r = rollup(docs, pv);
      lanes[pv] = r.lane;
      counters[pv] = r.counters;
    }
    const now = iso(Date.now());
    return {
      packageId: p.pkg.packageId,
      updatedAt: now,
      serverTime: now,
      lanes,
      counters,
      objectsInBronze: docs.filter((d) => d.uploadState === "IN_BRONZE").length,
      askReady: p.pkg.pipelineVersions.every((pv) => lanes[pv]?.ready),
      docs,
    };
  }

  private livePackage(p: PkgEntry): Package {
    const st = this.status(p);
    return { ...p.pkg, docCount: p.docIds.length, lanes: st.lanes, counters: st.counters, updatedAt: st.updatedAt };
  }

  // ---------------------------------------------------------------------------------------- routes
  async me(): Promise<Me> {
    await latency();
    return this.user;
  }

  async listPackages() {
    await latency();
    const items = [...this.packages.values()]
      .filter((p) => p.pkg.allowedPrincipals.some((x) => this.principals().includes(x)))
      .map((p) => this.livePackage(p));
    return { items };
  }

  async createPackage(body: PackageCreate): Promise<Package> {
    await latency();
    if (!body.name?.trim()) throw new RpApiError(400, "bad_request", "name is required");
    const groups = body.shareWithGroups ?? [];
    const bad = groups.filter((g) => !this.user.groups.includes(g));
    if (bad.length) throw new RpApiError(400, "bad_request", `shareWithGroups must be your groups: ${bad.join(", ")}`);
    const packageId = `pkg-${slug(body.name, 36)}-${hex(4)}`;
    const now = iso(Date.now());
    const pkg: Package = {
      packageId,
      name: body.name.trim(),
      description: body.description,
      ownerSub: this.user.sub,
      createdAt: now,
      updatedAt: now,
      allowedPrincipals: [`user:${this.user.sub}`, ...groups.map((g) => `group:${g}`)],
      pipelineVersions: body.pipelineVersions?.length ? body.pipelineVersions : PVS,
      docCount: 0,
      lanes: {},
      counters: {},
    };
    this.packages.set(packageId, { pkg, docIds: [] });
    return this.livePackage(this.packages.get(packageId)!);
  }

  async getPackage(packageId: string) {
    await latency();
    const p = this.pkgOr404(packageId);
    return {
      package: this.livePackage(p),
      documents: p.docIds.map((id) => this.projectedDoc(this.docs.get(id)!)),
      canWrite: this.isOwner(p),
    };
  }

  async deletePackage(packageId: string) {
    await latency();
    const p = this.pkgOr404(packageId);
    if (!this.isOwner(p)) throw new RpApiError(403, "forbidden", "only the owner can delete a package");
    this.packages.delete(packageId);
    for (const id of p.docIds) this.docs.delete(id);
    return { packageId, deleted: { documents: p.docIds.length, s3Objects: p.docIds.length * 24, aossDocs: 0, graphs: 1 } };
  }

  async requestUploadUrl(packageId: string, file: File): Promise<UploadUrlResponse> {
    await latency();
    const p = this.pkgOr404(packageId);
    if (!this.isOwner(p)) throw new RpApiError(403, "forbidden", "only the owner can upload");
    if (!/^[A-Za-z0-9._ -]+\.pdf$/i.test(file.name))
      throw new RpApiError(400, "bad_request", "fileName must match ^[A-Za-z0-9._ -]+\\.pdf$");
    if (file.size > 25 * 1024 * 1024) throw new RpApiError(400, "bad_request", "file is larger than 25 MB");
    const docId = await docIdFor(packageId, file.name);
    const key = `bronze/${this.user.sub}/${packageId}/${file.name}`;
    return {
      docId,
      key,
      url: `mock://s3/${key}`,
      method: "PUT",
      headers: { "Content-Type": "application/pdf" },
      expiresIn: 300,
    };
  }

  async upload(packageId: string, file: File, onProgress: (p: UploadProgress) => void): Promise<UploadHandle> {
    const signed = await this.requestUploadUrl(packageId, file);
    const p = this.packages.get(packageId)!;
    const now = Date.now();
    const item: DocumentItem = {
      packageId,
      docId: signed.docId,
      fileName: file.name,
      bronzeKey: signed.key,
      contentType: "application/pdf",
      sizeBytes: file.size,
      uploadedBy: this.user.sub,
      uploadState: "AWAITING_UPLOAD",
      uploadUrlIssuedAt: iso(now),
    };
    this.docs.set(signed.docId, { item, shift: 0, blobUrl: URL.createObjectURL(file) });
    if (!p.docIds.includes(signed.docId)) p.docIds.push(signed.docId);
    let aborted = false;
    const done = (async () => {
      const steps = 12;
      for (let i = 1; i <= steps; i++) {
        await sleep(90 + Math.random() * 60);
        if (aborted) throw new RpApiError(0, "network", "upload cancelled");
        onProgress({ loaded: Math.round((file.size * i) / steps), total: file.size });
      }
      await this.ensure(LENDING);
      this.startLanes(packageId, signed.docId, file);
    })();
    return { docId: signed.docId, key: signed.key, done, abort: () => (aborted = true) };
  }

  /** S3 Object Created -> EventBridge -> two rp-ingest executions: clone a template lane for this file. */
  private startLanes(packageId: string, docId: string, file: File): void {
    // the caller awaited `ensure(LENDING)`: the template's units, lineage, regions and artifacts are merged
    const lending = this.world.packages.find((w) => w.package.packageId === LENDING)!;
    const n = file.name.toLowerCase();
    const pick =
      lending.documents.find((d) =>
        /w-?2/.test(n)
          ? d.fileName.startsWith("w2_2025_maya")
          : /payslip|pay ?stub/.test(n)
            ? d.fileName.includes(/scan/.test(n) ? "_scan" : "payslip_2026-09_maya")
            : /bank|statement/.test(n)
              ? d.fileName.startsWith("bank_statement")
              : d.fileName.startsWith("w2_2025_maya"),
      ) ?? lending.documents[0];
    const tpl = pick.docId;
    const swap = (s: string) =>
      s
        .split(tpl).join(docId)
        .split(pick.fileName).join(file.name)
        .split(`/${LENDING}/`).join(`/${packageId}/`)
        .split(`:${LENDING}:`).join(`:${packageId}:`)
        .split(`"${LENDING}"`).join(`"${packageId}"`)
        .split(pick.bronzeKey).join(`bronze/${this.user.sub}/${packageId}/${file.name}`);
    const clone = <T,>(v: T): T => JSON.parse(swap(JSON.stringify(v))) as T;
    const w = this.world;
    w.lanes[docId] = clone(w.lanes[tpl]);
    w.unitLists[docId] = clone(w.unitLists[tpl]);
    for (const [k, v] of Object.entries(w.units)) if (k.startsWith(`${tpl}:`)) w.units[swap(k)] = clone(v);
    for (const [k, v] of Object.entries(w.lineage)) if (k.startsWith(`${tpl}:`)) w.lineage[swap(k)] = clone(v);
    for (const [k, v] of Object.entries(w.regions)) if (k.startsWith(`${tpl}:`)) w.regions[swap(k)] = clone(v);
    for (const [k, v] of Object.entries(w.artifacts))
      if (k.includes(`/${tpl}/`) || k === pick.bronzeKey) w.artifacts[swap(k)] = clone(v);
    const tplInBronze = ms(pick.inBronzeAt);
    const shift = Date.now() - tplInBronze;
    const entry = this.docs.get(docId)!;
    entry.template = tpl;
    entry.item = {
      ...clone(pick),
      sizeBytes: file.size,
      uploadUrlIssuedAt: iso(tplInBronze - 1400),
      inBronzeAt: iso(tplInBronze),
      uploadState: "IN_BRONZE",
    };
    entry.shift = shift;
    for (const pv of PVS) this.laneShift.set(`${docId}|${pv}`, shift);
  }

  async packageStatus(packageId: string) {
    await sleep(15);
    return this.status(this.pkgOr404(packageId));
  }

  async getDocument(docId: string): Promise<DocumentDetail> {
    await latency();
    const d = this.docOr404(docId);
    const p = this.packages.get(d.item.packageId)!;
    const lanes: DocumentDetail["lanes"] = {};
    for (const pv of PVS) {
      const l = this.lane(docId, pv);
      if (l) lanes[pv] = { lane: l.lane, stages: l.stages };
    }
    return { document: this.projectedDoc(d), lanes, canWrite: this.isOwner(p) };
  }

  async getStage(docId: string, pv: string, stageId: StageId) {
    await latency();
    this.docOr404(docId);
    const l = this.lane(docId, pv);
    const record = l?.stages.find((s) => s.stageId === stageId);
    if (!l || !record) throw new RpApiError(404, "not_found", `no ${stageId} record for ${docId} @ ${pv}`);
    const definition = this.world.pipelineDetails[pv]?.stages.find((s) => s.id === stageId);
    if (!definition) throw new RpApiError(404, "not_found", `pipeline ${pv} has no stage ${stageId}`);
    const i = STAGE_ORDER.indexOf(stageId);
    return {
      record,
      definition,
      events: l.events.filter((e) => e.stageId === stageId),
      previousStage: i > 0 ? STAGE_ORDER[i - 1] : undefined,
      nextStage: i < STAGE_ORDER.length - 1 ? STAGE_ORDER[i + 1] : undefined,
      history: [],
    };
  }

  async getOverlay(docId: string, pv: string, stageId: StageId, page: number): Promise<StageOverlay> {
    const { record } = await this.getStage(docId, pv, stageId);
    await this.ensure(this.pkgOfDoc(docId));
    // the API's own overlay for this stage once it has finished (exported by `make mock-world`)
    const tpl = this.docs.get(docId)?.template ?? docId;
    const exported = this.world.overlays?.[`${tpl}|${pv}|${stageId}|${page}`];
    if (exported && record.state === "SUCCEEDED") {
      return tpl === docId ? exported : (JSON.parse(JSON.stringify(exported).split(tpl).join(docId)) as StageOverlay);
    }
    const doc = await this.getDocument(docId);
    const imageKey = doc.lanes[pv]?.stages
      .find((s) => s.stageId === "promote")
      ?.artifacts.find((a) => a.kind === "page_image" && a.page === page)?.key;
    const read = async (key: string) => (this.world.artifacts[key] ? this.previewOf(key) : undefined);
    const url = imageKey && this.world.artifacts[imageKey] ? (await this.getArtifact(imageKey)).url : undefined;
    return overlayFromArtifacts(record, doc, page, read, url);
  }

  async getEvents(docId: string, pv: string, cursor?: string, limit = 200) {
    await latency();
    this.docOr404(docId);
    const all = this.lane(docId, pv)?.events ?? [];
    const start = cursor ? Number(cursor) : 0;
    const items = all.slice(start, start + limit);
    return { items, nextCursor: start + limit < all.length ? String(start + limit) : undefined };
  }

  async listUnits(docId: string, pv: string) {
    await latency();
    this.docOr404(docId);
    await this.ensure(this.pkgOfDoc(docId));
    if (!this.stageDone(docId, pv, "units")) return { items: [] };
    const indexed = this.stageDone(docId, pv, "index");
    const list = this.world.unitLists[docId]?.[pv] ?? { items: [] };
    return { items: list.items.map((u) => ({ ...u, indexed: indexed && u.indexed, aossId: indexed ? u.aossId : undefined })) };
  }

  async reprocess(docId: string, pv: string) {
    await latency();
    const d = this.docOr404(docId);
    if (!this.isOwner(this.packages.get(d.item.packageId)!))
      throw new RpApiError(403, "forbidden", "only the owner can reprocess");
    const wl = this.world.lanes[docId]?.[pv];
    if (!wl) throw new RpApiError(404, "not_found", `no lane ${pv}`);
    const laneStart = ms(wl.lane.startedAt) || ms(wl.events[0]?.at);
    this.laneShift.set(`${docId}|${pv}`, Date.now() + 300 - laneStart);
    return { docId, pipelineVersion: pv, executionArn: `${wl.lane.executionArn ?? "arn:aws:states:::execution:rp-ingest:x"}-re-${hex(4)}` };
  }

  private async unitOr404(unitId: string) {
    const docId = unitId.split(":")[0];
    this.docOr404(docId);
    await this.ensure(this.pkgOfDoc(docId));
    const pv = unitId.split(":")[1];
    if (!this.stageDone(docId, pv, "units")) throw new RpApiError(503, "not_ready", "units are not built yet");
    const v = this.world.units[unitId];
    if (!v) throw new RpApiError(404, "not_found", `unit ${unitId} not found`);
    return v;
  }

  async getUnit(unitId: string) {
    await latency();
    return await this.unitOr404(unitId);
  }

  async getLineage(unitId: string, fieldId?: string) {
    await latency();
    const v = await this.unitOr404(unitId);
    if (v.held) throw new RpApiError(404, "not_found", "held units stay in Silver: no lineage beyond the gate");
    const l = this.world.lineage[fieldId ? `${unitId}|${fieldId}` : unitId];
    if (!l) throw new RpApiError(404, "not_found", `no field ${fieldId} on ${unitId}`);
    return l;
  }

  async getRegion(unitId: string, fieldId?: string) {
    await latency();
    await this.ensure(this.pkgOfDoc(unitId.split(":")[0]));
    // a request the export made as this user through the real rp-get-region handler answers as it did (SC-7)
    const probe = this.world.regionChecks?.[`${unitId}|${fieldId ?? ""}`];
    if (probe && probe.status !== 200 && this.user.sub === this.world.users[probe.as]?.sub) {
      const err = (probe.body as { error?: { code?: string; message?: string } } | null)?.error;
      throw new RpApiError(probe.status, (err?.code ?? "forbidden") as ApiErrorCode, err?.message ?? "You don't have access to this region.");
    }
    try {
      this.docOr404(unitId.split(":")[0]);
    } catch (e) {
      if (e instanceof RpApiError && e.status === 403) throw new RpApiError(403, "forbidden", "You don't have access to this region.");
      throw e;
    }
    const v = await this.unitOr404(unitId);
    if (v.held) throw new RpApiError(404, "not_found", "held unit: no region");
    const r = this.world.regions[fieldId ? `${unitId}|${fieldId}` : unitId];
    if (!r) throw new RpApiError(404, "not_found", v.unit.bboxes.length ? `no field ${fieldId}` : "chunk has no page region");
    return { ...r, url: this.pageUrl(r.url, r.docId) };
  }

  private pageUrl(url: string | undefined, docId: string): string | undefined {
    const tpl = this.docs.get(docId)?.template;
    return url && tpl ? url.split(docId).join(tpl) : url;
  }

  /** An artifact's preview: inline in the world, or its own file for large ones (`previewFile`). */
  private async previewOf(key: string): Promise<string | undefined> {
    await this.ensure(this.pkgOfKey(key));
    const a = this.world.artifacts[key];
    if (!a?.previewFile) return a?.preview;
    const res = await fetch(`/mock/${a.previewFile}`);
    if (!res.ok) throw new RpApiError(500, "internal", `preview of ${key}: ${res.status} (run \`make mock-world\`)`);
    return res.text();
  }

  async getArtifact(key: string) {
    await latency();
    await this.ensure(this.pkgOfKey(key));
    const found = this.world.artifacts[key];
    if (!found) throw new RpApiError(404, "not_found", `no object ${key}`);
    const { previewFile, ...a } = found;
    if (previewFile) a.preview = await this.previewOf(key);
    const pkg = key.split("/")[a.tier === "bronze" ? 2 : 1];
    if (a.tier !== "config" && a.tier !== "runs" && pkg) this.pkgOr404(pkg);
    const docId = key.split("/")[a.tier === "bronze" ? 3 : 2];
    let url = a.url;
    if (a.tier === "bronze") {
      const d = [...this.docs.values()].find((x) => x.item.bronzeKey === key);
      url = d?.blobUrl;
    } else if (url && docId) url = this.pageUrl(url, docId);
    return { ...a, url, expiresIn: url ? 300 : undefined };
  }

  async listPipelines() {
    await latency();
    return this.world.pipelines;
  }

  async getPipeline(pv: string) {
    await latency();
    const p = this.world.pipelineDetails[pv];
    if (!p) throw new RpApiError(404, "not_found", `pipeline ${pv} not registered`);
    return p;
  }

  async diffPipelines(a: string, b: string) {
    await latency();
    if (a === this.world.diff.a && b === this.world.diff.b) return this.world.diff;
    throw new RpApiError(400, "bad_request", "mock mode has the 1.0 vs 2.3.1 diff only");
  }

  async listRuns(packageId: string) {
    await latency();
    this.pkgOr404(packageId);
    const items = this.runSummaries
      .filter((r) => r.packageId === packageId)
      .sort((x, y) => y.createdAt.localeCompare(x.createdAt));
    return { items };
  }

  async getRun(runId: string) {
    await latency();
    const r = this.runs.find((x) => x.runId === runId) ?? (await this.loggedRun(runId));
    if (!r) throw new RpApiError(404, "not_found", `run ${runId} not found`);
    this.pkgOr404(r.packageId);
    return r;
  }

  async getRunScores(packageId: string): Promise<Record<string, RunScore>> {
    await latency();
    this.pkgOr404(packageId);
    const ids = new Set(this.runSummaries.filter((r) => r.packageId === packageId).map((r) => r.runId));
    return Object.fromEntries(Object.entries(this.world.runScores ?? {}).filter(([id]) => ids.has(id)));
  }

  async getAskErrors(packageId: string): Promise<Record<string, number>> {
    await latency();
    this.pkgOr404(packageId);
    const rows = (this.world.localRuns?.summary ?? []).filter((s) => s.packageId === packageId && (s.errors ?? 0) > 0 && (s.generationProfile ?? "default") === "default" && (s.caller ?? "a") === "a");
    return Object.fromEntries(rows.map((s) => [`${s.qid}|${s.pipelineVersion}`, s.errors ?? 0]));
  }

  /** A logged run record (`/mock/runs/{runId}.json`, written by `make mock-world`), fetched once. */
  private async loggedRun(runId: string): Promise<RunRecord | undefined> {
    if (!this.world.runSummaries.some((s) => s.runId === runId)) return undefined;
    if (!this.logged.has(runId)) {
      this.logged.set(
        runId,
        fetch(`/mock/runs/${encodeURIComponent(runId)}.json`).then(async (res) => {
          if (!res.ok) throw new RpApiError(500, "internal", `run record ${runId}: ${res.status} (run \`make mock-world\`)`);
          return (await res.json()) as RunRecord;
        }),
      );
    }
    return this.logged.get(runId)!;
  }

  async listReview(packageId: string) {
    await latency();
    this.pkgOr404(packageId);
    await this.ensure(packageId);
    return { items: this.world.review.filter((r) => r.packageId === packageId).map((r) => this.decisions.get(`${packageId}|${r.reviewKey}`) ?? r) };
  }

  // ---------------------------------------------------------------------------------------- x-ray
  /** Serves the real-pipeline export from `make xray-fixtures` (mock-public/mock/xray), ACL-checked like the API. */
  async getXray(docId: string, page: number, size?: number): Promise<XrayPage> {
    await latency();
    this.docOr404(docId);
    const index = await loadXrayIndex([...this.packages.keys()]);
    const doc = index.documents.find((d) => d.docId === docId);
    if (!doc) throw new RpApiError(404, "not_found", `${docId} has no X-ray export (run make xray-fixtures)`);
    const file = doc.files[String(page)]?.[String(size ?? index.control.sizeTokens)];
    if (!file) throw new RpApiError(400, "bad_request", `no X-ray for ${docId} p${page} at ${size} tokens`);
    const res = await fetch(`${doc.base}${file}`);
    if (!res.ok) throw new RpApiError(res.status, "not_found", `x-ray fixture ${file}: ${res.status}`);
    const x = (await res.json()) as XrayPage;
    // fixture image paths are relative to the export folder
    const abs = (u?: string) => (u ? `${doc.base}${u}` : undefined);
    return { ...x, image: { ...x.image, url: abs(x.image.url)!, zoomUrl: abs(x.image.zoomUrl) } };
  }

  // ---------------------------------------------------------------------------------------- v2 inspection
  /** `world.builds` (the real `GET …/build` responses). The API answers 404 on the 1.0 lane: it has no build. */
  async getBuild(docId: string, pv: string): Promise<BuildReplay> {
    await latency();
    this.docOr404(docId);
    await this.ensure(this.pkgOfDoc(docId));
    const b = this.world.builds?.[docId]?.[pv];
    if (!b) throw new RpApiError(404, "not_found", `no build report for ${docId} on ${pv}`);
    return structuredClone(b);
  }

  /** The logged run's package is checked first, as the API does; then the exported response for that call. */
  private async loggedCall<T>(table: () => Record<string, T> | undefined, runId: string, callId: string, what: string): Promise<T> {
    await this.getRun(runId);
    await this.ensure(this.pkgOfRun(runId));
    const hit = table()?.[`${runId}|${callId}`];
    if (!hit) throw new RpApiError(404, "not_found", `${what} for ${runId} · ${callId} is not in the mock export (representative runs only)`);
    return structuredClone(hit);
  }

  async inspectFilters(runId: string, callId: string): Promise<FilterInspection> {
    return this.loggedCall(() => this.world.filterInspections, runId, callId, "filter inspection");
  }

  async getCallPath(runId: string, callId: string): Promise<GraphNeighbourhood> {
    return this.loggedCall(() => this.world.graphPaths, runId, callId, "graph path");
  }

  async listShowcases(): Promise<ShowcaseCatalog> {
    await latency();
    return structuredClone(this.world.showcaseCatalog ?? { items: [] });
  }

  /** `world.showcasePayloads`, keyed by the composer's params in order (ids compared case-insensitively). */
  async getShowcasePayload<T>(showcaseId: string, params: Record<string, string>): Promise<ShowcasePayload<T>> {
    await latency();
    const composer = this.world.showcaseCatalog?.items.find((i) => i.showcaseId === showcaseId);
    if (!composer) throw new RpApiError(404, "not_found", `no server composer for ${showcaseId}`);
    if (params.packageId) this.pkgOr404(params.packageId);
    await this.ensure(params.packageId);
    // the export keys a question by Python's normalise_question, the other params as given
    const part = (k: string) => (k === "question" ? pyNormaliseQuestion(params[k] ?? "") : (params[k] ?? ""));
    const key = [showcaseId, ...composer.params.map(part)].join("|").toLowerCase();
    const hit = Object.entries(this.world.showcasePayloads ?? {}).find(([k]) => k.toLowerCase() === key)?.[1];
    if (!hit) throw new RpApiError(404, "not_found", `no ${showcaseId} payload for ${JSON.stringify(params)}`);
    return structuredClone(hit) as ShowcasePayload<T>;
  }

  // ---------------------------------------------------------------------------------------- governance
  /** `GET /governance/definitions/{name}`, exported by `make mock-world` (the build's governance record). */
  async getGovernedDefinition(name: string): Promise<GovernedDefinitionView> {
    await latency();
    const g = this.world.governance;
    if (!g || (g.definition.name !== name && g.definition.iri !== name)) throw new RpApiError(404, "not_found", `no governed definition ${name}`);
    return structuredClone(g);
  }

  // ---------------------------------------------------------------------------------------- v2 routes
  /** `GET /access/decisions?runId=` (exported per logged run; the run's package ACL first). */
  async getAccessDecisions(runId: string): Promise<AccessDecisionPage> {
    await this.getRun(runId);
    await this.ensure(this.pkgOfRun(runId));
    const hit = this.world.accessDecisions?.[runId];
    if (!hit) throw new RpApiError(404, "not_found", `no access decisions exported for ${runId}`);
    return structuredClone(hit);
  }

  /** `POST /review/{packageId}/items/{reviewKey}/decision`, kept in this session (the API writes the table). */
  async decideReview(packageId: string, reviewKey: string, body: ReviewDecisionRequest): Promise<ReviewItem> {
    await latency();
    this.pkgOr404(packageId);
    await this.ensure(packageId);
    const k = `${packageId}|${reviewKey}`;
    const item = this.decisions.get(k) ?? this.world.review.find((r) => r.packageId === packageId && r.reviewKey === reviewKey);
    if (!item) throw new RpApiError(404, "not_found", `No review item ${reviewKey} in ${packageId}.`);
    this.docOr404(item.docId);
    // the route's optimistic check: the decision applies to the state the reviewer saw
    if ((item.decision?.at ?? null) !== (body.expectedDecisionAt ?? null)) throw new RpApiError(409, "conflict", "Someone decided this item after you opened it; reload it.");
    if (body.action === "CORRECT" && !body.value?.trim()) throw new RpApiError(400, "bad_request", "CORRECT needs a value");
    if (body.action !== "CORRECT" && body.value !== undefined) throw new RpApiError(400, "bad_request", "only CORRECT takes a value");
    const decision = { action: body.action, by: this.user.sub.slice(0, 6), at: iso(Date.now()), value: body.value?.trim(), note: body.note };
    const state = ({ ACCEPT: "ACCEPTED", CORRECT: "CORRECTED", REJECT: "REJECTED" } as const)[body.action];
    const next: ReviewItem = { ...item, state, decision, history: [...(item.history ?? []), ...(item.decision ? [item.decision] : [])] };
    this.decisions.set(k, next);
    return structuredClone(next);
  }

  /** `GET /eval/runs` (the publication flag picks the exported view). */
  async listEvalRuns(): Promise<EvalRunList> {
    await latency();
    const e = this.world.evalRuns;
    if (!e) return { items: [], published: PUBLIC_METRICS };
    const view = metricsView(e.list) ?? e.list.off;
    // a result spans packages: the route lists it only when the caller can read every one
    return { ...view, items: view.items.filter((i) => i.packageIds.every((p) => this.readable(p))) };
  }

  async getEvalRun(evalRunId: string): Promise<EvalRunView> {
    await latency();
    const hit = metricsView(this.world.evalRuns?.runs[evalRunId]);
    if (!hit) throw new RpApiError(404, "not_found", `Eval run ${evalRunId} not found.`);
    if (!hit.packageIds.every((p) => this.readable(p))) throw new RpApiError(403, "forbidden", "This result covers a package you don't have access to.");
    return structuredClone(hit);
  }

  /** `GET /cost?packageId=&groupBy=` (exported unpublished for docType; published for every groupBy only on request). */
  async getCost(packageId: string, groupBy = "docType"): Promise<CostView> {
    await latency();
    this.pkgOr404(packageId);
    await this.ensure(packageId);
    const views = this.world.cost?.[packageId];
    const hit = metricsView(views)?.[groupBy];
    if (!hit) throw new RpApiError(404, "not_found", `no cost view for ${packageId} by ${groupBy} in the mock export`);
    return structuredClone(hit);
  }

  private knowledgeExport(): Promise<KnowledgeExport> {
    if (!this.world.knowledge) return Promise.reject(new RpApiError(404, "not_found", "no knowledge.json in the mock export"));
    this.knowledge ??= loadKnowledge(this.world.knowledge);
    return this.knowledge;
  }

  /** `GET /ontology/classes?module=` */
  async listOntologyClasses(module?: string): Promise<OntologyTermList> {
    await latency();
    const { classes } = await this.knowledgeExport();
    if (!module) return structuredClone(classes);
    if (!classes.modules.some((m) => m.module === module)) throw new RpApiError(400, "bad_request", `unknown module ${module}`);
    return { ...structuredClone(classes), module, items: classes.items.filter((t) => t.module === module) };
  }

  /** `GET /ontology/terms/{iri}` (fin: and doc: terms carry `usedBy` as the export's User A saw it). */
  async getOntologyTerm(iri: string): Promise<OntologyTerm> {
    await latency();
    const k = await this.knowledgeExport();
    const t = k.terms[iri] ?? k.classes.items.find((x) => x.iri === iri || x.curie === iri);
    if (!t) throw new RpApiError(404, "not_found", `No term ${iri}.`);
    const usedBy = Object.fromEntries(Object.entries(t.usedBy ?? {}).filter(([p]) => this.readable(p)));
    return { ...structuredClone(t), usedBy };
  }

  /** `GET /graph/neighbourhood?iri=&depth=&packageId=&pv=` (2.3.1; 1.0 binds no ontology). */
  // ---- COA environment: a simulated state machine (mocks/coaEnv.ts), labelled "Simulated · mock mode" ----
  /** User A is the simulated environment admin (rp-admin); User B reads only. */
  private get coaAdmin(): boolean {
    return this.user.username === "user-a" || Boolean(this.user.email?.startsWith("user-a@"));
  }

  async getCoaEnvironment(): Promise<CoaEnvironmentView> {
    await latency();
    return coaSimulation().view(this.coaAdmin);
  }

  async coaUp(body: CoaUpRequest): Promise<CoaEnvironmentView> {
    await latency();
    return coaSimulation().up(this.coaAdmin, body, this.user.sub.slice(0, 6));
  }

  async coaDown(body: CoaDownRequest): Promise<CoaEnvironmentView> {
    await latency();
    return coaSimulation().down(this.coaAdmin, body, this.user.sub.slice(0, 6));
  }

  async coaSettings(body: CoaSettingsRequest): Promise<CoaEnvironmentView> {
    await latency();
    return coaSimulation().settings(this.coaAdmin, body);
  }

  async coaKeepAlive(body: CoaKeepAliveRequest): Promise<CoaEnvironmentView> {
    await latency();
    return coaSimulation().keepAlive(this.coaAdmin, body.expectedVersion);
  }

  coaSimulate(next: CoaSimulatedOutcome): void {
    coaSimulation().simulate(next);
  }

  async getGraphNeighbourhood(iri: string, packageId: string, depth: 1 | 2 = 1, pv = "2.3.1"): Promise<GraphNeighbourhood> {
    await latency();
    this.pkgOr404(packageId);
    if (pv !== "2.3.1") return { focus: iri, depth, nodes: [], edges: [], truncated: false, template: "neighbourhood_v1", source: "neptune", sparql: `# lane ${pv} binds no ontology: there is no graph to query` };
    await this.ensure(packageId);
    const hit = this.world.graphNeighbourhoods?.[packageId]?.[`${iri}|${depth}`];
    if (!hit) throw new RpApiError(404, "not_found", `no neighbourhood of ${iri} (depth ${depth}) in the mock export`);
    return structuredClone(hit);
  }

  private readable(packageId: string): boolean {
    const p = this.packages.get(packageId);
    return Boolean(p && p.pkg.allowedPrincipals.some((x) => this.principals().includes(x)));
  }

  // ---------------------------------------------------------------------------------------- ask
  async ask(body: AskRequest, { onEvent, signal }: AskHandlers): Promise<void> {
    await latency();
    const p = this.pkgOr404(body.packageId);
    await this.ensure(body.packageId);
    const st = this.status(p);
    if (!st.lanes[body.pipelineVersion]?.ready)
      throw new RpApiError(503, "not_ready", `lane ${body.pipelineVersion} is not indexed yet`);
    const headline =
      body.packageId === LENDING &&
      normaliseQuestion(body.question) === normaliseQuestion(this.world.headlineQuestion) &&
      !body.toolsDisabled?.length;
    const logged = !body.toolsDisabled?.length && (body.generationProfile ?? "default") === "default" ? this.replayLogged(body) : undefined;
    // a golden question replays its logged run under that run's own id (it is already in the run store)
    const events = headline ? this.replayHeadline(body.pipelineVersion) : (logged ?? this.genericAnswer(body, `r-${hex(12)}`));
    const replayed = Boolean(headline || logged);
    const started = Date.now();
    const toolStarted = new Map<string, number>();
    const sent: AskEvent[] = [];
    let lastT = 0;
    for (const raw of events) {
      if (signal?.aborted) return;
      const t = "t" in raw && typeof raw.t === "number" ? raw.t : lastT + 0.06;
      await sleep(Math.max(0, (t - lastT) * 1000 * 0.8));
      lastT = t;
      let ev = raw;
      // the stand-in's times are this browser's wall clock, never typed constants
      if (!replayed && ev.type === "tool_start") toolStarted.set(ev.callId, Date.now());
      if (!replayed && ev.type === "tool_end") ev = { ...ev, ms: Date.now() - (toolStarted.get(ev.callId) ?? started), t: (Date.now() - started) / 1000 };
      if (ev.type === "run" && !replayed) {
        ev = { ...ev, latencyMs: Date.now() - started, startedAt: iso(started) };
        this.recordRun(body, ev, [...sent, ev]);
      }
      sent.push(ev);
      onEvent(ev);
    }
  }

  /** The representative logged run of the headline question on this lane, as recorded (same run id). */
  private replayHeadline(pv: string): AskEvent[] {
    return structuredClone(this.world.asks[pv] ?? []);
  }

  /** The representative logged run of this golden question on this lane (`askReplays`), as recorded. */
  private replayLogged(body: AskRequest): AskEvent[] | undefined {
    const fixture = this.world.askReplays?.[`${body.packageId}|${pyNormaliseQuestion(body.question)}`]?.lanes[body.pipelineVersion];
    return fixture?.length ? structuredClone(fixture) : undefined;
  }

  /** The keyword stand-in's run record: kept in this session only, marked by its model id (never a logged run). */
  private recordRun(body: AskRequest, footer: Extract<AskEvent, { type: "run" }>, events: AskEvent[]): void {
    const answer = events.find((e): e is Extract<AskEvent, { type: "answer" }> => e.type === "answer");
    const check = events.find((e): e is Extract<AskEvent, { type: "check" }> => e.type === "check");
    const ends = events.filter((e): e is Extract<AskEvent, { type: "tool_end" }> => e.type === "tool_end");
    const starts = events.filter((e): e is Extract<AskEvent, { type: "tool_start" }> => e.type === "tool_start");
    const retrieved = ends.flatMap((e) => e.retrieved);
    const rec: RunRecord = {
      runId: footer.runId,
      createdAt: footer.startedAt,
      packageId: body.packageId,
      pipelineVersion: body.pipelineVersion,
      question: body.question,
      callerSub: this.user.sub,
      groups: this.user.groups,
      modelId: footer.modelId,
      promptSha: footer.promptSha,
      configSha: footer.configSha,
      blockShas: footer.blockShas,
      corpusHash: footer.corpusHash,
      memoryRecordsInjected: 0,
      toolsEnabled: events.find((e): e is Extract<AskEvent, { type: "run_start" }> => e.type === "run_start")?.tools ?? [],
      toolCalls: starts.map((s) => {
        const e = ends.find((x) => x.callId === s.callId);
        return {
          callId: s.callId,
          tool: s.tool,
          input: s.input,
          injected: { callerSub: `${this.user.sub.slice(0, 6)}…`, groups: this.user.groups, pipelineVersion: body.pipelineVersion, packageId: body.packageId, runId: footer.runId },
          startedAt: iso(ms(footer.startedAt) + s.t * 1000),
          ms: e?.ms ?? 0,
          status: e?.status ?? "error",
          output: { summary: e?.summary, units: e?.units, rows: e?.rows },
        };
      }),
      retrieved,
      retrievedText: retrieved.map((r) => r.text),
      answer: answer ? omitType(answer) : undefined,
      check: check ? { qid: check.qid, expected: check.expected, actual: check.actual, match: check.match } : undefined,
      usage: footer.usage,
      latencyMs: footer.latencyMs,
      status: "ok",
      runtimeSessionId: footer.runtimeSessionId,
      s3Key: `runs/${footer.runId}.json`,
    };
    this.runs.push(rec);
    this.runSummaries.push(summaryOf(rec));
  }

  /** Non-headline questions: a keyword retriever over the package's projected units (mock only, no model). */
  private genericAnswer(body: AskRequest, runId: string): AskEvent[] {
    const pv = body.pipelineVersion;
    const p = this.packages.get(body.packageId)!;
    // no lane config, model, prompt or usage: nothing here came from Bedrock, so it never counts as a lane run
    const units: Unit[] = [];
    for (const id of p.docIds) {
      if (!this.stageDone(id, pv, "index")) continue;
      for (const li of this.world.unitLists[id]?.[pv]?.items ?? []) {
        const v = this.world.units[li.unitId];
        if (v && !v.held) units.push(v.unit);
      }
    }
    const terms = normaliseQuestion(body.question).split(" ").filter((t) => t.length > 2);
    const raw = units.map((u) => {
      const text = `${u.breadcrumb.join(" ")} ${u.label} ${u.text}`.toLowerCase();
      const bm25 = terms.reduce((s, t) => s + (text.split(t).length - 1), 0);
      const dense = terms.filter((t) => text.includes(t)).length / Math.max(1, terms.length);
      return { u, bm25, dense };
    });
    const norm = (xs: number[]) => {
      const lo = Math.min(...xs);
      const hi = Math.max(...xs);
      return xs.map((x) => (hi === lo ? (hi > 0 ? 1 : 0) : (x - lo) / (hi - lo)));
    };
    const dn = norm(raw.map((r) => r.dense));
    const bn = norm(raw.map((r) => r.bm25));
    const scored = raw
      .map((r, i) => ({ ...r, dn: dn[i], bn: bn[i], fused: 0.7 * dn[i] + 0.3 * bn[i] }))
      .sort((a, b) => b.fused - a.fused || a.u.unitId.localeCompare(b.u.unitId))
      .slice(0, 5);
    const retrieved: RetrievedUnit[] = scored.map((s, i) => ({
      rank: i + 1,
      unitId: s.u.unitId,
      docId: s.u.docId,
      fileName: s.u.fileName,
      unitType: s.u.unitType,
      label: s.u.label,
      chunkLabel: s.u.chunkLabel,
      docType: s.u.docType,
      breadcrumb: s.u.breadcrumb.join(" › "),
      text: s.u.text,
      pages: s.u.pages,
      pageWidth: s.u.pageWidth,
      pageHeight: s.u.pageHeight,
      bboxes: s.u.bboxes,
      fields: s.u.fields.map((f) => ({ fieldId: f.fieldId, label: f.label, value: f.value, valueNumber: f.valueNumber, concept: f.concept, period: f.period, page: f.bbox?.page, bbox: f.bbox })),
      period: s.u.period,
      piiFlag: s.u.piiFlag,
      resolvable: s.u.bboxes.length > 0,
      scores: { denseNorm: round2(s.dn), bm25Norm: round2(s.bn), fused: round2(s.fused) },
    }));
    const top = scored[0]?.u;
    const field = top?.fields[0];
    const answerText = top
      ? field
        ? `Mock agent (no model in mock mode). The closest evidence is ${top.label}: ${field.label} = ${field.value}.`
        : `Mock agent (no model in mock mode). The closest evidence is ${top.label}.`
      : "Mock agent (no model in mock mode). I cannot answer this from the package: no unit matched.";
    const events: AskEvent[] = [
      { type: "run_start", runId, packageId: body.packageId, pipelineVersion: pv, question: body.question, modelId: MOCK_MODEL, configSha: "", promptSha: "", tools: ["search_units"], t: 0 },
      { type: "tool_start", callId: "c1", tool: "search_units", input: { query: body.question, k: 5 }, t: 0.3 },
      // ms and t are replaced with the wall clock as the event is sent
      { type: "tool_end", callId: "c1", tool: "search_units", ms: 0, status: "ok", summary: `${retrieved.length} units · keyword match over the projected units`, units: retrieved.map((r) => r.unitId), scores: retrieved.map((r) => ({ unitId: r.unitId, ...r.scores })), retrieved, rows: [], t: 0.5 },
      ...answerText.match(/.{1,48}(\s|$)/g)!.map((delta) => ({ type: "text" as const, delta })),
      {
        type: "answer",
        answer: answerText,
        claims: top
          ? [{
              text: field?.value ?? top.label,
              value: field?.valueNumber,
              unitId: top.unitId,
              fieldId: field?.fieldId,
              docId: top.docId,
              fileName: top.fileName,
              label: top.chunkLabel ?? `${top.label}${field ? ` · ${field.label}` : ""}`,
              page: field?.bbox?.page ?? top.bboxes[0]?.page,
              bbox: field?.bbox ?? top.bboxes[0],
              unitBbox: top.bboxes[0],
              resolvable: top.bboxes.length > 0,
              chunkLabel: top.chunkLabel,
              reason: top.bboxes.length ? undefined : "chunk has no page region",
            }]
          : [],
        caveats: ["Mock mode: answer composed by a keyword retriever, not a model."],
        citationRetries: 0,
        uncitedNumbers: [],
      },
    ];
    events.push({
      type: "run",
      runId,
      packageId: body.packageId,
      pipelineVersion: pv,
      configSha: "",
      blockShas: {},
      promptSha: "",
      modelId: MOCK_MODEL,
      corpusHash: "",
      memoryRecordsInjected: 0,
      latencyMs: 0,
      usage: {},
      startedAt: iso(Date.now()),
    });
    return events;
  }
}

function omitType<T extends { type: string }>(e: T): Omit<T, "type"> {
  const copy: Partial<T> = { ...e };
  delete copy.type;
  return copy as Omit<T, "type">;
}

function round2(x: number): number {
  return Math.round(x * 100) / 100;
}

function summaryOf(r: RunRecord): RunSummary {
  return {
    runId: r.runId,
    createdAt: r.createdAt,
    packageId: r.packageId,
    pipelineVersion: r.pipelineVersion,
    question: r.question,
    callerSub: r.callerSub,
    modelId: r.modelId,
    promptSha: r.promptSha,
    configSha: r.configSha,
    corpusHash: r.corpusHash,
    total: r.answer?.total,
    checkMatch: r.check?.match,
    qid: r.check?.qid,
    claims: r.answer?.claims.length ?? 0,
    resolvableClaims: r.answer?.claims.filter((c) => c.resolvable).length ?? 0,
    toolSequence: r.toolCalls.map((c) => c.tool),
    latencyMs: r.latencyMs,
    status: r.status,
    memoryRecordsInjected: r.memoryRecordsInjected,
    s3Key: r.s3Key,
  };
}

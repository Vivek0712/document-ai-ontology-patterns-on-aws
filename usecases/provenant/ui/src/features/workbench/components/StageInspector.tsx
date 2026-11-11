/**
 * Stage inspector (first-class): everything one stage of one (document, pipeline version) lane did:
 * state, attempts, heartbeats, timings, errors, engine, AWS services, inputs and outputs (S3 artifacts
 * with previews), metrics, config slice + sha, versions, the page with what the stage produced, events.
 */
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import type { DocumentDetail, StageDetail, StageId, StageRecord } from "@contracts/api";
import { useApi } from "../../../api/context";
import { useAsync } from "../../../hooks/useAsync";
import { dateTime, duration, shortExecution, shortSha, time } from "../../../lib/format";
import { roleColor } from "../../../lib/geometry";
import { PAGE_STAGES, STAGE_SHORT, TERMINAL } from "../../../lib/stages";
import { ArtifactList } from "./Artifacts";
import { type Overlay, PageView } from "./PageView";
import { EngineTag, ErrorBox, Json, KV, Loading, RunningTimer, ServiceTags, StageErrorBox, StatePill, StateTick } from "./ui";

interface Props {
  docId: string;
  pv: string;
  stageId: StageId;
  onSelectStage: (stageId: StageId) => void;
  onSelectPv?: (pv: string) => void;
}

const METRIC_WARN = new Set(["held", "heldRegions", "reviewItems", "emptyPages", "invariantViolations", "attempts"]);

export function StageInspector({ docId, pv, stageId, onSelectStage, onSelectPv }: Props) {
  const api = useApi();
  const [live, setLive] = useState(true);
  const stage = useAsync(() => api.getStage(docId, pv, stageId), [docId, pv, stageId], live ? 1000 : false);
  const doc = useAsync(() => api.getDocument(docId), [docId], live ? 2000 : false);
  const rec = stage.data?.record;
  useEffect(() => {
    // stop polling once the stage is terminal and the lane is done
    const laneDone = doc.data && TERMINAL.includes((doc.data.lanes[pv]?.lane.state ?? "PENDING") as never);
    setLive(!(rec && TERMINAL.includes(rec.state) && laneDone));
  }, [rec, doc.data, pv]);

  if (stage.error) return <ErrorBox error={stage.error} />;
  if (!stage.data || !rec) return <Loading what="loading stage record" />;
  const d = stage.data;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <Header detail={d} doc={doc.data} onSelectStage={onSelectStage} onSelectPv={onSelectPv} />
      <LaneStrip doc={doc.data} pv={pv} current={stageId} onSelectStage={onSelectStage} />
      {rec.state === "SKIPPED" && (
        <div className="card">
          <b style={{ color: "var(--text)" }}>Skipped</b> <span className="muted">— {rec.skipReason ?? d.definition.skipReason}</span>
          <div className="fine">Register wrote this record as SKIPPED from the pipeline config, so the reason is on record.</div>
        </div>
      )}
      {rec.error && <StageErrorBox error={rec.error} />}
      <Retries detail={d} />
      <PreviousRuns detail={d} />
      <div className="grid2">
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Execution</h2>
          <KV
            rows={[
              ["state", <StatePill state={rec.state} since={rec.startedAt} />],
              ["attempt", rec.attempt],
              ["queued", time(rec.queuedAt)],
              ["started", time(rec.startedAt)],
              ["heartbeats", `${rec.heartbeatCount}${rec.lastHeartbeatAt ? ` · last ${time(rec.lastHeartbeatAt)}` : ""}`],
              ["ended", time(rec.endedAt)],
              ["duration", rec.state === "RUNNING" ? <RunningTimer since={rec.startedAt} /> : duration(rec.durationMs)],
              ["progress", rec.progress ? `${rec.progress.done}${rec.progress.total !== undefined ? ` / ${rec.progress.total}` : ""} ${rec.progress.unit}` : "—"],
              ["message", rec.message],
              ["Lambda", <span className="mono">{rec.functionName ?? "—"}</span>],
              ["Step Functions state", <span className="mono">{rec.sfnState ?? d.definition.sfnStates.join(", ")}</span>],
              ["execution", <span className="mono" title={rec.executionArn}>{shortExecution(rec.executionArn)}</span>],
              ["updated", dateTime(rec.updatedAt)],
            ]}
          />
        </section>
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Config and versions</h2>
          <Versions rec={rec} />
          {rec.config && (
            <div style={{ marginTop: 12 }}>
              <div className="fine">config slice · {rec.config.paths.join(", ")} · sha <span className="mono">{shortSha(rec.config.sha, 12)}</span></div>
              <Json value={rec.config.values} maxHeight={240} />
            </div>
          )}
        </section>
      </div>
      {Object.keys(rec.metrics).length > 0 && (
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Metrics</h2>
          <div className="metric-tiles">
            {Object.entries(rec.metrics).map(([k, v]) => (
              <div key={k} className={`tile ${METRIC_WARN.has(k) && v > 0 ? "warn" : ""}`}>
                <b>{Number.isInteger(v) ? v.toLocaleString("en-US") : v.toFixed(2)}</b>
                <span>{k}</span>
              </div>
            ))}
          </div>
        </section>
      )}
      <div className="grid2">
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Inputs</h2>
          <ArtifactList items={rec.inputs} empty="no input artifacts recorded" />
          {rec.input && <div style={{ marginTop: 10 }}><Json value={rec.input} maxHeight={200} /></div>}
        </section>
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Outputs</h2>
          <ArtifactList items={rec.artifacts} empty={rec.state === "SUCCEEDED" ? "this stage writes no artifact" : "no artifacts yet"} />
          {rec.output && <div style={{ marginTop: 10 }}><Json value={rec.output} maxHeight={260} /></div>}
        </section>
      </div>
      {PAGE_STAGES.includes(rec.stageId) && <StagePage rec={rec} doc={doc.data} />}
      <section className="card">
        <h2 style={{ marginBottom: 10 }}>Definition</h2>
        <p className="muted" style={{ marginTop: 0 }}>{d.definition.description}</p>
        <KV
          rows={[
            ["engines", d.definition.engines.join(", ")],
            ["AWS services", <ServiceTags services={d.definition.services} />],
            ["functions", d.definition.functions.join(", ")],
            ["consumes", d.definition.consumes.join(", ") || "—"],
            ["produces", d.definition.produces.join(", ") || "—"],
            ["config paths", d.definition.configPaths.join(", ")],
            ["enabled in " + pv, d.definition.enabled ? "yes" : `no — ${d.definition.skipReason}`],
          ]}
        />
      </section>
      <section className="card">
        <h2 style={{ marginBottom: 10 }}>Events <span className="fine">(rp-doc-status, oldest first)</span></h2>
        {d.events.length === 0 ? (
          <div className="muted">no events yet</div>
        ) : (
          <div className="timeline">
            {d.events.map((e, i) => (
              <div className="ev" key={i}>
                <span className="mono">{time(e.at)}</span>
                <span className={e.event}>{e.event}</span>
                <span className="muted">attempt {e.attempt}</span>
                <span>
                  {e.durationMs !== undefined && `${duration(e.durationMs)} · `}
                  {e.progress && `${e.progress.done}${e.progress.total !== undefined ? `/${e.progress.total}` : ""} ${e.progress.unit} · `}
                  {e.error ? <b style={{ color: "var(--loss)" }}>{e.error.type}: {e.error.message}</b> : e.message}
                </span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Header({ detail, doc, onSelectStage, onSelectPv }: { detail: StageDetail; doc?: DocumentDetail; onSelectStage: (s: StageId) => void; onSelectPv?: (pv: string) => void }) {
  const r = detail.record;
  return (
    <div className="stack" style={{ gap: 8 }}>
      <div className="crumbs">
        {doc && <Link to={`/workbench/packages/${encodeURIComponent(doc.document.packageId)}`}>{doc.document.packageId}</Link>}
        <span>›</span>
        {doc && <Link to={`/workbench/docs/${encodeURIComponent(r.docId)}?pv=${encodeURIComponent(r.pipelineVersion)}&stage=${r.stageId}`}>{doc.document.fileName}</Link>}
        <span>›</span>
        <span>{r.pipelineVersion}</span>
      </div>
      <div className="row">
        <StateTick state={r.state} />
        <h1>{r.order}. {r.title}</h1>
        <StatePill state={r.state} since={r.startedAt} />
        <EngineTag engine={r.engine} />
        {r.headline && <span className="big">{r.headline}</span>}
      </div>
      <div className="row">
        <ServiceTags services={r.services.length ? r.services : detail.definition.services} />
        <span className="spacer" />
        {onSelectPv && doc && Object.keys(doc.lanes).length > 1 && (
          <div className="seg">
            {Object.keys(doc.lanes).map((pv) => (
              <button key={pv} className={pv === r.pipelineVersion ? "on" : ""} onClick={() => onSelectPv(pv)}>{pv}</button>
            ))}
          </div>
        )}
        <button className="btn small ghost" disabled={!detail.previousStage} onClick={() => detail.previousStage && onSelectStage(detail.previousStage)}>← {detail.previousStage ? STAGE_SHORT[detail.previousStage] : ""}</button>
        <button className="btn small ghost" disabled={!detail.nextStage} onClick={() => detail.nextStage && onSelectStage(detail.nextStage)}>{detail.nextStage ? STAGE_SHORT[detail.nextStage] : ""} →</button>
      </div>
    </div>
  );
}

function LaneStrip({ doc, pv, current, onSelectStage }: { doc?: DocumentDetail; pv: string; current: StageId; onSelectStage: (s: StageId) => void }) {
  const lane = doc?.lanes[pv];
  if (!lane) return null;
  return (
    <div className="lane-steps" aria-label="lane stages">
      {lane.stages.map((s) => (
        <button key={s.stageId} className={s.stageId === current ? "on" : ""} onClick={() => onSelectStage(s.stageId)} title={s.skipReason ?? s.headline ?? s.state}>
          <StateTick state={s.state} />
          {STAGE_SHORT[s.stageId]}
        </button>
      ))}
      <span className="fine" style={{ alignSelf: "center", marginLeft: 8 }}>
        lane {lane.lane.state.toLowerCase()} · {duration(lane.lane.durationMs)} · config {shortSha(lane.lane.configSha, 8)}
      </span>
    </div>
  );
}

function Retries({ detail }: { detail: StageDetail }) {
  const failures = detail.events.filter((e) => e.event === "FAILED");
  if (!failures.length || (detail.record.state === "FAILED" && failures.length === 1)) return null;
  return (
    <div className="card">
      <h3>Retry history</h3>
      {failures.map((f, i) => (
        <div key={i} className="row" style={{ marginTop: 6 }}>
          <span className="pill fail">× attempt {f.attempt}</span>
          <span className="mono">{time(f.at)}</span>
          <b style={{ color: "var(--text)" }}>{f.error?.type}</b>
          <span className="muted">{f.error?.message}</span>
          {f.error && <span className="pill">{f.error.retryable ? "retryable → Step Functions Retry" : "not retryable"}</span>}
        </div>
      ))}
      {detail.record.state === "SUCCEEDED" && <div className="fine" style={{ marginTop: 6 }}>succeeded on attempt {detail.record.attempt}</div>}
    </div>
  );
}

/** This stage in earlier executions (reprocess, re-upload): archived by register before the new run. */
function PreviousRuns({ detail }: { detail: StageDetail }) {
  const runs = detail.history ?? [];
  if (!runs.length) return null;
  return (
    <div className="card">
      <h3>Previous runs <span className="fine">(earlier executions of this stage, newest first)</span></h3>
      {runs.map((h, i) => (
        <div key={`${h.executionArn ?? i}`} className="row" style={{ marginTop: 6 }}>
          <StateTick state={h.state} />
          <span className="mono" title={h.executionArn}>{shortExecution(h.executionArn)}</span>
          <span className="mono">{h.startedAt ? time(h.startedAt) : "—"}</span>
          <span className="muted">{duration(h.durationMs)} · attempt {h.attempt}</span>
          {h.versions?.configSha && <span className="mono" title={h.versions.configSha}>config {shortSha(h.versions.configSha, 8)}</span>}
          {h.error ? <b style={{ color: "var(--loss)" }}>{h.error.type}: {h.error.message}</b> : <span>{h.headline ?? h.skipReason ?? ""}</span>}
        </div>
      ))}
    </div>
  );
}

function Versions({ rec }: { rec: StageRecord }) {
  const v = rec.versions;
  if (!v) return <div className="muted">no versions recorded</div>;
  return (
    <KV
      rows={[
        ["pipeline", v.pipelineVersion],
        ["config sha", <span className="mono" title={v.configSha}>{shortSha(v.configSha, 12)}</span>],
        ["config S3 version", <span className="mono">{v.configVersionId ? shortSha(v.configVersionId, 12) : "—"}</span>],
        ["ontology", v.ontologyVersion ?? "none"],
        ["engine version", v.engineVersion],
        ["model", <span className="mono">{v.modelId ?? "—"}</span>],
        ["prompt sha", <span className="mono" title={v.promptSha}>{shortSha(v.promptSha, 12)}</span>],
        ["code sha", <span className="mono">{v.codeSha ?? "—"}</span>],
      ]}
    />
  );
}

// ---------------------------------------------------------------------------------------- page overlay

function StagePage({ rec, doc }: { rec: StageRecord; doc?: DocumentDetail }) {
  const api = useApi();
  const pages = doc?.document.pageCount ?? 1;
  const [page, setPage] = useState(1);
  const overlay = useAsync(
    () => api.getOverlay(rec.docId, rec.pipelineVersion, rec.stageId, page),
    [rec.docId, rec.pipelineVersion, rec.stageId, rec.state, page],
  );

  const { overlays, legend } = useMemo(() => {
    const ov: Overlay[] = [];
    const lg = new Map<string, string>();
    for (const b of overlay.data?.boxes ?? []) {
      switch (b.kind) {
        case "block": {
          const c = roleColor(b.role ?? "other");
          lg.set(`${b.label} → ${b.role}`, c);
          ov.push({ bbox: b.bbox, kind: "block", color: c, title: `${b.ref ?? ""} · ${b.label} → ${b.role}` });
          break;
        }
        case "member": {
          const c = roleColor(b.role ?? "other");
          lg.set(b.role ?? "member", c);
          ov.push({
            bbox: b.bbox,
            kind: "member",
            color: c,
            dashed: b.phase === "B" || b.phase === "C",
            title: `${b.unitId} · ${b.ref} · ${b.role} · phase ${b.phase ?? "?"}${b.allocScore != null ? ` · alloc ${b.allocScore}` : ""}`,
          });
          break;
        }
        case "unit":
          lg.set("evidence unit", "var(--l2)");
          ov.push({ bbox: b.bbox, kind: "unit", label: `${b.ref ?? ""} ${b.label ?? ""}`, title: `${b.unitId} · ${b.piiFlag ?? "none"}` });
          break;
        case "field":
          lg.set("field (cited region)", "var(--boundary)");
          ov.push({ bbox: b.bbox, kind: "region", title: `${b.unitId} · ${b.ref} · ${b.label ?? ""}` });
          break;
        case "held":
          lg.set("held PII (black)", "var(--boundary)");
          ov.push({ bbox: b.bbox, kind: "held" });
          break;
      }
    }
    return { overlays: ov, legend: [...lg.entries()] };
  }, [overlay.data]);

  const naive = rec.pipelineVersion === "1.0";
  return (
    <section className="card">
      <div className="row" style={{ marginBottom: 10 }}>
        <h2>On the page</h2>
        <span className="fine">
          {rec.stageId === "extract" ? "engine blocks mapped to canonical roles" : rec.stageId === "units" ? "units (purple) and members by role; dashed = joined in phase B/C" : "unit outlines; held regions filled black"}
        </span>
        <span className="spacer" />
        {pages > 1 && (
          <div className="seg">
            {Array.from({ length: pages }, (_, i) => i + 1).map((p) => (
              <button key={p} className={p === page ? "on" : ""} onClick={() => setPage(p)}>p.{p}</button>
            ))}
          </div>
        )}
      </div>
      <div className="row" style={{ alignItems: "flex-start", gap: 20 }}>
        <PageView
          url={overlay.data?.pageImageUrl}
          pageWidth={overlay.data?.pageWidth}
          pageHeight={overlay.data?.pageHeight}
          page={page}
          overlays={overlays}
          maxWidth={620}
          missing={
            overlay.error
              ? `overlay unavailable: ${overlay.error.message}`
              : naive
              ? "1.0 has no page regions: text-layer chunks carry no bbox, so promote renders no page images."
              : rec.state !== "SUCCEEDED"
                ? "nothing to draw until this stage succeeds"
                : "the Gold page image appears once promote has run"
          }
        />
        <div className="stack" style={{ minWidth: 200, flex: 1 }}>
          <div className="legend" style={{ flexDirection: "column", gap: 6 }}>
            {legend.map(([k, c]) => (
              <span key={k}><i style={{ borderColor: c }} />{k}</span>
            ))}
          </div>
          {overlays.length === 0 && <div className="muted">{overlay.data?.note ?? (naive ? "" : "no boxes for this page")}</div>}
          {overlay.data && overlay.data.sources.length > 0 && (
            <div className="fine">from {overlay.data.sources.map((k) => k.split("/").slice(-2).join("/")).join(", ")}</div>
          )}
        </div>
      </div>
    </section>
  );
}

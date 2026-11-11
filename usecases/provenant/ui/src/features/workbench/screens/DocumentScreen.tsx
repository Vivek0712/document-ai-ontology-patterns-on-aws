/** One document, every pipeline version, every stage: the lane comparison plus the full stage inspector. */
import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import type { StageId } from "@contracts/api";
import { STAGE_ORDER } from "@contracts/api";
import { useApi } from "../../../api/context";
import { ArtifactList } from "../components/Artifacts";
import { StageInspector } from "../components/StageInspector";
import { ErrorBox, KV, Loading, RunningTimer, StatePill, StateTick, TierPill } from "../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { bytes, dateTime, duration, shortExecution, shortId, shortSha } from "../../../lib/format";
import { STAGE_SHORT, TERMINAL } from "../../../lib/stages";

export function DocumentScreen() {
  const { docId = "" } = useParams();
  const api = useApi();
  const [params, setParams] = useSearchParams();
  const { data, error, reload } = useAsync(() => api.getDocument(docId), [docId], 2000);
  const [msg, setMsg] = useState<string>();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const pvs = Object.keys(data.lanes);
  const pv = params.get("pv") ?? pvs[pvs.length - 1] ?? "2.3.1";
  const stageId = (params.get("stage") as StageId | null) ?? "extract";
  const set = (p: string, s: StageId) => setParams({ pv: p, stage: s }, { replace: true });
  const d = data.document;

  const reprocess = async (p: string) => {
    const r = await api.reprocess(docId, p);
    setMsg(`reprocess started for ${p}: ${shortExecution(r.executionArn)}`);
    reload();
  };

  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="crumbs">
        <Link to={`/workbench/packages/${encodeURIComponent(d.packageId)}`}>{d.packageId}</Link>
        <span>›</span>
        <span>{d.fileName}</span>
      </div>
      <div className="title-row">
        <h1>{d.fileName}</h1>
        <span className="sub mono">{d.docId}</span>
        <span className="spacer" />
        <Link className="btn small" to={`/workbench/docs/${encodeURIComponent(docId)}/units`}>Units explorer</Link>
      </div>
      <div className="grid2">
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Source object</h2>
          <KV
            rows={[
              ["upload state", d.uploadState === "IN_BRONZE" ? <TierPill tier="bronze" /> : <span className="pill fail">{d.uploadState}</span>],
              ["Bronze key", <span className="mono">{d.bronzeKey}</span>],
              ["S3 versionId", <span className="mono">{d.sourceVersionId ?? "—"}</span>],
              ["sha256", <span className="mono">{shortSha(d.sha256, 16)}</span>],
              ["size", bytes(d.sizeBytes)],
              ["pages", d.pageCount],
              ["doc type", d.docType ?? "—"],
              ["uploaded by", <span className="mono">{shortId(d.uploadedBy)}</span>],
              ["upload URL issued", dateTime(d.uploadUrlIssuedAt)],
              ["in Bronze", dateTime(d.inBronzeAt)],
              ...(d.quarantineReason ? ([["quarantined", <span style={{ color: "var(--loss)" }}>× {d.quarantineReason} · <span className="mono">{d.quarantineKey}</span></span>]] as [string, JSX.Element][]) : []),
            ]}
          />
          <div style={{ marginTop: 10 }}>
            <ArtifactList items={[{ key: d.quarantineKey ?? d.bronzeKey, kind: "source_pdf", contentType: "application/pdf", sizeBytes: d.sizeBytes, sha256: d.sha256, versionId: d.sourceVersionId }]} />
          </div>
        </section>
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Lanes</h2>
          <table className="t">
            <thead>
              <tr>
                <th>stage</th>
                {pvs.map((p) => <th key={p}>{p}</th>)}
              </tr>
            </thead>
            <tbody>
              {STAGE_ORDER.map((s) => (
                <tr key={s}>
                  <td>{STAGE_SHORT[s]}</td>
                  {pvs.map((p) => {
                    const r = data.lanes[p].stages.find((x) => x.stageId === s);
                    const on = p === pv && s === stageId;
                    return (
                      <td key={p}>
                        {r && (
                          <button className={`cell ${r.state} ${on ? "selected" : ""}`} style={{ minHeight: 36, padding: "2px 6px" }} onClick={() => set(p, s)}>
                            <StateTick state={r.state} />
                            <span className="txt">
                              <span className="l1">{r.state === "RUNNING" ? <RunningTimer since={r.startedAt} /> : r.state === "SKIPPED" ? "skipped" : r.headline ?? r.state.toLowerCase()}</span>
                              <span className="l2">{r.engine ?? ""} {r.durationMs !== undefined ? `· ${duration(r.durationMs)}` : ""}</span>
                            </span>
                          </button>
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr>
                <td className="muted">lane</td>
                {pvs.map((p) => {
                  const l = data.lanes[p].lane;
                  return (
                    <td key={p}>
                      <StatePill state={l.state} since={l.startedAt} />
                      <div className="fine">{duration(l.durationMs)} · config {shortSha(l.configSha, 8)} · prompt {shortSha(l.promptSha, 8)}</div>
                      <div className="fine">ontology {l.ontologyVersion ?? "none"} · {l.units ?? 0} units · {l.held ?? 0} held · {l.indexed ?? 0} indexed{l.triples ? ` · ${l.triples} triples` : ""}</div>
                      {data.canWrite && TERMINAL.includes(l.state as never) && (
                        <button className="btn small ghost" style={{ marginTop: 6 }} onClick={() => void reprocess(p)}>reprocess {p}</button>
                      )}
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
          {msg && <div className="fine" style={{ marginTop: 8 }}>{msg}</div>}
        </section>
      </div>
      <section>
        <StageInspector docId={docId} pv={pv} stageId={stageId} onSelectStage={(s) => set(pv, s)} onSelectPv={(p) => set(p, stageId)} />
      </section>
    </div>
  );
}

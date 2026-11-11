/** Package screen: upload, live pipeline board (document x pipeline version x stage), tiers, review. */
import { type DragEvent, useCallback, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import type { DocStatusView, PackageStatus, StageCell, StageId, UnitListItem } from "@contracts/api";
import { STAGE_ORDER } from "@contracts/api";
import { useApi } from "../../../api/context";
import type { UploadProgress } from "../../../api/types";
import { StageInspector } from "../components/StageInspector";
import { Drawer, ErrorBox, Loading, PiiPill, RunningTimer, StatePill, StateTick, TierPill, UnitTag } from "../components/ui";
import { useAsync, useNow } from "../../../hooks/useAsync";
import { bytes, duration, mmss, shortId } from "../../../lib/format";
import { ENGINE_SERVICE, GROUPS, STAGE_SHORT } from "../../../lib/stages";

interface UploadRow {
  name: string;
  size: number;
  key?: string;
  progress: number;
  state: "signing" | "uploading" | "in_bronze" | "failed";
  error?: string;
}

export function PackageScreen() {
  const { packageId = "" } = useParams();
  const api = useApi();
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const detail = useAsync(() => api.getPackage(packageId), [packageId], 10_000);
  const status = useAsync(() => api.packageStatus(packageId), [packageId], 1000);
  const [tab, setTab] = useState<"board" | "tiers" | "review">("board");
  const sel = params.get("doc") && params.get("pv") && params.get("stage")
    ? { docId: params.get("doc")!, pv: params.get("pv")!, stageId: params.get("stage") as StageId }
    : undefined;
  const select = useCallback(
    (s?: { docId: string; pv: string; stageId: StageId }) => {
      const p = new URLSearchParams(params);
      if (s) {
        p.set("doc", s.docId);
        p.set("pv", s.pv);
        p.set("stage", s.stageId);
      } else {
        p.delete("doc");
        p.delete("pv");
        p.delete("stage");
      }
      setParams(p, { replace: true });
    },
    [params, setParams],
  );

  if (detail.error) return <ErrorBox error={detail.error} />;
  if (!detail.data) return <Loading />;
  const pkg = detail.data.package;
  const st = status.data;

  const remove = async () => {
    if (!window.confirm(`Delete ${pkg.name}? This removes its S3 objects, DynamoDB rows, index documents and graphs.`)) return;
    await api.deletePackage(packageId);
    nav("/");
  };

  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="title-row">
        <h1>{pkg.name}</h1>
        <span className="sub mono">{pkg.packageId}</span>
        <span className="sub">shared with {pkg.allowedPrincipals.map((p) => (p.startsWith("user:") ? `user:${shortId(p.slice(5))}` : p)).join(", ")}</span>
        <span className="spacer" />
        <Link
          className={st?.askReady ? "btn" : "btn secondary"}
          to={`/workbench/packages/${encodeURIComponent(packageId)}/ask`}
          aria-disabled={!st?.askReady}
          title={st?.askReady ? undefined : "Ask unlocks when both lanes have indexed every document."}
        >
          {st?.askReady ? "Ask this package →" : "Ask · waiting for both lanes"}
        </Link>
        {detail.data.canWrite && <button className="btn small danger dev-only" onClick={() => void remove()}>delete</button>}
      </div>
      {st && <Counters st={st} />}
      <ErrorBox error={status.error} title="status poll" />
      {detail.data.canWrite && <Uploader packageId={packageId} onUploaded={detail.reload} />}
      <div className="tabs">
        <button className={tab === "board" ? "on" : ""} onClick={() => setTab("board")}>Pipeline board</button>
        <button className={tab === "tiers" ? "on" : ""} onClick={() => setTab("tiers")}>Bronze · Silver · PII gate · Gold</button>
        <button className={tab === "review" ? "on" : ""} onClick={() => setTab("review")}>Review queue</button>
      </div>
      {tab === "board" && (st ? <Board st={st} selected={sel} onSelect={select} /> : <Loading what="loading status" />)}
      {tab === "tiers" && st && <Tiers st={st} />}
      {tab === "review" && <Review packageId={packageId} />}
      {sel && (
        <Drawer onClose={() => select(undefined)}>
          <StageInspector
            docId={sel.docId}
            pv={sel.pv}
            stageId={sel.stageId}
            onSelectStage={(stageId) => select({ ...sel, stageId })}
            onSelectPv={(pv) => select({ ...sel, pv })}
          />
          <div style={{ marginTop: 16 }}>
            <Link className="btn small ghost" to={`/workbench/docs/${encodeURIComponent(sel.docId)}?pv=${encodeURIComponent(sel.pv)}&stage=${sel.stageId}`}>open full document view</Link>
          </div>
        </Drawer>
      )}
    </div>
  );
}

function Counters({ st }: { st: PackageStatus }) {
  const now = useNow();
  const starts = st.docs.flatMap((d) => Object.values(d.lanes).map((l) => l.startedAt)).filter(Boolean) as string[];
  const ends = st.docs.flatMap((d) => Object.values(d.lanes).map((l) => l.endedAt));
  const t0 = starts.length ? Math.min(...starts.map(Date.parse)) : undefined;
  const allDone = ends.length > 0 && ends.every(Boolean);
  const t1 = allDone ? Math.max(...(ends as string[]).map(Date.parse)) : now;
  const c = st.counters["2.3.1"] ?? Object.values(st.counters)[0];
  return (
    <div className="card counters">
      <div className="counter"><b>{t0 ? mmss(t1 - t0) : "00:00"}</b><span>elapsed</span></div>
      <div className="counter"><b>{st.objectsInBronze}</b><span><TierPill tier="bronze" /> objects in Bronze</span></div>
      {c && (
        <>
          <div className="counter"><b>{c.units}</b><span>units (2.3.1)</span></div>
          <div className="counter"><b>{c.held}</b><span>held</span></div>
          <div className="counter"><b>{c.indexed}</b><span>indexed</span></div>
          <div className="counter"><b>{c.triples}</b><span>triples</span></div>
        </>
      )}
      <span className="spacer" />
      <div className="row">
        {Object.entries(st.lanes).map(([pv, l]) => (
          <span key={pv} className={`pill ${l.ready ? "ok" : l.state === "FAILED" || l.state === "PARTIAL" ? "fail" : "running"}`} style={{ fontSize: "var(--fs-s)", padding: "6px 14px" }}>
            {l.ready ? "✓" : ""} {pv} {l.ready ? `indexed ${l.indexed}/${l.expected}` : l.state === "EMPTY" ? "no documents" : `${l.state.toLowerCase()} · ${l.docs.succeeded}/${l.docs.total} docs`}
          </span>
        ))}
        {st.askReady && <span className="pill ok" style={{ fontSize: "var(--fs-s)", padding: "6px 14px" }}>✓ both lanes indexed</span>}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------- upload

function Uploader({ packageId, onUploaded }: { packageId: string; onUploaded: () => void }) {
  const api = useApi();
  const [rows, setRows] = useState<UploadRow[]>([]);
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const patch = (name: string, p: Partial<UploadRow>) => setRows((rs) => rs.map((r) => (r.name === name ? { ...r, ...p } : r)));

  const start = async (files: File[]) => {
    const pdfs = files.filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    const rejected = files.filter((f) => !pdfs.includes(f));
    setRows((rs) => [
      ...rs.filter((r) => !files.some((f) => f.name === r.name)),
      ...pdfs.map((f) => ({ name: f.name, size: f.size, progress: 0, state: "signing" as const })),
      ...rejected.map((f) => ({ name: f.name, size: f.size, progress: 0, state: "failed" as const, error: "only PDF files are accepted" })),
    ]);
    await Promise.all(
      pdfs.map(async (f) => {
        try {
          const h = await api.upload(packageId, f, (p: UploadProgress) => patch(f.name, { progress: p.total ? p.loaded / p.total : 0, state: "uploading" }));
          patch(f.name, { key: h.key, state: "uploading" });
          await h.done;
          patch(f.name, { progress: 1, state: "in_bronze" });
          onUploaded();
        } catch (e) {
          patch(f.name, { state: "failed", error: e instanceof Error ? e.message : String(e) });
        }
      }),
    );
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setOver(false);
    void start([...e.dataTransfer.files]);
  };

  return (
    <section className="card stack">
      <div
        className={`drop ${over ? "over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
        onClick={() => input.current?.click()}
        role="button"
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && input.current?.click()}
      >
        <div className="big">Drop PDFs here</div>
        <div>or click to choose · PDF only · ≤ 25 MB each · presigned PUT straight to Amazon S3 Bronze</div>
        <input ref={input} type="file" accept="application/pdf,.pdf" multiple hidden onChange={(e) => void start([...(e.target.files ?? [])])} />
      </div>
      {rows.length > 0 && (
        <table className="t">
          <tbody>
            {rows.map((r) => (
              <tr key={r.name}>
                <td style={{ color: "var(--text)", fontWeight: 700 }}>{r.name}</td>
                <td className="muted">{bytes(r.size)}</td>
                <td style={{ width: "40%" }}>
                  {r.state === "failed" ? (
                    <span style={{ color: "var(--loss)" }}>× {r.error}</span>
                  ) : r.state === "in_bronze" ? (
                    <span><span style={{ color: "var(--cited)" }}>✓</span> in Bronze <span className="fine mono">{r.key}</span></span>
                  ) : (
                    <div className="stack" style={{ gap: 4 }}>
                      <div className="progress"><i style={{ width: `${Math.round(r.progress * 100)}%` }} /></div>
                      <span className="fine mono">{r.state === "signing" ? "POST upload-url…" : `PUT ${r.key ?? ""} · ${Math.round(r.progress * 100)}%`}</span>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------------------- board

function cellLines(c: StageCell): [string, string] {
  const svc = c.engine ? ENGINE_SERVICE[c.engine] : "";
  switch (c.state) {
    case "RUNNING":
      return ["", `${c.engine ?? ""}${c.attempt > 1 ? ` · attempt ${c.attempt}` : ""}${c.heartbeatCount ? ` · ♥${c.heartbeatCount}` : ""}`];
    case "QUEUED":
      return ["", c.engine ?? "queued"];
    case "SUCCEEDED":
      return [c.headline ?? "done", `${svc || c.engine || ""} · ${duration(c.durationMs)}${c.attempt > 1 ? ` · ×${c.attempt}` : ""}`];
    case "FAILED":
      return [c.error?.type ?? "failed", `attempt ${c.attempt}${c.error?.retryable ? " · retrying" : ""}`];
    case "SKIPPED":
      return ["skipped", c.skipReason ?? ""];
    default:
      return ["waiting", ""];
  }
}

function Cell({ c, selected, onClick, gate }: { c?: StageCell; selected: boolean; onClick: () => void; gate: boolean }) {
  if (!c) return <td className={gate ? "gate" : ""} />;
  const [l1, l2] = cellLines(c);
  return (
    <td className={gate ? "gate" : ""}>
      <button className={`cell ${c.state} ${selected ? "selected" : ""}`} onClick={onClick} title={`${c.title} · ${c.state}${c.skipReason ? ` · ${c.skipReason}` : ""}${c.error ? ` · ${c.error.message}` : ""}`}>
        <StateTick state={c.state} />
        <span className="txt">
          <span className="l1">
            {c.state === "RUNNING" ? <RunningTimer since={c.startedAt} /> : c.state === "QUEUED" ? <RunningTimer since={c.queuedAt} label="queued" /> : l1}
          </span>
          <span className="l2">{l2}</span>
        </span>
      </button>
    </td>
  );
}

/** The board scrolls sideways on narrow screens: a right-edge fade says so until the last column is in view. */
function markScrollEnd(el: HTMLElement) {
  el.dataset.more = String(el.scrollLeft + el.clientWidth < el.scrollWidth - 4);
}

function Board({ st, selected, onSelect }: { st: PackageStatus; selected?: { docId: string; pv: string; stageId: StageId }; onSelect: (s: { docId: string; pv: string; stageId: StageId }) => void }) {
  const pvs = Object.keys(st.lanes).length ? Object.keys(st.lanes) : ["1.0", "2.3.1"];
  if (!st.docs.length) return <div className="empty">No documents yet. Upload PDFs above; each one runs through both pipeline versions.</div>;
  const gateStages = new Set<StageId>(["pii_gate", "promote"]);
  return (
    <div className="board-wrap card" style={{ padding: 8 }} onScroll={(e) => markScrollEnd(e.currentTarget)} ref={(el) => el && markScrollEnd(el)}>
      <table className="board">
        <thead>
          <tr>
            <th rowSpan={2}>document</th>
            <th rowSpan={2}>version</th>
            {GROUPS.map((g) => (
              <th key={g.group} colSpan={g.stages.length} className={`group ${g.group === "gate" ? "gate" : ""}`}>{g.title}</th>
            ))}
          </tr>
          <tr>
            {STAGE_ORDER.map((s) => (
              <th key={s} className={gateStages.has(s) ? "sgate" : ""}>{STAGE_SHORT[s]}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {st.docs.map((d) =>
            pvs.map((pv, i) => (
              <tr key={`${d.docId}-${pv}`} className={i === 0 ? "doc-first" : ""}>
                {i === 0 && (
                  <td rowSpan={pvs.length} className="doc-cell">
                    <DocLabel d={d} />
                  </td>
                )}
                <td className="pv-cell">
                  <b style={{ color: "var(--text)" }}>{pv}</b>
                  <div>{d.lanes[pv] ? <StatePill state={d.lanes[pv].state} /> : <span className="fine">{d.uploadState === "AWAITING_UPLOAD" ? "uploading" : "—"}</span>}</div>
                </td>
                {STAGE_ORDER.map((s) => (
                  <Cell
                    key={s}
                    gate={gateStages.has(s)}
                    c={d.lanes[pv]?.stages.find((c) => c.stageId === s)}
                    selected={selected?.docId === d.docId && selected.pv === pv && selected.stageId === s}
                    onClick={() => onSelect({ docId: d.docId, pv, stageId: s })}
                  />
                ))}
              </tr>
            )),
          )}
        </tbody>
      </table>
      <div className="fine" style={{ padding: 8 }}>
        Click any cell to inspect the stage: inputs, outputs, S3 artifacts, config sha, versions, heartbeats, errors and events. Polled every second from rp-doc-status.
      </div>
    </div>
  );
}

function DocLabel({ d }: { d: DocStatusView }) {
  return (
    <div className="stack" style={{ gap: 4 }}>
      <Link className="name" to={`/workbench/docs/${encodeURIComponent(d.docId)}`} style={{ textDecoration: "none" }}>{d.fileName}</Link>
      <span className="fine">
        {d.docType ?? "type pending"} · {d.pageCount ?? "?"} p · {bytes(d.sizeBytes)}
      </span>
      <span className="row" style={{ gap: 6 }}>
        {d.uploadState === "IN_BRONZE" && <TierPill tier="bronze" />}
        {d.uploadState === "QUARANTINED" && <span className="pill fail">× quarantined</span>}
        {d.uploadState === "AWAITING_UPLOAD" && <span className="pill running">awaiting upload</span>}
        <Link className="fine" to={`/workbench/docs/${encodeURIComponent(d.docId)}/units`}>units</Link>
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------------------- tiers

function Tiers({ st }: { st: PackageStatus }) {
  const api = useApi();
  const nav = useNavigate();
  const pv = "2.3.1";
  const ready = st.docs.filter((d) => d.lanes[pv]?.stages.find((c) => c.stageId === "units")?.state === "SUCCEEDED").map((d) => d.docId);
  const promoted = new Set(st.docs.filter((d) => d.lanes[pv]?.stages.find((c) => c.stageId === "promote")?.state === "SUCCEEDED").map((d) => d.docId));
  const gated = new Set(st.docs.filter((d) => d.lanes[pv]?.stages.find((c) => c.stageId === "pii_gate")?.state === "SUCCEEDED").map((d) => d.docId));
  const key = ready.join(",");
  const units = useAsync(async () => {
    const out: (UnitListItem & { docId: string; fileName: string })[] = [];
    for (const docId of ready) {
      const list = await api.listUnits(docId, pv);
      const fileName = st.docs.find((d) => d.docId === docId)?.fileName ?? docId;
      out.push(...list.items.map((u) => ({ ...u, docId, fileName })));
    }
    return out;
  }, [key]);
  const all = units.data ?? [];
  const silver = all.filter((u) => !promoted.has(u.docId) || u.held);
  const gold = all.filter((u) => promoted.has(u.docId) && !u.held);
  const card = (u: UnitListItem & { fileName: string; docId: string }) => (
    <button key={u.unitId} className={`ucard ${u.held && gated.has(u.docId) ? "held" : ""}`} onClick={() => nav(`/workbench/units/${encodeURIComponent(u.unitId)}`)}>
      <span className="row" style={{ gap: 6 }}>
        <UnitTag type={u.unitType} />
        {u.held && gated.has(u.docId) && <PiiPill flag="held" />}
      </span>
      <span>{u.fileName.replace(/\.pdf$/, "")} · p.{u.pages.join(",")} · {u.label}</span>
    </button>
  );
  return (
    <section className="card">
      <div className="fine" style={{ marginBottom: 10 }}>Pipeline 2.3.1 · units move Silver → Gold through the PII gate; a held unit stays in Silver and is never indexed.</div>
      <div className="tiers">
        <div className="tier">
          <h3><TierPill tier="bronze" /></h3>
          {st.docs.filter((d) => d.uploadState === "IN_BRONZE").map((d) => (
            <div key={d.docId} className="ucard" style={{ cursor: "default" }}>{d.fileName}</div>
          ))}
        </div>
        <div className="tier">
          <h3><TierPill tier="silver" /></h3>
          {units.loading && !units.data && <Loading />}
          {silver.map(card)}
        </div>
        <div className="gate-col" title="PII gate (Amazon Comprehend)" />
        <div className="tier">
          <h3><TierPill tier="gold" /></h3>
          {gold.slice(0, 8).map(card)}
          {gold.length > 8 && <span className="pill">+{gold.length - 8}</span>}
        </div>
      </div>
    </section>
  );
}

// ---------------------------------------------------------------------------------------- review

function Review({ packageId }: { packageId: string }) {
  const api = useApi();
  const { data, error } = useAsync(() => api.listReview(packageId), [packageId]);
  const nav = useNavigate();
  const items = useMemo(() => data?.items ?? [], [data]);
  return (
    <section className="card">
      <div className="fine" style={{ marginBottom: 10 }}>Fields whose extraction confidence is below the threshold (rp-review-queue). Read-only in v1: accept/correct actions are deferred.</div>
      <ErrorBox error={error} />
      {items.length === 0 ? (
        <div className="empty">nothing to review</div>
      ) : (
        <table className="t">
          <thead>
            <tr><th>unit · field</th><th>label</th><th>value</th><th>confidence</th><th>engine</th><th>state</th></tr>
          </thead>
          <tbody>
            {items.map((r) => (
              <tr key={r.reviewKey} className="click" onClick={() => nav(`/workbench/trace/${encodeURIComponent(r.unitId)}?field=${encodeURIComponent(r.fieldId)}`)}>
                <td className="mono">{r.unitId} · {r.fieldId}</td>
                <td>{r.label}</td>
                <td style={{ color: "var(--text)", fontWeight: 700 }}>{r.value}</td>
                <td>{r.confidence.toFixed(2)} <span className="fine">&lt; {r.threshold.toFixed(2)}</span></td>
                <td>{r.engine}</td>
                <td><span className="pill running">{r.state}</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

/** Unit inspector: unit outline + members on the page, fields, PII decision, status, JSON. */
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useApi } from "../../../api/context";
import { ArtifactList } from "../components/Artifacts";
import { type Overlay, PageView } from "../components/PageView";
import { ErrorBox, Json, KV, Loading, PiiPill, TierPill, UnitTag } from "../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { dateTime, shortSha } from "../../../lib/format";
import { fmtBox, roleColor } from "../../../lib/geometry";

export function UnitScreen() {
  const { unitId = "" } = useParams();
  const docId = unitId.split(":")[0];
  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="crumbs">
        <Link to={`/workbench/docs/${encodeURIComponent(docId)}`}>{docId}</Link>
        <span>›</span>
        <Link to={`/workbench/docs/${encodeURIComponent(docId)}/units`}>units</Link>
        <span>›</span>
        <span className="mono">{unitId}</span>
      </div>
      <UnitBody unitId={unitId} />
    </div>
  );
}

export function UnitBody({ unitId, compact = false }: { unitId: string; compact?: boolean }) {
  const api = useApi();
  const { data, error } = useAsync(() => api.getUnit(unitId), [unitId]);
  const region = useAsync(
    async () => (data && data.unit.bboxes.length && !data.held ? api.getRegion(unitId) : undefined),
    [data?.unit.unitId, data?.held],
  );
  const [page, setPage] = useState<number>();
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const u = data.unit;
  const p = page ?? u.pages[0] ?? 1;
  const overlays: Overlay[] = [
    ...u.members.filter((m) => m.bbox).map((m) => ({
      bbox: m.bbox!,
      kind: "member" as const,
      color: roleColor(m.role),
      dashed: m.phase === "B" || m.phase === "C",
      title: `${m.memberId} · ${m.role} · phase ${m.phase ?? "?"}${m.allocScore !== undefined ? ` · alloc ${m.allocScore}` : ""}`,
    })),
    ...u.bboxes.map((b) => ({ bbox: b, kind: "unit" as const, label: u.unitType })),
    ...(region.data?.heldRegions ?? []).map((b) => ({ bbox: b, kind: "held" as const })),
  ];
  return (
    <div className="stack" style={{ gap: 16 }}>
      {!compact && (
        <div className="title-row">
          <h1>{u.label}</h1>
          <UnitTag type={u.unitType} />
          <PiiPill flag={u.piiFlag} />
          <span className="sub">{u.breadcrumb.join(" › ")}</span>
        </div>
      )}
      {data.held && (
        <div className="card" style={{ borderColor: "var(--boundary)" }}>
          <b style={{ color: "var(--text)" }}>⏸ held: PII.</b> <span className="muted">This unit stays in Silver: never indexed, never in Gold, no region. Text is redacted in the API.</span>
        </div>
      )}
      <div className="grid2">
        <div className="stack">
          {u.bboxes.length > 0 ? (
            <>
              {u.pages.length > 1 && (
                <div className="seg">
                  {u.pages.map((n) => <button key={n} className={n === p ? "on" : ""} onClick={() => setPage(n)}>p.{n}</button>)}
                </div>
              )}
              <PageView url={region.data?.url} page={p} pageWidth={u.pageWidth} pageHeight={u.pageHeight} overlays={overlays} maxWidth={compact ? 460 : 640} missing={data.held ? "held unit: no page region is served" : region.error ? "page image unavailable" : "loading page…"} />
              <div className="legend"><span><i style={{ borderColor: "var(--l2)" }} />unit bbox</span><span><i style={{ borderColor: "var(--l1)" }} />members by role (dashed = phase B/C)</span></div>
            </>
          ) : (
            <div className="page-missing">{u.unitType === "chunk" ? "Fixed chunk: no bbox, no page region (text-layer extraction in 1.0)." : "no bbox"}</div>
          )}
        </div>
        <div className="stack">
          <KV
            rows={[
              ["unitId", <span className="mono">{u.unitId}</span>],
              ["file", u.fileName],
              ["pipeline", u.pipelineVersion],
              ["type", <UnitTag type={u.unitType} />],
              ["doc type", u.docType ?? "—"],
              ["period", u.period ?? "—"],
              ["pages", u.pages.join(", ")],
              ["bbox", u.bboxes.map(fmtBox).join(" | ") || "none"],
              ["chars · tokens", `${u.chars} · ${u.tokens ?? "—"}`],
              ["confidence", u.confidence?.toFixed(2) ?? "—"],
              ["concepts", u.fieldConcepts.join(", ") || "—"],
              ["allowed", u.allowedPrincipals.map((x) => (x.startsWith("user:") ? `user:${x.slice(5, 11)}…` : x)).join(", ")],
              ["invariants", "I1/I2 not evaluated in this lane (deferred to pipeline 2.4.0)"],
            ]}
          />
          <div className="code wrap" style={{ maxHeight: 180 }} tabIndex={0} role="region" aria-label="Unit text">{u.text}</div>
        </div>
      </div>
      {u.fields.length > 0 && (
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Fields</h2>
          <table className="t">
            <thead><tr><th>field</th><th>label</th><th>value</th><th>concept</th><th>period</th><th>bbox</th><th>source</th><th>conf.</th><th /></tr></thead>
            <tbody>
              {u.fields.map((f) => (
                <tr key={f.fieldId}>
                  <td className="mono">{f.fieldId}</td>
                  <td>{f.label}</td>
                  <td style={{ color: "var(--text)", fontWeight: 700 }}>{f.value}</td>
                  <td><span className="pill unit">{f.concept ?? "—"}</span></td>
                  <td>{f.period ?? "—"}</td>
                  <td className="mono fine">{fmtBox(f.bbox)}</td>
                  <td className="fine">{f.source}</td>
                  <td>{f.confidence?.toFixed(2) ?? "—"}</td>
                  <td>{f.bbox && !data.held && <Link className="btn small" to={`/workbench/trace/${encodeURIComponent(u.unitId)}?field=${encodeURIComponent(f.fieldId)}`}>region + lineage</Link>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      {u.members.length > 0 && (
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Members ({u.members.length})</h2>
          <table className="t">
            <thead><tr><th>member</th><th>role</th><th>phase</th><th>alloc</th><th>text</th><th>engine block</th></tr></thead>
            <tbody>
              {u.members.map((m) => (
                <tr key={m.memberId}>
                  <td className="mono">{m.memberId}</td>
                  <td><span className="pill" style={{ borderColor: roleColor(m.role) }}>{m.role}</span></td>
                  <td>{m.phase ?? "—"}</td>
                  <td>{m.allocScore?.toFixed(2) ?? "—"}</td>
                  <td>{m.text ?? ""}</td>
                  <td className="mono fine">{m.blockRef ? `${m.blockRef.engine} ${m.blockRef.pointer} (${m.blockRef.engineLabel ?? ""})` : "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
      <div className="grid2">
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>PII gate decision</h2>
          {data.pii ? (
            <>
              <KV rows={[["decision", <PiiPill flag={data.pii.decision} />], ["rule", data.pii.rule], ["held regions", data.pii.heldBboxes.map(fmtBox).join(" | ") || "none"]]} />
              {data.pii.entities.length > 0 && (
                <table className="t" style={{ marginTop: 8 }}>
                  <thead><tr><th>entity</th><th>score</th><th>span</th><th>digits</th><th>masked</th></tr></thead>
                  <tbody>{data.pii.entities.map((e, i) => <tr key={i}><td>{e.type}</td><td>{e.score.toFixed(2)}</td><td>{e.begin}–{e.end}</td><td>{e.digits}</td><td>{e.masked ? "yes" : "no"}</td></tr>)}</tbody>
                </table>
              )}
            </>
          ) : <div className="muted">not gated yet</div>}
        </section>
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Status and provenance</h2>
          {data.status && (
            <KV
              rows={[
                ["Silver", <span className="mono"><TierPill tier="silver" /> {data.status.silverKey}</span>],
                ["Gold", data.status.goldKey ? <span className="mono"><TierPill tier="gold" /> {data.status.goldKey}</span> : "—"],
                ["OpenSearch _id", <span className="mono">{data.status.aossId ?? "—"}</span>],
                ["indexed", dateTime(data.status.indexedAt)],
                ["graph triples", data.status.graphTriples ?? 0],
              ]}
            />
          )}
          <div style={{ marginTop: 8 }}>
            <KV
              rows={[
                ["engine", `${u.provenance.engine} · ${u.provenance.engineVersion ?? ""}`],
                ["ontology", u.provenance.ontologyVersion ?? "none"],
                ["config sha", <span className="mono">{shortSha(u.provenance.configSha, 12)}</span>],
                ["source", <span className="mono">{u.provenance.sourceKey} · sha {shortSha(u.provenance.sourceSha, 10)}</span>],
              ]}
            />
          </div>
          <div style={{ marginTop: 8 }}>
            <ArtifactList items={u.provenance.extractArtifacts.map((k) => ({ key: k, kind: k.includes("textract") ? "textract_json" : k.includes("docling") ? "docling_json" : "text_layer", contentType: "application/json" }))} />
          </div>
        </section>
      </div>
      {!compact && (
        <details className="card">
          <summary style={{ cursor: "pointer" }}>Unit JSON</summary>
          <Json value={u} />
        </details>
      )}
    </div>
  );
}

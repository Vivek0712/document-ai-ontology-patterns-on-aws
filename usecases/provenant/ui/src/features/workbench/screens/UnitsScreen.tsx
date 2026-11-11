/** Units explorer: v1.0 fixed chunks vs v2.3.1 evidence units for one document, side by side. */
import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import type { UnitListItem } from "@contracts/api";
import { useApi } from "../../../api/context";
import { ErrorBox, Loading, PiiPill, UnitTag } from "../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { UnitBody } from "./UnitScreen";

export function UnitsScreen() {
  const { docId = "" } = useParams();
  const api = useApi();
  const doc = useAsync(() => api.getDocument(docId), [docId]);
  if (doc.error) return <ErrorBox error={doc.error} />;
  if (!doc.data) return <Loading />;
  const pvs = Object.keys(doc.data.lanes);
  return (
    <div className="stack" style={{ gap: 18 }}>
      <div className="crumbs">
        <Link to={`/workbench/packages/${encodeURIComponent(doc.data.document.packageId)}`}>{doc.data.document.packageId}</Link>
        <span>›</span>
        <Link to={`/workbench/docs/${encodeURIComponent(docId)}`}>{doc.data.document.fileName}</Link>
        <span>›</span>
        <span>units</span>
      </div>
      <div className="title-row">
        <h1>Units · {doc.data.document.fileName}</h1>
        <span className="sub">Same document, two pipeline versions. Only preprocessing differs.</span>
      </div>
      <div className="grid2" style={{ gridTemplateColumns: `repeat(${pvs.length}, minmax(0, 1fr))` }}>
        {pvs.map((pv) => (
          <LaneUnits key={pv} docId={docId} pv={pv} />
        ))}
      </div>
    </div>
  );
}

function LaneUnits({ docId, pv }: { docId: string; pv: string }) {
  const api = useApi();
  const { data, error, loading } = useAsync(() => api.listUnits(docId, pv), [docId, pv]);
  const [open, setOpen] = useState<string>();
  const items = data?.items ?? [];
  const meanChars = items.length ? Math.round(items.reduce((s, u) => s + u.chars, 0) / items.length) : 0;
  return (
    <section className="card stack">
      <div className="row">
        <h2>{pv}</h2>
        <span className="muted">{pv === "1.0" ? "fixed chunks · text layer" : "evidence units · layout + ontology"}</span>
        <span className="spacer" />
        <span className="fine">{items.length} units · {items.reduce((s, u) => s + u.fieldCount, 0)} fields · mean {meanChars} chars · {items.filter((u) => u.held).length} held</span>
      </div>
      {loading && <Loading />}
      <ErrorBox error={error} />
      {data && items.length === 0 && <div className="empty">no units in this lane{pv === "1.0" ? " (a scanned page has no text layer)" : ""}</div>}
      {items.map((u: UnitListItem) => (
        <div key={u.unitId} className="card" style={{ padding: 12, borderColor: u.held ? "var(--boundary)" : undefined }}>
          <div className="row" style={{ cursor: "pointer" }} onClick={() => setOpen(open === u.unitId ? undefined : u.unitId)}>
            <span className="mono" style={{ color: "var(--text)" }}>{u.unitId.split(":").pop()}</span>
            <UnitTag type={u.unitType} />
            <b style={{ color: "var(--text)" }}>{u.label}</b>
            <span className="fine">p.{u.pages.join(",")} · {u.chars} chars · {u.fieldCount} fields</span>
            {u.piiFlag !== "none" && <PiiPill flag={u.piiFlag} />}
            <span className={`pill ${u.indexed ? "ok" : ""}`}>{u.indexed ? "✓ indexed" : "not indexed"}</span>
            {u.reviewItems > 0 && <span className="pill running">review {u.reviewItems}</span>}
            <span className="spacer" />
            <Link className="btn small ghost" to={`/workbench/units/${encodeURIComponent(u.unitId)}`} onClick={(e) => e.stopPropagation()}>inspect</Link>
          </div>
          {open === u.unitId && (
            <div style={{ marginTop: 12 }}>
              <UnitBody unitId={u.unitId} compact />
            </div>
          )}
        </div>
      ))}
    </section>
  );
}

import { type FormEvent, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import type { Package } from "@contracts/api";
import { useApi, useMe } from "../../../api/context";
import { ErrorBox, Loading, StatePill } from "../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { relative, shortId } from "../../../lib/format";

function LaneSummary({ pkg }: { pkg: Package }) {
  return (
    <div className="row" style={{ gap: 8 }}>
      {pkg.pipelineVersions.map((pv) => {
        const l = pkg.lanes[pv];
        const c = pkg.counters[pv];
        return (
          <span key={pv} className="row" style={{ gap: 6 }}>
            <b style={{ color: "var(--text)" }}>{pv}</b>
            <StatePill state={l?.ready ? "ready" : (l?.state ?? "EMPTY")} />
            {c && <span className="fine">{c.units} units · {c.held} held · {c.indexed} indexed{c.triples ? ` · ${c.triples} triples` : ""}</span>}
          </span>
        );
      })}
    </div>
  );
}

export function PackagesScreen() {
  const api = useApi();
  const me = useMe();
  const nav = useNavigate();
  const { data, error, loading, reload } = useAsync(() => api.listPackages(), [api], 5000);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [share, setShare] = useState<string[]>(me.groups.slice(0, 1));
  const [busy, setBusy] = useState(false);
  const [createError, setCreateError] = useState<unknown>();

  const create = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setCreateError(undefined);
    try {
      const pkg = await api.createPackage({ name, description: description || undefined, shareWithGroups: share, pipelineVersions: ["1.0", "2.3.1"] });
      reload();
      nav(`/workbench/packages/${encodeURIComponent(pkg.packageId)}`);
    } catch (err) {
      setCreateError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack" style={{ gap: 20 }}>
      <div className="title-row">
        <h1>Packages</h1>
        <span className="sub">A package is a set of PDFs ingested by both pipeline versions (1.0 naive, 2.3.1 evidence units).</span>
      </div>
      <div className="grid2" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(0, 1fr)" }}>
        <section className="card">
          {loading && !data && <Loading />}
          <ErrorBox error={error} />
          {data && data.items.length === 0 && <div className="empty">No packages you can read yet. Create one to upload PDFs.</div>}
          {data && data.items.length > 0 && (
            <table className="t">
              <thead>
                <tr>
                  <th>Package</th>
                  <th>Docs</th>
                  <th>Lanes</th>
                  <th>Owner</th>
                  <th>Updated</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((p) => (
                  <tr key={p.packageId} className="click" onClick={() => nav(`/workbench/packages/${encodeURIComponent(p.packageId)}`)}>
                    <td>
                      <Link to={`/workbench/packages/${encodeURIComponent(p.packageId)}`} style={{ color: "var(--text)", fontWeight: 700, textDecoration: "none" }}>{p.name}</Link>
                      <div className="fine mono">{p.packageId}</div>
                      {p.description && <div className="fine">{p.description}</div>}
                    </td>
                    <td>{p.docCount}</td>
                    <td><LaneSummary pkg={p} /></td>
                    <td className="mono">{p.ownerSub === me.sub ? "you" : shortId(p.ownerSub)}</td>
                    <td className="muted">{relative(p.updatedAt)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </section>
        <form className="card stack" onSubmit={create}>
          <h2>New package</h2>
          <label className="field">
            Name
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} required maxLength={60} placeholder="Lending package · Fictus household" />
          </label>
          <label className="field">
            Description
            <input className="input" value={description} onChange={(e) => setDescription(e.target.value)} maxLength={200} />
          </label>
          <fieldset className="field" style={{ border: 0, padding: 0, margin: 0 }}>
            <span>Share with your groups</span>
            {me.groups.length === 0 && <span className="fine">you belong to no groups: only you can read it</span>}
            {me.groups.map((g) => (
              <label key={g} className="row" style={{ gap: 6, color: "var(--text)" }}>
                <input type="checkbox" checked={share.includes(g)} onChange={(e) => setShare(e.target.checked ? [...share, g] : share.filter((x) => x !== g))} />
                group:{g}
              </label>
            ))}
          </fieldset>
          <div className="fine">Both pipeline versions run on every upload: 1.0 and 2.3.1.</div>
          <button className="btn" disabled={busy || !name.trim()} type="submit">{busy ? "creating…" : "Create package"}</button>
          <ErrorBox error={createError} />
        </form>
      </div>
    </div>
  );
}

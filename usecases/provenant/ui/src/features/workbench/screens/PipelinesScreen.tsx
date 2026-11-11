/** Pipelines: registered pipeline versions, their config/stages/prompt, and the config diff. */
import { Fragment, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import type { ConfigDiff, DiffLine } from "@contracts/api";
import { useApi } from "../../../api/context";
import { ErrorBox, KV, Loading, ServiceTags } from "../components/ui";
import { useAsync } from "../../../hooks/useAsync";
import { dateTime, shortSha } from "../../../lib/format";

export function PipelinesScreen() {
  const { view } = useParams();
  const api = useApi();
  const nav = useNavigate();
  const list = useAsync(() => api.listPipelines(), [api]);
  const items = list.data?.items ?? [];
  const current = view ?? "diff";
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="title-row">
        <h1>Pipelines</h1>
        <span className="sub">Configs are versioned objects in S3 (config/pipelines/v{"{pv}"}.yaml); every stage records the config sha it ran with.</span>
      </div>
      <ErrorBox error={list.error} />
      <div className="tabs">
        <button className={current === "diff" ? "on" : ""} onClick={() => nav("/settings/pipelines/diff")}>Diff 1.0 → 2.3.1</button>
        {items.map((p) => (
          <button key={p.pipelineVersion} className={current === p.pipelineVersion ? "on" : ""} onClick={() => nav(`/settings/pipelines/${encodeURIComponent(p.pipelineVersion)}`)}>
            {p.title}
          </button>
        ))}
      </div>
      {current === "diff" ? <Diff a={items[0]?.pipelineVersion ?? "1.0"} b={items[items.length - 1]?.pipelineVersion ?? "2.3.1"} /> : <Detail pv={current} />}
    </div>
  );
}

function Detail({ pv }: { pv: string }) {
  const api = useApi();
  const { data, error } = useAsync(() => api.getPipeline(pv), [pv]);
  const [tab, setTab] = useState<"yaml" | "resolved" | "prompt">("yaml");
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  const it = data.item;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <div className="grid2">
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>{it.title}</h2>
          <KV
            rows={[
              ["mode", it.mode],
              ["status", it.status],
              ["config key", <span className="mono">{it.configKey}</span>],
              ["S3 versionId", <span className="mono">{it.configVersionId ?? "—"}</span>],
              ["config sha", <span className="mono" title={it.configSha}>{shortSha(it.configSha, 16)}</span>],
              ["prompt sha", <span className="mono" title={it.promptSha}>{shortSha(it.promptSha, 16)}</span>],
              ["ontology", it.ontologyVersion ?? "none"],
              ["registered", dateTime(it.registeredAt)],
            ]}
          />
        </section>
        <section className="card">
          <h2 style={{ marginBottom: 10 }}>Models and block shas</h2>
          <KV rows={Object.entries(it.modelIds).map(([k, v]) => [k, <span className="mono">{v}</span>])} />
          <div style={{ marginTop: 10 }}>
            <KV rows={Object.entries(it.blockShas).map(([k, v]) => [k, <span className="mono" title={v}>{shortSha(v, 16)}</span>])} />
          </div>
        </section>
      </div>
      <section className="card">
        <h2 style={{ marginBottom: 10 }}>Stages in {pv}</h2>
        <table className="t">
          <thead>
            <tr><th>#</th><th>stage</th><th>group</th><th>enabled</th><th>engines</th><th>AWS services</th><th>Step Functions</th><th>produces</th></tr>
          </thead>
          <tbody>
            {data.stages.map((s) => (
              <tr key={s.id}>
                <td>{s.order}</td>
                <td><b style={{ color: "var(--text)" }}>{s.title}</b><div className="fine">{s.description}</div></td>
                <td>{s.group}</td>
                <td>{s.enabled ? "✓" : <span className="muted">skipped: {s.skipReason}</span>}</td>
                <td>{s.engines.join(", ")}</td>
                <td><ServiceTags services={s.services} /></td>
                <td className="mono fine">{s.sfnStates.join(", ")}</td>
                <td className="fine">{s.produces.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className="card">
        <div className="tabs">
          <button className={tab === "yaml" ? "on" : ""} onClick={() => setTab("yaml")}>YAML</button>
          <button className={tab === "resolved" ? "on" : ""} onClick={() => setTab("resolved")}>resolved</button>
          <button className={tab === "prompt" ? "on" : ""} onClick={() => setTab("prompt")}>prompt</button>
        </div>
        <pre className="code wrap" tabIndex={0} aria-label="Pipeline config">{tab === "yaml" ? data.yaml : tab === "prompt" ? data.promptText : JSON.stringify(data.resolved, null, 2)}</pre>
      </section>
    </div>
  );
}

function Diff({ a, b }: { a: string; b: string }) {
  const api = useApi();
  const { data, error } = useAsync(() => api.diffPipelines(a, b), [a, b]);
  if (error) return <ErrorBox error={error} />;
  if (!data) return <Loading />;
  return (
    <div className="stack" style={{ gap: 16 }}>
      <section className="card stack">
        <div className="summary-bar">{data.summary}</div>
        <div className="grid3">
          {Object.entries(data.blocks).map(([k, v]) => (
            <div key={k} className="mono" style={{ color: v.equal ? "var(--text)" : "var(--loss)" }}>
              {v.equal ? "✓" : "×"} {k} {shortSha(v.a)} {v.equal ? "=" : "≠"} {shortSha(v.b)}
            </div>
          ))}
          <div className="mono" style={{ color: data.promptSha.equal ? "var(--text)" : "var(--loss)" }}>
            {data.promptSha.equal ? "✓" : "×"} prompt {shortSha(data.promptSha.a)} {data.promptSha.equal ? "=" : "≠"} {shortSha(data.promptSha.b)}
          </div>
          <div className="mono" style={{ color: data.modelId.equal ? "var(--text)" : "var(--loss)" }}>
            {data.modelId.equal ? "✓" : "×"} model {data.modelId.a} {data.modelId.equal ? "=" : "≠"} {data.modelId.b}
          </div>
        </div>
        <div className="fine">config {data.a} {shortSha(data.aConfigSha, 12)} · config {data.b} {shortSha(data.bConfigSha, 12)}</div>
      </section>
      <SideBySide diff={data} />
      <div className="fine">
        <Link to={`/settings/pipelines/${encodeURIComponent(a)}`}>{a} details</Link> · <Link to={`/settings/pipelines/${encodeURIComponent(b)}`}>{b} details</Link>
      </div>
    </div>
  );
}

/** Two panes from the unified line diff; identical blocks collapse to one line with their sha. */
function SideBySide({ diff }: { diff: ConfigDiff }) {
  const rows: ({ kind: "pair"; left?: DiffLine; right?: DiffLine } | { kind: "collapsed"; block: string })[] = [];
  const collapsedShown = new Set<string>();
  const lines = diff.lines;
  for (let i = 0; i < lines.length; i++) {
    const l = lines[i];
    if (l.block && diff.identicalBlocks.includes(l.block) && l.op === " ") {
      if (!collapsedShown.has(l.block)) {
        collapsedShown.add(l.block);
        rows.push({ kind: "collapsed", block: l.block });
      }
      continue;
    }
    if (l.op === " ") rows.push({ kind: "pair", left: l, right: l });
    else if (l.op === "-") {
      const dels: DiffLine[] = [];
      while (i < lines.length && lines[i].op === "-") dels.push(lines[i++]);
      const adds: DiffLine[] = [];
      while (i < lines.length && lines[i].op === "+") adds.push(lines[i++]);
      i--;
      for (let k = 0; k < Math.max(dels.length, adds.length); k++) rows.push({ kind: "pair", left: dels[k], right: adds[k] });
    } else rows.push({ kind: "pair", right: l });
  }
  const side = (l: DiffLine | undefined, which: "a" | "b") => {
    if (!l) return <div className="ln blank"><span /><span /><span /></div>;
    const cls = l.op === "-" ? "del" : l.op === "+" ? "add" : "";
    return (
      <div className={`ln ${cls}`}>
        <span className="no">{which === "a" ? l.aNo : l.bNo}</span>
        <span>{l.op === " " ? "" : l.op === "-" ? "−" : "+"}</span>
        <span>{l.text}</span>
      </div>
    );
  };
  return (
    <div className="diff" tabIndex={0} role="region" aria-label="Config diff">
      <div className="ln" style={{ fontFamily: "var(--sans)", color: "var(--secondary)" }}><span /><span /><span>v{diff.a}.yaml</span></div>
      <div className="ln" style={{ fontFamily: "var(--sans)", color: "var(--secondary)" }}><span /><span /><span>v{diff.b}.yaml</span></div>
      {rows.map((r, i) =>
        r.kind === "collapsed" ? (
          <div key={i} className="collapsed">
            {r.block}: identical (sha {shortSha(diff.blocks[r.block]?.a)})
          </div>
        ) : (
          <Fragment key={i}>
            {side(r.left, "a")}
            {side(r.right, "b")}
          </Fragment>
        ),
      )}
    </div>
  );
}

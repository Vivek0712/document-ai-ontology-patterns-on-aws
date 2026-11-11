import { type ReactNode, useEffect } from "react";
import type { PiiFlag, StageError, StageState, UnitType } from "@contracts/api";
import { RpApiError } from "../../../api/types";
import { mmss } from "../../../lib/format";
import { useNow } from "../../../hooks/useAsync";

const GLYPH: Record<StageState, string> = {
  PENDING: "",
  QUEUED: "…",
  RUNNING: "▸",
  SUCCEEDED: "✓",
  FAILED: "×",
  SKIPPED: "–",
};

export function StateTick({ state }: { state: StageState }) {
  return (
    <span className={`tick ${state}`} title={state} aria-label={state}>
      {GLYPH[state]}
    </span>
  );
}

/** "running 00:48": static outline pill, ticks once per second (no spinners). */
export function RunningTimer({ since, label = "running" }: { since?: string; label?: string }) {
  const now = useNow(Boolean(since));
  return <span>{label} {mmss(since ? now - Date.parse(since) : 0)}</span>;
}

export function StatePill({ state, since }: { state: StageState | string; since?: string }) {
  const cls =
    state === "SUCCEEDED" || state === "ready"
      ? "ok"
      : state === "FAILED" || state === "PARTIAL"
        ? "fail"
        : state === "RUNNING" || state === "QUEUED"
          ? "running"
          : "";
  return (
    <span className={`pill ${cls}`}>
      <i className="dot" aria-hidden />
      {state === "RUNNING" && since ? <RunningTimer since={since} /> : <>{cls === "ok" ? "✓ " : cls === "fail" ? "× " : ""}{String(state).toLowerCase()}</>}
    </span>
  );
}

/** Medallion tier: neutral text with a small tier glyph (never a coloured box that could read as the boundary). */
export function TierPill({ tier }: { tier: string }) {
  return (
    <span className={`pill tierpill tier-${tier}`}>
      <i className="dot" aria-hidden />
      {tier.charAt(0).toUpperCase() + tier.slice(1)}
    </span>
  );
}

export function UnitTag({ type }: { type: UnitType | string }) {
  return <span className="pill unit">{type}</span>;
}

export function EngineTag({ engine }: { engine?: string }) {
  return engine ? <span className="pill engine">{engine}</span> : null;
}

export function ServiceTags({ services }: { services: string[] }) {
  return (
    <span className="row" style={{ gap: 6 }}>
      {services.map((s) => (
        <span key={s} className="pill svc">
          {s}
        </span>
      ))}
    </span>
  );
}

export function PiiPill({ flag }: { flag: PiiFlag }) {
  if (flag === "held") return <span className="pill held">⏸ held: PII</span>;
  if (flag === "masked") return <span className="pill masked">masked</span>;
  return <span className="pill">pii: none</span>;
}

export function ErrorBox({ error, title }: { error: unknown; title?: string }) {
  if (!error) return null;
  const e = error instanceof RpApiError ? error : undefined;
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="err" role="alert">
      <b>× {title ?? (e ? `${e.status || ""} ${e.code}` : "error")}</b> {msg}
      {e?.requestId && <div className="fine">request {e.requestId}</div>}
    </div>
  );
}

export function StageErrorBox({ error }: { error: StageError }) {
  return (
    <div className="err">
      <div className="row">
        <b>× {error.type}</b>
        <span className={`pill ${error.retryable ? "running" : "fail"}`}>{error.retryable ? "retryable" : "not retryable"}</span>
        <span className="fine">{error.at}</span>
      </div>
      <div style={{ marginTop: 6 }}>{error.message}</div>
      {error.cause && <pre className="code wrap" tabIndex={0} style={{ marginTop: 8, maxHeight: 220 }}>{error.cause}</pre>}
    </div>
  );
}

export function Loading({ what = "loading" }: { what?: string }) {
  return <div className="muted">{what}…</div>;
}

export function KV({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}

/** JSON with key/value colouring (keys --l2). */
export function Json({ value, maxHeight }: { value: unknown; maxHeight?: number }) {
  const text = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  return (
    <pre className="code" tabIndex={0} style={maxHeight ? { maxHeight } : undefined}>
      {highlight(text ?? "null")}
    </pre>
  );
}

function highlight(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /("(?:\\.|[^"\\])*")(\s*:)?|\b(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b|\b(true|false|null)\b/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<span key={i++} className={m[2] ? "j-key" : "j-str"}>{m[1]}</span>, m[2] ?? "");
    else if (m[3]) out.push(<span key={i++} className="j-num">{m[3]}</span>);
    else out.push(<span key={i++} className="j-lit">{m[4]}</span>);
    last = re.lastIndex;
    if (out.length > 20000) break;
  }
  out.push(text.slice(last));
  return out;
}

export function Drawer({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  return (
    <>
      <div className="drawer-back" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="true">
        <button className="btn small ghost close" onClick={onClose} aria-label="Close">
          Esc ×
        </button>
        {children}
      </aside>
    </>
  );
}

export function Modal({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEscape(onClose);
  return (
    <>
      <div className="modal-back" onClick={onClose} />
      <div className="modal" role="dialog" aria-modal="true">
        <button className="btn small ghost" style={{ float: "right" }} onClick={onClose} aria-label="Close">
          Esc ×
        </button>
        {children}
      </div>
    </>
  );
}

export function useEscape(fn: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.stopPropagation();
        fn();
      }
    };
    window.addEventListener("keydown", h, true);
    return () => window.removeEventListener("keydown", h, true);
  }, [fn]);
}

export function Section({ title, right, children }: { title: ReactNode; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="row" style={{ marginBottom: 12 }}>
        <h2>{title}</h2>
        <span className="spacer" />
        {right}
      </div>
      {children}
    </section>
  );
}

import { type ReactNode, useCallback, useState } from "react";
import type { ArtifactRef, ArtifactView } from "@contracts/api";
import { useApi } from "../../../api/context";
import { useAsync } from "../../../hooks/useAsync";
import { bytes, dateTime, shortSha } from "../../../lib/format";
import { ErrorBox, Json, KV, Loading, Modal, TierPill } from "./ui";

function tierOf(key: string): string {
  return key.split("/")[0] ?? "";
}

/** Preview body for GET /artifacts?key= by content type (JSON, JSONL, YAML/text, image, PDF). */
export function ArtifactPreview({ view }: { view: ArtifactView }) {
  const ct = view.contentType;
  if (ct.startsWith("image/")) {
    return view.url ? (
      <img src={view.url} alt={view.key} style={{ maxWidth: "100%", background: "#fff" }} />
    ) : (
      <div className="empty">no presigned URL for this tier</div>
    );
  }
  if (ct === "application/pdf") {
    return view.url ? (
      <object data={view.url} type="application/pdf" style={{ width: "100%", height: "70vh" }}>
        <a href={view.url} target="_blank" rel="noreferrer">open PDF</a>
      </object>
    ) : (
      <div className="empty">PDF preview not available: presigned GET is issued for Bronze to the owner only, never for Silver.</div>
    );
  }
  if (view.preview === undefined || view.preview === null) return <div className="empty">no text preview</div>;
  if (ct.includes("ndjson") || view.key.endsWith(".jsonl")) {
    const lines = view.preview.split("\n").filter(Boolean);
    return (
      <div className="stack">
        <div className="fine">{lines.length} lines</div>
        {lines.map((l, i) => {
          let parsed: unknown = l;
          try {
            parsed = JSON.parse(l);
          } catch {
            /* show raw */
          }
          return (
            <details key={i} open={i === 0}>
              <summary className="mono" style={{ cursor: "pointer" }}>
                line {i + 1}
                {typeof parsed === "object" && parsed && "unitId" in parsed ? ` · ${(parsed as { unitId: string }).unitId}` : ""}
              </summary>
              <Json value={parsed} maxHeight={360} />
            </details>
          );
        })}
      </div>
    );
  }
  if (ct.includes("json")) {
    try {
      return <Json value={JSON.parse(view.preview)} />;
    } catch {
      /* fall through to text */
    }
  }
  return (
    <pre className="code" tabIndex={0} aria-label="Artifact preview">
      {view.preview.split("\n").map((l, i) => (
        <div key={i}>
          <span style={{ color: "var(--fine)", userSelect: "none", display: "inline-block", width: 40 }}>{i + 1}</span>
          {l}
        </div>
      ))}
    </pre>
  );
}

function ArtifactModalBody({ artifactKey }: { artifactKey: string }) {
  const api = useApi();
  const { data, error, loading } = useAsync(() => api.getArtifact(artifactKey), [artifactKey]);
  return (
    <div className="stack">
      <div className="row">
        <TierPill tier={tierOf(artifactKey)} />
        <h2 className="mono" style={{ fontSize: "var(--fs-s)", overflowWrap: "anywhere" }}>{artifactKey}</h2>
      </div>
      {loading && <Loading />}
      <ErrorBox error={error} />
      {data && (
        <>
          <KV
            rows={[
              ["content type", data.contentType],
              ["size", bytes(data.sizeBytes)],
              ["S3 versionId", <span className="mono">{data.versionId ?? "—"}</span>],
              ["etag", <span className="mono">{data.etag ?? "—"}</span>],
              ["last modified", dateTime(data.lastModified)],
              ["preview", `${data.previewTruncated ? "truncated to 64 KB" : "complete"}${data.previewMasked ? " · long digit runs masked" : ""}`],
              ["download", data.url ? <a href={data.url} target="_blank" rel="noreferrer">presigned GET ({data.expiresIn ?? 300} s)</a> : "not issued for this tier"],
            ]}
          />
          <ArtifactPreview view={data} />
        </>
      )}
    </div>
  );
}

/** Hook: `open(key)` shows the artifact modal; render `element` once. */
export function useArtifactModal(): [(key: string) => void, ReactNode] {
  const [key, setKey] = useState<string>();
  const close = useCallback(() => setKey(undefined), []);
  const element = key ? (
    <Modal onClose={close}>
      <ArtifactModalBody artifactKey={key} />
    </Modal>
  ) : null;
  return [setKey, element];
}

export function ArtifactList({ items, empty = "none" }: { items: ArtifactRef[]; empty?: string }) {
  const [open, modal] = useArtifactModal();
  if (!items.length) return <div className="muted">{empty}</div>;
  return (
    <div>
      {items.map((a, i) => (
        <div className="art-row" key={`${a.key}-${i}`}>
          <span className="row" style={{ gap: 6 }}>
            <TierPill tier={tierOf(a.key)} />
            <span className="pill">{a.kind}</span>
          </span>
          <span className="key">
            {a.key}
            {a.note && <span className="fine"> · {a.note}</span>}
          </span>
          <span className="muted">
            {bytes(a.sizeBytes)}
            {a.count !== undefined && <div className="fine">{a.count} items</div>}
          </span>
          <span className="fine mono" title={a.versionId}>
            v {shortSha(a.versionId, 8)}
            <br />
            sha {shortSha(a.sha256, 8)}
          </span>
          <button className="btn small" onClick={() => open(a.key)}>
            open
          </button>
        </div>
      ))}
      {modal}
    </div>
  );
}

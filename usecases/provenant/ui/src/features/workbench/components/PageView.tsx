/**
 * Page image + overlays in the canonical frame (PDF points, top-left origin). The SVG overlay uses a
 * viewBox of the page size in points, so every bbox is drawn at its stored coordinates with no flip;
 * the browser does the imageWidthPx / pageWidth scaling.
 */
import { type ReactNode, useState } from "react";
import type { BBox } from "@contracts/api";

export interface Overlay {
  bbox: BBox;
  kind: "region" | "unit" | "member" | "held" | "block";
  color?: string;
  label?: string;
  dashed?: boolean;
  title?: string;
  onClick?: () => void;
}

interface Props {
  url?: string;
  page: number;
  pageWidth?: number;
  pageHeight?: number;
  overlays: Overlay[];
  maxWidth?: number;
  missing?: ReactNode;
}

export function PageView({ url, page, pageWidth = 612, pageHeight = 792, overlays, maxWidth = 760, missing }: Props) {
  const [failed, setFailed] = useState(false);
  const mine = overlays.filter((o) => o.bbox.page === page);
  if (!url || failed) {
    return (
      <div className="page-missing" style={{ maxWidth }}>
        {missing ?? (failed ? "page image could not be loaded (presigned URL expired? reopen to refresh)" : "no page image")}
      </div>
    );
  }
  // draw order: blocks/members, unit outline, held fills, region stroke on top
  const order = { block: 0, member: 1, unit: 2, held: 3, region: 4 } as const;
  const sorted = [...mine].sort((a, b) => order[a.kind] - order[b.kind]);
  return (
    <div className="page-view" style={{ maxWidth }}>
      <img src={url} alt={`page ${page}`} onError={() => setFailed(true)} />
      <svg className="overlay" viewBox={`0 0 ${pageWidth} ${pageHeight}`} preserveAspectRatio="none">
        {sorted.map((o, i) => {
          const { x0, y0, x1, y1 } = o.bbox;
          const w = x1 - x0;
          const h = y1 - y0;
          if (o.kind === "held") {
            return (
              <g key={i}>
                <rect x={x0} y={y0} width={w} height={h} fill="#000" stroke="var(--boundary)" strokeWidth={2} />
                <rect x={x0 + 6} y={y0 + h / 2 - 9} width={84} height={18} rx={3} fill="#2E2E2E" />
                <text x={x0 + 12} y={y0 + h / 2 + 4} fill="#fff" fontSize={11} fontFamily="Arial" fontWeight={700}>
                  held: PII
                </text>
              </g>
            );
          }
          if (o.kind === "region") {
            const pad = 4;
            return (
              <rect
                key={i}
                className="stroke-draw"
                x={x0 - pad}
                y={y0 - pad}
                width={w + 2 * pad}
                height={h + 2 * pad}
                rx={6}
                fill="none"
                stroke="var(--boundary)"
                strokeWidth={4}
              >
                {o.title && <title>{o.title}</title>}
              </rect>
            );
          }
          const color = o.color ?? (o.kind === "unit" ? "var(--l2)" : "var(--l1)");
          return (
            <g key={i} style={{ cursor: o.onClick ? "pointer" : undefined }} onClick={o.onClick}>
              <rect
                x={x0}
                y={y0}
                width={w}
                height={h}
                fill={o.kind === "member" || o.kind === "block" ? "rgba(61,155,255,0.06)" : "none"}
                stroke={color}
                strokeWidth={o.kind === "unit" ? 2.5 : 1.4}
                strokeDasharray={o.dashed ? "5 3" : undefined}
              >
                <title>{o.title ?? o.label ?? ""}</title>
              </rect>
              {o.label && (
                <text x={x0 + 2} y={y0 - 2} fill={color} fontSize={8} fontFamily="Arial" fontWeight={700}>
                  {o.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

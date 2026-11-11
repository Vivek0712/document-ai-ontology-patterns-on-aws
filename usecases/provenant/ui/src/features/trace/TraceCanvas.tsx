/**
 * The page side of a trace (W-08, SC-9, SC-0 step 7): the masked Gold page with the unit boundary, the cited
 * field, its engine blocks and a crosshair with corner coordinates. Each layer takes a 0..1 amount, so a story can
 * drive it per frame and the workbench can show everything at 1.
 */
import type { ReactNode } from "react";
import type { Trace } from "../../api/trace";
import { type Rect, pad, toRect } from "../../viz/geom";
import { Boundary, BoxOutline, Crosshair, HeldRegions, Mark, PageCanvas, Tag } from "../../viz/PageCanvas";

/** Decimals for region coordinates in PDF points: the canvas ticks and the lineage card print the same numbers. */
export const REGION_DIGITS = 1;

export interface TraceLayers {
  /** boundary draw progress */
  unit?: number;
  field?: number;
  /** the ✓ beside the field (green only for the cited value) */
  mark?: number;
  crosshair?: number;
  blocks?: number;
}

/** Camera rects for a trace: the page, the unit, and the field zoom (at most `maxZoom`× the page; spec ≤ 6×). */
export function traceFrames(t: Trace, maxZoom = 4.5): { page: Rect; unit: Rect; field: Rect; zoom: Rect } {
  const page: Rect = [0, 0, t.region.pageWidth, t.region.pageHeight];
  const field = toRect(t.region.bbox);
  const [cx, cy] = [(field[0] + field[2]) / 2, (field[1] + field[3]) / 2];
  const w = Math.max(field[2] - field[0] + 48, page[2] / maxZoom);
  const h = Math.max(field[3] - field[1] + 48, page[3] / maxZoom);
  return { page, unit: pad(toRect(t.region.unitBbox), 16), field, zoom: [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2] };
}

/** Engine blocks grouped by identical box (Docling often returns two items for one cell): one outline, one tag. */
function blockGroups(t: Trace): { key: string; rect: Rect; label: string }[] {
  const groups = new Map<string, { key: string; rect: Rect; label: string }>();
  for (const b of t.blocks.items) {
    if (!b.bbox) continue;
    const rect = toRect(b.bbox);
    const key = rect.map((n) => n.toFixed(1)).join(",");
    const label = `${b.pointer} · ${b.engineLabel ?? "block"}`;
    const g = groups.get(key);
    groups.set(key, g ? { ...g, label: `${g.label}  ${label}` } : { key, rect, label });
  }
  return [...groups.values()];
}

export function TraceCanvas({
  trace: t,
  focus,
  widthFit = 0,
  layers = {},
  animate = false,
  interactive = false,
  imageOpacity = 1,
  frame,
  children,
}: {
  trace: Trace;
  focus?: Rect;
  widthFit?: number;
  layers?: TraceLayers;
  animate?: boolean;
  interactive?: boolean;
  imageOpacity?: number;
  frame?: ReactNode;
  children?: ReactNode;
}) {
  const page: Rect = [0, 0, t.region.pageWidth, t.region.pageHeight];
  const field = toRect(t.region.bbox);
  const unit = toRect(t.region.unitBbox);
  const { unit: u = 0, field: f = 0, mark = 0, crosshair = 0, blocks = 0 } = layers;
  return (
    <PageCanvas
      ariaLabel={`${t.source.fileName}, page ${t.region.page}`}
      image={t.region.image.url ? { src: t.region.image.url, dpi: t.region.image.dpi } : undefined}
      pageWidth={t.region.pageWidth}
      pageHeight={t.region.pageHeight}
      focus={focus ?? page}
      widthFit={widthFit}
      animate={animate}
      interactive={interactive}
      imageOpacity={imageOpacity}
      frame={frame}
    >
      <HeldRegions regions={t.region.heldRegions.map(toRect)} />
      {blocks > 0 &&
        blockGroups(t).map((g, i) => (
          <g key={g.key} opacity={blocks}>
            <BoxOutline rect={pad(g.rect, 2 + i * 2)} tone="layout" dashed title={g.label} />
            <Tag x={g.rect[0] - 2 - i * 2} y={g.rect[3] + 2 + i * 2} text={g.label} tone="layout" placement="below" mono />
          </g>
        ))}
      {u > 0 && <Boundary rect={unit} width={1} tint={0.6} progress={u} />}
      {f > 0 && <BoxOutline rect={pad(field, 2)} tone="cited" width={1.5} fill="var(--cited-tint)" opacity={f} />}
      {mark > 0 && <Mark x={field[2] + 2} y={field[1]} kind="cited" opacity={mark} />}
      <Crosshair rect={field} opacity={crosshair} digits={REGION_DIGITS} />
      {children}
    </PageCanvas>
  );
}

/** Adapters from the X-ray contract (rect objects) to PageCanvas layer props (Rect tuples). */
import type { XrayChunk, XrayPage } from "../api/xray";
import { toRect } from "./geom";
import type { ChunkSlice, PathStop } from "./PageCanvas";

export const sliceOf = (c: XrayChunk): ChunkSlice => ({ chunkId: c.chunkId, label: c.label, rects: c.rects.map(toRect) });

export const stopsOf = (x: XrayPage): PathStop[] =>
  x.lines.filter((l) => l.bbox).map((l) => ({ ordinal: l.ordinal, kind: l.kind, rect: toRect(l.bbox!) }));
